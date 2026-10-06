"""
process_module.py
Pestaña "Procesos" del dashboard (build_process_tab) -- administrador de
tareas simple: RAM en MB y %CPU por proceso, ícono si resuelve uno,
botón para cerrarlo (con confirmación), y una línea de disco libre
arriba. Se arma una sola vez (mismo patrón lazy que Personalización/Menu
en dashboard.py) y se refresca sola cada REFRESH_MS mientras el
dashboard sigue abierto.

Agrupados por nombre de proceso (Gtk.Expander, colapsado por default):
esta máquina corre ~400 procesos en background, muchos repetidos --
Chromium/Electron abre varios "Isolated Web Co"/helpers por pestaña,
vesktop/steam tienen varios procesos hijos con el mismo nombre. Listarlos
sueltos (versión anterior de esta pestaña) hacía la lista larguísima con
el mismo nombre repetido; agrupar por nombre (RAM/CPU sumados, contador
"×N") y permitir desplegar cada grupo para ver los PIDs individuales
resuelve eso sin perder la posibilidad real de cerrar un PID puntual. Los
procesos con una sola instancia se muestran sueltos, sin el overhead
visual de un desplegable que no aporta nada ahí.

self._expanded (nombres de grupo desplegados) se preserva entre
refrescos -- las filas se reconstruyen enteras cada REFRESH_MS (mismo
patrón que refresh() en menu_module.py), así que sin este set cualquier
grupo abierto se volvería a cerrar solo cada 2.5s.

psutil.process_iter() cachea sus objetos Process internamente entre
llamadas (confirmado a mano) -- alcanza con volver a llamarlo en cada
refresco y usar cpu_percent(None) cada vez para tener una lectura real
(la primera lectura de un Process nuevo siempre da 0.0, sin muestra
anterior con la que comparar); no hace falta mantener un cache propio de
objetos Process como si fueran a perderse entre refrescos.
""" 

import os
import subprocess
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, GLib, GdkPixbuf, Pango

import psutil

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import system_stats

sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from waybar_lib import load_module_css, svg_icon_image, _icon_text_button
from i18n import t

sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "toggles"))
from bar_toggles import build_sysmon_toggle

MODULE_CSS = os.path.join(SCRIPT_DIR, "process_module.css")
SERVICES_POPUP = os.path.join(SCRIPT_DIR, "..", "services", "services_popup.py")
DISK_POPUP = os.path.join(SCRIPT_DIR, "..", "disk", "disk_usage_popup.py")

ICONS_DIR = os.path.join(SCRIPT_DIR, "icons")
SERVICES_ICON = os.path.join(ICONS_DIR, "services.svg")

ICON_SIZE = 22
GROUP_LIMIT = 40
REFRESH_MS = 2500

SORT_OPTIONS = [
    ("mem_mb", t("procesos", "sort_ram")),
    ("cpu", t("procesos", "sort_cpu")),
    ("name", t("procesos", "sort_nombre")),
]


class ProcessTab:
    def __init__(self, container):
        self._icon_cache = {}
        self._expanded = set()
        self._timeout_id = None
        self.sort_key = "mem_mb"

        # header: disco a la izquierda, "Servicios" a la derecha -- fila
        # homogeneous 50/50, fill=True o el halign no se respeta adentro.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        header.set_homogeneous(True)

        self.disk_label = Gtk.Label()
        disk_btn = Gtk.Button()
        disk_btn.add(self.disk_label)
        disk_btn.set_halign(Gtk.Align.START)
        disk_btn.set_tooltip_text(t("procesos", "disco_tooltip"))
        disk_btn.get_style_context().add_class("process-disk-label")
        disk_btn.connect("clicked", lambda _b: subprocess.Popen(["python3", DISK_POPUP]))
        header.pack_start(disk_btn, False, True, 0)

        services_btn = _icon_text_button(
            SERVICES_ICON, t("procesos", "servicios_btn"), t("procesos", "servicios_tooltip"))
        services_btn.get_style_context().add_class("process-services-btn")
        services_btn.set_halign(Gtk.Align.CENTER)
        services_btn.connect("clicked", self._on_open_services)
        header.pack_start(services_btn, False, True, 0)

        container.pack_start(header, False, False, 0)

        # "Métricas de Uso" (custom/sysmon en la barra)
        # "Configuración del Panel" de la pestaña Sistema
        # sysmon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        # build_sysmon_toggle(sysmon_row)
        # container.pack_start(sysmon_row, False, False, 0)

        self._build_sort_row(container)

        self._build_totals_row(container)

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("process-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.listbox = Gtk.ListBox()
        self.listbox.set_name("process-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.add(self.listbox)
        container.pack_start(scroller, True, True, 0)

        container.connect("destroy", self._on_destroy)

        self.refresh()
        self._timeout_id = GLib.timeout_add(REFRESH_MS, self._on_tick)

    # ---- Ciclo de vida --------------------------------------------------

    def _on_destroy(self, *_a):
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _on_tick(self):
        self.refresh()
        return True  # GLib.timeout_add: seguir repitiendo

    def _on_open_services(self, _btn):
        subprocess.Popen(["python3", SERVICES_POPUP])

    # ---- Totales ----------------------------------------------------------

    def _build_totals_row(self, container):
        """Fila FUERA del Gtk.ListBox/scroller -- siempre hasta arriba, no
        scrollea con los procesos. Reusa _make_mem_label/_make_cpu_label
        para que los números queden en la columna exacta de la lista."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("process-row")
        row.get_style_context().add_class("process-totals-row")

        self.totals_name_label = Gtk.Label()
        self.totals_name_label.set_halign(Gtk.Align.START)
        self.totals_name_label.get_style_context().add_class("process-totals-name")
        row.pack_start(self.totals_name_label, True, True, 0)

        self.totals_mem_label = self._make_mem_label(0)
        self.totals_cpu_label = self._make_cpu_label(0)
        row.pack_start(self.totals_mem_label, False, False, 0)
        row.pack_start(self.totals_cpu_label, False, False, 0)

        # Espaciador invisible del mismo ancho que el botón ✕ real (no un
        # ancho fijo a mano) -- así CPU queda alineado con la columna real.
        close_spacer = self._make_close_btn([], "")
        close_spacer.set_sensitive(False)
        close_spacer.set_opacity(0)
        close_spacer.set_tooltip_text(None)
        row.pack_start(close_spacer, False, False, 0)

        container.pack_start(row, False, False, 0)

    # ---- Orden -----------------------------------------------------------

    def _build_sort_row(self, container):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_name("process-sort-row")

        caption = Gtk.Label(label=t("procesos", "ordenar_por"))
        caption.get_style_context().add_class("process-sort-label")

        sysmon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        build_sysmon_toggle(sysmon_row)
        sysmon_row.get_style_context().add_class('sysmon-row')

        row.pack_start(caption, False, False, 0)

        self.sort_buttons = {}
        for key, label in SORT_OPTIONS:
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("process-sort-btn")
            btn.connect("clicked", self._on_sort_clicked, key)
            row.pack_start(btn, False, False, 0)
            self.sort_buttons[key] = btn

        row.pack_start(sysmon_row, True, True, 0)

        container.pack_start(row, False, False, 0)

        self._sync_sort_buttons()

    def _on_sort_clicked(self, _btn, key):
        self.sort_key = key
        self._sync_sort_buttons()
        self.refresh()

    def _sync_sort_buttons(self):
        for key, btn in self.sort_buttons.items():
            ctx = btn.get_style_context()
            if key == self.sort_key:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

    # ---- Datos ------------------------------------------------------------

    def refresh(self):
        du = system_stats.disk_usage("/")
        self.disk_label.set_text(t(
            "procesos", "disco_label",
            free_tb=du["free_gb"] / 1024, total_tb=du["total_gb"] / 1024,
        ))

        groups, total_mem, total_cpu = self._collect_groups()
        self.totals_name_label.set_text(t("procesos", "total_procesos", n=len(groups)))
        self.totals_mem_label.set_text(t("procesos", "mem_label", mem_mb=total_mem))
        self.totals_cpu_label.set_text(t("procesos", "cpu_label", cpu=total_cpu))

        for child in self.listbox.get_children():
            self.listbox.remove(child)
        for group in groups[:GROUP_LIMIT]:
            self.listbox.add(self._make_group_row(group))
        self.listbox.show_all()

    def _collect_groups(self):
        """Sobre TODOS los procesos -- el total no se recorta a
        GROUP_LIMIT, o dejaría de representar el consumo real."""
        groups = {}
        total_mem = 0.0
        total_cpu = 0.0
        ncpu = psutil.cpu_count() or 1
        for p in psutil.process_iter(["pid", "name"]):
            try:
                cpu = p.cpu_percent(None) / ncpu
                mem_mb = p.memory_info().rss / (1024 * 1024)
                name = p.info["name"] or f"pid {p.info['pid']}"
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

            group = groups.setdefault(name, {"name": name, "cpu": 0.0, "mem_mb": 0.0, "instances": []})
            group["cpu"] += cpu
            group["mem_mb"] += mem_mb
            group["instances"].append({"pid": p.info["pid"], "name": name, "cpu": cpu, "mem_mb": mem_mb})
            total_mem += mem_mb
            total_cpu += cpu

        groups = list(groups.values())
        if self.sort_key == "name":
            groups.sort(key=lambda g: g["name"].lower())
        else:
            groups.sort(key=lambda g: g[self.sort_key], reverse=True)
        return groups, total_mem, total_cpu

    # ---- Fila (grupo con 1 sola instancia -- sin desplegable) ----------

    def _make_group_row(self, group):
        if len(group["instances"]) == 1:
            return self._make_single_row(group["instances"][0])
        return self._make_expander_row(group)

    def _make_single_row(self, info):
        # Espaciador invisible del ancho real de la flecha de un grupo
        # desplegable -- mismo criterio que close_spacer arriba.
        arrow_spacer = Gtk.Image.new_from_icon_name("pan-end-symbolic", Gtk.IconSize.SMALL_TOOLBAR)
        arrow_spacer.set_opacity(0)
        row = self._make_stat_row(info["name"], info, leading=[arrow_spacer])

        close_btn = self._make_close_btn([info["pid"]], info["name"])
        row.pack_start(close_btn, False, False, 0)

        wrap = Gtk.ListBoxRow()
        wrap.set_selectable(False)
        wrap.add(row)
        return wrap

    def _make_stat_row(self, label_text, totals, with_icon=True, leading=None):
        """Fila compartida entre proceso suelto y cabecera de grupo
        desplegable (leading=[flecha]). Sin Gtk.Expander -- ver CLAUDE.md
        "Desplegable de grupo sin Gtk.Expander"."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("process-row")

        for widget in leading or []:
            row.pack_start(widget, False, False, 0)

        if with_icon:
            row.pack_start(self._make_icon_slot(totals.get("name", label_text)), False, False, 0)

        name_label = Gtk.Label(label=label_text)
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_max_width_chars(28)
        name_label.get_style_context().add_class("process-name")
        row.pack_start(name_label, True, True, 0)

        row.pack_start(self._make_mem_label(totals["mem_mb"]), False, False, 0)
        row.pack_start(self._make_cpu_label(totals["cpu"]), False, False, 0)

        return row

    def _make_mem_label(self, mem_mb):
        label = Gtk.Label(label=t("procesos", "mem_label", mem_mb=mem_mb))
        label.get_style_context().add_class("process-stat")
        # width_chars + xalign=1 -- columnas alineadas de verdad (los
        # glifos de distintos dígitos no pesan igual en píxeles).
        label.set_width_chars(8)
        label.set_xalign(1.0)
        return label

    def _make_cpu_label(self, cpu_pct):
        label = Gtk.Label(label=t("procesos", "cpu_label", cpu=cpu_pct))
        label.get_style_context().add_class("process-stat")
        label.set_width_chars(11)
        label.set_xalign(1.0)
        return label

    def _make_close_btn(self, pids, name):
        close_btn = Gtk.Button()
        close_btn.add(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
        close_btn.get_style_context().add_class("process-close-btn")
        close_btn.set_tooltip_text(
            t("procesos", "cerrar_app_tooltip") if len(pids) == 1
            else t("procesos", "cerrar_instancias_tooltip", n=len(pids)))
        close_btn.connect("clicked", self._on_close_clicked, pids, name)
        return close_btn

    # ---- Fila (grupo con varias instancias -- desplegable) -------------

    def _make_expander_row(self, group):
        name = group["name"]
        is_open = name in self._expanded

        arrow = Gtk.Image.new_from_icon_name(
            "pan-down-symbolic" if is_open else "pan-end-symbolic", Gtk.IconSize.SMALL_TOOLBAR)
        header = self._make_stat_row(f'{name}  ×{len(group["instances"])}', group, leading=[arrow])
        # El padding de ".process-row" se mueve a header_row -- dejarlo acá
        # también lo duplicaría de un lado (RAM/CPU corridos vs. fila suelta).
        header.get_style_context().remove_class("process-row")

        # EventBox, no Gtk.Button/Gtk.Expander -- mismo motivo que el
        # drag-and-drop de menu_module.py (ver CLAUDE.md).
        event_box = Gtk.EventBox()
        event_box.add(header)
        event_box.get_style_context().add_class("process-group-header")

        # Botón de cerrar grupo AFUERA del EventBox (hermano, no hijo) --
        # zonas de click separadas, sin depender de que no se propague.
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        header_row.get_style_context().add_class("process-row")
        header_row.pack_start(event_box, True, True, 0)
        header_row.pack_start(
            self._make_close_btn([i["pid"] for i in group["instances"]], name), False, False, 0)

        child_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        child_box.get_style_context().add_class("process-instance-box")
        for inst in sorted(group["instances"], key=lambda i: i["mem_mb"], reverse=True):
            child_box.pack_start(self._make_instance_row(inst), False, False, 0)
        # no_show_all: sin esto, el show_all() que corre refresh() sobre
        # toda la lista revelaría los grupos colapsados de nuevo.
        child_box.set_no_show_all(not is_open)
        child_box.set_visible(is_open)

        event_box.connect("button-press-event", self._on_group_header_clicked, name, child_box, arrow)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.pack_start(header_row, False, False, 0)
        outer.pack_start(child_box, False, False, 0)

        wrap = Gtk.ListBoxRow()
        wrap.set_selectable(False)
        wrap.add(outer)
        return wrap

    def _on_group_header_clicked(self, _widget, _event, name, child_box, arrow):
        opening = name not in self._expanded
        if opening:
            self._expanded.add(name)
        else:
            self._expanded.discard(name)

        child_box.set_no_show_all(not opening)
        child_box.set_visible(opening)
        if opening:
            child_box.show_all()
        arrow.set_from_icon_name(
            "pan-down-symbolic" if opening else "pan-end-symbolic", Gtk.IconSize.SMALL_TOOLBAR)

    def _make_instance_row(self, info):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("process-instance-row")

        pid_label = Gtk.Label(label=t("procesos", "pid_prefix", pid=info["pid"]))
        pid_label.set_halign(Gtk.Align.START)
        pid_label.get_style_context().add_class("process-instance-pid")
        row.pack_start(pid_label, True, True, 0)

        row.pack_start(self._make_mem_label(info["mem_mb"]), False, False, 0)
        row.pack_start(self._make_cpu_label(info["cpu"]), False, False, 0)

        row.pack_start(self._make_close_btn([info["pid"]], info["name"]), False, False, 0)
        return row

    # ---- Ícono (async, cacheado por nombre) ----------------------------

    def _make_icon_slot(self, name):
        slot = Gtk.Box()
        slot.set_size_request(ICON_SIZE, ICON_SIZE)

        cached = self._icon_cache.get(name)
        if cached == "none":
            slot.add(Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.SMALL_TOOLBAR))
            return slot
        if cached is not None:
            slot.add(Gtk.Image.new_from_pixbuf(cached))
            return slot

        slot.add(Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.SMALL_TOOLBAR))

        info = Gtk.IconTheme.get_default().lookup_icon(
            name.lower(), ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
        icon_path = info.get_filename() if info else None
        if not icon_path:
            self._icon_cache[name] = "none"
            return slot

        def worker():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, ICON_SIZE, ICON_SIZE)
            except GLib.Error:
                GLib.idle_add(self._icon_cache.__setitem__, name, "none")
                return
            GLib.idle_add(self._apply_icon, slot, pixbuf, name)

        threading.Thread(target=worker, daemon=True).start()
        return slot

    def _apply_icon(self, slot, pixbuf, name):
        self._icon_cache[name] = pixbuf
        # El slot puede haber quedado desprendido si un refresco reconstruyó
        # la lista antes de que el hilo terminara -- no rompe nada, el
        # cache ya quedó completo para la próxima fila con este nombre.
        for child in slot.get_children():
            slot.remove(child)
        slot.add(Gtk.Image.new_from_pixbuf(pixbuf))
        slot.show_all()
        return False  # GLib.idle_add: correr una sola vez

    # ---- Cerrar proceso (con confirmación) -----------------------------

    def _on_close_clicked(self, btn, pids, name):
        win = btn.get_toplevel()
        if len(pids) == 1:
            text = t("procesos", "confirmar_cerrar", name=name)
            secondary = t("procesos", "pid_prefix", pid=pids[0])
        else:
            text = t("procesos", "confirmar_cerrar_instancias", n=len(pids), name=name)
            secondary = t("procesos", "pids_lista", pids=", ".join(str(p) for p in pids))
        dialog = Gtk.MessageDialog(
            transient_for=win, flags=0, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=text,
        )
        dialog.format_secondary_text(secondary)

        # Mismo workaround de layer-shell que theme-editor.py -- ver
        # CLAUDE.md "Editor de temas".
        original_prgname = GLib.get_prgname()
        GLib.set_prgname("process-close-dialog")
        dialog.show_all()
        GLib.set_prgname(original_prgname)

        win.hide()
        response = dialog.run()
        dialog.destroy()
        win.show()

        if response == Gtk.ResponseType.YES:
            for pid in pids:
                try:
                    psutil.Process(pid).terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            GLib.timeout_add(400, self._refresh_once)

    def _refresh_once(self):
        self.refresh()
        return False  # GLib.timeout_add: correr una sola vez


def build_process_tab(container):
    load_module_css(MODULE_CSS)
    ProcessTab(container)

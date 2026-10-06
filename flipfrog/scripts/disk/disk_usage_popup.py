#!/usr/bin/env python3
"""Popup "Uso de disco" -- árbol tipo TreeSize de lo que ocupa cada
carpeta de `/`. Se abre con click en la línea de disco de la pestaña
"Procesos". El recorrido lo hace disk_scan.py en un proceso aparte
(como el usuario, o como root vía `pkexec` si hay carpetas protegidas y
el usuario lo pide -- el agente de polkit es el que pide la contraseña).

Árbol perezoso: cada carpeta agrega sus hijos a la TreeStore recién al
expandirse (fila "cargando" de relleno mientras tanto). Los archivos de
una carpeta van juntos en una fila "[N archivos]", expandible a los más
pesados. El último escaneo se cachea unos minutos para que cerrar el
popup al abrir Thunar no obligue a pedir la contraseña otra vez."""

import json
import os
import subprocess
import sys
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top
from common import atomic_write
from i18n import t

CSS_FILE = os.path.join(SCRIPT_DIR, "disk_usage_popup.css")
SCAN_SCRIPT = os.path.join(SCRIPT_DIR, "disk_scan.py")
LOCK = "/tmp/disk-usage.pid"
CACHE_FILE = os.path.expanduser("~/.cache/flipfrog-disk-usage.json")
CACHE_MAX_AGE = 15 * 60
ROOT = "/"
FILE_MANAGER = "thunar"
PKEXEC_DENIED = (126, 127)

TREE_WIDTH = 880
TREE_HEIGHT = 540

KIND_DIR, KIND_FILES, KIND_FILE, KIND_DENIED, KIND_PLACEHOLDER = range(5)
COL_NAME, COL_SIZE, COL_SIZE_TEXT, COL_PERCENT, COL_FILES, COL_PATH, COL_KIND, COL_ICON = range(8)
ICONS = {
    KIND_DIR: "folder-symbolic",
    KIND_FILES: "view-list-symbolic",
    KIND_FILE: "text-x-generic-symbolic",
    KIND_DENIED: "changes-prevent-symbolic",
    KIND_PLACEHOLDER: None,
}


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def _count_denied(node):
    return node[6] + sum(_count_denied(c) for c in node[5])


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("root") != ROOT or len(data.get("tree", [])) != 8 or time.time() - data.get("time", 0) > CACHE_MAX_AGE:
        return None
    return data


def save_cache(raw):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        atomic_write(CACHE_FILE, raw)
        os.chmod(CACHE_FILE, 0o600)
    except OSError:
        pass


class DiskUsagePopup:
    def __init__(self):
        self.window, container = build_layer_window("disk-usage", CSS_FILE)
        container.set_name("du-container")
        position_fixed_top(container, margin=60)

        self.nodes = {}
        self.proc = None
        self.as_root = False

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title = Gtk.Label(label=t("disco", "titulo"), xalign=0)
        title.set_name("du-title")
        header.pack_start(title, False, False, 0)

        self.rescan_btn = self._icon_button("view-refresh-symbolic", t("disco", "reescanear"))
        self.rescan_btn.connect("clicked", lambda _b: self.start_scan(self.as_root))
        header.pack_end(self.rescan_btn, False, False, 0)
        self.open_btn = self._text_button("folder-open-symbolic", t("disco", "abrir_thunar"))
        self.open_btn.set_tooltip_text(t("disco", "abrir_thunar_tooltip"))
        self.open_btn.connect("clicked", lambda _b: self._open_selected())
        header.pack_end(self.open_btn, False, False, 0)
        self.admin_btn = self._text_button("changes-allow-symbolic", t("disco", "escanear_admin"))
        self.admin_btn.set_tooltip_text(t("disco", "escanear_admin_tooltip"))
        self.admin_btn.connect("clicked", lambda _b: self.start_scan(True))
        self.admin_btn.set_no_show_all(True)
        header.pack_end(self.admin_btn, False, False, 0)
        container.pack_start(header, False, False, 0)

        self.status = Gtk.Label(xalign=0)
        self.status.set_name("du-status")
        self.status.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.status.set_max_width_chars(1)
        self.status.set_hexpand(True)
        container.pack_start(self.status, False, False, 0)

        self.store = Gtk.TreeStore(str, object, str, int, str, str, int, str)
        self.store.set_sort_column_id(COL_SIZE, Gtk.SortType.DESCENDING)
        self.store.set_sort_func(COL_SIZE, self._sort_by_size)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_name("du-tree")
        self.tree.set_enable_search(False)
        self.tree.set_enable_tree_lines(False)
        self.tree.connect("test-expand-row", self._on_expand)
        self.tree.connect("row-activated", self._on_activated)
        self.tree.connect("button-press-event", self._on_button_press)
        self.tree.get_selection().connect("changed", lambda _s: self._sync_open_btn())
        self._build_columns()

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("du-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_size_request(TREE_WIDTH, TREE_HEIGHT)
        scroller.add(self.tree)
        container.pack_start(scroller, True, True, 0)

        self.footer = Gtk.Label(xalign=0)
        self.footer.set_name("du-footer")
        container.pack_start(self.footer, False, False, 0)
        self._update_footer()

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()
        self._sync_open_btn()

        cached = load_cache()
        if cached:
            self._apply_result(cached)
        else:
            self.start_scan(False)

    # ---- widgets ------------------------------------------------------------

    def _icon_button(self, icon, tooltip):
        btn = Gtk.Button()
        btn.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR))
        btn.set_tooltip_text(tooltip)
        btn.get_style_context().add_class("du-btn")
        return btn

    def _text_button(self, icon, text):
        btn = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR), False, False, 0)
        box.pack_start(Gtk.Label(label=text), False, False, 0)
        btn.add(box)
        btn.get_style_context().add_class("du-btn")
        return btn

    def _build_columns(self):
        name_col = Gtk.TreeViewColumn(t("disco", "col_nombre"))
        icon = Gtk.CellRendererPixbuf()
        name_col.pack_start(icon, False)
        name_col.add_attribute(icon, "icon-name", COL_ICON)
        name = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
        name_col.pack_start(name, True)
        name_col.add_attribute(name, "text", COL_NAME)
        name_col.set_cell_data_func(name, self._style_name)
        name_col.set_expand(True)
        name_col.set_sort_column_id(COL_NAME)
        self.tree.append_column(name_col)
        self.tree.set_expander_column(name_col)

        bar = Gtk.CellRendererProgress(text="")
        bar_col = Gtk.TreeViewColumn(t("disco", "col_porcentaje"), bar, value=COL_PERCENT)
        bar_col.set_cell_data_func(bar, self._style_bar)
        bar_col.set_min_width(150)
        self.tree.append_column(bar_col)

        for title, col, width, sort in ((t("disco", "col_tamano"), COL_SIZE_TEXT, 100, COL_SIZE),
                                        (t("disco", "col_archivos"), COL_FILES, 90, None)):
            cell = Gtk.CellRendererText(xalign=1.0)
            column = Gtk.TreeViewColumn(title, cell, text=col)
            column.set_alignment(1.0)
            column.set_min_width(width)
            if sort is not None:
                column.set_sort_column_id(sort)
            self.tree.append_column(column)

    def _style_name(self, _col, cell, model, it, _data):
        kind = model[it][COL_KIND]
        cell.set_property("style", Pango.Style.ITALIC if kind in (KIND_FILES, KIND_DENIED, KIND_PLACEHOLDER)
                          else Pango.Style.NORMAL)
        cell.set_property("weight", 700 if kind == KIND_DIR else 400)

    def _style_bar(self, _col, cell, model, it, _data):
        percent = model[it][COL_PERCENT]
        cell.set_property("visible", model[it][COL_KIND] != KIND_PLACEHOLDER)
        cell.set_property("text", f"{percent}%")

    @staticmethod
    def _sort_by_size(model, a, b, _data):
        sa, sb = model[a][COL_SIZE] or 0, model[b][COL_SIZE] or 0
        return (sa > sb) - (sa < sb)

    # ---- escaneo --------------------------------------------------------------

    def start_scan(self, as_root):
        if self.proc is not None:
            return
        self.store.clear()
        self.nodes.clear()
        self.admin_btn.hide()
        self.rescan_btn.set_sensitive(False)
        self.status.set_text(t("disco", "esperando_contrasena") if as_root else t("disco", "escaneando_inicio"))

        cmd = [sys.executable, SCAN_SCRIPT, ROOT]
        if as_root:
            cmd = ["pkexec"] + cmd
            # El diálogo de polkit es otra superficie -- con la ventana
            # layer-shell encima no se podría escribir la contraseña.
            self.window.hide()
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError:
            self.window.show()
            self._scan_failed(t("disco", "error_escaneo"))
            return
        threading.Thread(target=self._read_progress, args=(self.proc, as_root), daemon=True).start()
        threading.Thread(target=self._read_result, args=(self.proc, as_root), daemon=True).start()

    def _read_progress(self, proc, as_root):
        shown = not as_root
        for line in proc.stderr:
            count, _, path = line.rstrip("\n").partition("\t")
            if not count.isdigit():
                continue
            if not shown:
                shown = True
                GLib.idle_add(self.window.show)
            GLib.idle_add(self.status.set_text,
                          t("disco", "escaneando", n=f"{int(count):,}", path=path))

    def _read_result(self, proc, as_root):
        raw = proc.stdout.read()
        code = proc.wait()
        data = None
        if code == 0:
            try:
                data = json.loads(raw)
            except ValueError:
                pass
        if data is not None:
            save_cache(raw)
        GLib.idle_add(self._on_scan_done, data, code, as_root)

    def _on_scan_done(self, data, code, as_root):
        self.proc = None
        if as_root:
            self.window.show()
        if data is None:
            if as_root and code in PKEXEC_DENIED:
                msg = t("disco", "admin_cancelado")
            else:
                msg = t("disco", "error_escaneo")
            self._scan_failed(msg)
            return False
        self._apply_result(data)
        return False

    def _scan_failed(self, msg):
        self.rescan_btn.set_sensitive(True)
        self.status.set_text(msg)
        cached = load_cache()
        if cached and not self.nodes:
            self._apply_result(cached, keep_status=True)

    def _apply_result(self, data, keep_status=False):
        self.rescan_btn.set_sensitive(True)
        self.as_root = data.get("as_root", False)
        tree = data["tree"]
        denied = _count_denied(tree)

        self.store.clear()
        self.nodes.clear()
        root_it = self._append(None, tree, data["root"], tree[1])
        self.tree.expand_row(self.store.get_path(root_it), False)

        if not keep_status:
            age_min = int((time.time() - data["time"]) // 60)
            parts = [t("disco", "resumen", size=human_size(tree[1]), n=f"{data['entries']:,}")]
            if age_min:
                parts.append(t("disco", "hace_min", n=age_min))
            if self.as_root:
                parts.append(t("disco", "como_admin"))
            elif denied:
                parts.append(t("disco", "protegidas", n=denied))
            self.status.set_text(" · ".join(parts))
        self.admin_btn.set_visible(bool(denied) and not self.as_root)
        self._update_footer()

    # ---- árbol ----------------------------------------------------------------

    def _append(self, parent_it, node, path, parent_size):
        name, size, files, _fsize, _top, children, denied, total_files = node
        kind = KIND_DENIED if denied else KIND_DIR
        label = t("disco", "sin_acceso", name=name) if denied else name
        it = self.store.append(parent_it, self._row(label, size, parent_size, total_files, path, kind))
        self.nodes[path] = node
        if children or files:
            self.store.append(it, self._placeholder())
        return it

    def _row(self, name, size, parent_size, files, path, kind):
        percent = round(100 * size / parent_size) if parent_size else 0
        files_text = f"{files:,}" if kind in (KIND_DIR, KIND_FILES) and files else ""
        return [name, size, human_size(size), percent, files_text, path, kind, ICONS[kind]]

    def _placeholder(self):
        return [t("disco", "cargando"), None, "", 0, "", "", KIND_PLACEHOLDER, None]

    def _on_expand(self, _tree, it, _path):
        child = self.store.iter_children(it)
        if child is None or self.store[child][COL_KIND] != KIND_PLACEHOLDER:
            return False
        path = self.store[it][COL_PATH]
        kind = self.store[it][COL_KIND]
        if kind == KIND_FILES:
            node = self.nodes[os.path.dirname(path)]
            for fname, fsize in node[4]:
                self.store.append(it, self._row(fname, fsize, node[3], 0,
                                                os.path.join(os.path.dirname(path), fname), KIND_FILE))
        else:
            node = self.nodes[path]
            for sub in node[5]:
                self._append(it, sub, os.path.join(path, sub[0]), node[1])
            if node[2]:
                files_row = self._row(t("disco", "archivos_fila", n=f"{node[2]:,}"), node[3], node[1],
                                      node[2], os.path.join(path, ""), KIND_FILES)
                files_it = self.store.append(it, files_row)
                if node[4]:
                    self.store.append(files_it, self._placeholder())
        self.store.remove(child)
        return False

    def _on_activated(self, tree, path, _col):
        if tree.row_expanded(path):
            tree.collapse_row(path)
        else:
            tree.expand_row(path, False)

    def _on_button_press(self, tree, event):
        if event.button != 3:
            return False
        hit = tree.get_path_at_pos(int(event.x), int(event.y))
        if hit is None:
            return False
        tree.get_selection().select_path(hit[0])
        if self._selected_dir() is None:
            return True
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label=t("disco", "abrir_thunar"))
        item.connect("activate", lambda _i: self._open_selected())
        menu.append(item)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    # ---- Thunar -----------------------------------------------------------------

    def _selected_dir(self):
        model, it = self.tree.get_selection().get_selected()
        if it is None:
            return None
        kind = model[it][COL_KIND]
        path = model[it][COL_PATH]
        if kind == KIND_PLACEHOLDER:
            return None
        if kind == KIND_FILE:
            return os.path.dirname(path)
        if kind == KIND_FILES:
            return path.rstrip("/") or "/"
        return path

    def _sync_open_btn(self):
        self.open_btn.set_sensitive(self._selected_dir() is not None)

    def _open_selected(self):
        path = self._selected_dir()
        if path is None:
            return
        subprocess.Popen([FILE_MANAGER, path], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Thunar abre debajo de esta superficie layer-shell -- cerrar para
        # que se vea (el escaneo queda en caché, ver load_cache()).
        self.window.destroy()

    # ---- pie / ciclo de vida -------------------------------------------------------

    def _update_footer(self):
        try:
            st = os.statvfs(ROOT)
        except OSError:
            return
        self.footer.set_text(t("disco", "libre", free=human_size(st.f_bavail * st.f_frsize),
                               total=human_size(st.f_blocks * st.f_frsize)))

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        return False

    def _on_destroy(self, *_args):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    DiskUsagePopup()
    Gtk.main()


if __name__ == "__main__":
    main()

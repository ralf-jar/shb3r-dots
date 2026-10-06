#!/usr/bin/env python3
"""launcher.py
Launcher propio de aplicaciones (reemplazo de Wofi, SUPER+SPACE) --
reusa la blacklist/nombres/íconos custom de menu_module.py
(desktop_apps.py) para que "lo que se ve acá" sea exactamente "lo que
se ve en Visibles" de la pestaña Menu del dashboard, sin mantener una
segunda lista aparte.

Estructura: barra de búsqueda, fila de las 5 apps más abiertas (solo
íconos, oculta si nunca se registró ninguna apertura o mientras hay
texto en la búsqueda), y el listado principal en 3 columnas -- por
default alfabético (formato texto, sin ícono, agrupado por letra, ver
CLAUDE.md "Referencia: launcher Android"), o por categoría si se
togglea la vista (categorías editables, ver launcher_store.py).

Bang de búsqueda: si el texto termina en "?" y se presiona Enter, no se
interpreta como nombre de app -- se abre como consulta en el navegador
(brave-origin) contra un asistente de IA en vez de listar resultados.

Modo comando: si el texto empieza con "/", tampoco se interpreta como
nombre de app -- Enter lo ejecuta tal cual en la shell del usuario
(ej. "/shutdown" apaga la PC), sin listar resultados.
"""

import os
import subprocess
import sys
import threading
import urllib.parse

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gio, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from waybar_lib import (kill_existing, kill_group, build_layer_window,
                         position_fixed_top, svg_icon_image)
from i18n import t

sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "menu"))
import desktop_apps
import launcher_store

LOCK = "/tmp/app-launcher.pid"
CSS_FILE = os.path.join(SCRIPT_DIR, "launcher.css")

ICONS_DIR = os.path.join(SCRIPT_DIR, "icons")
LIST_ICON = os.path.join(ICONS_DIR, "list.svg")
GRID_ICON = os.path.join(ICONS_DIR, "grid.svg")
CLOSE_ICON = os.path.join(ICONS_DIR, "close.svg")

TOP_ICON_SIZE = 32
TOP_ICON_RENDER_SCALE = 2  # cargar a 2x -- nítido incluso en HDMI-A-2 (scale 1.5)
ROW_ICON_SIZE = 20
TOP_APPS_LIMIT = 5
NUM_COLUMNS = 1

# https://claude.ai/new?q=<texto> antepone el texto al chat nuevo y lo
# manda -- confirmado a mano solo hasta cierto punto (ver documento de
# dudas que deja esta sesión): si en el futuro deja de auto-enviar, o si
# se prefiere Gemini, alcanza con cambiar esta constante.
BANG_URL_TEMPLATE = "https://claude.ai/new?q={query}"
BROWSER_BIN = "brave-origin"


def _resolve_icon_path(icon_name):
    """Misma lógica que menu_module.py: Icon= puede ser un nombre de
    tema o una ruta absoluta ya resuelta."""
    if not icon_name:
        return None
    if os.path.isabs(icon_name) and os.path.exists(icon_name):
        return icon_name
    info = Gtk.IconTheme.get_default().lookup_icon(
        icon_name, ROW_ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
    return info.get_filename() if info else None


class AppLauncher:
    def __init__(self):
        self.window, container = build_layer_window("app-launcher", CSS_FILE)
        container.set_name("launcher-container")
        position_fixed_top(container, margin=90)

        self.apps_full = desktop_apps.list_all_apps()
        self.show_list_icons = launcher_store.get_setting("show_list_icons")
        self._row_icon_cache = {}
        visible, _hidden = desktop_apps.visible_and_blacklisted(self.apps_full)
        self.visible_apps = visible  # [(desktop_id, name), ...] ordenado por nombre

        self.view_mode = "alfabetico"
        self._flat_order = []
        self._selected_index = 0
        self._selection_active = False  # recién True tras la primera Up/Down
        self._cell_widgets = {}
        self._expanded_categories = set()  # vacío = todas colapsadas al abrir

        self.search = Gtk.SearchEntry()
        self.search.set_name("launcher-search")
        self.search.set_placeholder_text(t("launcher", "buscar_placeholder"))
        self.search.set_tooltip_text(t("launcher", "buscar_tooltip"))
        # "changed" (Gtk.Editable), no "search-changed" -- esa segunda
        # señal de Gtk.SearchEntry trae un retraso interno de ~150ms
        # (coalescer typing rápido); si Enter llega dentro de esa
        # ventana, "activate" corría antes de que _flat_order reflejara
        # el texto ya tipeado y lanzaba el primer resultado de la
        # búsqueda ANTERIOR. "changed" es síncrona por tecla.
        self.search.connect("changed", self._on_search_changed)
        self.search.connect("activate", self._on_search_activate)
        # Left/Right en la entry -- a diferencia de Up/Down, Gtk.Entry SÍ
        # bindea esas teclas para mover el cursor de texto, así que hay
        # que interceptarlas ACÁ (no alcanza con key-press-event de la
        # ventana) y devolver True para frenar el binding nativo.
        self.search.connect("key-press-event", self._on_search_key)

        # Botón A-Z/Categorías superpuesto a la barra de búsqueda, pegado
        # a la derecha -- Gtk.Overlay ignora halign/valign/margin de sus
        # hijos DIRECTOS en este entorno (ver waybar_lib.py), así que el
        # hijo overlay es un Box de ancho completo (mismo tamaño que la
        # entry) con un spacer expansivo + el botón vía pack_start normal
        # -- eso sí respeta alineación, confirmado antes de aplicar.
        search_overlay = Gtk.Overlay()
        search_overlay.add(self.search)
        toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toggle_row.set_hexpand(True)
        toggle_row.set_vexpand(True)
        spacer = Gtk.Box()
        toggle_row.pack_start(spacer, True, True, 0)
        toggle_row.pack_start(self._build_view_switcher(), False, False, 10)
        search_overlay.add_overlay(toggle_row)
        search_overlay.set_overlay_pass_through(toggle_row, True)
        container.pack_start(search_overlay, False, False, 0)

        self.top_row = self._build_top_row()
        if self.top_row is not None:
            container.pack_start(self.top_row, False, False, 0)

        self.apps_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_name("launcher-scroller")
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_min_content_height(540)
        self.scroller.set_max_content_height(540)
        self.scroller.add(self.apps_container)

        self.editor_box = self._build_category_editor()

        self.stack = Gtk.Stack()
        self.stack.add_named(self.scroller, "apps")
        self.stack.add_named(self.editor_box, "editor")
        container.pack_start(self.stack, True, True, 0)

        self._render_apps()

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()
        self.search.grab_focus()

    # ---- Fila "más abiertas" (solo íconos) -----------------------------

    def _build_top_row(self):
        if not launcher_store.get_setting("show_top_apps"):
            return None
        top_ids = launcher_store.top_apps(self.apps_full, limit=TOP_APPS_LIMIT)
        if not top_ids:
            return None
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.set_name("launcher-top-row")
        row.set_halign(Gtk.Align.CENTER)
        for desktop_id in top_ids:
            app = self.apps_full[desktop_id]
            row.pack_start(self._make_top_icon(desktop_id, app.get("icon", ""), app["name"]), False, False, 0)
        return row

    def _make_top_icon(self, desktop_id, icon_name, name):
        btn = Gtk.Button()
        btn.set_tooltip_text(name)
        btn.get_style_context().add_class("launcher-top-icon-btn")

        slot = Gtk.Box()
        slot.set_size_request(TOP_ICON_SIZE, TOP_ICON_SIZE)
        slot.add(Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG))
        btn.add(slot)
        btn.connect("clicked", lambda _b: self._launch(desktop_id))

        icon_path = _resolve_icon_path(icon_name)
        if icon_path:
            def worker():
                try:
                    # Cargar a TOP_ICON_RENDER_SCALE-x y taguear el
                    # cairo.Surface con ese scale -- nítido en monitores
                    # HiDPI (HDMI-A-2 a 1.5x) sin depender de
                    # get_scale_factor(), que en __init__ todavía no
                    # sabe en qué monitor va a caer esta ventana (recién
                    # se sabe tras mapearla).
                    px = TOP_ICON_SIZE * TOP_ICON_RENDER_SCALE
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, px, px)
                except GLib.Error:
                    return
                GLib.idle_add(self._apply_top_icon, slot, pixbuf)
            threading.Thread(target=worker, daemon=True).start()
        return btn

    def _apply_top_icon(self, slot, pixbuf):
        for child in slot.get_children():
            slot.remove(child)
        surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, TOP_ICON_RENDER_SCALE, None)
        slot.add(Gtk.Image.new_from_surface(surface))
        slot.show_all()
        return False  # GLib.idle_add: correr una sola vez

    # ---- Switcher alfabético/categoría + editor de categorías ----------

    def _build_view_switcher(self):
        """Un solo botón que alterna A-Z/Categorías -- el ícono mostrado
        es el del modo AL QUE SE CAMBIA al hacer clic (no el actual)."""
        btn = Gtk.Button()
        btn.set_valign(Gtk.Align.CENTER)
        btn.get_style_context().add_class("launcher-view-toggle-btn")

        self._view_toggle_icon_slot = Gtk.Box()
        btn.add(self._view_toggle_icon_slot)
        self._view_toggle_btn = btn
        self._update_view_toggle()

        btn.connect("clicked", self._on_view_toggle_clicked)
        return btn

    def _on_view_toggle_clicked(self, _btn):
        self.view_mode = "categoria" if self.view_mode == "alfabetico" else "alfabetico"
        self._update_view_toggle()
        self._render_apps()

    def _update_view_toggle(self):
        target_icon = LIST_ICON if self.view_mode == "categoria" else GRID_ICON
        target_label = (t("launcher", "ver_alfabetico") if self.view_mode == "categoria"
                         else t("launcher", "ver_categorias"))
        for child in self._view_toggle_icon_slot.get_children():
            self._view_toggle_icon_slot.remove(child)
        self._view_toggle_icon_slot.add(svg_icon_image(target_icon, size=15, color_name="myforegroundhover"))
        self._view_toggle_icon_slot.show_all()
        self._view_toggle_btn.set_tooltip_text(target_label)

    def _build_category_editor(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_name("launcher-category-editor")

        self.category_rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(self.category_rows_box, False, False, 0)

        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.new_category_entry = Gtk.Entry()
        self.new_category_entry.set_placeholder_text(t("launcher", "nueva_categoria"))
        self.new_category_entry.connect("activate", self._on_add_category)
        add_btn = Gtk.Button()
        add_btn.set_image(Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON))
        add_btn.get_style_context().add_class("apply-btn")
        add_btn.connect("clicked", self._on_add_category)
        add_row.pack_start(self.new_category_entry, True, True, 0)
        add_row.pack_start(add_btn, False, False, 0)
        box.pack_start(add_row, False, False, 0)

        back_btn = Gtk.Button(label=t("launcher", "volver"))
        back_btn.get_style_context().add_class("launcher-editor-back-btn")
        back_btn.connect("clicked", lambda _b: self._set_editor_visible(False))
        box.pack_start(back_btn, False, False, 0)

        self._refresh_category_rows()
        return box

    def _refresh_category_rows(self):
        for child in self.category_rows_box.get_children():
            self.category_rows_box.remove(child)
        categories, _assignments = launcher_store.load_categories()
        for cat in categories:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            entry = Gtk.Entry()
            entry.set_text(cat)
            entry.connect("activate", self._on_rename_category, cat)
            entry.connect("focus-out-event", self._make_rename_on_focus_out(cat))
            del_btn = Gtk.Button()
            del_btn.set_image(svg_icon_image(CLOSE_ICON, size=12))
            del_btn.get_style_context().add_class("launcher-category-del-btn")
            del_btn.connect("clicked", lambda _b, c=cat: self._on_remove_category(c))
            row.pack_start(entry, True, True, 0)
            row.pack_start(del_btn, False, False, 0)
            self.category_rows_box.pack_start(row, False, False, 0)
        self.category_rows_box.show_all()

    def _make_rename_on_focus_out(self, old_name):
        def handler(entry, _event):
            self._on_rename_category(entry, old_name)
            return False
        return handler

    def _on_add_category(self, _widget):
        launcher_store.add_category(self.new_category_entry.get_text())
        self.new_category_entry.set_text("")
        self._refresh_category_rows()

    def _on_rename_category(self, entry, old_name, *_args):
        new_name = entry.get_text().strip()
        if new_name and new_name != old_name:
            launcher_store.rename_category(old_name, new_name)
            self._refresh_category_rows()

    def _on_remove_category(self, name):
        launcher_store.remove_category(name)
        self._refresh_category_rows()

    def _set_editor_visible(self, show):
        if not show:
            self._render_apps()  # categorías/asignaciones pudieron cambiar
        self.stack.set_visible_child_name("editor" if show else "apps")

    # ---- Listado principal ----------------------------------------------

    def _grouped_alphabetically(self):
        groups = {}
        order = []
        for desktop_id, name in self.visible_apps:
            letter = name[0].upper() if name and name[0].isalpha() else "#"
            if letter not in groups:
                groups[letter] = []
                order.append(letter)
            groups[letter].append((desktop_id, name))
        return [(letter, groups[letter]) for letter in order]

    def _partition_into_columns(self, groups):
        """Reparte `groups` (lista de (título, items) YA filtrados, en
        orden) en NUM_COLUMNS columnas CONTIGUAS -- lectura hacia abajo
        dentro de cada columna, nunca hacia la derecha (pedido explícito
        del usuario: la segunda/tercera columna tienen sus PROPIAS letras
        A, B, C..., no continúan la fila de la columna anterior).
        Precalcula cuántos grupos completos entran en cada columna
        buscando alturas (header + 1 fila por app) lo más parejas
        posible -- nunca parte un grupo a la mitad entre dos columnas."""
        heights = [1 + len(items) for _, items in groups]
        total = sum(heights)
        if total == 0:
            return [[] for _ in range(NUM_COLUMNS)]
        target = total / NUM_COLUMNS

        # Corte contra un límite ACUMULADO (target * columna actual) --
        # comparar cuánto se desvía del límite CON el próximo grupo vs.
        # SIN él, y quedarse con lo que quede más cerca. Sin esto, un
        # grupo grande que empuja el acumulado justo antes del límite
        # queda absorbido igual (por poco) y ese sobrante se arrastra
        # -- con varios grupos grandes seguidos, termina cayendo entero
        # en la última columna.
        columns = []
        current = []
        cumulative = 0
        boundary = target
        for group, height in zip(groups, heights):
            if (current and len(columns) < NUM_COLUMNS - 1
                    and cumulative + height >= boundary
                    and abs(cumulative - boundary) <= abs(cumulative + height - boundary)):
                columns.append(current)
                current = []
                boundary += target
            current.append(group)
            cumulative += height
        columns.append(current)
        while len(columns) < NUM_COLUMNS:
            columns.append([])
        return columns

    def _render_apps(self):
        for child in self.apps_container.get_children():
            self.apps_container.remove(child)

        raw_text = self.search.get_text().strip()
        self._flat_order = []
        self._flat_group_id = []
        self._group_headers = {}
        self._cell_widgets = {}
        self._selected_index = 0

        if raw_text.startswith("/"):
            self._render_command_hint(raw_text[1:].strip())
            return

        query = raw_text.lower()
        is_category_mode = self.view_mode == "categoria"
        groups = (launcher_store.grouped_by_category(self.visible_apps)
                  if is_category_mode else self._grouped_alphabetically())

        filtered_groups = []
        for title, items in groups:
            filtered = [(d, n) for d, n in items if query in n.lower()]
            if filtered:
                filtered_groups.append((title, filtered))

        if not filtered_groups:
            empty = Gtk.Label(label=t("launcher", "sin_resultados"))
            empty.get_style_context().add_class("launcher-empty")
            self.apps_container.pack_start(empty, False, False, 0)
            self.apps_container.show_all()
            self._selected_index = 0
            self._update_selection_highlight()
            return

        # En modo categoría, colapsada = solo el header entra al layout
        # (las apps ni se cuentan para el balanceo de columnas ni se
        # arman como celdas) -- salvo que haya texto de búsqueda, ahí
        # se fuerza expandida para no esconder resultados que matchean.
        def is_expanded(title):
            return not is_category_mode or query or title in self._expanded_categories

        display_groups = [(title, items if is_expanded(title) else [])
                           for title, items in filtered_groups]

        # Gap entre columnas vía CSS (.launcher-column margin-left), no
        # spacing acá -- Gtk.Box.spacing no es una propiedad CSS-stylable
        # en GTK3.
        # homogeneous=True + expand/fill en la fila y en cada columna:
        # NUM_COLUMNS columnas de ancho fijo, cada una exactamente
        # 1/NUM_COLUMNS del ancho total (no centrado por contenido).
        columns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        columns_row.set_homogeneous(True)

        group_id = -1
        for column_groups in self._partition_into_columns(display_groups):
            column_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            column_box.get_style_context().add_class("launcher-column")
            for title, items in column_groups:
                group_id += 1
                if is_category_mode:
                    header = self._make_category_header(title, is_expanded(title))
                else:
                    header = Gtk.Label(label=title)
                    header.set_halign(Gtk.Align.START)
                    header.get_style_context().add_class("launcher-section-title")
                self._group_headers[group_id] = header
                column_box.pack_start(header, False, False, 0)
                for desktop_id, name in items:
                    cell = self._make_app_cell(desktop_id, name)
                    column_box.pack_start(cell, False, False, 0)
                    self._flat_order.append(desktop_id)
                    self._flat_group_id.append(group_id)
                    self._cell_widgets[desktop_id] = cell
            columns_row.pack_start(column_box, True, True, 0)

        self.apps_container.pack_start(columns_row, True, True, 0)
        self.apps_container.show_all()
        self._selected_index = 0
        self._update_selection_highlight()

    def _render_command_hint(self, command):
        text = (t("launcher", "ejecutar_consola", command=command) if command
                else t("launcher", "escribe_comando"))
        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(40)
        label.get_style_context().add_class("launcher-command-hint")
        self.apps_container.pack_start(label, False, False, 0)
        self.apps_container.show_all()

    def _make_category_header(self, title, expanded):
        chevron = "▾" if expanded else "▸"
        label = Gtk.Label(label=f"{chevron}  {title}")
        label.set_halign(Gtk.Align.START)
        label.get_style_context().add_class("launcher-section-title")

        event_box = Gtk.EventBox()
        event_box.add(label)
        event_box.get_style_context().add_class("launcher-category-header")
        event_box.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        event_box.connect("enter-notify-event",
                           lambda w, e: w.set_state_flags(Gtk.StateFlags.PRELIGHT, False))
        event_box.connect("leave-notify-event",
                           lambda w, e: w.unset_state_flags(Gtk.StateFlags.PRELIGHT))
        event_box.connect("button-press-event", self._on_category_header_click, title)
        return event_box

    def _on_category_header_click(self, _widget, _event, title):
        self._expanded_categories.symmetric_difference_update({title})
        self._render_apps()
        return True

    def _make_app_cell(self, desktop_id, name):
        label = Gtk.Label(label=name)
        label.set_halign(Gtk.Align.START)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(22)
        label.get_style_context().add_class("launcher-app-label")

        content = label
        if self.show_list_icons:
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            content.pack_start(self._make_row_icon(self.apps_full[desktop_id].get("icon", "")), False, False, 0)
            content.pack_start(label, True, True, 0)

        event_box = Gtk.EventBox()
        event_box.add(content)
        event_box.get_style_context().add_class("launcher-app-cell")
        # Gtk.EventBox no pide ENTER/LEAVE_NOTIFY por default, y ademas
        # -- a diferencia de Gtk.Button -- ningun widget genérico
        # gestiona PRELIGHT solo al recibirlos; confirmado mandando un
        # enter-notify sintético sin handler propio: no pasó nada. Hay
        # que setear/limpiar el estado a mano para que ":hover" del CSS
        # dispare.
        event_box.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        event_box.connect("enter-notify-event",
                           lambda w, e: w.set_state_flags(Gtk.StateFlags.PRELIGHT, False))
        event_box.connect("leave-notify-event",
                           lambda w, e: w.unset_state_flags(Gtk.StateFlags.PRELIGHT))
        event_box.connect("button-press-event", self._on_app_click, desktop_id)
        return event_box

    def _make_row_icon(self, icon_name):
        """Ícono de la lista principal (switch "Iconos en el lanzador" de
        Personalización). Nombre de tema -> Gtk.Image por nombre (el tema
        lo cachea y resuelve la escala del monitor solo); ruta absoluta ->
        pixbuf cacheado por ruta (la lista se rearma en cada tecla)."""
        if icon_name and os.path.isabs(icon_name):
            if icon_name not in self._row_icon_cache:
                try:
                    self._row_icon_cache[icon_name] = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        icon_name, ROW_ICON_SIZE, ROW_ICON_SIZE)
                except GLib.Error:
                    self._row_icon_cache[icon_name] = None
            pixbuf = self._row_icon_cache[icon_name]
            image = Gtk.Image.new_from_pixbuf(pixbuf) if pixbuf else Gtk.Image()
        else:
            theme = Gtk.IconTheme.get_default()
            name = icon_name if icon_name and theme.has_icon(icon_name) else "application-x-executable"
            image = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.MENU)
            image.set_pixel_size(ROW_ICON_SIZE)
        image.set_size_request(ROW_ICON_SIZE, ROW_ICON_SIZE)
        image.set_valign(Gtk.Align.CENTER)
        return image

    def _on_app_click(self, widget, event, desktop_id):
        if event.button == 1:
            self._launch(desktop_id)
            return True
        if event.button == 3:
            self._open_category_menu(desktop_id)
            return True
        return False

    def _open_category_menu(self, desktop_id):
        categories, assignments = launcher_store.load_categories()
        current = assignments.get(desktop_id)

        menu = Gtk.Menu()
        none_item = Gtk.CheckMenuItem(label=launcher_store.UNCATEGORIZED)
        none_item.set_active(current is None)
        none_item.connect("toggled", self._on_category_menu_item, desktop_id, None)
        menu.append(none_item)
        menu.append(Gtk.SeparatorMenuItem())
        for cat in categories:
            item = Gtk.CheckMenuItem(label=cat)
            item.set_active(current == cat)
            item.connect("toggled", self._on_category_menu_item, desktop_id, cat)
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        edit_item = Gtk.MenuItem(label=t("launcher", "editar_categorias"))
        edit_item.connect("activate", lambda _i: self._set_editor_visible(True))
        menu.append(edit_item)
        menu.show_all()
        menu.popup_at_pointer(None)

    def _on_category_menu_item(self, item, desktop_id, category):
        if not item.get_active():
            return
        launcher_store.set_app_category(desktop_id, category)
        if self.view_mode == "categoria":
            self._render_apps()

    # ---- Selección por teclado -------------------------------------------

    def _update_selection_highlight(self):
        for i, desktop_id in enumerate(self._flat_order):
            cell = self._cell_widgets.get(desktop_id)
            if cell is None:
                continue
            ctx = cell.get_style_context()
            if self._selection_active and i == self._selected_index:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")
        # idle_add: recién tras el próximo size-allocate la celda tiene
        # coordenadas válidas (crítico justo después de _render_apps,
        # donde las celdas son widgets nuevos sin allocation todavía).
        GLib.idle_add(self._scroll_to_selected)

    def _scroll_to_selected(self):
        if not self._selection_active or not self._flat_order:
            return False
        i = self._selected_index
        cell = self._cell_widgets.get(self._flat_order[i])
        if cell is None:
            return False
        # Si es el primer item de su grupo, el límite superior es el
        # HEADER de la letra/categoría (no la celda) -- si no, al volver
        # al principio el scroll se frena justo debajo del header y lo
        # deja cortado arriba.
        top_widget = cell
        if i == 0 or self._flat_group_id[i - 1] != self._flat_group_id[i]:
            top_widget = self._group_headers.get(self._flat_group_id[i], cell)
        top_coords = top_widget.translate_coordinates(self.apps_container, 0, 0)
        bottom_coords = cell.translate_coordinates(self.apps_container, 0, 0)
        if top_coords is None or bottom_coords is None:
            return False
        top = top_coords[1]
        bottom = bottom_coords[1] + cell.get_allocated_height()
        adj = self.scroller.get_vadjustment()
        if top < adj.get_value():
            adj.set_value(top)
        elif bottom > adj.get_value() + adj.get_page_size():
            adj.set_value(bottom - adj.get_page_size())
        return False  # GLib.idle_add: correr una sola vez

    def _move_selection(self, delta):
        if not self._flat_order:
            return
        # Primera Up/Down sin selección activa todavía: selecciona el
        # primer item en vez de mover relativo a un índice 0 invisible.
        if not self._selection_active:
            self._selection_active = True
            self._selected_index = 0
        else:
            self._selected_index = max(0, min(len(self._flat_order) - 1, self._selected_index + delta))
        self._update_selection_highlight()

    def _move_to_adjacent_group(self, direction):
        """direction=+1 (Right): primer item del siguiente grupo (letra
        en A-Z, categoría en Categorías). direction=-1 (Left): último
        item del grupo anterior."""
        if not self._flat_order:
            return
        if not self._selection_active:
            self._selection_active = True
            self._selected_index = 0
            self._update_selection_highlight()
            return
        current_group = self._flat_group_id[self._selected_index]
        if direction > 0:
            for i in range(self._selected_index + 1, len(self._flat_order)):
                if self._flat_group_id[i] != current_group:
                    self._selected_index = i
                    break
            else:
                self._selected_index = len(self._flat_order) - 1
        else:
            start = self._selected_index
            while start > 0 and self._flat_group_id[start - 1] == current_group:
                start -= 1
            if start > 0:
                self._selected_index = start - 1
        self._update_selection_highlight()

    # ---- Búsqueda ---------------------------------------------------------

    def _on_search_changed(self, entry):
        if self.top_row is not None:
            self.top_row.set_visible(not entry.get_text().strip())
        self._render_apps()

    def _on_search_activate(self, entry):
        text = entry.get_text().strip()
        if text.startswith("/"):
            command = text[1:].strip()
            if command:
                self._run_console_command(command)
            return
        if text.endswith("?"):
            self._launch_web_query(text)
            return
        if self._flat_order:
            index = self._selected_index if self._selected_index < len(self._flat_order) else 0
            self._launch(self._flat_order[index])

    def _launch_web_query(self, text):
        url = BANG_URL_TEMPLATE.format(query=urllib.parse.quote(text))
        try:
            subprocess.Popen([BROWSER_BIN, url], start_new_session=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass
        self.window.destroy()

    def _run_console_command(self, command):
        # start_new_session=True -- el proceso sobrevive a que el
        # launcher se cierre (mismo patrón que _launch_web_query). Se
        # ejecuta con la shell real del usuario ($SHELL) para que
        # aliases/funciones de la config de shell también resuelvan,
        # no solo binarios en PATH.
        shell = os.environ.get("SHELL", "/bin/sh")
        try:
            subprocess.Popen([shell, "-c", command], start_new_session=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass
        self.window.destroy()

    # ---- Lanzar apps --------------------------------------------------------

    def _launch(self, desktop_id):
        info = Gio.DesktopAppInfo.new(desktop_id)
        if info is None:
            return
        if launcher_store.is_game(desktop_id):
            self._arm_gaming_workspace()
        try:
            info.launch([], None)
        except GLib.Error:
            return
        launcher_store.record_launch(desktop_id)
        self.window.destroy()

    def _arm_gaming_workspace(self):
        # La regla vive en hypr/config/windowrules.lua: manda a gaming la
        # próxima ventana que abra (el juego hereda el workspace del
        # launcher por misc:initial_workspace_tracking si no).
        try:
            subprocess.run(["hyprctl", "eval", "flipfrog_arm_game_launch()"], timeout=2,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass

    # ---- Teclado / cierre -----------------------------------------------

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
        elif event.keyval in (Gdk.KEY_Down,):
            self._move_selection(1)
        elif event.keyval in (Gdk.KEY_Up,):
            self._move_selection(-1)

    def _on_search_key(self, _widget, event):
        # Left/Right acá (no en _on_key/ventana): Gtk.Entry SÍ bindea
        # esas teclas para mover el cursor de texto -- devolver True
        # frena ese binding nativo, ver comentario en __init__.
        if event.keyval == Gdk.KEY_Right:
            self._move_to_adjacent_group(1)
            return True
        if event.keyval == Gdk.KEY_Left:
            self._move_to_adjacent_group(-1)
            return True
        return False

    def _on_destroy(self, *_args):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    AppLauncher()
    Gtk.main()


if __name__ == "__main__":
    main()

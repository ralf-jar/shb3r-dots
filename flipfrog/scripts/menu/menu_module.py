"""
menu_module.py
Módulo "Menu" del dashboard (build_menu_selector) -- blacklist de apps
para el launcher, ver CLAUDE.md. Antes vivía en su propia ventana
(menu-editor.py, ahora eliminado) -- se embebió como pestaña propia del
dashboard a pedido del usuario, mismo motivo que build_theme_selector.

Dos listas (Gtk.ListBox) con drag-and-drop entre ellas: "Visibles" (lo
que el launcher muestra hoy) y "Ocultas" (blacklist). Arrastrar una fila de una
lista a la otra llama blacklist_app()/unblacklist_app() (desktop_apps.py)
y reconstruye ambas listas desde cero -- más simple y a prueba de bugs
de sincronización que mover el Gtk.ListBoxRow a mano entre modelos.

Búsqueda (Gtk.SearchEntry) filtra ambas listas a la vez por substring
sobre el nombre, case-insensitive -- sin esto, ~100+ apps instaladas
hacen la pestaña inmanejable. Filtra sobre self.apps (ya escaneado en
refresh()), no vuelve a parsear los .desktop en cada tecla.

Los íconos de cada fila se decodifican en un hilo aparte y se aplican
con GLib.idle_add (_make_icon_slot/_apply_icon) -- decodificar ~100+
íconos (SVG sobre todo) de forma sincrónica es lo que hacía tardada esta
pestaña.

Panel único (debajo de las dos listas, _build_editor_panel) para DOS
modos, unificados a pedido del usuario en vez de dos paneles separados:
  - Editar una app existente: clickear una fila en cualquiera de las dos
    listas (Gtk.ListBox.SELECTION_SINGLE, ver _on_row_selected) carga su
    nombre/ícono ahí. "Guardar" aplica lo que haya cambiado
    (rename_app/set_app_icon), "Restablecer nombre"/"Restablecer ícono"
    vuelven cada campo a su original (reset_app_name/reset_app_icon) --
    deshabilitados si ese campo no tiene nada custom que resetear.
  - Agregar un script .sh nuevo: "Elegir script .sh..." limpia cualquier
    selección de fila y pasa el panel a modo "nueva entrada" -- mismos
    campos Nombre/Ícono, pero "Guardar" ahora arma un .desktop nuevo
    (add_script_entry) en vez de modificar uno existente.
Ambos modos son mutuamente excluyentes (self.selected_id vs.
self.pending_script_path); elegir uno limpia el otro.

El campo Ícono no es un Gtk.Entry con el nombre/ruta como texto -- alcanza
con la preview (un botón con la imagen del ícono actual); clickearlo abre
un selector de archivo y listo, ver _build_icon_field.
"""

import os
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

import desktop_apps

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "launcher"))
import common
import launcher_store
from waybar_lib import load_module_css, svg_icon_image, _icon_text_button, build_switch_row
from i18n import t

MODULE_CSS = os.path.join(SCRIPT_DIR, "menu_module.css")

ICONS_DIR = os.path.join(SCRIPT_DIR, "icons")
SCRIPT_ICON = os.path.join(ICONS_DIR, "script.svg")
SAVE_ICON = os.path.join(ICONS_DIR, "save.svg")
RESET_ICON = os.path.join(ICONS_DIR, "reset.svg")

ICON_SIZE = 20
EDITOR_ICON_PREVIEW_SIZE = 32

DND_TARGET = Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)

IMAGE_PATTERNS = ("*.png", "*.svg", "*.svgz", "*.xpm", "*.jpg", "*.jpeg")

# Carpeta inicial del selector de ícono -- tema de íconos activo del sistema.
PAPIRUS_APPS_DIR = "/usr/share/icons/Papirus-Dark/128x128/apps"

LAUNCHER_LIST_ICON = os.path.join(SCRIPT_DIR, "..", "toggles", "icons", "menu.svg")
LAUNCHER_TOP_ICON = os.path.join(SCRIPT_DIR, "..", "launcher", "icons", "grid.svg")


class MenuSelector:
    def __init__(self, container):
        container.pack_start(self._build_launcher_row(), False, False, 0)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text(t("aplicaciones", "buscar_placeholder"))
        self.search.connect("search-changed", self._on_search_changed)
        container.pack_start(self.search, False, False, 0)

        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        columns.set_name("menu-columns")

        self.visible_box, self.visible_list = self._build_column(t("aplicaciones", "columna_visibles"))
        self.hidden_box, self.hidden_list = self._build_column(t("aplicaciones", "columna_ocultas"))

        columns.pack_start(self.visible_box, True, True, 0)
        columns.pack_start(self.hidden_box, True, True, 0)
        container.pack_start(columns, True, True, 0)

        self.selected_id = None
        self.pending_script_path = None
        container.pack_start(self._build_editor_panel(), False, False, 0)

        self.refresh()

    # ---- Opciones del lanzador ------------------------------------------

    def _build_launcher_row(self):
        """Opciones del lanzador (SUPER+SPACE, launcher/launcher.py) -- se
        leen al abrirlo, sin recargar nada. Las listas de abajo son las
        mismas apps que muestra el lanzador."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        row.set_homogeneous(True)
        row.set_name("menu-launcher-row")
        for key, text_code, icon_path in (("show_list_icons", "lanzador_iconos", LAUNCHER_LIST_ICON),
                                          ("show_top_apps", "lanzador_frecuentes", LAUNCHER_TOP_ICON)):
            build_switch_row(row, svg_icon_image(icon_path), t("aplicaciones", text_code),
                             launcher_store.get_setting(key),
                             lambda state, key=key: common.run_async(lambda: launcher_store.set_setting(key, state)))
        return row

    # ---- Listas -------------------------------------------------------

    def _build_column(self, title):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_name("menu-column")

        label = Gtk.Label(label=title)
        label.set_name("menu-column-title")
        label.set_halign(Gtk.Align.CENTER)
        box.pack_start(label, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # 0, no un valor fijo -- el alto de las listas se controla desde
        # dashboard.css (min-height en #menu-columns), no desde acá.
        scroller.set_min_content_height(0)

        listbox = Gtk.ListBox()
        listbox.set_name("menu-app-list")
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        listbox.drag_dest_set(Gtk.DestDefaults.ALL, [DND_TARGET], Gdk.DragAction.MOVE)
        listbox.connect("drag-data-received", self._on_drag_data_received)
        listbox.connect("row-selected", self._on_row_selected)

        scroller.add(listbox)
        box.pack_start(scroller, True, True, 0)
        return box, listbox

    def _resolve_icon_path(self, icon_name):
        """Solo la RUTA del archivo detrás de icon_name -- Icon= puede ser
        un nombre de tema O una ruta absoluta (algunas apps empaquetan su
        propio PNG/SVG). `IconTheme.lookup_icon` únicamente consulta el
        tema (rápido, no decodifica nada), así que corre en el hilo
        principal; la decodificación de verdad (lenta sobre todo para SVG
        vía librsvg) queda para _make_icon_slot, en un hilo aparte."""
        if not icon_name:
            return None
        if os.path.isabs(icon_name) and os.path.exists(icon_name):
            return icon_name
        info = Gtk.IconTheme.get_default().lookup_icon(
            icon_name, ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
        return info.get_filename() if info else None

    def _make_icon_slot(self, icon_name):
        """~100+ apps instaladas -- decodificar el ícono sincrónico sumaba
        espera perceptible. Fila con placeholder genérico ya, ícono real
        decodificado en un hilo aparte (mismo patrón que
        attach_wallpaper_preview_async), aplicado con GLib.idle_add."""
        slot = Gtk.Box()
        slot.set_size_request(ICON_SIZE, ICON_SIZE)
        slot.add(Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.MENU))

        icon_path = self._resolve_icon_path(icon_name)
        if not icon_path:
            return slot

        def worker():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, ICON_SIZE, ICON_SIZE)
            except GLib.Error:
                return
            GLib.idle_add(self._apply_icon, slot, pixbuf)

        threading.Thread(target=worker, daemon=True).start()
        return slot

    def _apply_icon(self, slot, pixbuf):
        for child in slot.get_children():
            slot.remove(child)
        slot.add(Gtk.Image.new_from_pixbuf(pixbuf))
        slot.show_all()
        return False  # GLib.idle_add: correr una sola vez

    def _make_row(self, desktop_id, name, icon_name):
        row = Gtk.ListBoxRow()
        row.desktop_id = desktop_id

        # EventBox -- ver CLAUDE.md "Gotchas de la UI" (menu_module.py).
        event_box = Gtk.EventBox()

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.set_margin_start(8)
        content.set_margin_end(8)
        content.set_margin_top(4)
        content.set_margin_bottom(4)
        content.pack_start(self._make_icon_slot(icon_name), False, False, 0)

        label = Gtk.Label(label=name)
        label.set_halign(Gtk.Align.START)
        # ellipsize + max_width_chars -- ver CLAUDE.md "Gotchas de la UI"
        # (nombre largo desbordaba las otras 4 pestañas del dashboard).
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(24)
        content.pack_start(label, True, True, 0)

        event_box.add(content)
        row.add(event_box)

        event_box.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [DND_TARGET], Gdk.DragAction.MOVE)
        event_box.connect("drag-data-get", self._on_drag_data_get, desktop_id)
        return row

    def _on_drag_data_get(self, _widget, _drag_context, data, _info, _time, desktop_id):
        data.set_text(desktop_id, -1)

    def _on_drag_data_received(self, target_listbox, _drag_context, _x, _y, data, _info, _time):
        desktop_id = data.get_text()
        if not desktop_id:
            return
        if target_listbox is self.hidden_list:
            if desktop_id in self.apps:
                desktop_apps.blacklist_app(desktop_id, self.apps)
        else:
            desktop_apps.unblacklist_app(desktop_id)
        self.refresh()

    def _on_search_changed(self, _entry):
        self._populate(self.search.get_text().strip().lower())

    def refresh(self):
        self.apps = desktop_apps.list_all_apps()
        self._populate(self.search.get_text().strip().lower())
        self.pending_script_path = None
        self._load_editor(None)

    def _populate(self, query):
        visible, hidden = desktop_apps.visible_and_blacklisted(self.apps)

        for listbox in (self.visible_list, self.hidden_list):
            for child in listbox.get_children():
                listbox.remove(child)

        for desktop_id, name in visible:
            if query in name.lower():
                icon = self.apps.get(desktop_id, {}).get("icon", "")
                self.visible_list.add(self._make_row(desktop_id, name, icon))
        for desktop_id, name in hidden:
            if query in name.lower():
                icon = self.apps.get(desktop_id, {}).get("icon", "")
                self.hidden_list.add(self._make_row(desktop_id, name, icon))

        self.visible_list.show_all()
        self.hidden_list.show_all()

    # ---- Selección de fila ---------------------------------------------

    def _on_row_selected(self, listbox, row):
        if row is None:
            return
        other = self.hidden_list if listbox is self.visible_list else self.visible_list
        # Selección única entre las DOS listas -- si no, quedaría
        # ambiguo a qué app se refiere el panel de edición compartido.
        other.unselect_all()
        self.pending_script_path = None
        self._load_editor(row.desktop_id)

    # ---- Campo de ícono (preview clickeable, sin texto) ----------------

    def _build_icon_field(self):
        """Solo un botón con la preview del ícono actual -- no hace falta
        mostrar el nombre/ruta como texto (la preview ya dice todo lo que
        importa visualmente), clickearlo abre el selector de archivo
        directo. Devuelve (widget, get_value, set_value): sin
        Gtk.Entry no hay dónde guardar el valor elegido más que en un
        closure propio."""
        state = {"icon": ""}

        btn = Gtk.Button()
        btn.set_tooltip_text(t("aplicaciones", "click_elegir_icono"))
        btn.get_style_context().add_class("menu-icon-preview-btn")
        preview = Gtk.Image()
        preview.set_size_request(EDITOR_ICON_PREVIEW_SIZE, EDITOR_ICON_PREVIEW_SIZE)
        btn.add(preview)

        def update_preview():
            path = self._resolve_icon_path(state["icon"])
            if path:
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        path, EDITOR_ICON_PREVIEW_SIZE, EDITOR_ICON_PREVIEW_SIZE)
                    preview.set_from_pixbuf(pixbuf)
                    return
                except GLib.Error:
                    pass
            preview.set_from_icon_name("image-missing", Gtk.IconSize.LARGE_TOOLBAR)

        def on_click(_btn):
            win = btn.get_toplevel()
            dialog = Gtk.FileChooserDialog(
                title=t("aplicaciones", "elegir_icono_titulo"), transient_for=win,
                action=Gtk.FileChooserAction.OPEN)
            dialog.add_button(t("aplicaciones", "cancelar"), Gtk.ResponseType.CANCEL)
            dialog.add_button(t("aplicaciones", "elegir"), Gtk.ResponseType.OK)
            if os.path.isdir(PAPIRUS_APPS_DIR):
                dialog.set_current_folder(PAPIRUS_APPS_DIR)
            img_filter = Gtk.FileFilter()
            img_filter.set_name(t("aplicaciones", "filtro_imagenes"))
            for pattern in IMAGE_PATTERNS:
                img_filter.add_pattern(pattern)
            dialog.add_filter(img_filter)

            # Mismo workaround de layer-shell que theme_module.py -- ver
            # CLAUDE.md "Editor de temas".
            original_prgname = GLib.get_prgname()
            GLib.set_prgname("menu-icon-picker-dialog")
            dialog.show_all()
            GLib.set_prgname(original_prgname)

            win.hide()
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                state["icon"] = dialog.get_filename()
                update_preview()
            dialog.destroy()
            win.show()

        btn.connect("clicked", on_click)
        update_preview()

        def get_value():
            return state["icon"]

        def set_value(value):
            state["icon"] = value or ""
            update_preview()

        return btn, get_value, set_value

    # ---- Panel único: editar existente o agregar script nuevo ---------

    def _build_editor_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_name("menu-editor-panel")

        # Subtítulo + línea, mismo patrón que theme_module.py/dashboard.py.
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_row.set_name("menu-editor-title-row")

        title = Gtk.Label(label=t("aplicaciones", "editar_aplicacion"))
        title.set_name("menu-editor-title")
        title_row.pack_start(title, False, False, 0)

        title_divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        title_divider.set_name("menu-editor-title-divider")
        title_divider.set_valign(Gtk.Align.CENTER)
        title_row.pack_start(title_divider, True, True, 0)

        box.pack_start(title_row, False, False, 0)

        fields_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

        icon_btn, self._get_icon_value, self._set_icon_value = self._build_icon_field()
        fields_row.pack_start(icon_btn, False, False, 0)

        name_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.editor_name_entry = Gtk.Entry()
        self.editor_name_entry.set_placeholder_text(t("aplicaciones", "nombre_placeholder"))
        self.editor_name_entry.get_style_context().add_class("editor-name-entry")

        name_col.pack_start(self.editor_name_entry, False, False, 0)
        fields_row.pack_start(name_col, True, True, 0)

        box.pack_start(fields_row, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        
        self.choose_script_btn = _icon_text_button(
            SCRIPT_ICON, t("aplicaciones", "elegir_script"), t("aplicaciones", "elegir_script_tooltip"))
        self.choose_script_btn.get_style_context().add_class("menu-editor-btn")
        self.choose_script_btn.connect("clicked", self._on_choose_script)

        self.editor_save_btn = _icon_text_button(
            SAVE_ICON, t("aplicaciones", "guardar"), t("aplicaciones", "guardar_tooltip"))
        self.editor_save_btn.get_style_context().add_class("menu-editor-btn")
        self.editor_save_btn.connect("clicked", self._on_save_clicked)

        self.editor_reset_name_btn = _icon_text_button(
            RESET_ICON, t("aplicaciones", "restaurar_nombre"), t("aplicaciones", "restaurar_nombre_tooltip"))
        self.editor_reset_name_btn.get_style_context().add_class("menu-editor-btn")
        self.editor_reset_name_btn.connect("clicked", self._on_reset_name_clicked)

        self.editor_reset_icon_btn = _icon_text_button(
            RESET_ICON, t("aplicaciones", "restaurar_icono"), t("aplicaciones", "restaurar_icono_tooltip"))
        self.editor_reset_icon_btn.get_style_context().add_class("menu-editor-btn")
        self.editor_reset_icon_btn.connect("clicked", self._on_reset_icon_clicked)
        
        for b in (self.choose_script_btn, self.editor_save_btn,
                  self.editor_reset_name_btn, self.editor_reset_icon_btn):
            btn_row.pack_start(b, True, True, 0)

        box.pack_start(btn_row, False, False, 0)

        self._set_editor_enabled(False)
        return box

    def _set_editor_enabled(self, enabled):
        for w in (self.editor_name_entry, self.editor_save_btn):
            w.set_sensitive(enabled)
        if not enabled:
            self.editor_reset_name_btn.set_sensitive(False)
            self.editor_reset_icon_btn.set_sensitive(False)

    def _load_editor(self, desktop_id):
        """desktop_id=None: sin nada seleccionado (estado inicial o tras
        refresh) -- mismo aspecto tanto si nunca se eligió nada como si
        se acaba de guardar/resetear algo."""
        self.selected_id = desktop_id
        if desktop_id is None:
            self.editor_name_entry.set_text("")
            self._set_icon_value("")
            self._set_editor_enabled(False)
            return

        app = self.apps.get(desktop_id)
        if app is None:
            return

        self.editor_name_entry.set_text(app["name"])
        self._set_icon_value(app.get("icon", ""))
        self._set_editor_enabled(True)

        customized = desktop_apps.get_customizations().get(desktop_id, {})
        self.editor_reset_name_btn.set_sensitive("Name" in customized)
        self.editor_reset_icon_btn.set_sensitive("Icon" in customized)

    def _on_save_clicked(self, _btn):
        name = self.editor_name_entry.get_text().strip()
        if not name:
            return
        icon = self._get_icon_value()

        if self.pending_script_path:
            desktop_apps.add_script_entry(name, icon, self.pending_script_path)
        elif self.selected_id:
            app = self.apps.get(self.selected_id)
            if app is None:
                return
            if name != app["name"]:
                desktop_apps.rename_app(self.selected_id, self.apps, name)
            if icon != app.get("icon", ""):
                desktop_apps.set_app_icon(self.selected_id, self.apps, icon)
        else:
            return

        self.refresh()

    def _on_reset_name_clicked(self, _btn):
        if not self.selected_id:
            return
        desktop_apps.reset_app_name(self.selected_id)
        self.refresh()

    def _on_reset_icon_clicked(self, _btn):
        if not self.selected_id:
            return
        desktop_apps.reset_app_icon(self.selected_id)
        self.refresh()

    def _on_choose_script(self, btn):
        win = btn.get_toplevel()
        dialog = Gtk.FileChooserDialog(
            title=t("aplicaciones", "elegir_script_titulo"), transient_for=win,
            action=Gtk.FileChooserAction.OPEN)
        dialog.add_button(t("aplicaciones", "cancelar"), Gtk.ResponseType.CANCEL)
        dialog.add_button(t("aplicaciones", "elegir"), Gtk.ResponseType.OK)
        sh_filter = Gtk.FileFilter()
        sh_filter.set_name(t("aplicaciones", "filtro_scripts_shell"))
        sh_filter.add_pattern("*.sh")
        dialog.add_filter(sh_filter)

        original_prgname = GLib.get_prgname()
        GLib.set_prgname("menu-script-picker-dialog")
        dialog.show_all()
        GLib.set_prgname(original_prgname)

        win.hide()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            path = dialog.get_filename()
            self.visible_list.unselect_all()
            self.hidden_list.unselect_all()
            self.selected_id = None
            self.pending_script_path = path

            base = os.path.basename(path)
            if base.endswith(".sh"):
                base = base[:-3]
            self.editor_name_entry.set_text(base)
            self._set_icon_value("")
            self._set_editor_enabled(True)
            self.editor_reset_name_btn.set_sensitive(False)
            self.editor_reset_icon_btn.set_sensitive(False)
        dialog.destroy()
        win.show()


def build_menu_selector(container):
    """La instancia de MenuSelector se descarta al volver -- no hace
    falta guardarla: una vez armados los widgets y conectadas sus señales
    (con `self` como closure sobre `self.apps`/las listas), quedan vivos
    mientras el contenedor exista, sin que dashboard.py necesite retener
    ninguna referencia."""
    load_module_css(MODULE_CSS)
    MenuSelector(container)

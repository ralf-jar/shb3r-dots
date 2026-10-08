"""Base de los selectores con vista previa de "Personalización": cursor
(cursor_picker.py) e íconos (icon_picker.py). Dos modos, como "Fuente" /
"Obtener fuentes": los temas instalados (clic aplica, la ventana sigue
abierta) o "Obtener" con pestañas Repositorios (repo_themes.py, pkexec
pacman) y KDE Store (kde_store.py, a ~/.local/share/icons).

Cada subclase da el `kind` (theme_kinds.py), cómo listar/aplicar los
instalados y cómo cargar la vista previa de un tema (en un hilo) y
dibujarla (en el de GTK)."""

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango
import cairo
import math
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
import kde_store
import repo_themes
from i18n import t
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top

CSS_FILE = os.path.join(SCRIPT_DIR, "theme_picker.css")
MAX_LIST_HEIGHT = 520
STORE_WORKERS = 3
SHOT_W, SHOT_H, SHOT_RADIUS = 150, 84, 6


def _clear(box):
    for child in box.get_children():
        child.destroy()


def _note(text=""):
    label = Gtk.Label(label=text)
    label.get_style_context().add_class("picker-note")
    label.set_halign(Gtk.Align.START)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    return label


def _set_note(entry, text):
    entry["note"].set_text(text or "")
    entry["note"].set_visible(bool(text))


def _small_button(text, tooltip, on_click):
    button = Gtk.Button(label=text)
    button.get_style_context().add_class("picker-action-btn")
    button.set_valign(Gtk.Align.CENTER)
    button.set_tooltip_text(tooltip)
    button.connect("clicked", lambda _b: on_click())
    return button


def _scroller(child):
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroller.set_max_content_height(MAX_LIST_HEIGHT)
    scroller.set_propagate_natural_height(True)
    scroller.add(child)
    return scroller


def _shot_surface(path, scale):
    """Captura de la tienda recortada a SHOT_W×SHOT_H (cover) con las
    esquinas horneadas: GTK3 no recorta un Gtk.Image por CSS."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    except GLib.Error:
        return None
    width, height = SHOT_W * scale, SHOT_H * scale
    factor = max(width / pixbuf.get_width(), height / pixbuf.get_height())
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    radius = SHOT_RADIUS * scale
    cr.new_sub_path()
    for x, y, angle in ((width - radius, radius, -90), (width - radius, height - radius, 0),
                        (radius, height - radius, 90), (radius, radius, 180)):
        cr.arc(x, y, radius, math.radians(angle), math.radians(angle + 90))
    cr.close_path()
    cr.clip()
    cr.translate((width - pixbuf.get_width() * factor) / 2, (height - pixbuf.get_height() * factor) / 2)
    cr.scale(factor, factor)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    surface.set_device_scale(scale, scale)
    return surface


class ThemePicker:
    KIND = None
    NAMESPACE = None
    LOCK = None
    # Códigos de i18n (módulo "temas") propios de cada selector.
    TEXTS = {}
    # Vista previa de lo que no está instalado: automática (cursores, pocos
    # KB) o con un botón (íconos, varios MB por tema).
    AUTO_REMOTE_PREVIEW = True

    def __init__(self, get_more):
        self.get_more = get_more
        self.window, container = build_layer_window(self.NAMESPACE, CSS_FILE)
        container.set_name("container")
        position_fixed_top(container, margin=60)
        self.scale = self.window.get_scale_factor()

        self.active = self.current()
        self.cards = {}
        self.hovered = None
        self.busy = False
        self.generation = {"installed": 0, "repos": 0, "store": 0}
        self.store_query = ""
        self.store_page = 0
        self.pool = ThreadPoolExecutor(max_workers=STORE_WORKERS)

        header = Gtk.Label(label=self.text("obtener" if get_more else "titulo"))
        header.set_name("header")
        container.pack_start(header, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_vhomogeneous(False)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.installed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.repos_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.store_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text(self.text("buscar"))
        self.search.connect("search-changed", lambda e: self._store_search(e.get_text().strip()))

        self.tab_buttons = {}
        if get_more:
            store_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            store_page.pack_start(self.search, False, False, 0)
            store_page.pack_start(_scroller(self.store_box), False, False, 0)
            self.stack.add_named(_scroller(self.repos_box), "repos")
            self.stack.add_named(store_page, "store")

            tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            tabs.set_name("picker-tabs")
            tabs.set_halign(Gtk.Align.CENTER)
            for name, code in (("repos", "cursor_tab_repos"), ("store", "cursor_tab_tienda")):
                button = Gtk.Button(label=t("temas", code))
                button.get_style_context().add_class("picker-tab")
                button.connect("clicked", lambda _b, n=name: self._show_tab(n))
                tabs.pack_start(button, False, False, 0)
                self.tab_buttons[name] = button
            container.pack_start(tabs, False, False, 0)
        else:
            self.stack.add_named(_scroller(self.installed_box), "installed")
        container.pack_start(self.stack, False, False, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self._build_installed()
        self.window.show_all()
        self._show_tab("repos" if get_more else "installed")

    # ---- lo que da cada subclase ----------------------------------------------

    def text(self, code, **kwargs):
        return t("temas", self.TEXTS.get(code, "cursor_" + code), **kwargs)

    def current(self):
        raise NotImplementedError

    def list_installed(self):
        raise NotImplementedError

    def apply(self, theme):
        """Corre en common.run_async."""
        raise NotImplementedError

    def load_preview(self, theme, bases=None):
        """En un hilo: (datos de vista previa, nota o None)."""
        raise NotImplementedError

    def preview_widgets(self, data):
        """En el hilo de GTK: lista de widgets para la fila de la tarjeta."""
        raise NotImplementedError

    def hover_cursor(self, data):
        return None

    def remote_bases(self, theme_dir):
        """Carpetas donde buscar un tema sin instalar (la suya primero)."""
        raise NotImplementedError

    # ---- pestañas -------------------------------------------------------------

    def _show_tab(self, name):
        self.stack.set_visible_child_name(name)
        for tab, button in self.tab_buttons.items():
            context = button.get_style_context()
            (context.add_class if tab == name else context.remove_class)("active")
        if name == "repos" and not self.repos_box.get_children():
            self._build_repos()
        elif name == "store" and not self.store_box.get_children():
            self._store_search(self.search.get_text().strip())
        elif name == "store":
            self.search.grab_focus()

    # ---- tarjetas -------------------------------------------------------------

    def _card(self, key, title, box, on_click=None, actions=(), shot=False):
        """EventBox + Box (estilo en la caja interna: GTK3 ignora el margin
        y padding de un EventBox). :hover a mano, la caja no tiene ventana
        propia."""
        entry = {"cursor": None, "actions": list(actions)}
        events = Gtk.EventBox()
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.get_style_context().add_class("picker-card")

        if shot:
            entry["shot"] = Gtk.Image()
            entry["shot"].get_style_context().add_class("picker-shot")
            entry["shot"].set_size_request(SHOT_W, SHOT_H)
            entry["shot"].set_valign(Gtk.Align.CENTER)
            card.pack_start(entry["shot"], False, False, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        body.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=title)
        name.get_style_context().add_class("picker-name")
        name.set_halign(Gtk.Align.START)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(28)
        body.pack_start(name, False, False, 0)
        # Debajo del nombre y oculta mientras esté vacía (_set_note).
        entry["note"] = _note()
        entry["note"].set_no_show_all(True)
        body.pack_start(entry["note"], False, False, 0)
        entry["previews"] = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        entry["previews"].get_style_context().add_class("picker-previews")
        entry["previews"].set_margin_top(4)
        body.pack_start(entry["previews"], False, False, 0)
        card.pack_start(body, True, True, 0)
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        buttons.set_valign(Gtk.Align.CENTER)
        for action in actions:
            buttons.pack_start(action, False, False, 0)
        card.pack_end(buttons, False, False, 0)

        events.add(card)
        events.connect("enter-notify-event", lambda _w, _e: self._hover(key))
        events.connect("leave-notify-event",
                       lambda _w, e: self._hover(None) if e.detail != Gdk.NotifyType.INFERIOR else False)
        if on_click is not None:
            events.set_tooltip_text(self.text("card_tooltip"))
            events.connect("button-release-event", lambda _w, e: self._on_card_click(entry, e, on_click))
        entry["card"] = card
        entry["events"] = events
        self.cards[key] = entry
        box.pack_start(events, False, False, 0)
        events.show_all()
        return entry

    def _on_card_click(self, entry, event, on_click):
        """El clic en un botón de la tarjeta (Quitar) también llega aquí."""
        on_button = any(a.get_state_flags() & Gtk.StateFlags.PRELIGHT for a in entry["actions"])
        if event.button != 1 or on_button:
            return False
        on_click()
        return True

    def _set_previews(self, key, data, note=None):
        entry = self.cards.get(key)
        if entry is None:
            return
        if note is not None:
            _set_note(entry, note)
        _clear(entry["previews"])
        for widget in self.preview_widgets(data):
            entry["previews"].pack_start(widget, False, False, 0)
        entry["previews"].show_all()
        entry["cursor"] = self.hover_cursor(data)

    def _remove_card(self, key):
        entry = self.cards.pop(key, None)
        if entry is not None:
            entry["events"].destroy()

    def _hover(self, key):
        if self.hovered in self.cards:
            self.cards[self.hovered]["card"].get_style_context().remove_class("hover")
        self.hovered = key
        entry = self.cards.get(key)
        if entry is not None:
            entry["card"].get_style_context().add_class("hover")
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            gdk_window.set_cursor(entry["cursor"] if entry else None)
        return False

    def _status(self, box, text):
        label = _note(text)
        label.get_style_context().add_class("picker-status")
        box.pack_start(label, False, False, 0)
        label.show()
        return label

    def _next_generation(self, name):
        self.generation[name] += 1
        return self.generation[name]

    def _drop_cards(self, prefix):
        for key in [k for k in self.cards if k.startswith(prefix)]:
            self.cards.pop(key)

    # ---- instalados -----------------------------------------------------------

    def _build_installed(self):
        if self.get_more:
            return
        generation = self._next_generation("installed")
        self._drop_cards("i:")
        _clear(self.installed_box)

        themes = self.list_installed()
        if self.active not in themes:
            themes.insert(0, self.active)
        for theme in themes:
            actions = []
            if kde_store.installed_from_store(theme):
                remove = _small_button(t("temas", "cursor_quitar"), t("temas", "cursor_quitar_tooltip"),
                                       lambda th=theme: self._uninstall(th))
                remove.set_sensitive(theme != self.active)
                actions.append(remove)
            entry = self._card("i:" + theme, theme, self.installed_box,
                               on_click=lambda th=theme: self._apply(th), actions=actions)
            if theme == self.active:
                entry["card"].get_style_context().add_class("active")

        def work():
            for theme in themes:
                data, note = self.load_preview(theme)
                GLib.idle_add(self._show_installed, generation, theme, data, note)

        threading.Thread(target=work, daemon=True).start()

    def _show_installed(self, generation, theme, data, note):
        if generation == self.generation["installed"]:
            self._set_previews("i:" + theme, data, note)
        return False

    def _apply(self, theme):
        if theme == self.active:
            return
        previous, current = self.cards["i:" + self.active], self.cards["i:" + theme]
        previous["card"].get_style_context().remove_class("active")
        current["card"].get_style_context().add_class("active")
        # El tema en uso no se puede quitar.
        for entry, sensitive in ((previous, True), (current, False)):
            for action in entry["actions"]:
                action.set_sensitive(sensitive)
        self.active = theme
        common.run_async(lambda: self.apply(theme))

    def _uninstall(self, theme):
        if theme == self.active:
            return
        kde_store.uninstall(theme)
        self._build_installed()

    # ---- repositorios ---------------------------------------------------------

    def _build_repos(self):
        generation = self._next_generation("repos")
        self._drop_cards("r:")
        _clear(self.repos_box)
        status = self._status(self.repos_box, t("temas", "cursor_buscando"))
        installed = set(self.list_installed())

        def work():
            pkgs = repo_themes.not_installed(self.KIND)
            sizes = repo_themes.download_sizes(pkgs) if pkgs else {}
            found = False
            for pkg in pkgs:
                if pkg not in sizes:
                    continue
                icons = repo_themes.preview_dir(self.KIND, pkg, download=self.AUTO_REMOTE_PREVIEW)
                if icons is None and self.AUTO_REMOTE_PREVIEW:
                    continue
                previews = []
                if icons is not None:
                    for name, path in repo_themes.package_themes(self.KIND, icons).items():
                        if name not in installed:
                            data, _note_text = self.load_preview(name, self.remote_bases(path))
                            if data:
                                previews.append((name, data))
                    if not previews:
                        continue
                found = True
                GLib.idle_add(self._show_package, generation, pkg, sizes[pkg], previews)
            GLib.idle_add(self._repos_done, generation, status, found)

        threading.Thread(target=work, daemon=True).start()

    def _show_package(self, generation, pkg, size, previews):
        if generation != self.generation["repos"]:
            return False
        if not previews:
            # Sin bajar todavía: una tarjeta por paquete con "Ver".
            self._package_card(pkg, pkg, size, None)
        for theme, data in previews:
            self._package_card(theme, pkg, size, data)
        return False

    def _package_card(self, theme, pkg, size, data):
        key = "r:" + theme
        install = _small_button(t("temas", "cursor_instalar"),
                                t("temas", "cursor_instalar_tooltip", pkg=pkg),
                                lambda: self._install_package(pkg))
        actions = [install]
        if data is None:
            view = _small_button(self.text("ver"), self.text("ver_tooltip"),
                                 lambda: self._view_package(key, pkg, size))
            actions.insert(0, view)
        entry = self._card(key, theme, self.repos_box, actions=actions)
        note = pkg if pkg != theme else None
        if data is not None:
            self._set_previews(key, data, note)
        else:
            _set_note(entry, size)

    def _view_package(self, key, pkg, size):
        entry = self.cards.get(key)
        if entry is None:
            return
        entry["actions"][0].set_sensitive(False)
        _set_note(entry, t("temas", "cursor_cargando"))
        generation = self.generation["repos"]
        installed = set(self.list_installed())

        def work():
            icons = repo_themes.preview_dir(self.KIND, pkg)
            previews = []
            for name, path in (repo_themes.package_themes(self.KIND, icons).items() if icons else ()):
                if name not in installed:
                    data, _note_text = self.load_preview(name, self.remote_bases(path))
                    if data:
                        previews.append((name, data))
            GLib.idle_add(self._package_viewed, generation, key, pkg, size, previews)

        self.pool.submit(work)

    def _package_viewed(self, generation, key, pkg, size, previews):
        if generation != self.generation["repos"] or key not in self.cards:
            return False
        if not previews:
            _set_note(self.cards[key], t("temas", "cursor_sin_vista"))
            return False
        # La tarjeta del paquete se reemplaza por una por tema, en su lugar.
        position = self.repos_box.get_children().index(self.cards[key]["events"])
        self._remove_card(key)
        for offset, (theme, data) in enumerate(previews):
            self._package_card(theme, pkg, size, data)
            self.repos_box.reorder_child(self.cards["r:" + theme]["events"], position + offset)
        return False

    def _repos_done(self, generation, status, found):
        if generation == self.generation["repos"]:
            if found:
                status.destroy()
            else:
                status.set_text(t("temas", "cursor_nada_mas"))
        return False

    def _install_package(self, pkg):
        """Ventana oculta mientras dura: el diálogo de polkit lo abre otro
        proceso y quedaría debajo de esta superficie layer-shell (mismo
        criterio que UFW y el uso de disco)."""
        if self.busy:
            return
        self.busy = True
        self.window.hide()

        def work():
            ok = repo_themes.install(pkg)
            GLib.idle_add(self._package_done, pkg, ok)

        threading.Thread(target=work, daemon=True).start()

    def _package_done(self, pkg, ok):
        self.busy = False
        self.window.show()
        if not ok:
            self._notify_error(t("temas", "cursor_error_instalar", pkg=pkg))
        self._build_repos()
        return False

    # ---- KDE Store ------------------------------------------------------------

    def _store_search(self, query):
        if query == self.store_query and self.store_box.get_children():
            return
        self.store_query = query
        self.store_page = 0
        self._next_generation("store")
        self._drop_cards("s:")
        _clear(self.store_box)
        self._store_load()

    def _store_load(self):
        generation = self.generation["store"]
        query, page = self.store_query, self.store_page
        status = self._status(self.store_box, t("temas", "cursor_buscando_tienda"))

        def work():
            try:
                items, total = kde_store.search(self.KIND, query, page)
            except Exception:
                GLib.idle_add(self._store_failed, generation, status)
                return
            GLib.idle_add(self._show_store_page, generation, status, items, total)

        threading.Thread(target=work, daemon=True).start()

    def _store_failed(self, generation, status):
        if generation == self.generation["store"]:
            status.set_text(t("temas", "cursor_error_tienda"))
        return False

    def _show_store_page(self, generation, status, items, total):
        if generation != self.generation["store"]:
            return False
        status.destroy()
        if not items and self.store_page == 0:
            self._status(self.store_box, t("temas", "cursor_sin_resultados"))
            return False
        for item in items:
            key = "s:" + item["id"]
            if key in self.cards:
                continue
            install = _small_button(t("temas", "cursor_instalar"), self.text("tienda_tooltip"),
                                    lambda it=item: self._install_store(it))
            actions = [install]
            if not self.AUTO_REMOTE_PREVIEW:
                actions.insert(0, _small_button(self.text("ver"), self.text("ver_tooltip"),
                                                lambda it=item: self._view_store_item(it)))
            entry = self._card(key, item["name"], self.store_box, actions=actions,
                               shot=not self.AUTO_REMOTE_PREVIEW)
            entry["item"] = item
            _set_note(entry, self._store_note(item))
            if self.AUTO_REMOTE_PREVIEW:
                install.set_sensitive(False)
                _set_note(entry, t("temas", "cursor_cargando"))
                self.pool.submit(self._fetch_store_item, generation, item)
            else:
                self.pool.submit(self._fetch_store_shot, generation, item)
                self.pool.submit(self._check_store_cache, generation, item)

        if (self.store_page + 1) * kde_store.PAGE_SIZE < total:
            more = Gtk.Button(label=t("temas", "cursor_cargar_mas"))
            more.get_style_context().add_class("picker-more-btn")
            more.connect("clicked", lambda b: self._store_more(b))
            self.store_box.pack_start(more, False, False, 0)
            more.show()
        return False

    def _store_note(self, item, themes=None):
        parts = [t("temas", "cursor_descargas", n=f"{item['downloads']:,}")]
        if not self.AUTO_REMOTE_PREVIEW:
            parts.insert(0, f"{item['files'][0][2] / 1024:.1f} MiB")
        if themes and len(themes) > 1:
            parts.insert(0, t("temas", "cursor_variantes", n=len(themes)))
        return " · ".join(parts)

    def _store_more(self, button):
        button.destroy()
        self.store_page += 1
        self._store_load()

    def _fetch_store_shot(self, generation, item):
        if generation != self.generation["store"]:
            return
        path = kde_store.preview_image(self.KIND, item)
        if path:
            GLib.idle_add(self._show_store_shot, generation, item, path)

    def _show_store_shot(self, generation, item, path):
        entry = self.cards.get("s:" + item["id"])
        if generation == self.generation["store"] and entry is not None and "shot" in entry:
            surface = _shot_surface(path, self.scale)
            if surface is not None:
                entry["shot"].set_from_surface(surface)
        return False

    def _check_store_cache(self, generation, item):
        """Si ya se bajó antes, la vista previa sale sin pedirla."""
        if generation == self.generation["store"] and kde_store.cached(self.KIND, item):
            self._fetch_store_item(generation, item)

    def _view_store_item(self, item):
        entry = self.cards.get("s:" + item["id"])
        if entry is None:
            return
        entry["actions"][0].set_sensitive(False)
        _set_note(entry, t("temas", "cursor_cargando"))
        self.pool.submit(self._fetch_store_item, self.generation["store"], item)

    def _fetch_store_item(self, generation, item):
        if generation != self.generation["store"]:
            return
        themes = kde_store.fetch(self.KIND, item)
        data = None
        if themes:
            first = sorted(themes)[0]
            data, _note_text = self.load_preview(os.path.basename(themes[first]),
                                                 self.remote_bases(themes[first]))
        GLib.idle_add(self._show_store_item, generation, item, themes, data)

    def _show_store_item(self, generation, item, themes, data):
        key = "s:" + item["id"]
        if generation != self.generation["store"] or key not in self.cards:
            return False
        entry = self.cards[key]
        if not data:
            if self.AUTO_REMOTE_PREVIEW:
                self._remove_card(key)
            else:
                _set_note(entry, t("temas", "cursor_sin_vista"))
            return False
        entry["themes"] = themes
        if not self.AUTO_REMOTE_PREVIEW:
            entry["actions"][0].hide()
            # Los íconos reales reemplazan la captura (las dos juntas
            # ensanchaban todo el popup).
            entry["shot"].hide()
        self._set_previews(key, data, self._store_note(item, themes))
        self._refresh_store_button(entry)
        return False

    def _refresh_store_button(self, entry):
        install = entry["actions"][-1]
        themes = entry.get("themes")
        if not themes:
            return
        names = {kde_store.safe_name(name) for name in themes}
        installed = names <= set(self.list_installed())
        install.set_label(t("temas", "cursor_instalado" if installed else "cursor_instalar"))
        install.set_sensitive(not installed)

    def _install_store(self, item):
        entry = self.cards.get("s:" + item["id"])
        if entry is None:
            return
        entry["actions"][-1].set_sensitive(False)
        for action in entry["actions"][:-1]:
            action.set_sensitive(False)

        def work():
            themes = entry.get("themes") or kde_store.fetch(self.KIND, item)
            try:
                ok = bool(themes) and kde_store.install(themes, item["id"]) is not None
            except OSError:
                ok = False
            GLib.idle_add(self._store_installed, item, themes, ok)

        threading.Thread(target=work, daemon=True).start()

    def _store_installed(self, item, themes, ok):
        entry = self.cards.get("s:" + item["id"])
        if not ok:
            self._notify_error(t("temas", "cursor_error_instalar", pkg=item["name"]))
            if entry is not None:
                for action in entry["actions"]:
                    action.set_sensitive(True)
        if entry is not None and themes:
            entry["themes"] = themes
            self._refresh_store_button(entry)
        return False

    # ---- varios ---------------------------------------------------------------

    def _notify_error(self, message):
        common.run_async(lambda: subprocess.run(
            ["notify-send", "-i", "dialog-error", self.text("titulo"), message], timeout=5))

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        self.pool.shutdown(wait=False, cancel_futures=True)
        if os.path.exists(self.LOCK):
            os.remove(self.LOCK)
        Gtk.main_quit()


def run(picker_class, process_name):
    common.set_process_name(process_name)
    kill_existing(picker_class.LOCK)
    kill_group(picker_class.LOCK)
    with open(picker_class.LOCK, "w") as f:
        f.write(str(os.getpid()))
    picker_class(get_more="--get" in sys.argv)
    Gtk.main()

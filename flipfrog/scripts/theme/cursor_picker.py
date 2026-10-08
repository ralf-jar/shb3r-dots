#!/usr/bin/env python3
"""Selector de cursor con vista previa (botón "Cursor" de la pestaña
"Personalización"). Tres pestañas: temas instalados, temas de los
repositorios sin instalar (cursor_packages.py, pkexec pacman) y la KDE
Store (cursor_store.py, a ~/.local/share/icons). Cada tarjeta muestra los
cursores principales (xcursor.py); pasar el mouse pone su flecha como
cursor de esta ventana, clic en una instalada la aplica a todo el sistema
(look_settings) y la ventana queda abierta para seguir probando."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango
import cairo
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
import cursor_packages
import cursor_store
import look_settings
import xcursor
from i18n import t
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top

CSS_FILE = os.path.join(SCRIPT_DIR, "cursor_picker.css")
LOCK = "/tmp/cursor-picker.pid"
PREVIEW_SIZE = 32
MAX_LIST_HEIGHT = 520
STORE_WORKERS = 3


def _surface(image, scale):
    width, height, _xhot, _yhot, pixels = image
    surface = cairo.ImageSurface.create_for_data(bytearray(pixels), cairo.FORMAT_ARGB32,
                                                 width, height, width * 4)
    surface.set_device_scale(scale, scale)
    return surface


def _clear(box):
    for child in box.get_children():
        child.destroy()


def _note(text=""):
    label = Gtk.Label(label=text)
    label.get_style_context().add_class("cursor-note")
    label.set_halign(Gtk.Align.START)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    return label


def _small_button(text, tooltip, on_click):
    button = Gtk.Button(label=text)
    button.get_style_context().add_class("cursor-action-btn")
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


class CursorPicker:
    def __init__(self, get_more):
        """`get_more`: "Obtener cursores" (repositorios + KDE Store, con
        pestañas); si no, solo los instalados, como "Fuente" / "Obtener
        fuentes" en la pestaña "Personalización"."""
        self.get_more = get_more
        self.window, container = build_layer_window("cursor-picker", CSS_FILE)
        container.set_name("container")
        position_fixed_top(container, margin=60)
        self.scale = self.window.get_scale_factor()

        look = look_settings.load()
        self.active = look["cursor_theme"]
        self.size = look["cursor_size"]
        self.cards = {}
        self.hovered = None
        self.busy = False
        self.generation = {"installed": 0, "repos": 0, "store": 0}
        self.store_query = ""
        self.store_page = 0
        self.pool = ThreadPoolExecutor(max_workers=STORE_WORKERS)

        header = Gtk.Label(label=t("temas", "obtener_cursores" if get_more else "cursor_titulo"))
        header.set_name("header")
        container.pack_start(header, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_vhomogeneous(False)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)

        self.installed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if not get_more:
            self.stack.add_named(_scroller(self.installed_box), "installed")
            container.pack_start(self.stack, False, False, 0)

        self.repos_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if get_more:
            self.stack.add_named(_scroller(self.repos_box), "repos")

        store_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text(t("temas", "cursor_buscar"))
        self.search.connect("search-changed", lambda e: self._store_search(e.get_text().strip()))
        store_page.pack_start(self.search, False, False, 0)
        self.store_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        store_page.pack_start(_scroller(self.store_box), False, False, 0)
        if get_more:
            self.stack.add_named(store_page, "store")

        self.tab_buttons = {}
        if get_more:
            tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            tabs.set_name("cursor-tabs")
            tabs.set_halign(Gtk.Align.CENTER)
            for name, code in (("repos", "cursor_tab_repos"), ("store", "cursor_tab_tienda")):
                button = Gtk.Button(label=t("temas", code))
                button.get_style_context().add_class("cursor-tab")
                button.connect("clicked", lambda _b, n=name: self._show_tab(n))
                tabs.pack_start(button, False, False, 0)
                self.tab_buttons[name] = button
            container.pack_start(tabs, False, False, 0)
            container.pack_start(self.stack, False, False, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self._build_installed()
        self.window.show_all()
        self._show_tab("repos" if get_more else "installed")

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

    def _card(self, key, title, box, on_click=None, action=None):
        """EventBox + Box (estilo en la caja interna: GTK3 ignora el margin
        y padding de un EventBox). :hover a mano, la caja no tiene ventana
        propia."""
        entry = {"cursor": None, "action": action}
        events = Gtk.EventBox()
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        card.get_style_context().add_class("cursor-card")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        names = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=title)
        name.get_style_context().add_class("cursor-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(28)
        names.pack_start(name, False, False, 0)
        entry["note"] = _note()
        names.pack_start(entry["note"], False, False, 0)
        body.pack_start(names, False, False, 0)
        entry["previews"] = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        entry["previews"].get_style_context().add_class("cursor-previews")
        body.pack_start(entry["previews"], False, False, 0)
        card.pack_start(body, True, True, 0)
        if action is not None:
            card.pack_end(action, False, False, 0)

        events.add(card)
        events.connect("enter-notify-event", lambda _w, _e: self._hover(key))
        events.connect("leave-notify-event",
                       lambda _w, e: self._hover(None) if e.detail != Gdk.NotifyType.INFERIOR else False)
        if on_click is not None:
            events.set_tooltip_text(t("temas", "cursor_card_tooltip"))
            events.connect("button-release-event", lambda _w, e: self._on_card_click(entry, e, on_click))
        entry["card"] = card
        entry["events"] = events
        self.cards[key] = entry
        box.pack_start(events, False, False, 0)
        events.show_all()
        return entry

    def _on_card_click(self, entry, event, on_click):
        """El clic en el botón de la tarjeta (Quitar) también llega aquí."""
        action = entry["action"]
        if event.button != 1 or (action is not None and action.get_state_flags() & Gtk.StateFlags.PRELIGHT):
            return False
        on_click()
        return True

    def _set_previews(self, key, images, note=None):
        entry = self.cards.get(key)
        if entry is None:
            return
        if note is not None:
            entry["note"].set_text(note)
        _clear(entry["previews"])
        for image in images:
            # Cada tema trae otros tamaños nominales: todas a PREVIEW_SIZE.
            fit = max(image[0] / PREVIEW_SIZE, 1)
            entry["previews"].pack_start(Gtk.Image.new_from_surface(_surface(image, fit)),
                                         False, False, 0)
        entry["previews"].show_all()
        if images:
            xhot, yhot = images[0][2:4]
            entry["cursor"] = Gdk.Cursor.new_from_surface(
                self.window.get_display(), _surface(images[0], self.scale),
                xhot / self.scale, yhot / self.scale)

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
        label.get_style_context().add_class("cursor-status")
        box.pack_start(label, False, False, 0)
        label.show()
        return label

    def _next_generation(self, name):
        self.generation[name] += 1
        return self.generation[name]

    # ---- instalados -----------------------------------------------------------

    def _build_installed(self):
        if self.get_more:
            return
        generation = self._next_generation("installed")
        for key in [k for k in self.cards if k.startswith("i:")]:
            self.cards.pop(key)
        _clear(self.installed_box)

        themes = look_settings.list_cursor_themes()
        if self.active not in themes:
            themes.insert(0, self.active)
        for theme in themes:
            action = None
            if cursor_store.installed_from_store(theme):
                action = _small_button(t("temas", "cursor_quitar"), t("temas", "cursor_quitar_tooltip"),
                                       lambda th=theme: self._uninstall(th))
                action.set_sensitive(theme != self.active)
            entry = self._card("i:" + theme, theme, self.installed_box,
                               on_click=lambda th=theme: self._apply(th), action=action)
            if theme == self.active:
                entry["card"].get_style_context().add_class("active")

        def work():
            size = PREVIEW_SIZE * self.scale
            for theme in themes:
                images = xcursor.preview_images(theme, size)
                resolved = xcursor.resolved_theme(theme)
                GLib.idle_add(self._show_installed, generation, theme, images, resolved)

        threading.Thread(target=work, daemon=True).start()

    def _show_installed(self, generation, theme, images, resolved):
        if generation == self.generation["installed"]:
            if not images:
                note = t("temas", "cursor_sin_vista")
            elif resolved != theme:
                note = t("temas", "cursor_hereda", name=resolved)
            else:
                note = None
            self._set_previews("i:" + theme, images, note)
        return False

    def _apply(self, theme):
        if theme == self.active:
            return
        previous, current = self.cards["i:" + self.active], self.cards["i:" + theme]
        previous["card"].get_style_context().remove_class("active")
        current["card"].get_style_context().add_class("active")
        # El tema en uso no se puede quitar.
        for entry, sensitive in ((previous, True), (current, False)):
            if entry["action"] is not None:
                entry["action"].set_sensitive(sensitive)
        self.active = theme
        size = self.size
        common.run_async(lambda: look_settings.apply_cursor(theme, size))

    def _uninstall(self, theme):
        if theme == self.active:
            return
        cursor_store.uninstall(theme)
        self._build_installed()
        self._refresh_store_buttons()

    # ---- repositorios ---------------------------------------------------------

    def _build_repos(self):
        generation = self._next_generation("repos")
        for key in [k for k in self.cards if k.startswith("r:")]:
            self.cards.pop(key)
        _clear(self.repos_box)
        status = self._status(self.repos_box, t("temas", "cursor_buscando"))
        installed = set(look_settings.list_cursor_themes())

        def work():
            size = PREVIEW_SIZE * self.scale
            found = False
            for pkg in cursor_packages.not_installed():
                icons = cursor_packages.preview_dir(pkg)
                if icons is None:
                    continue
                bases = cursor_packages.preview_bases(icons)
                previews = [(name, xcursor.preview_images(name, size, bases))
                            for name in cursor_packages.package_themes(icons) if name not in installed]
                previews = [(name, images) for name, images in previews if images]
                if previews:
                    found = True
                    GLib.idle_add(self._show_package, generation, pkg, previews)
            GLib.idle_add(self._repos_done, generation, status, found)

        threading.Thread(target=work, daemon=True).start()

    def _show_package(self, generation, pkg, previews):
        if generation != self.generation["repos"]:
            return False
        for theme, images in previews:
            install = _small_button(t("temas", "cursor_instalar"),
                                    t("temas", "cursor_instalar_tooltip", pkg=pkg),
                                    lambda: self._install_package(pkg))
            self._card("r:" + theme, theme, self.repos_box, action=install)
            self._set_previews("r:" + theme, images, pkg if pkg != theme else None)
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
            ok = cursor_packages.install(pkg)
            GLib.idle_add(self._package_done, pkg, ok)

        threading.Thread(target=work, daemon=True).start()

    def _package_done(self, pkg, ok):
        self.busy = False
        self.window.show()
        if not ok:
            self._notify_error(t("temas", "cursor_error_instalar", pkg=pkg))
        self._build_installed()
        self._build_repos()
        return False

    # ---- KDE Store ------------------------------------------------------------

    def _store_search(self, query):
        if query == self.store_query and self.store_box.get_children():
            return
        self.store_query = query
        self.store_page = 0
        self._next_generation("store")
        for key in [k for k in self.cards if k.startswith("s:")]:
            self.cards.pop(key)
        _clear(self.store_box)
        self._store_load()

    def _store_load(self):
        generation = self.generation["store"]
        query, page = self.store_query, self.store_page
        status = self._status(self.store_box, t("temas", "cursor_buscando_tienda"))

        def work():
            try:
                items, total = cursor_store.search(query, page)
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
            install = _small_button(t("temas", "cursor_instalar"), t("temas", "cursor_tienda_tooltip"),
                                    lambda it=item: self._install_store(it))
            install.set_sensitive(False)
            entry = self._card(key, item["name"], self.store_box, action=install)
            entry["note"].set_text(t("temas", "cursor_cargando"))
            entry["item"] = item
            self.pool.submit(self._fetch_store_item, generation, item)

        if (self.store_page + 1) * cursor_store.PAGE_SIZE < total:
            more = Gtk.Button(label=t("temas", "cursor_cargar_mas"))
            more.get_style_context().add_class("cursor-more-btn")
            more.connect("clicked", lambda b: self._store_more(b))
            self.store_box.pack_start(more, False, False, 0)
            more.show()
        return False

    def _store_more(self, button):
        button.destroy()
        self.store_page += 1
        self._store_load()

    def _fetch_store_item(self, generation, item):
        if generation != self.generation["store"]:
            return
        themes = cursor_store.fetch(item)
        size = PREVIEW_SIZE * self.scale
        images = []
        if themes:
            first = sorted(themes)[0]
            images = xcursor.preview_images(os.path.basename(themes[first]), size,
                                            [os.path.dirname(themes[first]), *look_settings.CURSOR_DIRS])
        GLib.idle_add(self._show_store_item, generation, item, themes, images)

    def _show_store_item(self, generation, item, themes, images):
        key = "s:" + item["id"]
        if generation != self.generation["store"] or key not in self.cards:
            return False
        if not images:
            self._remove_card(key)
            return False
        entry = self.cards[key]
        entry["themes"] = themes
        parts = [t("temas", "cursor_descargas", n=f"{item['downloads']:,}")]
        if len(themes) > 1:
            parts.insert(0, t("temas", "cursor_variantes", n=len(themes)))
        self._set_previews(key, images, " · ".join(parts))
        self._refresh_store_button(entry)
        return False

    def _refresh_store_button(self, entry):
        themes = entry.get("themes")
        if not themes:
            return
        names = {cursor_store.safe_name(name) for name in themes}
        installed = names <= set(look_settings.list_cursor_themes())
        entry["action"].set_label(t("temas", "cursor_instalado" if installed else "cursor_instalar"))
        entry["action"].set_sensitive(not installed)

    def _refresh_store_buttons(self):
        for key, entry in self.cards.items():
            if key.startswith("s:"):
                self._refresh_store_button(entry)

    def _install_store(self, item):
        entry = self.cards.get("s:" + item["id"])
        if entry is None or not entry.get("themes"):
            return
        themes = entry["themes"]
        entry["action"].set_sensitive(False)

        def work():
            try:
                cursor_store.install(themes, item["id"])
                ok = True
            except OSError:
                ok = False
            GLib.idle_add(self._store_installed, item, ok)

        threading.Thread(target=work, daemon=True).start()

    def _store_installed(self, item, ok):
        if not ok:
            self._notify_error(t("temas", "cursor_error_instalar", pkg=item["name"]))
        self._build_installed()
        self._refresh_store_buttons()
        return False

    # ---- varios ---------------------------------------------------------------

    def _notify_error(self, message):
        common.run_async(lambda: subprocess.run(
            ["notify-send", "-i", "dialog-error", t("temas", "cursor_titulo"), message], timeout=5))

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        self.pool.shutdown(wait=False, cancel_futures=True)
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    common.set_process_name("ff-cursor-pick")
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    CursorPicker(get_more="--get" in sys.argv)
    Gtk.main()


if __name__ == "__main__":
    main()

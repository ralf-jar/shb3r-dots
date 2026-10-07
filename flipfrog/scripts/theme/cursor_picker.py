#!/usr/bin/env python3
"""Selector de cursor con vista previa (botón "Cursor" de la pestaña
"Personalización"): una tarjeta por tema con sus cursores principales
(xcursor.py). Pasar el mouse por una tarjeta pone su flecha como cursor
de esta ventana; clic lo aplica a todo el sistema (look_settings) y la
ventana queda abierta para seguir probando. "Obtener más": temas de los
repositorios sin instalar (cursor_packages.py), con vista previa e
instalación vía pkexec."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango
import cairo
import os
import subprocess
import sys
import threading

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
import cursor_packages
import look_settings
import xcursor
from i18n import t
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top

CSS_FILE = os.path.join(SCRIPT_DIR, "cursor_picker.css")
LOCK = "/tmp/cursor-picker.pid"
PREVIEW_SIZE = 32
MAX_LIST_HEIGHT = 520


def _surface(image, scale):
    width, height, _xhot, _yhot, pixels = image
    surface = cairo.ImageSurface.create_for_data(bytearray(pixels), cairo.FORMAT_ARGB32,
                                                 width, height, width * 4)
    surface.set_device_scale(scale, scale)
    return surface


def _section_title(text):
    label = Gtk.Label(label=text)
    label.get_style_context().add_class("cursor-section")
    label.set_halign(Gtk.Align.START)
    return label


class CursorPicker:
    def __init__(self):
        self.window, container = build_layer_window("cursor-picker", CSS_FILE)
        container.set_name("container")
        position_fixed_top(container, margin=60)
        self.scale = self.window.get_scale_factor()

        look = look_settings.load()
        self.active = look["cursor_theme"]
        self.size = look["cursor_size"]
        self.cards = {}
        self.generation = 0
        self.installing = False

        header = Gtk.Label(label=t("temas", "cursor_titulo"))
        header.set_name("header")
        container.pack_start(header, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(MAX_LIST_HEIGHT)
        scroller.set_propagate_natural_height(True)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scroller.add(self.list_box)
        container.pack_start(scroller, False, False, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self._rebuild()
        self.window.show_all()

    # ---- lista ----------------------------------------------------------------

    def _rebuild(self):
        """Arma todo de nuevo (al abrir y tras instalar un paquete); los
        resultados de hilos de una generación anterior se ignoran."""
        self.generation += 1
        self.cards = {}
        for child in self.list_box.get_children():
            child.destroy()

        installed = look_settings.list_cursor_themes()
        if self.active not in installed:
            installed.insert(0, self.active)
        for theme in installed:
            self._add_installed_card(theme)

        self.list_box.pack_start(_section_title(t("temas", "cursor_obtener_mas")), False, False, 0)
        self.status = Gtk.Label(label=t("temas", "cursor_buscando"))
        self.status.get_style_context().add_class("cursor-note")
        self.status.set_halign(Gtk.Align.START)
        self.list_box.pack_start(self.status, False, False, 0)
        self.list_box.show_all()

        generation = self.generation
        threading.Thread(target=self._load, args=(generation, installed), daemon=True).start()

    def _card_body(self, theme, entry):
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        names = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=theme)
        name.get_style_context().add_class("cursor-name")
        name.set_halign(Gtk.Align.START)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(28)
        names.pack_start(name, False, False, 0)
        entry["note"] = Gtk.Label()
        entry["note"].get_style_context().add_class("cursor-note")
        entry["note"].set_ellipsize(Pango.EllipsizeMode.END)
        names.pack_start(entry["note"], False, False, 0)
        body.pack_start(names, False, False, 0)

        entry["previews"] = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        entry["previews"].get_style_context().add_class("cursor-previews")
        body.pack_start(entry["previews"], False, False, 0)
        return body

    def _add_installed_card(self, theme):
        entry = {"cursor": None}
        card = Gtk.Button()
        card.get_style_context().add_class("cursor-card")
        if theme == self.active:
            card.get_style_context().add_class("active")
        card.set_tooltip_text(t("temas", "cursor_card_tooltip"))
        card.add(self._card_body(theme, entry))
        card.connect("clicked", lambda _b: self._apply(theme))
        card.connect("enter-notify-event", lambda _w, _e: self._try_cursor(theme))
        card.connect("leave-notify-event", lambda _w, _e: self._try_cursor(None))
        entry["card"] = card
        self.cards[theme] = entry
        self.list_box.pack_start(card, False, False, 0)

    def _add_package_card(self, theme, pkg):
        """Tarjeta de un tema sin instalar: misma vista previa y prueba al
        pasar el mouse, botón "Instalar" en vez de clic para aplicar
        (instala el paquete entero, puede traer varios temas)."""
        entry = {"cursor": None}
        events = Gtk.EventBox()
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        card.get_style_context().add_class("cursor-card")
        card.pack_start(self._card_body(theme, entry), True, True, 0)

        install = Gtk.Button(label=t("temas", "cursor_instalar"))
        install.get_style_context().add_class("cursor-install-btn")
        install.set_valign(Gtk.Align.CENTER)
        install.set_tooltip_text(t("temas", "cursor_instalar_tooltip", pkg=pkg))
        install.connect("clicked", lambda _b: self._install(pkg))
        card.pack_end(install, False, False, 0)

        events.add(card)
        events.connect("enter-notify-event", lambda _w, _e: self._hover_package(card, theme))
        events.connect("leave-notify-event", lambda _w, event: self._hover_package(None, None)
                       if event.detail != Gdk.NotifyType.INFERIOR else False)
        entry["card"] = card
        if pkg != theme:
            entry["note"].set_text(pkg)
        self.cards[theme] = entry
        self.list_box.pack_start(events, False, False, 0)
        events.show_all()

    # ---- carga en segundo plano -----------------------------------------------

    def _load(self, generation, installed):
        size = PREVIEW_SIZE * self.scale
        for theme in installed:
            GLib.idle_add(self._show_previews, generation, theme,
                          xcursor.preview_images(theme, size), xcursor.resolved_theme(theme))

        known = set(installed)
        found = False
        for pkg in cursor_packages.not_installed():
            icons = cursor_packages.preview_dir(pkg)
            if icons is None:
                continue
            themes = [name for name in cursor_packages.package_themes(icons) if name not in known]
            bases = cursor_packages.preview_bases(icons)
            previews = [(name, xcursor.preview_images(name, size, bases)) for name in themes]
            previews = [(name, images) for name, images in previews if images]
            if previews:
                found = True
                GLib.idle_add(self._show_package, generation, pkg, previews)
        GLib.idle_add(self._load_done, generation, found)

    def _show_package(self, generation, pkg, previews):
        if generation != self.generation:
            return False
        for theme, images in previews:
            self._add_package_card(theme, pkg)
            self._show_previews(generation, theme, images, theme, keep_note=True)
        return False

    def _load_done(self, generation, found):
        if generation == self.generation:
            if found:
                self.status.destroy()
            else:
                self.status.set_text(t("temas", "cursor_nada_mas"))
        return False

    def _show_previews(self, generation, theme, images, resolved, keep_note=False):
        entry = self.cards.get(theme)
        if generation != self.generation or entry is None:
            return False
        if not keep_note:
            if not images:
                entry["note"].set_text(t("temas", "cursor_sin_vista"))
            elif resolved != theme:
                entry["note"].set_text(t("temas", "cursor_hereda", name=resolved))
        for image in images:
            entry["previews"].pack_start(Gtk.Image.new_from_surface(_surface(image, self.scale)),
                                         False, False, 0)
        entry["previews"].show_all()
        if images:
            xhot, yhot = images[0][2:4]
            entry["cursor"] = Gdk.Cursor.new_from_surface(
                self.window.get_display(), _surface(images[0], self.scale),
                xhot / self.scale, yhot / self.scale)
        return False

    # ---- acciones -------------------------------------------------------------

    def _try_cursor(self, theme):
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            entry = self.cards.get(theme)
            gdk_window.set_cursor(entry["cursor"] if entry else None)
        return False

    def _hover_package(self, card, theme):
        """:hover a mano: el Gtk.Box de la tarjeta no tiene ventana propia."""
        for entry in self.cards.values():
            entry["card"].get_style_context().remove_class("hover")
        if card is not None:
            card.get_style_context().add_class("hover")
        return self._try_cursor(theme)

    def _apply(self, theme):
        if theme == self.active:
            return
        self.cards[self.active]["card"].get_style_context().remove_class("active")
        self.cards[theme]["card"].get_style_context().add_class("active")
        self.active = theme
        size = self.size
        common.run_async(lambda: look_settings.apply_cursor(theme, size))

    def _install(self, pkg):
        """Ventana oculta mientras dura: el diálogo de polkit lo abre otro
        proceso y quedaría debajo de esta superficie layer-shell (mismo
        criterio que UFW y el uso de disco)."""
        if self.installing:
            return
        self.installing = True
        self.window.hide()

        def work():
            ok = cursor_packages.install(pkg)
            GLib.idle_add(self._install_done, pkg, ok)

        threading.Thread(target=work, daemon=True).start()

    def _install_done(self, pkg, ok):
        self.installing = False
        self.window.show()
        if not ok:
            message = t("temas", "cursor_error_instalar", pkg=pkg)
            common.run_async(lambda: subprocess.run(
                ["notify-send", "-i", "dialog-error", t("temas", "cursor_titulo"), message],
                timeout=5))
        self._rebuild()
        return False

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    common.set_process_name("ff-cursor-pick")
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    CursorPicker()
    Gtk.main()


if __name__ == "__main__":
    main()

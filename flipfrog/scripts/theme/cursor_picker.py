#!/usr/bin/env python3
"""Selector de cursor con vista previa (botón "Cursor" de la pestaña
"Personalización"): una tarjeta por tema con sus cursores principales
(xcursor.py). Pasar el mouse por una tarjeta pone su flecha como cursor
de esta ventana; clic lo aplica a todo el sistema (look_settings) y la
ventana queda abierta para seguir probando."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango
import cairo
import os
import sys
import threading

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
import look_settings
import xcursor
from i18n import t
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top

CSS_FILE = os.path.join(SCRIPT_DIR, "cursor_picker.css")
LOCK = "/tmp/cursor-picker.pid"
PREVIEW_SIZE = 32
MAX_LIST_HEIGHT = 460


def _surface(image, scale):
    width, height, _xhot, _yhot, pixels = image
    surface = cairo.ImageSurface.create_for_data(bytearray(pixels), cairo.FORMAT_ARGB32,
                                                 width, height, width * 4)
    surface.set_device_scale(scale, scale)
    return surface


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

        themes = look_settings.list_cursor_themes()
        if self.active not in themes:
            themes.insert(0, self.active)
        for theme in themes:
            self._add_card(theme)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()
        threading.Thread(target=self._load_previews, args=(themes,), daemon=True).start()

    def _add_card(self, theme):
        card = Gtk.Button()
        card.get_style_context().add_class("cursor-card")
        if theme == self.active:
            card.get_style_context().add_class("active")
        card.set_tooltip_text(t("temas", "cursor_card_tooltip"))

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        names = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=theme)
        name.get_style_context().add_class("cursor-name")
        name.set_halign(Gtk.Align.START)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(28)
        names.pack_start(name, False, False, 0)
        note = Gtk.Label()
        note.get_style_context().add_class("cursor-note")
        names.pack_start(note, False, False, 0)
        body.pack_start(names, False, False, 0)

        previews = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        previews.get_style_context().add_class("cursor-previews")
        body.pack_start(previews, False, False, 0)
        card.add(body)

        card.connect("clicked", lambda _b: self._apply(theme))
        card.connect("enter-notify-event", lambda _w, _e: self._try_cursor(theme))
        card.connect("leave-notify-event", lambda _w, _e: self._try_cursor(None))
        self.list_box.pack_start(card, False, False, 0)
        self.cards[theme] = {"card": card, "note": note, "previews": previews, "cursor": None}

    def _load_previews(self, themes):
        for theme in themes:
            images = xcursor.preview_images(theme, PREVIEW_SIZE * self.scale)
            GLib.idle_add(self._show_previews, theme, images, xcursor.resolved_theme(theme))

    def _show_previews(self, theme, images, resolved):
        entry = self.cards[theme]
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

    def _try_cursor(self, theme):
        gdk_window = self.window.get_window()
        if gdk_window is not None:
            entry = self.cards.get(theme)
            gdk_window.set_cursor(entry["cursor"] if entry else None)
        return False

    def _apply(self, theme):
        if theme == self.active:
            return
        self.cards[self.active]["card"].get_style_context().remove_class("active")
        self.cards[theme]["card"].get_style_context().add_class("active")
        self.active = theme
        size = self.size
        common.run_async(lambda: look_settings.apply_cursor(theme, size))

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

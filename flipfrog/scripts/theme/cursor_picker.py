#!/usr/bin/env python3
"""Selector de cursor con vista previa (fila "Cursor" de "Personalización")
y, con --get, "Obtener cursores" (repositorios + KDE Store). Base común en
theme_picker.py; aquí solo lo propio de los cursores: vista previa con
los archivos Xcursor (xcursor.py) y, al pasar el mouse por una tarjeta,
su flecha como cursor de la ventana."""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import look_settings
import theme_kinds
import theme_picker  # antes de gi.repository: fija Gtk 3.0
import xcursor
from i18n import t
from gi.repository import Gdk, Gtk
import cairo

PREVIEW_SIZE = 32


def _surface(image, scale):
    width, height, _xhot, _yhot, pixels = image
    surface = cairo.ImageSurface.create_for_data(bytearray(pixels), cairo.FORMAT_ARGB32,
                                                 width, height, width * 4)
    surface.set_device_scale(scale, scale)
    return surface


class CursorPicker(theme_picker.ThemePicker):
    KIND = theme_kinds.CURSORS
    NAMESPACE = "cursor-picker"
    LOCK = "/tmp/cursor-picker.pid"
    TEXTS = {"obtener": "obtener_cursores"}

    def current(self):
        self.size = look_settings.load()["cursor_size"]
        return look_settings.load()["cursor_theme"]

    def list_installed(self):
        return look_settings.list_cursor_themes()

    def apply(self, theme):
        look_settings.apply_cursor(theme, self.size)

    def load_preview(self, theme, bases=None):
        images = xcursor.preview_images(theme, PREVIEW_SIZE * self.scale, bases)
        if not images:
            return images, t("temas", "cursor_sin_vista")
        resolved = xcursor.resolved_theme(theme, bases)
        return images, (t("temas", "cursor_hereda", name=resolved) if resolved != theme else None)

    def preview_widgets(self, images):
        # Cada tema trae otros tamaños nominales: todas a PREVIEW_SIZE.
        return [Gtk.Image.new_from_surface(_surface(image, max(image[0] / PREVIEW_SIZE, 1)))
                for image in images]

    def hover_cursor(self, images):
        if not images:
            return None
        xhot, yhot = images[0][2:4]
        return Gdk.Cursor.new_from_surface(self.window.get_display(), _surface(images[0], self.scale),
                                           xhot / self.scale, yhot / self.scale)

    def remote_bases(self, theme_dir):
        return [os.path.dirname(theme_dir), *look_settings.CURSOR_DIRS]


if __name__ == "__main__":
    theme_picker.run(CursorPicker, "ff-cursor-pick")

#!/usr/bin/env python3
"""Selector de pack de íconos con vista previa (fila "Pack de íconos" de
"Personalización") y, con --get, "Obtener íconos" (repositorios + KDE
Store). Base común en theme_picker.py; aquí solo lo propio de los íconos:
vista previa con icon_lookup.py y aplicar con menu/icon_packs.py. Los
temas sin instalar pesan varios MB: su vista previa se pide con un botón,
la tienda muestra antes la captura que sube el autor."""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "menu"))
import icon_lookup
import icon_packs
import theme_kinds
import theme_picker  # antes de gi.repository: fija Gtk 3.0
from i18n import t
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

PREVIEW_SIZE = 32


class IconPicker(theme_picker.ThemePicker):
    KIND = theme_kinds.ICONS
    NAMESPACE = "icon-picker"
    LOCK = "/tmp/icon-picker.pid"
    AUTO_REMOTE_PREVIEW = False
    TEXTS = {
        "titulo": "iconos_titulo",
        "obtener": "obtener_iconos",
        "card_tooltip": "iconos_card_tooltip",
        "tienda_tooltip": "iconos_tienda_tooltip",
        "ver": "iconos_ver",
        "ver_tooltip": "iconos_ver_tooltip",
    }

    def current(self):
        return icon_packs.current_theme()

    def list_installed(self):
        return icon_packs.installed_themes()

    def apply(self, theme):
        icon_packs.apply_theme(theme)

    def load_preview(self, theme, bases=None):
        size = PREVIEW_SIZE * self.scale
        pixbufs = []
        for path in icon_lookup.preview_files(theme, PREVIEW_SIZE, bases):
            try:
                pixbufs.append(GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True))
            except GLib.Error:
                continue
        return pixbufs, (None if pixbufs else t("temas", "cursor_sin_vista"))

    def preview_widgets(self, pixbufs):
        return [Gtk.Image.new_from_surface(Gdk.cairo_surface_create_from_pixbuf(pixbuf, self.scale, None))
                for pixbuf in pixbufs]

    def remote_bases(self, theme_dir):
        return [os.path.dirname(theme_dir), *icon_lookup.ICON_DIRS]


if __name__ == "__main__":
    theme_picker.run(IconPicker, "ff-icon-pick")

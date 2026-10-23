#!/usr/bin/env python3
"""Selector de la pantalla de arranque (tema de Plymouth) con vista previa
(fila "Pantalla de arranque" de "Personalización") y, con --get, "Obtener
pantallas de arranque" (KDE Store), como el de Plasma. Base común en
theme_picker.py; lo propio de Plymouth (pkexec, initramfs) en
plymouth_themes.py.

Plymouth no tiene modo de prueba que conviva con Hyprland (necesita la
pantalla para él solo): la vista previa es una aproximación con las
imágenes del tema -- su secuencia de frames animada sobre su color de
fondo, con el doble del tamaño que tendría en una pantalla de 1080 px."""

import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import kde_store
import plymouth_themes
import theme_kinds
import theme_picker  # antes de gi.repository: fija Gtk 3.0
from i18n import t
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
import cairo
import math

PREVIEW_W, PREVIEW_H = 240, 135
SCREEN_H = 1080
# A escala real casi todo (un spinner de 32 px) quedaría de 4 px.
ZOOM = 2
FRAME_MS = 40
RADIUS = 6


def _rounded(cr, width, height, radius):
    cr.new_sub_path()
    for x, y, angle in ((width - radius, radius, -90), (width - radius, height - radius, 0),
                        (radius, height - radius, 90), (radius, radius, 180)):
        cr.arc(x, y, radius, math.radians(angle), math.radians(angle + 90))
    cr.close_path()


def _load(path):
    try:
        return GdkPixbuf.Pixbuf.new_from_file(path)
    except GLib.Error:
        return None


def _place(cr, image, factor, align, width, height):
    """`image` a `factor` (sin pasarse del tamaño de la vista previa) en
    la posición relativa `align` de la pantalla, como two-step."""
    fit = min(factor, width / image.get_width(), height / image.get_height())
    w, h = image.get_width() * fit, image.get_height() * fit
    cr.save()
    cr.translate((width - w) * align[0], (height - h) * align[1])
    cr.scale(fit, fit)
    Gdk.cairo_set_source_pixbuf(cr, image, 0, 0)
    cr.paint()
    cr.restore()


def _compose(info, scale):
    """Una superficie por frame (o una sola, con imagen fija): fondo +
    marca de agua + frame en su posición, esquinas horneadas."""
    width, height = PREVIEW_W * scale, PREVIEW_H * scale
    factor = ZOOM * height / SCREEN_H
    backdrop = _load(info["backdrop"]) if info["backdrop"] else None
    watermark = _load(info["watermark"]) if info["watermark"] else None
    images = [p for p in (_load(f) for f in info["frames"] or [info["still"]] if f) if p]
    if not images and backdrop is None and watermark is None:
        return []
    surfaces = []
    for image in images or [None]:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(surface)
        _rounded(cr, width, height, RADIUS * scale)
        cr.clip()
        cr.set_source_rgb(*info["background"])
        cr.paint()
        if backdrop is not None:
            cover = max(width / backdrop.get_width(), height / backdrop.get_height())
            cr.save()
            cr.translate((width - backdrop.get_width() * cover) / 2, (height - backdrop.get_height() * cover) / 2)
            cr.scale(cover, cover)
            Gdk.cairo_set_source_pixbuf(cr, backdrop, 0, 0)
            cr.paint()
            cr.restore()
        if watermark is not None:
            _place(cr, watermark, factor, info["watermark_align"], width, height)
        if image is not None:
            _place(cr, image, factor, info["align"], width, height)
        surface.set_device_scale(scale, scale)
        surfaces.append(surface)
    return surfaces


def _requires_note(path, theme):
    module = plymouth_themes.missing_module(path, theme)
    return t("temas", "arranque_falta_modulo", module=module) if module else None


class BootPicker(theme_picker.ThemePicker):
    KIND = theme_kinds.PLYMOUTH
    NAMESPACE = "boot-picker"
    LOCK = "/tmp/boot-picker.pid"
    TABS = ("store",)
    STORE_SHOT_ONLY = True
    NEEDS_AUTH = True
    TEXTS = {
        "titulo": "arranque_titulo",
        "obtener": "obtener_arranque",
        "card_tooltip": "arranque_card_tooltip",
        "tienda_tooltip": "arranque_tienda_tooltip",
        "quitar_tooltip": "arranque_quitar_tooltip",
    }

    def __init__(self, get_more):
        self.animations = []
        self.store_ids = set()
        super().__init__(get_more)
        GLib.timeout_add(FRAME_MS, self._tick)

    def current(self):
        return plymouth_themes.current()

    def list_installed(self):
        return plymouth_themes.installed()

    def apply(self, theme):
        notification = self._notify(t("temas", "arranque_aplicando", name=theme), persistent=True)
        ok = plymouth_themes.apply(theme, lambda: GLib.idle_add(self._show_applying, theme))
        self._notify(t("temas", "arranque_aplicado" if ok else "arranque_error_aplicar", name=theme),
                     replaces=notification)
        # El error ya salió en la misma notificación.
        return None

    def _show_applying(self, theme):
        """La ventana vuelve en cuanto se autorizó (la base la ocultó para
        el diálogo de polkit); los clics siguen ignorados mientras busy."""
        self.window.show()
        entry = self.cards.get("i:" + theme)
        if entry is not None:
            theme_picker._set_note(entry, t("temas", "arranque_reconstruyendo"))
        return False

    def load_preview(self, theme, bases=None):
        path = plymouth_themes.theme_dir(theme)
        notes = []
        description = plymouth_themes.config(path, theme).get("Plymouth Theme", "Name", fallback="").strip()
        if description and description.lower() != theme.lower():
            notes.append(description)
        requires = _requires_note(path, theme)
        if requires:
            notes.append(requires)
        info = plymouth_themes.preview(path, theme)
        if info["firmware"]:
            notes.append(t("temas", "arranque_logo_fabricante"))
        surfaces = _compose(info, self.scale)
        if not surfaces:
            notes.append(t("temas", "arranque_solo_texto"))
        return surfaces, " · ".join(notes) or None

    def preview_widgets(self, surfaces):
        if not surfaces:
            return []
        image = Gtk.Image.new_from_surface(surfaces[0])
        if len(surfaces) > 1:
            animation = [image, surfaces, 0]
            self.animations.append(animation)
            image.connect("destroy", lambda _w: self.animations.remove(animation))
        return [image]

    def _tick(self):
        for animation in self.animations:
            image, surfaces, index = animation
            animation[2] = (index + 1) % len(surfaces)
            image.set_from_surface(surfaces[animation[2]])
        return True

    def store_item_installed(self, item):
        return item["id"] in self.store_ids

    def store_installed(self, theme):
        return plymouth_themes.installed_from_store(theme)

    def store_install(self, themes, item_id):
        return plymouth_themes.install(themes, item_id)

    def store_uninstall(self, theme):
        plymouth_themes.uninstall(theme)

    def _notify(self, message, persistent=False, replaces=None):
        """Id de la notificación (para reemplazarla después) o None."""
        cmd = ["notify-send", "-p", "-i", "system-reboot", self.text("titulo"), message]
        if persistent:
            # Hasta que la reemplace el resultado; con tope, por si el
            # selector se cierra a media reconstrucción.
            cmd[1:1] = ["-t", str(plymouth_themes.APPLY_TIMEOUT * 1000)]
        if replaces:
            cmd[1:1] = ["-r", replaces]
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            return None

    # ---- KDE Store ------------------------------------------------------------

    def _show_store_page(self, *args):
        self.store_ids = plymouth_themes.store_ids()
        return super()._show_store_page(*args)

    def _fetch_store_shot(self, generation, item):
        """Además de la captura, baja el tema (pocos MB) para avisar antes
        de instalarlo si le falta su plugin."""
        super()._fetch_store_shot(generation, item)
        if generation == self.generation["store"]:
            themes = kde_store.fetch(self.KIND, item)
            GLib.idle_add(self._store_checked, generation, item, themes)

    def _store_checked(self, generation, item, themes):
        entry = self.cards.get("s:" + item["id"])
        if generation != self.generation["store"] or entry is None:
            return False
        if not themes:
            theme_picker._set_note(entry, t("temas", "arranque_no_es_tema"))
            entry["actions"][-1].set_sensitive(False)
            return False
        entry["themes"] = themes
        name = sorted(themes)[0]
        requires = _requires_note(themes[name], name)
        note = self._store_note(item, themes)
        theme_picker._set_note(entry, f"{requires} · {note}" if requires else note)
        self._refresh_store_button(entry)
        return False


if __name__ == "__main__":
    theme_picker.run(BootPicker, "ff-boot-pick")

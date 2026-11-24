#!/usr/bin/env python3
"""Ajustar fondo de pantalla (acción "Poner como fondo de pantalla" de
Thunar): vista previa por monitor y modo de cada uno -- rellenar con
recorte a mano, ajustar, doble capa (desenfocada atrás, completa encima),
estirar o centrar. Guarda el ajuste por archivo (themer/wallpaper_fit.py)
y aplica con wallpaper.py; opcionalmente saca la paleta del tema del
fondo (theme/palette_detect.py).

    wallpaper_adjust.py <imagen|gif|video>
    wallpaper_adjust.py          el fondo actual (SUPER+R); repetir cierra
"""

import hashlib
import os
import signal
import subprocess
import sys
import threading

import cairo
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
SCRIPTS_DIR = os.path.dirname(SCRIPT_DIR)
THEMER_DIR = os.path.normpath(os.path.join(SCRIPTS_DIR, "..", "themer"))
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, os.path.join(SCRIPTS_DIR, "theme"))
sys.path.insert(0, THEMER_DIR)
from waybar_lib import (kill_group, kill_existing, build_layer_window, position_fixed_top,
                        build_switch_row, svg_icon_image, load_module_css, suppress_close)
from i18n import t
import common
import wallpaper_fit as fit
import wallpaper

CSS_FILE = os.path.join(SCRIPT_DIR, "wallpaper_adjust.css")
TOGGLE_CSS = os.path.join(SCRIPTS_DIR, "toggles", "toggle_common.css")
DETECT_ICON = os.path.join(SCRIPTS_DIR, "theme", "icons", "detect.svg")
RENDER_ICON = os.path.join(SCRIPTS_DIR, "theme", "icons", "download.svg")
LOCK = "/tmp/wallpaper-adjust.pid"
STILL_CACHE = os.path.expanduser("~/.cache/flipfrog-wallpaper-fit/stills")

PREVIEW_MAX = 1600       # lado largo del pixbuf de trabajo
BLUR_BASE = 320          # ancho de la copia que se desenfoca para la vista previa
EDITOR_W, EDITOR_H = 780, 280
LAYOUT_W, LAYOUT_H = 780, 140
HANDLE = 12
MIN_CROP = 0.05
MODE_LABELS = {
    "fill": t("fondo", "modo_rellenar"),
    "fit": t("fondo", "modo_ajustar"),
    "blur": t("fondo", "modo_doble_capa"),
    "stretch": t("fondo", "modo_estirar"),
    "center": t("fondo", "modo_centrar"),
}
MODE_TIPS = {
    "fill": t("fondo", "tip_rellenar"),
    "fit": t("fondo", "tip_ajustar"),
    "blur": t("fondo", "tip_doble_capa"),
    "stretch": t("fondo", "tip_estirar"),
    "center": t("fondo", "tip_centrar"),
}


def _rgb(name, fallback):
    try:
        r, g, b, _a = common.theme_color_rgba(name)
        return r / 255, g / 255, b / 255
    except (OSError, AttributeError, ValueError):
        return fallback


def still_image(path):
    """Imagen fija para la vista previa (un frame si es video)."""
    if not wallpaper.is_video(path):
        return path
    os.makedirs(STILL_CACHE, exist_ok=True)
    key = hashlib.sha1(f"{path}:{os.path.getmtime(path)}".encode()).hexdigest() + ".png"
    frame = os.path.join(STILL_CACHE, key)
    if not os.path.isfile(frame):
        subprocess.run(["ffmpegthumbnailer", "-i", path, "-o", frame, "-s", "0"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    return frame if os.path.isfile(frame) else None


def cover_rect_in(rect, ratio_px, src_w, src_h):
    """Rectángulo más grande con proporción `ratio_px` (ancho/alto en
    pixeles) dentro de `rect`, centrado en él."""
    x, y, w, h = rect
    w_px, h_px = w * src_w, h * src_h
    if w_px / h_px > ratio_px:
        nw = h_px * ratio_px / src_w
        return [x + (w - nw) / 2, y, nw, h]
    nh = w_px / ratio_px / src_h
    return [x, y + (h - nh) / 2, w, nh]


class WallpaperAdjust:
    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.monitors = fit.monitors()
        still = still_image(self.path)
        if not still or not self.monitors:
            subprocess.run(["notify-send", t("fondo", "titulo"), t("fondo", "error_abrir")])
            sys.exit(1)
        self.still = still

        _fmt, self.src_w, self.src_h = GdkPixbuf.Pixbuf.get_file_info(still)
        self.pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(still, PREVIEW_MAX, PREVIEW_MAX, True)
        self.blur_cache = {}
        self.mask_cache = {}
        self.small = None

        saved = fit.load(self.path)
        self.settings = {}
        for m in self.monitors:
            s = saved.get(m["name"])
            if s:
                self.settings[m["name"]] = fit.normalized(s)
            else:
                self.settings[m["name"]] = fit.normalized(
                    {"mode": "fill", "crop": fit.cover_crop(self.src_w, self.src_h, m["width"], m["height"])})
        # El monitor más ancho primero: casi siempre es el que hay que ajustar.
        self.primary = max(self.monitors, key=lambda m: m["width"] / m["height"])["name"]
        self.current = self.primary
        self.drag = None
        self.applying = False

        self.accent = _rgb("myforegroundhover", (0.8, 0.6, 0.5))
        self.fg = _rgb("myforeground", (1, 1, 1))

        self.window, container = build_layer_window("wallpaper-adjust", CSS_FILE)
        load_module_css(TOGGLE_CSS)
        container.set_name("wa-container")
        position_fixed_top(container, margin=50)
        self.window.connect("key-press-event", self._on_key)

        title = Gtk.Label(label=t("fondo", "titulo"))
        title.set_name("wa-title")
        container.pack_start(title, False, False, 0)
        name = Gtk.Label(label=os.path.basename(self.path))
        name.set_name("wa-file")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(70)
        container.pack_start(name, False, False, 0)

        self.layout_area = Gtk.DrawingArea()
        self.layout_area.set_size_request(LAYOUT_W, self._layout_height())
        self.layout_area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.layout_area.connect("draw", self._draw_layout)
        self.layout_area.connect("button-press-event", self._on_layout_click)
        self.layout_area.set_tooltip_text(t("fondo", "tip_monitores"))
        container.pack_start(self._section(t("fondo", "seccion_resultado")), False, False, 0)
        container.pack_start(self.layout_area, False, False, 0)

        self.editor_label = self._section("")
        container.pack_start(self.editor_label, False, False, 0)
        self.editor = Gtk.DrawingArea()
        self.editor.set_name("wa-editor")
        self.editor.set_size_request(EDITOR_W, EDITOR_H)
        self.editor.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                               | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK)
        self.editor.connect("draw", self._draw_editor)
        self.editor.connect("button-press-event", self._on_press)
        self.editor.connect("button-release-event", self._on_release)
        self.editor.connect("motion-notify-event", self._on_motion)
        self.editor.connect("scroll-event", self._on_scroll)
        self.editor.set_tooltip_text(t("fondo", "tip_recorte"))
        container.pack_start(self.editor, False, False, 0)

        modes = Gtk.Box(spacing=6)
        modes.set_name("wa-modes")
        modes.set_homogeneous(True)
        self.mode_buttons = {}
        for mode in fit.MODES:
            btn = Gtk.ToggleButton(label=MODE_LABELS[mode])
            btn.get_style_context().add_class("wa-mode")
            btn.set_tooltip_text(MODE_TIPS[mode])
            btn.connect("toggled", self._on_mode, mode)
            self.mode_buttons[mode] = btn
            modes.pack_start(btn, True, True, 0)
        container.pack_start(modes, False, False, 0)

        self.blur_row = Gtk.Box(spacing=12)
        self.blur_row.set_name("wa-blur-row")
        self.blur_scale = self._slider(self.blur_row, t("fondo", "desenfoque"), 0.5, 10, 0.5, "blur")
        self.dim_scale = self._slider(self.blur_row, t("fondo", "oscurecer"), 0, 80, 5, "dim")
        container.pack_start(self.blur_row, False, False, 0)

        self.size_row = Gtk.Box(spacing=12)
        self.size_row.set_name("wa-size-row")
        self.size_scale = self._slider(self.size_row, t("fondo", "tamano_capa"), fit.MIN_SIZE, 100, 5, "size")
        self.size_scale.set_tooltip_text(t("fondo", "tip_tamano_capa"))
        self.feather_scale = self._slider(self.size_row, t("fondo", "degradar_bordes"), 0, fit.MAX_FEATHER, 5, "feather")
        self.feather_scale.set_tooltip_text(t("fondo", "tip_degradar_bordes"))
        container.pack_start(self.size_row, False, False, 0)

        tools = Gtk.Box(spacing=8)
        tools.set_name("wa-tools")
        reset = Gtk.Button(label=t("fondo", "restablecer"))
        reset.set_tooltip_text(t("fondo", "tip_restablecer"))
        reset.connect("clicked", self._on_reset)
        same = Gtk.Button(label=t("fondo", "igual_en_todos"))
        same.set_tooltip_text(t("fondo", "tip_igual_en_todos"))
        same.connect("clicked", self._on_copy_all)
        same.set_sensitive(len(self.monitors) > 1)
        for btn in (reset, same):
            btn.get_style_context().add_class("wa-tool")
            tools.pack_start(btn, False, False, 0)
        container.pack_start(tools, False, False, 0)

        palette_row = Gtk.Box()
        palette_row.set_name("wa-palette-row")
        _w, self.palette_switch, _h = build_switch_row(
            palette_row, svg_icon_image(DETECT_ICON, size=16), t("fondo", "detectar_paleta"),
            bool(fit.get_option("detect_palette", False)), lambda _state: None)
        palette_row.set_tooltip_text(t("fondo", "tip_detectar_paleta"))
        container.pack_start(palette_row, False, False, 0)

        self.prerender_switch = None
        if wallpaper.is_video(self.path):
            prerender_row = Gtk.Box()
            prerender_row.set_name("wa-prerender-row")
            _w, self.prerender_switch, _h = build_switch_row(
                prerender_row, svg_icon_image(RENDER_ICON, size=16), t("fondo", "prerender"),
                bool(fit.get_option("prerender_video", True)), lambda _state: None)
            prerender_row.set_tooltip_text(t("fondo", "tip_prerender"))
            container.pack_start(prerender_row, False, False, 0)

        bottom = Gtk.Box(spacing=8)
        bottom.set_name("wa-bottom")
        self.status = Gtk.Label()
        self.status.set_name("wa-status")
        self.status.set_halign(Gtk.Align.START)
        bottom.pack_start(self.status, True, True, 0)
        cancel = Gtk.Button(label=t("fondo", "cancelar"))
        cancel.get_style_context().add_class("wa-tool")
        cancel.connect("clicked", lambda *_: self.window.destroy())
        self.apply_btn = Gtk.Button(label=t("fondo", "aplicar"))
        self.apply_btn.get_style_context().add_class("wa-apply")
        self.apply_btn.connect("clicked", self._on_apply)
        bottom.pack_end(self.apply_btn, False, False, 0)
        bottom.pack_end(cancel, False, False, 0)
        container.pack_start(bottom, False, False, 0)

        self.window.connect("destroy", lambda *_: Gtk.main_quit())
        self.window.show_all()
        self._select(self.current)

    # --- widgets -----------------------------------------------------

    def _section(self, text):
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("wa-section")
        label.set_halign(Gtk.Align.START)
        return label

    def _slider(self, row, text, lo, hi, step, key):
        box = Gtk.Box(spacing=8)
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("wa-slider-label")
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        value = Gtk.Label()
        value.get_style_context().add_class("wa-slider-value")
        value.set_width_chars(4)
        value.set_xalign(1.0)
        scale.connect("value-changed", self._on_slider, key, value)
        box.pack_start(label, False, False, 0)
        box.pack_start(scale, True, True, 0)
        box.pack_start(value, False, False, 0)
        row.pack_start(box, True, True, 0)
        return scale

    def _monitor(self, name):
        return next(m for m in self.monitors if m["name"] == name)

    def _select(self, name):
        self.current = name
        s = self.settings[name]
        m = self._monitor(name)
        self._syncing = True
        for mode, btn in self.mode_buttons.items():
            btn.set_active(mode == s["mode"])
        self.blur_scale.set_value(s["blur"])
        self.dim_scale.set_value(s["dim"])
        self.size_scale.set_value(s["size"])
        self.feather_scale.set_value(s["feather"])
        self._syncing = False
        self.blur_row.set_visible(s["mode"] == "blur")
        self.size_row.set_visible(s["mode"] in ("fit", "blur"))
        self.editor_label.set_text(t("fondo", "seccion_recorte", monitor=name,
                                     size=f"{m['width']}×{m['height']}"))
        self._redraw()

    def _redraw(self):
        self.layout_area.queue_draw()
        self.editor.queue_draw()

    # --- geometría ---------------------------------------------------

    def _ratio(self, name=None):
        m = self._monitor(name or self.current)
        return m["width"] / m["height"]

    def _image_rect(self):
        """Dónde se dibuja la imagen completa dentro del editor."""
        aw, ah = self.editor.get_allocated_width(), self.editor.get_allocated_height()
        scale = min(aw / self.src_w, ah / self.src_h)
        iw, ih = self.src_w * scale, self.src_h * scale
        return (aw - iw) / 2, (ah - ih) / 2, iw, ih

    def _crop_widget_rect(self):
        ix, iy, iw, ih = self._image_rect()
        x, y, w, h = self.settings[self.current]["crop"]
        return ix + x * iw, iy + y * ih, w * iw, h * ih

    def _layout_scale(self):
        min_x = min(m["x"] for m in self.monitors)
        min_y = min(m["y"] for m in self.monitors)
        max_x = max(m["x"] + m["lw"] for m in self.monitors)
        max_y = max(m["y"] + m["lh"] for m in self.monitors)
        scale = min((LAYOUT_W - 20) / (max_x - min_x), (LAYOUT_H - 10) / (max_y - min_y))
        off_x = (LAYOUT_W - (max_x - min_x) * scale) / 2
        return scale, min_x, min_y, off_x

    def _layout_height(self):
        scale, min_y = self._layout_scale()[0], min(m["y"] for m in self.monitors)
        max_y = max(m["y"] + m["lh"] for m in self.monitors)
        return int((max_y - min_y) * scale) + 6

    def _monitor_boxes(self):
        scale, min_x, min_y, off_x = self._layout_scale()
        gap = 3
        return [(m, off_x + (m["x"] - min_x) * scale + gap, (m["y"] - min_y) * scale + gap,
                 m["lw"] * scale - 2 * gap, m["lh"] * scale - 2 * gap) for m in self.monitors]

    # --- dibujo ------------------------------------------------------

    def _blurred(self, radius):
        key = round(radius, 1)
        if key not in self.blur_cache:
            from PIL import Image, ImageFilter
            if self.small is None:
                img = Image.open(self.still).convert("RGB")
                img.thumbnail((BLUR_BASE, BLUR_BASE * 4))
                self.small = img
            out = self.small.filter(ImageFilter.GaussianBlur(radius=max(0.1, key)))
            data = GLib.Bytes.new(out.tobytes())
            self.blur_cache[key] = GdkPixbuf.Pixbuf.new_from_bytes(
                data, GdkPixbuf.Colorspace.RGB, False, 8, out.width, out.height, out.width * 3)
        return self.blur_cache[key]

    def _feather_mask(self, dw, dh, feather):
        """Máscara de alfa de los bordes degradados, mismas proporciones
        que la de wallpaper_fit.filter_graph."""
        mw, mh = max(8, int(dw / 2)), max(8, int(dh / 2))
        key = (mw, mh, feather)
        if key not in self.mask_cache:
            from PIL import Image, ImageDraw, ImageFilter
            edge = max(1.0, feather / 100 * min(mw, mh))
            inset = max(1, round(edge / 2))
            img = Image.new("L", (mw, mh), 0)
            ImageDraw.Draw(img).rectangle((inset, inset, mw - inset - 1, mh - inset - 1), fill=255)
            img = img.filter(ImageFilter.GaussianBlur(max(0.5, edge / 4)))
            img = img.point(lambda v: v * v // 255)  # misma curva que el lut de ffmpeg
            stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_A8, mw)
            raw = img.tobytes()
            data = bytearray(stride * mh)
            for row in range(mh):
                data[row * stride:row * stride + mw] = raw[row * mw:(row + 1) * mw]
            self.mask_cache[key] = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_A8, mw, mh, stride)
        return self.mask_cache[key]

    def _paint_region(self, cr, pixbuf, region, dx, dy, dw, dh):
        """Pinta la parte `region` (fracciones) de `pixbuf` estirada a
        (dx, dy, dw, dh)."""
        x, y, w, h = region
        pw, ph = pixbuf.get_width(), pixbuf.get_height()
        sx, sy = dw / (w * pw), dh / (h * ph)
        cr.save()
        cr.rectangle(dx, dy, dw, dh)
        cr.clip()
        cr.translate(dx - x * pw * sx, dy - y * ph * sy)
        cr.scale(sx, sy)
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()
        cr.restore()

    def _paint_result(self, cr, m, s, x0, y0, w0, h0):
        """Misma geometría que wallpaper_fit.filter_graph, en chico."""
        cr.save()
        cr.rectangle(x0, y0, w0, h0)
        cr.clip()
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        cx, cy, cw, ch = s["crop"]
        cw_px, ch_px = cw * self.src_w, ch * self.src_h
        mode = s["mode"]
        if mode in ("fill", "stretch"):
            region = s["crop"] if mode == "stretch" else cover_rect_in(s["crop"], w0 / h0, self.src_w, self.src_h)
            self._paint_region(cr, self.pixbuf, region, x0, y0, w0, h0)
        elif mode == "center":
            k = w0 / m["width"]
            dw, dh = cw_px * k, ch_px * k
            self._paint_region(cr, self.pixbuf, s["crop"], x0 + (w0 - dw) / 2, y0 + (h0 - dh) / 2, dw, dh)
        else:
            if mode == "blur":
                cover = max(m["width"] / cw_px, m["height"] / ch_px)
                radius = s["blur"] * m["width"] / 100 / cover * (BLUR_BASE / self.src_w)
                bg = self._blurred(radius)
                self._paint_region(cr, bg, cover_rect_in(s["crop"], w0 / h0, self.src_w, self.src_h),
                                   x0, y0, w0, h0)
                cr.set_source_rgba(0, 0, 0, s["dim"] / 100)
                cr.paint()
            k = min(w0 / cw_px, h0 / ch_px) * s["size"] / 100
            dw, dh = cw_px * k, ch_px * k
            fx, fy = x0 + (w0 - dw) / 2, y0 + (h0 - dh) / 2
            if s["feather"]:
                cr.push_group()
                self._paint_region(cr, self.pixbuf, s["crop"], fx, fy, dw, dh)
                cr.pop_group_to_source()
                mask = self._feather_mask(dw, dh, s["feather"])
                cr.translate(fx, fy)
                cr.scale(dw / mask.get_width(), dh / mask.get_height())
                cr.mask_surface(mask, 0, 0)
            else:
                self._paint_region(cr, self.pixbuf, s["crop"], fx, fy, dw, dh)
        cr.restore()

    def _draw_layout(self, _area, cr):
        for m, x, y, w, h in self._monitor_boxes():
            self._paint_result(cr, m, self.settings[m["name"]], x, y, w, h)
            selected = m["name"] == self.current
            cr.set_line_width(3 if selected else 1)
            if selected:
                cr.set_source_rgb(*self.accent)
            else:
                cr.set_source_rgba(*self.fg, 0.35)
            cr.rectangle(x, y, w, h)
            cr.stroke()
            cr.set_font_size(12)
            label = f"{m['name']} · {MODE_LABELS[self.settings[m['name']]['mode']]}"
            ext = cr.text_extents(label)
            cr.set_source_rgba(0, 0, 0, 0.6)
            cr.rectangle(x + 6, y + 6, ext.width + 12, 20)
            cr.fill()
            cr.set_source_rgb(*(self.accent if selected else self.fg))
            cr.move_to(x + 12, y + 20)
            cr.show_text(label)

    def _draw_editor(self, _area, cr):
        ix, iy, iw, ih = self._image_rect()
        self._paint_region(cr, self.pixbuf, fit.FULL, ix, iy, iw, ih)
        rx, ry, rw, rh = self._crop_widget_rect()
        cr.set_source_rgba(0, 0, 0, 0.6)
        cr.rectangle(ix, iy, iw, ih)
        cr.rectangle(rx, ry, rw, rh)
        cr.set_fill_rule(1)  # EVEN_ODD: oscurece solo lo de fuera del recorte
        cr.fill()
        cr.set_fill_rule(0)
        cr.set_source_rgb(*self.accent)
        cr.set_line_width(2)
        cr.rectangle(rx, ry, rw, rh)
        cr.stroke()
        # tercios
        cr.set_source_rgba(*self.fg, 0.25)
        cr.set_line_width(1)
        for i in (1, 2):
            cr.move_to(rx + rw * i / 3, ry)
            cr.line_to(rx + rw * i / 3, ry + rh)
            cr.move_to(rx, ry + rh * i / 3)
            cr.line_to(rx + rw, ry + rh * i / 3)
        cr.stroke()
        cr.set_source_rgb(*self.accent)
        for hx, hy in self._corners(rx, ry, rw, rh):
            cr.rectangle(hx - HANDLE / 2, hy - HANDLE / 2, HANDLE, HANDLE)
        cr.fill()

    @staticmethod
    def _corners(rx, ry, rw, rh):
        return ((rx, ry), (rx + rw, ry), (rx, ry + rh), (rx + rw, ry + rh))

    # --- recorte -----------------------------------------------------

    def _locked(self):
        return self.settings[self.current]["mode"] == "fill"

    def _on_press(self, _w, event):
        if event.button != 1:
            return False
        rx, ry, rw, rh = self._crop_widget_rect()
        crop = list(self.settings[self.current]["crop"])
        for i, (hx, hy) in enumerate(self._corners(rx, ry, rw, rh)):
            if abs(event.x - hx) <= HANDLE and abs(event.y - hy) <= HANDLE:
                x, y, w, h = crop
                fixed = ((x + w, y + h), (x, y + h), (x + w, y), (x, y))[i]
                self.drag = ("resize", fixed)
                return True
        if rx <= event.x <= rx + rw and ry <= event.y <= ry + rh:
            self.drag = ("move", event.x, event.y, crop)
            self.editor.get_window().set_cursor(Gdk.Cursor.new_from_name(self.editor.get_display(), "grabbing"))
            return True
        return False

    def _on_release(self, _w, _event):
        if self.drag:
            self.drag = None
            self.editor.get_window().set_cursor(None)

    def _on_motion(self, _w, event):
        if not self.drag:
            rx, ry, rw, rh = self._crop_widget_rect()
            cursor = None
            if any(abs(event.x - hx) <= HANDLE and abs(event.y - hy) <= HANDLE
                   for hx, hy in self._corners(rx, ry, rw, rh)):
                cursor = "crosshair"
            elif rx <= event.x <= rx + rw and ry <= event.y <= ry + rh:
                cursor = "grab"
            win = self.editor.get_window()
            win.set_cursor(Gdk.Cursor.new_from_name(self.editor.get_display(), cursor) if cursor else None)
            return
        ix, iy, iw, ih = self._image_rect()
        s = self.settings[self.current]
        if self.drag[0] == "move":
            _kind, x0, y0, (cx, cy, cw, ch) = self.drag
            nx = min(max(0.0, cx + (event.x - x0) / iw), 1 - cw)
            ny = min(max(0.0, cy + (event.y - y0) / ih), 1 - ch)
            s["crop"] = [nx, ny, cw, ch]
        else:
            fx, fy = self.drag[1]
            mx = min(max(0.0, (event.x - ix) / iw), 1.0)
            my = min(max(0.0, (event.y - iy) / ih), 1.0)
            s["crop"] = self._resize(fx, fy, mx, my)
        self._redraw()

    def _resize(self, fx, fy, mx, my):
        dw_px = max(abs(mx - fx) * self.src_w, 1)
        dh_px = max(abs(my - fy) * self.src_h, 1)
        max_w = (fx if mx < fx else 1 - fx) * self.src_w
        max_h = (fy if my < fy else 1 - fy) * self.src_h
        if self._locked():
            r = self._ratio()
            if dw_px / dh_px > r:
                dh_px = dw_px / r
            else:
                dw_px = dh_px * r
            k = min(1.0, max_w / dw_px, max_h / dh_px)
            dw_px, dh_px = dw_px * k, dh_px * k
            min_px = MIN_CROP * min(self.src_w, self.src_h)
            if dw_px < min_px or dh_px < min_px:
                k = min_px / min(dw_px, dh_px)
                dw_px, dh_px = dw_px * k, dh_px * k
        else:
            dw_px = min(max(dw_px, MIN_CROP * self.src_w), max_w)
            dh_px = min(max(dh_px, MIN_CROP * self.src_h), max_h)
        w, h = dw_px / self.src_w, dh_px / self.src_h
        x = fx - w if mx < fx else fx
        y = fy - h if my < fy else fy
        return [max(0.0, x), max(0.0, y), min(w, 1.0), min(h, 1.0)]

    def _on_scroll(self, _w, event):
        s = self.settings[self.current]
        x, y, w, h = s["crop"]
        if event.direction == Gdk.ScrollDirection.UP:
            k = 0.92
        elif event.direction == Gdk.ScrollDirection.DOWN:
            k = 1 / 0.92
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            k = 1.0 + 0.08 * event.delta_y
        else:
            return False
        k = min(k, 1 / w, 1 / h)
        nw, nh = w * k, h * k
        if min(nw * self.src_w, nh * self.src_h) < MIN_CROP * min(self.src_w, self.src_h):
            return True
        cx, cy = x + w / 2, y + h / 2
        nx = min(max(0.0, cx - nw / 2), 1 - nw)
        ny = min(max(0.0, cy - nh / 2), 1 - nh)
        s["crop"] = [nx, ny, nw, nh]
        self._redraw()
        return True

    # --- controles ---------------------------------------------------

    def _default_crop(self, mode, name=None):
        m = self._monitor(name or self.current)
        if mode == "fill":
            return fit.cover_crop(self.src_w, self.src_h, m["width"], m["height"])
        return list(fit.FULL)

    def _on_mode(self, btn, mode):
        if getattr(self, "_syncing", False):
            return
        if not btn.get_active():
            if self.settings[self.current]["mode"] == mode:
                self._syncing = True
                btn.set_active(True)  # siempre queda uno marcado
                self._syncing = False
            return
        s = self.settings[self.current]
        previous = s["mode"]
        s["mode"] = mode
        if mode == "fill" and previous != "fill":
            s["crop"] = cover_rect_in(s["crop"], self._ratio(), self.src_w, self.src_h)
        elif previous == "fill" and mode != "fill":
            # Con el recorte del monitor, ajustar se vería igual que rellenar.
            s["crop"] = list(fit.FULL)
        self._select(self.current)

    def _on_slider(self, scale, key, value_label):
        v = scale.get_value()
        value_label.set_text(f"{v:.1f}" if key == "blur" else f"{int(v)}%")
        if getattr(self, "_syncing", False):
            return
        self.settings[self.current][key] = round(v, 1) if key == "blur" else int(v)
        self._redraw()

    def _on_reset(self, _btn):
        s = self.settings[self.current]
        s["crop"] = self._default_crop(s["mode"])
        self._redraw()

    def _on_copy_all(self, _btn):
        src = self.settings[self.current]
        for m in self.monitors:
            if m["name"] == self.current:
                continue
            s = dict(src)
            s["crop"] = (self._default_crop("fill", m["name"]) if src["mode"] == "fill"
                         else list(src["crop"]))
            self.settings[m["name"]] = s
        self._redraw()

    def _on_layout_click(self, _w, event):
        for m, x, y, w, h in self._monitor_boxes():
            if x <= event.x <= x + w and y <= event.y <= y + h:
                self._select(m["name"])
                return True
        return False

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._on_apply(None)
            return True
        return False

    # --- aplicar -----------------------------------------------------

    def _on_apply(self, _btn):
        if self.applying:
            return
        self.applying = True
        self.apply_btn.set_sensitive(False)
        self.status.set_text(t("fondo", "aplicando"))
        detect = self.palette_switch.get_active()
        if self.prerender_switch is not None:
            fit.set_option("prerender_video", self.prerender_switch.get_active())
        settings = {}
        for m in self.monitors:
            s = fit.normalized(self.settings[m["name"]])
            if not fit.is_default(s, (self.src_w, self.src_h), (m["width"], m["height"])):
                settings[m["name"]] = s
        # La paleta sale de lo que se ve en el monitor más ancho.
        primary = self.settings[self.primary]["crop"]
        threading.Thread(target=self._apply_worker, args=(settings, detect, primary), daemon=True).start()

    def _apply_worker(self, settings, detect, box):
        fit.save(self.path, settings)
        if bool(fit.get_option("detect_palette", False)) != detect:
            fit.set_option("detect_palette", detect)
        subprocess.run([sys.executable, os.path.join(THEMER_DIR, "wallpaper.py"), "set", self.path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        message = None
        if detect:
            import palette_detect
            colors, err = palette_detect.colors_from_image(self.still, box=box)
            if err:
                message = err
            else:
                # reload.sh hace hyprctl reload (click afuera sintético).
                suppress_close(5)
                if not palette_detect.apply_colors(colors):
                    message = t("fondo", "error_recargar")
        GLib.idle_add(self._applied, message)

    def _applied(self, message):
        """La ventana sigue abierta para seguir ajustando."""
        self.applying = False
        self.apply_btn.set_sensitive(True)
        self.status.set_text(message or t("fondo", "aplicado"))


def main():
    if len(sys.argv) == 1:
        kill_existing(LOCK)
        path = wallpaper.current_path()
    elif len(sys.argv) == 2:
        path = sys.argv[1]
        # Otro archivo desde Thunar con el ajustador abierto: reemplaza al
        # anterior en vez de solo cerrarlo.
        try:
            with open(LOCK) as f:
                os.kill(int(f.read().strip()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
    else:
        path = None
    if not path or not os.path.isfile(path):
        sys.exit("uso: wallpaper_adjust.py [imagen|gif|video]")
    common.set_process_name("ff-wall-adjust")
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    try:
        WallpaperAdjust(path)
        Gtk.main()
    finally:
        try:
            os.remove(LOCK)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()

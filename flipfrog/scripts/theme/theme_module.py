#!/usr/bin/env python3 

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, GdkPixbuf, Gdk, GLib, Pango
import cairo
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "toggles"))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "menu"))
import common
import icon_packs
import look_settings
from waybar_lib import load_module_css, svg_icon_image, suppress_close, build_switch_row
import i18n
from i18n import t
from blur_toggle import build_blur_toggle

MODULE_CSS = os.path.join(SCRIPT_DIR, "theme_module.css")

ICONS_DIR = os.path.join(SCRIPT_DIR, "icons")
CLOCK_ICON = os.path.join(ICONS_DIR, "clock.svg")
CORNER_ICON = os.path.join(ICONS_DIR, "corner.svg")
DOWNLOAD_ICON = os.path.join(ICONS_DIR, "download.svg")
PALETTE_ICON = os.path.join(ICONS_DIR, "palette.svg")
FONT_ICON = os.path.join(ICONS_DIR, "font.svg")

THEMER_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "themer"))
THEMES_DIR = os.path.join(THEMER_DIR, "themes")
COLORS_CSS = os.path.join(THEMER_DIR, "colors.css")
GLOBAL_CSS = os.path.join(THEMER_DIR, "global.css")
APPLY_SCRIPT = os.path.join(THEMER_DIR, "apply-theme.sh")
FAVORITES_FILE = os.path.join(THEMER_DIR, "favorites.json")
ROTATION_CONFIG = os.path.join(THEMER_DIR, "rotation.json")
ROTATOR_SCRIPT = os.path.join(SCRIPT_DIR, "theme-rotator.py")
THEME_EDITOR_SCRIPT = os.path.join(SCRIPT_DIR, "theme-editor.py")
THEME_GALLERY_SCRIPT = os.path.join(SCRIPT_DIR, "theme_gallery.py")
CURSOR_PICKER_SCRIPT = os.path.join(SCRIPT_DIR, "cursor_picker.py")

GOOGLE_FONTS_SCRIPT = os.path.join(SCRIPT_DIR, "..", "fonts", "google-fonts.py")
RADIUS_SYNC_SCRIPT = os.path.join(THEMER_DIR, "sync_radius.py")
CORNER_RADIUS_FILE = os.path.join(THEMER_DIR, "corner-radius.json")
MIN_RADIUS, MAX_RADIUS, DEFAULT_RADIUS = 0, 30, 25
COMBO_WIDTH_CHARS = 12
FONT_SIZE_SYNC_SCRIPT = os.path.join(THEMER_DIR, "sync_font_size.py")
FONT_SIZE_FILE = os.path.join(THEMER_DIR, "font-size.json")
MIN_FONT_SIZE, MAX_FONT_SIZE, BASE_FONT_SIZE = 10, 20, 13

THUMB_CACHE_DIR = os.path.expanduser("~/.cache/waybar-theme-thumbs")
VIDEO_EXTS = {"mp4", "webm", "mkv", "mov", "m4v", "avi"}
THUMB_W, THUMB_H = 370, 180

FONT_FAMILY_RE = re.compile(r'font-family:\s*"([^"]+)"')
DEFAULT_FONT_FAMILY = "JetBrainsMono Nerd Font"


def current_font_family():
    try:
        with open(GLOBAL_CSS) as f:
            content = f.read()
    except FileNotFoundError:
        return DEFAULT_FONT_FAMILY
    m = FONT_FAMILY_RE.search(content)
    return m.group(1) if m else DEFAULT_FONT_FAMILY


def current_font_size():
    try:
        with open(FONT_SIZE_FILE) as f:
            return int(json.load(f).get("user_size", BASE_FONT_SIZE))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return BASE_FONT_SIZE


def apply_font_size(value):
    subprocess.Popen(["python3", FONT_SIZE_SYNC_SCRIPT, str(value)],
                      start_new_session=True,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def set_font_family(name):
    try:
        with open(GLOBAL_CSS) as f:
            content = f.read()
    except FileNotFoundError:
        return
    escaped = name.replace('"', '\\"')
    new_value = f'font-family: "{escaped}", "Symbols Nerd Font", sans-serif;'
    new_content, count = re.subn(r'font-family:\s*[^;]+;', new_value, content, count=1)
    if count == 0:
        return
    common.atomic_write(GLOBAL_CSS, new_content)


_live_font_provider = None


def _apply_font_live():
    global _live_font_provider
    screen = Gdk.Screen.get_default()
    if _live_font_provider is not None:
        Gtk.StyleContext.remove_provider_for_screen(screen, _live_font_provider)
    _live_font_provider = Gtk.CssProvider()
    _live_font_provider.load_from_path(GLOBAL_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, _live_font_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def _on_pick_font(font_btn):
    win = font_btn.get_toplevel()
    current = current_font_family()
    current_size = current_font_size()

    dialog = Gtk.Dialog(title=t("temas", "dialogo_fuente_titulo"), transient_for=win, modal=True)
    dialog.add_button(t("temas", "cancelar"), Gtk.ResponseType.CANCEL)
    dialog.add_button(t("temas", "aceptar"), Gtk.ResponseType.OK)
    dialog.set_default_size(480, 420)

    chooser = Gtk.FontChooserWidget()
    chooser.set_level(Gtk.FontChooserLevel.FAMILY)
    chooser.set_font(f"{current} {current_size}")
    dialog.get_content_area().pack_start(chooser, True, True, 0)

    original_prgname = GLib.get_prgname()
    GLib.set_prgname("theme-font-picker-dialog")
    dialog.show_all()
    GLib.set_prgname(original_prgname)

    win.hide()
    response = dialog.run()
    if response == Gtk.ResponseType.OK:
        family = chooser.get_font_family()
        size_units = chooser.get_font_size()
        size = size_units // Pango.SCALE if size_units > 0 else 0
        if family is not None:
            name = family.get_name()
            set_font_family(name)
            font_btn.set_tooltip_text(t("temas", "tooltip_fuente_actual", name=name))
            font_btn.set_label(name)
            _apply_font_live()
        if size and size > 0 and size != current_size:
            apply_font_size(size)
    dialog.destroy()
    win.show()


def list_themes():
    if not os.path.isdir(THEMES_DIR):
        return []
    return sorted(f for f in os.listdir(THEMES_DIR) if f.endswith(".theme"))


def load_favorites():
    try:
        with open(FAVORITES_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_favorites(favs):
    with open(FAVORITES_FILE, "w") as f:
        json.dump(sorted(favs), f)


def _strip_wallpaper_comment(content):
    lines = content.splitlines()
    if lines and lines[0].startswith("/* wallpaper:"):
        lines = lines[1:]
    return "\n".join(lines).strip()


WALLPAPER_LINE_RE = re.compile(r"^/\*\s*wallpaper:\s*(.+?)\s*\*/$")


def theme_wallpaper_path(fname):
    try:
        with open(os.path.join(THEMES_DIR, fname)) as f:
            first_line = f.readline().strip()
    except FileNotFoundError:
        return None
    m = WALLPAPER_LINE_RE.match(first_line)
    if not m:
        return None
    return os.path.expanduser(m.group(1))


def _cover_pixbuf(path, width, height):
    try:
        info = GdkPixbuf.Pixbuf.get_file_info(path)
        if not info or not info[1] or not info[2]:
            return None
        _, orig_w, orig_h = info
        scale = max(width / orig_w, height / orig_h)
        scaled_w = max(1, round(orig_w * scale))
        scaled_h = max(1, round(orig_h * scale))
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, scaled_w, scaled_h, False)
        x = min(max(0, (scaled_w - width) // 2), max(0, scaled_w - width))
        y = min(max(0, (scaled_h - height) // 2), max(0, scaled_h - height))
        return pixbuf.new_subpixbuf(x, y, min(width, scaled_w), min(height, scaled_h))
    except Exception:
        return None


def _round_corners(pixbuf, radius):
    width, height = pixbuf.get_width(), pixbuf.get_height()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    ctx = cairo.Context(surface)
    deg = math.pi / 180.0
    ctx.new_sub_path()
    ctx.arc(width - radius, radius, radius, -90 * deg, 0)
    ctx.arc(width - radius, height - radius, radius, 0, 90 * deg)
    ctx.arc(radius, height - radius, radius, 90 * deg, 180 * deg)
    ctx.arc(radius, radius, radius, 180 * deg, 270 * deg)
    ctx.close_path()
    ctx.clip()
    Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 0, 0)
    ctx.paint()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)


def _video_thumbnail_path(video_path, cache_key):
    os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(THUMB_CACHE_DIR, cache_key + ".png")
    if os.path.exists(cache_path):
        return cache_path
    try:
        subprocess.run(
            ["ffmpegthumbnailer", "-i", video_path, "-o", cache_path,
             "-s", str(max(THUMB_W, THUMB_H))],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return cache_path if os.path.exists(cache_path) else None


def active_theme_name():
    try:
        with open(COLORS_CSS) as f:
            current = _strip_wallpaper_comment(f.read())
    except FileNotFoundError:
        return None
    for fname in list_themes():
        with open(os.path.join(THEMES_DIR, fname)) as f:
            theme_content = _strip_wallpaper_comment(f.read())
        if theme_content == current:
            return fname
    return None


def apply_theme(fname):
    """apply-theme.sh desligado (sobrevive si se cierra el dashboard a
    mitad); la barra toma el tema sola al cambiar colors.css.
    suppress_close cubre TODO el proceso (incluye `hyprctl reload`; con
    video tarda más, espera a que muera mpvpaper) y se vuelve a armar al
    terminar."""
    suppress_close(60.0)
    theme_path = os.path.join(THEMES_DIR, fname)
    proc = subprocess.Popen(["bash", APPLY_SCRIPT, theme_path],
                            start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait():
        proc.wait()
        suppress_close(3.0)
    threading.Thread(target=wait, daemon=True).start()


def open_theme_editor():
    subprocess.Popen(["python3", THEME_EDITOR_SCRIPT],
                      start_new_session=True,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_google_fonts():
    subprocess.Popen(["python3", GOOGLE_FONTS_SCRIPT],
                      start_new_session=True,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def current_radius():
    try:
        with open(CORNER_RADIUS_FILE) as f:
            return int(json.load(f).get("radius", DEFAULT_RADIUS))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return DEFAULT_RADIUS


def apply_radius(value):
    subprocess.Popen(["python3", RADIUS_SYNC_SCRIPT, str(value)],
                      start_new_session=True,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_rotation_config():
    try:
        with open(ROTATION_CONFIG) as f:
            data = json.load(f)
        return bool(data.get("enabled", False)), float(data.get("interval_minutes", 60))
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        return False, 60


def save_rotation_config(enabled, minutes, reset_last_rotation=False):
    try:
        with open(ROTATION_CONFIG) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.update(enabled=enabled, interval_minutes=minutes)
    if reset_last_rotation:
        data["last_rotation"] = time.time()
    # Atómico -- theme-rotator.py (proceso aparte) también escribe este archivo.
    fd, tmp_path = tempfile.mkstemp(dir=THEMER_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp_path, ROTATION_CONFIG)
    except BaseException:
        os.remove(tmp_path)
        raise


def rotator_enabled():
    return load_rotation_config()[0]


def _run_rotator(flag):
    subprocess.Popen(["python3", ROTATOR_SCRIPT, flag],
                      start_new_session=True,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_rotator(minutes):
    save_rotation_config(True, minutes, reset_last_rotation=True)
    _run_rotator("--schedule")


def stop_rotator():
    _, minutes = load_rotation_config()
    save_rotation_config(False, minutes)
    _run_rotator("--stop")


def _number_field(value, unit, lo, hi, on_commit, tooltip, decimals=False):
    """Entry numérico + unidad + check (clase .number-group, ver
    theme_module.css). on_commit(valor) solo si cambió; el valor se
    acota a [lo, hi]."""
    group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    group.get_style_context().add_class("number-group")
    group.set_valign(Gtk.Align.CENTER)

    entry = Gtk.Entry()
    entry.set_width_chars(4)
    entry.set_max_length(5)
    entry.set_alignment(1.0)
    entry.set_valign(Gtk.Align.CENTER)
    entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
    entry.set_tooltip_text(tooltip)
    fmt = (lambda v: f"{v:g}") if decimals else (lambda v: str(int(v)))
    entry.set_text(fmt(value))
    state = {"value": value}

    def on_insert(e, text, _length, _pos):
        for ch in text:
            if ch.isdigit() or (decimals and ch == "." and "." not in e.get_text()):
                continue
            e.stop_emission_by_name("insert-text")
            return

    def commit(*_args):
        try:
            v = float(entry.get_text()) if decimals else int(entry.get_text())
        except ValueError:
            v = state["value"]
        v = max(lo, min(hi, v))
        entry.set_text(fmt(v))
        if v != state["value"]:
            state["value"] = v
            on_commit(v)
        return False

    entry.connect("insert-text", on_insert)
    entry.connect("activate", commit)
    entry.connect("focus-out-event", commit)
    group.pack_start(entry, False, False, 0)

    unit_label = Gtk.Label(label=unit)
    unit_label.get_style_context().add_class("radius-unit-label")
    unit_label.set_margin_start(6)
    unit_label.set_width_chars(2)
    unit_label.set_xalign(0)
    group.pack_start(unit_label, False, False, 0)

    apply_btn = Gtk.Button()
    apply_btn.set_image(Gtk.Image.new_from_icon_name("object-select-symbolic", Gtk.IconSize.BUTTON))
    apply_btn.set_always_show_image(True)
    apply_btn.set_tooltip_text(t("temas", "aplicar"))
    apply_btn.get_style_context().add_class("apply-btn")
    apply_btn.set_valign(Gtk.Align.CENTER)
    apply_btn.set_margin_start(8)
    apply_btn.connect("clicked", commit)
    group.pack_start(apply_btn, False, False, 0)
    return group


def _combo(tooltip):
    """Ancho natural acotado (COMBO_WIDTH_CHARS + elipsis): sin esto un
    ComboBoxText pide el ancho de su opción más larga y estira la fila."""
    combo = Gtk.ComboBoxText()
    for cell in combo.get_cells():
        cell.set_property("ellipsize", Pango.EllipsizeMode.END)
        cell.set_property("width-chars", COMBO_WIDTH_CHARS)
        cell.set_property("xalign", 1.0)
    combo.set_halign(Gtk.Align.END)
    combo.get_style_context().add_class("setting-combo")
    combo.set_tooltip_text(tooltip)
    combo.set_valign(Gtk.Align.CENTER)
    return combo


def _action_button(text, on_click, tooltip=None):
    btn = Gtk.Button(label=text)
    btn.get_style_context().add_class("setting-btn")
    btn.set_valign(Gtk.Align.CENTER)
    if tooltip:
        btn.set_tooltip_text(tooltip)
    btn.connect("clicked", on_click)
    return btn


def _row_icon(icon_path=None, icon_name=None):
    if icon_path:
        return svg_icon_image(icon_path)
    image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
    image.get_style_context().add_class("look-icon")
    return image


def _setting_row(icon, label_text, control):
    """Misma fila que build_switch_row (chip + etiqueta + control a la
    derecha) para controles que no son switch -- toda la pestaña se lee
    igual."""
    wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    wrapper.get_style_context().add_class("toggle-list-row")
    chip = Gtk.Box()
    chip.get_style_context().add_class("toggle-icon-chip")
    icon.set_halign(Gtk.Align.CENTER)
    icon.set_valign(Gtk.Align.CENTER)
    chip.pack_start(icon, True, False, 0)
    wrapper.pack_start(chip, False, False, 0)
    label = Gtk.Label(label=label_text)
    label.set_halign(Gtk.Align.START)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.get_style_context().add_class("toggle-row-label")
    wrapper.pack_start(label, True, True, 0)
    wrapper.pack_end(control, False, False, 0)
    return wrapper


def _switch_cell(icon, label_text, active, on_toggle):
    cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    build_switch_row(cell, icon, label_text, active, on_toggle)
    return cell


def _apply_look(**changes):
    """Guarda en hypr-look.json y recarga Hyprland -- en el worker de
    common.run_async, con suppress_close antes del reload (CLAUDE.md,
    checklist de toggles, punto 10). La densidad del blur va por
    `hyprctl eval`, sin reload."""
    def work():
        look_settings.save(**changes)
        if set(changes) == {"blur_size"}:
            look_settings.apply_blur_size(changes["blur_size"])
        else:
            suppress_close(2.5)
            look_settings.reload_hyprland()
    common.run_async(work)


def _build_icon_pack_combo():
    """Pack de íconos (antes en "Aplicaciones") -- ver menu/icon_packs.py.
    La barra rearma tray/íconos de ventanas sola al cambiar el tema de
    íconos (bar.py)."""
    combo = _combo(t("aplicaciones", "pack_iconos_tooltip"))
    options = [None] + icon_packs.discover_packs()
    # Repara symlinks de ~/.local/share/icons que apuntan a una ruta vieja
    # (quedaron así al mover themer/ a flipfrog/) -- barato, un readlink
    # por pack.
    for pack in options[1:]:
        try:
            icon_packs.ensure_installed(pack)
        except OSError:
            pass
    combo.append_text(t("aplicaciones", "pack_sistema"))
    for pack in options[1:]:
        combo.append_text(pack["name"])
    active = icon_packs.current_theme()
    combo.set_active(next((i for i, p in enumerate(options) if p and p["name"] == active), 0))

    def on_changed(c):
        index = c.get_active()
        if not 0 <= index < len(options):
            return
        pack = options[index]

        def work():
            name = icon_packs.apply_pack(pack)
            GLib.idle_add(Gtk.Settings.get_default().set_property, "gtk-icon-theme-name", name)
        common.run_async(work)

    combo.connect("changed", on_changed)
    return combo


def _build_cursor_controls(look):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    picker_btn = _action_button(look["cursor_theme"], lambda _b: subprocess.Popen(
        ["python3", CURSOR_PICKER_SCRIPT], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), t("temas", "cursor_elegir_tooltip"))
    label = picker_btn.get_child()
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_width_chars(min(len(look["cursor_theme"]), COMBO_WIDTH_CHARS))
    label.set_max_width_chars(COMBO_WIDTH_CHARS)

    def on_size(v):
        theme = look_settings.load()["cursor_theme"]
        common.run_async(lambda: look_settings.apply_cursor(theme, v))

    box.pack_start(picker_btn, False, False, 0)
    box.pack_start(_number_field(look["cursor_size"], t("temas", "px"), 16, 96, on_size,
                                 t("temas", "tamano_cursor_tooltip")), False, False, 0)
    return box


def _section_title(text):
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.set_name("theme-title-row")
    row.get_style_context().add_class("look-section")
    title = Gtk.Label(label=text)
    title.set_name("theme-title")
    divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    divider.set_name("theme-title-divider")
    divider.set_valign(Gtk.Align.CENTER)
    row.pack_start(title, False, False, 0)
    row.pack_start(divider, True, True, 0)
    return row


def _grid(cells):
    """2 columnas iguales; `cells` se llena por renglones (None deja la
    celda vacía)."""
    grid = Gtk.Grid()
    grid.set_column_homogeneous(True)
    grid.get_style_context().add_class("settings-grid")
    for i, cell in enumerate(cells):
        if cell is not None:
            cell.set_hexpand(True)
            grid.attach(cell, i % 2, i // 2, 1, 1)
    return grid


def build_theme_selector(container):
    load_module_css(MODULE_CSS)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.get_style_context().add_class("module")
    box.get_style_context().add_class("theme-module")
    look = look_settings.load()

    # ---- Interfaz ------------------------------------------------------------
    blur_cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    build_blur_toggle(blur_cell)

    radius_row = _setting_row(
        _row_icon(CORNER_ICON), t("temas", "redondeo_bordes"),
        _number_field(current_radius(), t("temas", "px"), MIN_RADIUS, MAX_RADIUS, apply_radius,
                      t("temas", "tooltip_radio_px", min=MIN_RADIUS, max=MAX_RADIUS)))

    current_font = current_font_family()
    font_btn = Gtk.Button(label=current_font)
    font_btn.get_style_context().add_class("setting-btn")
    font_btn.set_valign(Gtk.Align.CENTER)
    font_btn.set_tooltip_text(t("temas", "tooltip_fuente_actual", name=current_font))
    font_btn.connect("clicked", lambda _b: _on_pick_font(font_btn))
    font_row = _setting_row(_row_icon(FONT_ICON), t("temas", "fuente"), font_btn)

    get_fonts_row = _setting_row(
        _row_icon(DOWNLOAD_ICON), t("temas", "obtener_fuentes"),
        _action_button(t("temas", "abrir"), lambda _b: open_google_fonts(), t("temas", "obtener_mas_fuentes")))

    language_combo = _combo(None)
    for code, nombre in i18n.list_languages():
        language_combo.append(code, nombre)
    language_combo.set_active_id(i18n.current_lang())

    def on_language_changed(combo):
        code = combo.get_active_id()
        if code and code != i18n.current_lang():
            i18n.set_lang(code)

    language_combo.connect("changed", on_language_changed)
    language_row = _setting_row(_row_icon(icon_name="preferences-desktop-locale-symbolic"),
                                t("temas", "idioma_fila"), language_combo)

    icon_pack_row = _setting_row(_row_icon(icon_name="applications-graphics-symbolic"),
                                 t("aplicaciones", "pack_iconos"), _build_icon_pack_combo())
    cursor_row = _setting_row(_row_icon(icon_name="input-mouse-symbolic"), t("temas", "cursor"),
                              _build_cursor_controls(look))

    get_cursors_row = _setting_row(
        _row_icon(DOWNLOAD_ICON), t("temas", "obtener_cursores"),
        _action_button(t("temas", "abrir"), lambda _b: subprocess.Popen(
            ["python3", CURSOR_PICKER_SCRIPT, "--get"], start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), t("temas", "obtener_cursores_tooltip")))

    gallery_row = _setting_row(
        _row_icon(PALETTE_ICON), t("temas", "galeria_btn"),
        _action_button(t("temas", "abrir"), lambda _b: subprocess.Popen(
            ["python3", THEME_GALLERY_SCRIPT], start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), t("temas", "galeria_btn_tooltip")))

    # ---- Ventanas (Hyprland, hypr/config/look.lua) ----
    anim_cell = _switch_cell(_row_icon(icon_name="media-playback-start-symbolic"), t("temas", "animaciones"),
                             look["animations"], lambda state: _apply_look(animations=state))
    blur_size_row = _setting_row(
        _row_icon(icon_name="blur-symbolic"), t("temas", "densidad_blur"),
        _number_field(look["blur_size"], t("temas", "px"), 1, 20,
                      lambda v: _apply_look(blur_size=v), t("temas", "densidad_blur_tooltip")))
    opacity_row = _setting_row(
        _row_icon(icon_name="weather-fog-symbolic"), t("temas", "opacidad"),
        _number_field(round(look["opacity"] * 100), "%", 10, 100,
                      lambda v: _apply_look(opacity=round(v / 100, 2)), t("temas", "opacidad_tooltip")))
    gap_in_row = _setting_row(
        _row_icon(icon_name="view-grid-symbolic"), t("temas", "gap_interno"),
        _number_field(look["gaps_in"], t("temas", "px"), 0, 60,
                      lambda v: _apply_look(gaps_in=v), t("temas", "gaps_tooltip")))
    gap_out_row = _setting_row(
        _row_icon(icon_name="view-fullscreen-symbolic"), t("temas", "gap_externo"),
        _number_field(look["gaps_out"], t("temas", "px"), 0, 100,
                      lambda v: _apply_look(gaps_out=v), t("temas", "gaps_tooltip")))
    border_row = _setting_row(
        _row_icon(icon_name="checkbox-symbolic"), t("temas", "borde"),
        _number_field(look["border_size"], t("temas", "px"), 0, 10,
                      lambda v: _apply_look(border_size=v), t("temas", "borde_tooltip")))

    box.pack_start(_section_title(t("temas", "titulo_interfaz")), False, False, 0)
    box.pack_start(_grid([
        font_row, get_fonts_row,
        cursor_row, get_cursors_row,
        language_row, icon_pack_row,
        gallery_row, None,
    ]), False, False, 0)
    box.pack_start(_section_title(t("temas", "titulo_ventanas")), False, False, 0)
    box.pack_start(_grid([
        anim_cell, blur_cell,
        opacity_row, blur_size_row,
        gap_in_row, gap_out_row,
        border_row, radius_row,
    ]), False, False, 0)

    container.pack_start(box, True, True, 0)

#!/usr/bin/env python3
"""
theme-editor.py
Editor visual de themer/colors.css para Waybar (reemplaza al servidor
Node + UI en navegador de themer/server.js).

Lee y reescribe únicamente las 8 líneas @define-color de PROPERTIES,
preservando el resto de colors.css intacto. Todas las propiedades se
guardan como rgba(...) (incluidas las que estaban en rgb sin alpha), con
un slider de alpha propio por fila. Al guardar, corre themer/reload.sh.
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import colorsys
import hashlib
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top, _icon_text_button
# theme_module.py vive en este mismo directorio (theme/), no hace falta
# sys.path.insert aparte -- se reusa su THEMES_DIR/active_theme_name para
# no duplicar la lógica de "qué tema está aplicado ahora mismo", y
# VIDEO_EXTS/_video_thumbnail_path para no duplicar la llamada a
# ffmpegthumbnailer en la detección automática (ver detect_theme_from_wallpaper).
from theme_module import (
    THEMES_DIR, active_theme_name, VIDEO_EXTS, _video_thumbnail_path,
    ICONS_DIR, THUMB_CACHE_DIR,
)
from i18n import t

DETECT_ICON = os.path.join(ICONS_DIR, "detect.svg")
OVERWRITE_ICON = os.path.join(ICONS_DIR, "overwrite.svg")
SAVE_THEME_ICON = os.path.join(ICONS_DIR, "save-theme.svg")

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
THEMER_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "themer"))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
RELOAD_SCRIPT = os.path.join(THEMER_DIR, "reload.sh")
SAVE_THEME_SCRIPT = os.path.join(THEMER_DIR, "save-theme.sh")
sys.path.insert(0, THEMER_DIR)
import wallpaper as wallpaper_state
CSS_FILE = os.path.join(SCRIPT_DIR, "theme-editor.css")
LOCK = "/tmp/theme-editor.pid"

# Mismas 7 que traía server.js originalmente + myforegroundhover2 (agregada
# para el degradado del reloj, ver clock/clock.py).
PROPERTIES = [
    "mybackground",
    "mybackgroundhover",
    "myborders",
    "myborders2",
    "myborderinactive",
    "myforeground",
    "myforegroundhover",
    "myforegroundhover2",
]

LABELS = {
    "mybackground": t("temas", "swatch_mybackground"),
    "mybackgroundhover": t("temas", "swatch_mybackgroundhover"),
    "myborders": t("temas", "swatch_myborders"),
    "myborders2": t("temas", "swatch_myborders2"),
    "myborderinactive": t("temas", "swatch_myborderinactive"),
    "myforeground": t("temas", "swatch_myforeground"),
    "myforegroundhover": t("temas", "swatch_myforegroundhover"),
    "myforegroundhover2": t("temas", "swatch_myforegroundhover2"),
}

# 3 columnas lado a lado en vez de las 8 filas apiladas en una sola columna
# (se veía muy angosto y alto) -- pedido explícito del usuario: secciones
# por categoría, cada una en su propia columna vertical (no una fila
# horizontal de swatches chicos).
SECTIONS = [
    (t("temas", "seccion_fondos"), ["mybackground", "mybackgroundhover"]),
    (t("temas", "seccion_bordes"), ["myborders", "myborders2", "myborderinactive"]),
    (t("temas", "seccion_textos"), ["myforeground", "myforegroundhover", "myforegroundhover2"]),
]

DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(rgba?)\(([^)]+)\)\s*;?")


def parse_colors(content):
    """Lee tanto rgb(...) como rgba(...) (colors.css puede traer una mezcla
    de propiedades viejas en rgb sin canal alpha), pero todo lo que entra a
    `colors` se trata como rgba a partir de aquí -- ver to_css_value. Las
    que eran rgb sin alpha quedan con a=1.0 (opacas, visualmente
    idénticas), listas para ganar su propio slider de alpha en la UI."""
    colors = {}
    for line in content.splitlines():
        m = DEFINE_COLOR_RE.search(line)
        if not m:
            continue
        name, kind, raw_values = m.groups()
        if name not in PROPERTIES:
            continue
        parts = [float(p.strip()) for p in raw_values.split(",")]
        r, g, b = parts[0], parts[1], parts[2]
        a = parts[3] if kind == "rgba" and len(parts) > 3 else 1.0
        colors[name] = {"r": r, "g": g, "b": b, "a": a}
    return colors


def clamp_int(n):
    return max(0, min(255, round(n)))


def clamp_alpha(n):
    return max(0.0, min(1.0, n))


def to_css_value(value):
    """Todas las propiedades se guardan como rgba(...), incluidas las que
    en colors.css estaban como rgb(...) sin canal alpha -- prueba pedida
    por el usuario: si todo lo que importa colors.css (waybar, dunst,
    hyprland/hyprlua) sigue andando bien con rgba() en
    todas partes, se queda así definitivo."""
    ri, gi, bi = clamp_int(value["r"]), clamp_int(value["g"]), clamp_int(value["b"])
    return f"rgba({ri}, {gi}, {bi}, {clamp_alpha(value['a']):.2f})"


def build_css_content(lines, colors):
    """Aplica `colors` sobre `lines` (contenido original de colors.css,
    cacheado al abrir el editor -- ver nota en write_colors sobre por qué
    no se relee el archivo del disco aquí) y devuelve el resultado como
    texto, sin escribir nada (se reusa tanto para guardar el .css global
    como para armar el snapshot que recibe save-theme.sh)."""

    def update_line(line):
        m = DEFINE_COLOR_RE.search(line)
        if not m:
            return line
        name = m.group(1)
        if name not in colors:
            return line
        return f"@define-color {name} {to_css_value(colors[name])};"

    return "\n".join(update_line(line) for line in lines) + "\n"


def write_colors(lines, colors):
    """Escribe colors.css de forma atómica (archivo temporal + os.replace).
    open(CSS_PATH, "w") directo trunca el archivo a 0 bytes antes de
    escribir el contenido nuevo; si otro proceso (p.ej. otra instancia del
    editor, o reload.sh/sync_dunst.py leyendo en paralelo) lee justo en esa
    ventana, ve un archivo vacío. os.replace es atómico en el mismo
    filesystem: cualquier lector ve el archivo viejo completo o el nuevo
    completo, nunca un estado a medio escribir."""
    content = build_css_content(lines, colors)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(CSS_PATH), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, CSS_PATH)
    except BaseException:
        os.remove(tmp_path)
        raise


def run_reload():
    r = subprocess.run(["bash", RELOAD_SCRIPT], capture_output=True, text=True)
    return r.returncode == 0, r.stderr


def save_as_theme(lines, colors, name):
    """Guarda el estado actual (editado o no) como themer/themes/<name>.theme,
    delegando en save-theme.sh: detecta el wallpaper activo y evita
    sobreescribir un tema existente con el mismo nombre."""
    content = build_css_content(lines, colors)
    with tempfile.NamedTemporaryFile("w", suffix=".css", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        r = subprocess.run(["bash", SAVE_THEME_SCRIPT, name, tmp_path],
                            capture_output=True, text=True)
        return r.returncode == 0, r.stderr
    finally:
        os.remove(tmp_path)


def overwrite_theme(name, colors):
    """Sobrescribe themer/themes/<name> (el tema detectado como aplicado al
    abrir el editor, ver active_theme_name en theme_module.py) con `colors`
    aplicados sobre el contenido PROPIO de ese archivo, no sobre
    self.original_lines de colors.css -- colors.css nunca tiene el
    comentario `/* wallpaper: ... */` (apply-theme.sh lo descarta al copiar,
    ver CLAUDE.md), así que partir de las líneas del .theme preserva esa
    línea intacta (no matchea DEFINE_COLOR_RE, build_css_content la deja
    tal cual) en vez de perderla. Mismo patrón atómico que write_colors."""
    theme_path = os.path.join(THEMES_DIR, name)
    with open(theme_path) as f:
        theme_lines = f.read().splitlines()

    # El wallpaper también pasa a ser el activo -- mismo formato que
    # save-theme.sh (~ sin expandir). Sin esto un tema guardado sin esa
    # línea nunca la recupera.
    wallpaper = wallpaper_state.current_path()
    if wallpaper:
        home = os.path.expanduser("~")
        if wallpaper.startswith(home + "/"):
            wallpaper = "~" + wallpaper[len(home):]
        if theme_lines and theme_lines[0].startswith("/* wallpaper:"):
            theme_lines = theme_lines[1:]
        theme_lines.insert(0, f"/* wallpaper: {wallpaper} */")

    content = build_css_content(theme_lines, colors)
    fd, tmp_path = tempfile.mkstemp(dir=THEMES_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, theme_path)
    except BaseException:
        os.remove(tmp_path)
        raise

    # Miniatura de video cacheada por nombre de tema -- regenerar.
    try:
        os.remove(os.path.join(THUMB_CACHE_DIR, name[: -len(".theme")] + ".png"))
    except FileNotFoundError:
        pass


def _wallpaper_still_image(wallpaper_path):
    """Imagen fija que PIL pueda abrir: el wallpaper mismo si ya es una
    imagen, o un frame extraído con ffmpegthumbnailer si es video (mismo
    mecanismo que las miniaturas de tema en theme_module, reusado aquí con
    una cache_key propia -- no hay nombre de tema todavía en este punto)."""
    ext = os.path.splitext(wallpaper_path)[1].lstrip(".").lower()
    if ext not in VIDEO_EXTS:
        return wallpaper_path
    cache_key = "_autodetect_" + hashlib.sha1(wallpaper_path.encode()).hexdigest()[:16]
    return _video_thumbnail_path(wallpaper_path, cache_key)


def _extract_palette(image_path, n_colors=12):
    """Paleta de colores dominantes vía Image.quantize (MEDIANCUT) sobre una
    copia reducida -- da colores dominantes de verdad, a diferencia de un
    histograma de píxeles crudo que se ensucia con antialiasing/gradientes.
    Cada entrada trae su HSV, que es lo que usan _pick_accents/_pick_shadow
    para filtrar por saturación/brillo."""
    img = Image.open(image_path).convert("RGB")
    img.thumbnail((150, 150))
    quant = img.quantize(colors=n_colors, method=Image.MEDIANCUT)
    palette = quant.getpalette()
    counts = quant.getcolors(maxcolors=n_colors) or []
    total = sum(count for count, _ in counts) or 1
    entries = []
    for count, idx in counts:
        r, g, b = palette[idx * 3], palette[idx * 3 + 1], palette[idx * 3 + 2]
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        entries.append({"h": h, "s": s, "v": v, "freq": count / total})
    return entries


def _hue_distance(h1, h2):
    d = abs(h1 - h2)
    return min(d, 1.0 - d)


def _pick_accents(entries):
    """2 colores de acento: los más saturados/prominentes de la imagen,
    evitando elegir dos tonos casi idénticos entre sí (umbral de distancia
    de matiz). El umbral de saturación mínima se relaja en pasos si la
    imagen no tiene suficientes candidatos vívidos (wallpapers muy
    desaturados) -- siempre termina devolviendo 2 colores, en el peor caso
    los 2 más frecuentes sin más criterio."""
    candidates = []
    for min_s in (0.35, 0.2, 0.1, 0.0):
        candidates = sorted(
            (e for e in entries if e["s"] >= min_s and 0.2 <= e["v"] <= 0.95),
            key=lambda e: e["freq"] * e["s"],
            reverse=True,
        )
        if len(candidates) >= 2:
            break
    if not candidates:
        candidates = sorted(entries, key=lambda e: e["freq"], reverse=True)
    accent1 = candidates[0]
    accent2 = next(
        (e for e in candidates[1:] if _hue_distance(e["h"], accent1["h"]) > 0.08),
        candidates[1] if len(candidates) > 1 else accent1,
    )
    return accent1, accent2


def _pick_shadow(entries):
    """Tono oscuro dominante de la imagen (sombras/zonas oscuras), base de
    mybackgroundhover -- mybackground en sí queda fijo (ver
    detect_theme_from_wallpaper): las 8 paletas reales en themer/themes/
    coinciden en usarlo casi negro sin importar el wallpaper, así que
    derivarlo de la imagen solo arriesgaría contraste ilegible sin ganar
    nada."""
    candidates = sorted((e for e in entries if e["v"] < 0.6), key=lambda e: e["freq"], reverse=True)
    return candidates[0] if candidates else min(entries, key=lambda e: e["v"])


def _hsv_rgba(h, s, v, a=1.0):
    r, g, b = colorsys.hsv_to_rgb(h, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return {"r": r * 255, "g": g * 255, "b": b * 255, "a": a}


def detect_theme_from_wallpaper():
    """Genera las 8 propiedades a partir del wallpaper activo. Devuelve
    (colors_dict, None) en éxito o (None, mensaje_de_error) -- nunca
    excepciona, para que el botón de detección solo tenga que mostrar el
    mensaje en self.status."""
    wallpaper = wallpaper_state.current_path()
    if not wallpaper or not os.path.isfile(wallpaper):
        return None, t("temas", "error_sin_wallpaper")
    image_path = _wallpaper_still_image(wallpaper)
    if not image_path or not os.path.isfile(image_path):
        return None, t("temas", "error_sin_frame")
    try:
        entries = _extract_palette(image_path)
    except Exception as e:
        return None, t("temas", "error_analisis_imagen", e=e)
    if not entries:
        return None, t("temas", "error_sin_colores")

    accent1, accent2 = _pick_accents(entries)
    shadow = _pick_shadow(entries)

    # Acento 1 → borde (algo más apagado) + texto hover (más brillante),
    # acento 2 → mismo criterio en su par de propiedades -- mismo patrón
    # que los temas guardados a mano (myforegroundhover suele ser una
    # versión más clara del tono de myborders, no un color aparte).
    border1 = _hsv_rgba(accent1["h"], min(1.0, accent1["s"] * 1.1), max(0.5, min(accent1["v"], 0.8)))
    fg1 = _hsv_rgba(accent1["h"], min(1.0, accent1["s"] * 0.9), max(0.75, accent1["v"]))
    border2 = _hsv_rgba(accent2["h"], min(1.0, accent2["s"] * 1.1), max(0.5, min(accent2["v"], 0.8)))
    fg2 = _hsv_rgba(accent2["h"], min(1.0, accent2["s"] * 0.9), max(0.75, accent2["v"]))
    bg_hover = _hsv_rgba(shadow["h"], min(0.6, shadow["s"]), 0.16)
    # Mismo RGB que myborders a alpha 0.27 -- patrón que siguen 6 de los 7
    # temas guardados hoy en themer/themes/.
    border_inactive = dict(border1, a=0.27)

    return {
        "mybackground": {"r": 0, "g": 0, "b": 0, "a": 0.90},
        "mybackgroundhover": bg_hover,
        "myborders": border1,
        "myborders2": border2,
        "myborderinactive": border_inactive,
        "myforeground": {"r": 255, "g": 255, "b": 255, "a": 1.0},
        "myforegroundhover": fg1,
        "myforegroundhover2": fg2,
    }, None


class ThemeEditorPopup:
    def __init__(self):
        self.window, container = build_layer_window("theme-editor", CSS_FILE)
        container.set_name("container")
        position_fixed_top(container, margin=60)

        header = Gtk.Label(label=t("temas", "editor_tema_header"))
        header.set_name("header")
        header.set_halign(Gtk.Align.CENTER)
        container.pack_start(header, False, False, 0)

        # Arriba de todo, antes de las filas: pensado como el primer paso
        # opcional del flujo ("prueba la detección, después ajusta a mano
        # lo que no te convenza"), no como una acción final tipo Guardar.
        detect_btn = _icon_text_button(
            DETECT_ICON, t("temas", "detectar_paleta"),
            t("temas", "tooltip_detectar_paleta"))
        detect_btn.get_style_context().add_class("detect-btn")
        detect_btn.connect("clicked", self._on_detect)
        container.pack_start(detect_btn, False, False, 0)

        with open(CSS_PATH, "r") as f:
            original_content = f.read()
        # Cacheado al abrir: guardar y "guardar como tema" trabajan sobre
        # esto, no releen el disco (ver nota en write_colors).
        self.original_lines = original_content.splitlines()
        self.colors = parse_colors(original_content)
        # Detectado UNA sola vez, al abrir (contra el colors.css que se
        # acaba de leer arriba) -- "el tema actual" que puede sobrescribirse
        # es el que estaba aplicado al entrar al editor, no se recalcula en
        # cada edición en vivo.
        self.active_theme = active_theme_name()

        self.alpha_value_labels = {}
        self.swatches = {}
        self.alpha_scales = {}

        columns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        columns_row.set_name("columns-row")
        for section_label, names in SECTIONS:
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            column.get_style_context().add_class("section-column")

            title = Gtk.Label(label=section_label)
            title.get_style_context().add_class("section-title")
            title.set_halign(Gtk.Align.START)
            column.pack_start(title, False, False, 0)

            for name in names:
                value = self.colors.get(name)
                if value is None:
                    continue
                column.pack_start(self._build_row(name, value), False, False, 0)

            columns_row.pack_start(column, True, True, 0)
        container.pack_start(columns_row, False, False, 0)

        self.status = Gtk.Label(label="")
        self.status.set_name("status")
        self.status.set_halign(Gtk.Align.CENTER)
        if not self.colors:
            self.status.set_text(t("temas", "colors_css_invalido"))
        container.pack_start(self.status, False, False, 0)

        save_btn = Gtk.Button(label=t("temas", "guardar_aplicar"))
        save_btn.get_style_context().add_class("save-btn")
        save_btn.connect("clicked", self._on_save)
        container.pack_start(save_btn, False, False, 0)

        # Solo aparece si al abrir el editor colors.css coincidía con algún
        # .theme guardado (ver active_theme_name) -- si se editó "desde
        # cero" o el tema activo se borró/movió del disco entre medio, no
        # hay nada que sobrescribir y el botón ni se agrega.
        if self.active_theme:
            active_name = self.active_theme[: -len(".theme")]
            overwrite_btn = _icon_text_button(
                OVERWRITE_ICON, t("temas", "sobrescribir_tema_actual", name=active_name),
                t("temas", "tooltip_sobrescribir_tema"))
            overwrite_btn.get_style_context().add_class("overwrite-btn")
            overwrite_btn.connect("clicked", self._on_overwrite_theme)
            container.pack_start(overwrite_btn, False, False, 0)

        theme_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        theme_row.set_name("theme-row")

        self.theme_name_entry = Gtk.Entry()
        self.theme_name_entry.set_placeholder_text(t("temas", "placeholder_nombre_tema"))
        self.theme_name_entry.set_hexpand(True)
        self.theme_name_entry.connect("activate", self._on_save_as_theme)
        theme_row.pack_start(self.theme_name_entry, True, True, 0)

        theme_btn = _icon_text_button(
            SAVE_THEME_ICON, t("temas", "guardar_tema"), t("temas", "tooltip_guardar_tema"))
        theme_btn.get_style_context().add_class("theme-btn")
        theme_btn.connect("clicked", self._on_save_as_theme)
        theme_row.pack_start(theme_btn, False, False, 0)

        container.pack_start(theme_row, False, False, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

    def _build_row(self, name, value):
        """Fila: nombre de la propiedad arriba (sin el valor rgba(...)
        crudo -- se sacó a pedido del usuario, no aportaba nada que el
        swatch ya no mostrara visualmente) y, debajo, swatch + slider de
        alpha en la MISMA fila. Ese reparto es un Gtk.Grid de 5 columnas
        homogéneas (swatch ocupa 1 columna = 20%, `alpha_row` ocupa las
        otras 4 = 80%) en vez de un Gtk.Box: Box solo soporta
        expand=True/False parejo entre hijos, no una proporción fija como
        20/80 -- Grid con columnas homogéneas + column-span sí."""
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        wrapper.get_style_context().add_class("color-row")

        label = Gtk.Label(label=LABELS.get(name, name))
        label.get_style_context().add_class("row-name")
        label.set_halign(Gtk.Align.START)
        wrapper.pack_start(label, False, False, 0)

        swatch = Gtk.Button()
        swatch.get_style_context().add_class("color-swatch")
        self._paint_swatch(swatch, value)
        swatch.connect("clicked", self._on_pick_color, name, swatch)
        self.swatches[name] = swatch

        alpha_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        alpha_row.get_style_context().add_class("alpha-row")

        alpha_label = Gtk.Label(label=t("temas", "alpha_label"))
        alpha_label.get_style_context().add_class("alpha-label")
        alpha_row.pack_start(alpha_label, False, False, 0)

        alpha_adj = Gtk.Adjustment(value=value["a"], lower=0.0, upper=1.0,
                                    step_increment=0.05, page_increment=0.05,
                                    page_size=0)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=alpha_adj)
        scale.set_digits(2)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.connect("value-changed", self._on_alpha_changed, name, swatch)
        alpha_row.pack_start(scale, True, True, 0)
        self.alpha_scales[name] = scale

        alpha_value = Gtk.Label(label=f"{value['a']:.2f}")
        alpha_value.get_style_context().add_class("alpha-value")
        alpha_row.pack_start(alpha_value, False, False, 0)
        self.alpha_value_labels[name] = alpha_value

        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_column_spacing(8)
        grid.attach(swatch, 0, 0, 1, 1)
        grid.attach(alpha_row, 1, 0, 4, 1)
        wrapper.pack_start(grid, False, False, 0)

        return wrapper

    def _apply_color(self, name, value):
        """Pisa self.colors[name] y refleja el cambio en swatch/slider/label
        de esa fila -- usado por _on_detect para volcar el resultado de
        detect_theme_from_wallpaper() en la UI ya existente, sin un camino
        de pintado aparte del que ya usan los pickers manuales."""
        self.colors[name] = value
        swatch = self.swatches.get(name)
        if swatch is not None:
            self._paint_swatch(swatch, value)
        scale = self.alpha_scales.get(name)
        if scale is not None:
            scale.set_value(value["a"])
        label = self.alpha_value_labels.get(name)
        if label is not None:
            label.set_text(f"{value['a']:.2f}")

    def _on_detect(self, _btn):
        self.status.set_text(t("temas", "status_detectando"))
        detected, err = detect_theme_from_wallpaper()
        if err:
            self.status.set_text(t("temas", "status_deteccion_error", err=err))
            return
        for name, value in detected.items():
            if name not in self.colors:
                continue
            self._apply_color(name, value)
        self.status.set_text(t("temas", "status_colores_detectados"))

    def _on_alpha_changed(self, scale, name, swatch):
        value = self.colors[name]
        value["a"] = scale.get_value()
        self.alpha_value_labels[name].set_text(f"{value['a']:.2f}")
        self._paint_swatch(swatch, value)

    def _paint_swatch(self, swatch, value):
        """Pinta el botón-swatch con el color actual vía CSS inline (no hay
        Gtk.ColorButton aquí, ver _on_pick_color)."""
        provider = Gtk.CssProvider()
        provider.load_from_data(
            f"button {{ background-color: {to_css_value(value)}; }}".encode()
        )
        ctx = swatch.get_style_context()
        old = getattr(swatch, "_color_provider", None)
        if old is not None:
            ctx.remove_provider(old)
        ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        swatch._color_provider = provider

    def _on_pick_color(self, _btn, name, swatch):
        """Solo RGB -- el alpha se maneja aparte con el slider de
        `_build_row` (mismo split que el `input[type=color]` + slider de
        themer/public/index.html original). use_alpha=False saca el
        control de alpha de aquí, que quedaría redundante con el slider de
        la fila. show_editor=False (default del widget, antes forzado a
        True aquí) es lo que muestra la vista de paleta -- grilla de tonos
        claro→oscuro por columna de matiz + fila de "usados recientemente"
        + botón de "+" para personalizado -- en vez de saltar directo al
        editor de sliders H/S/V; ese editor sigue disponible clickeando el
        "+" de la paleta, solo que ya no es la vista inicial."""
        value = self.colors[name]
        rgba = Gdk.RGBA(value["r"] / 255, value["g"] / 255, value["b"] / 255, 1.0)

        dialog = Gtk.Dialog(title=LABELS.get(name, name),
                             transient_for=self.window, modal=True)
        dialog.add_button(t("temas", "cancelar"), Gtk.ResponseType.CANCEL)
        dialog.add_button(t("temas", "aceptar"), Gtk.ResponseType.OK)
        chooser = Gtk.ColorChooserWidget(rgba=rgba, use_alpha=False)
        dialog.get_content_area().pack_start(chooser, True, True, 0)

        # Ver CLAUDE.md "Editor de temas" -- diálogos nativos sobre una
        # ventana layer-shell Layer.TOP.
        original_prgname = GLib.get_prgname()
        GLib.set_prgname("theme-editor-color-dialog")
        dialog.show_all()
        GLib.set_prgname(original_prgname)

        self.window.hide()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            new_rgba = chooser.get_rgba()
            value["r"] = new_rgba.red * 255
            value["g"] = new_rgba.green * 255
            value["b"] = new_rgba.blue * 255
            self._paint_swatch(swatch, value)
        dialog.destroy()
        self.window.show()

    def _on_save(self, _btn):
        if not self.colors:
            self.status.set_text(t("temas", "status_nada_que_guardar"))
            return
        self.status.set_text(t("temas", "status_guardando_recargando"))
        write_colors(self.original_lines, self.colors)
        ok, err = run_reload()
        if ok:
            self.status.set_text(t("temas", "status_guardado_recargado"))
        else:
            self.status.set_text(t("temas", "status_guardado_reload_fallo"))
            print(err)

    def _on_overwrite_theme(self, _btn):
        if not self.colors or not self.active_theme:
            return
        active_name = self.active_theme[: -len(".theme")]
        self.status.set_text(t("temas", "status_sobrescribiendo", name=active_name))
        try:
            overwrite_theme(self.active_theme, self.colors)
        except OSError as e:
            self.status.set_text(t("temas", "status_sobrescribir_error"))
            print(e)
            return
        write_colors(self.original_lines, self.colors)
        ok, err = run_reload()
        if ok:
            self.status.set_text(t("temas", "status_sobrescrito_ok", name=active_name))
        else:
            self.status.set_text(t("temas", "status_sobrescrito_reload_fallo"))
            print(err)

    def _on_save_as_theme(self, _widget):
        name = self.theme_name_entry.get_text().strip()
        if not name or "/" in name:
            self.status.set_text(t("temas", "status_nombre_invalido"))
            return
        self.status.set_text(t("temas", "status_guardando_tema"))
        ok, err = save_as_theme(self.original_lines, self.colors, name)
        if ok:
            self.status.set_text(t("temas", "status_tema_guardado_ok", name=name))
            self.theme_name_entry.set_text("")
        else:
            self.status.set_text(t("temas", "status_tema_guardado_error"))
            print(err)

    def _on_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    ThemeEditorPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

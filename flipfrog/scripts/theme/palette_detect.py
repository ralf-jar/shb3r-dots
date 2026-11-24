#!/usr/bin/env python3
"""Paleta del tema (8 propiedades de colors.css) a partir de una imagen,
sin GTK. La usan el editor de temas (botón "Detectar paleta") y el
ajustador de fondo (scripts/wallpaper/wallpaper_adjust.py)."""

import colorsys
import os
import re
import subprocess
import tempfile

from PIL import Image

from i18n import t

THEMER_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "themer"))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
RELOAD_SCRIPT = os.path.join(THEMER_DIR, "reload.sh")
DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(rgba?)\(([^)]+)\)\s*;?")


def _extract_palette(image_path, n_colors=12, box=None):
    """Paleta de colores dominantes vía Image.quantize (MEDIANCUT) sobre una
    copia reducida -- da colores dominantes de verdad, a diferencia de un
    histograma de píxeles crudo que se ensucia con antialiasing/gradientes.
    Cada entrada trae su HSV, que es lo que usan _pick_accents/_pick_shadow
    para filtrar por saturación/brillo."""
    img = Image.open(image_path).convert("RGB")
    if box:
        x, y, w, h = box
        img = img.crop((round(x * img.width), round(y * img.height),
                        round((x + w) * img.width), round((y + h) * img.height)))
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


def colors_from_image(image_path, box=None):
    """(colors_dict, None) o (None, mensaje). `box` = (x, y, w, h) en
    fracciones: analiza solo esa parte (lo que se ve en el monitor)."""
    try:
        entries = _extract_palette(image_path, box=box)
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




def to_css_value(value):
    ri, gi, bi = (max(0, min(255, round(value[k]))) for k in ("r", "g", "b"))
    return f"rgba({ri}, {gi}, {bi}, {max(0.0, min(1.0, value['a'])):.2f})"


def apply_colors(colors):
    """Escribe `colors` en colors.css (atómico) y corre reload.sh."""
    with open(CSS_PATH) as f:
        lines = f.read().splitlines()

    def update(line):
        m = DEFINE_COLOR_RE.search(line)
        if m and m.group(1) in colors:
            return f"@define-color {m.group(1)} {to_css_value(colors[m.group(1)])};"
        return line

    fd, tmp = tempfile.mkstemp(dir=THEMER_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(update(line) for line in lines) + "\n")
        os.replace(tmp, CSS_PATH)
    except BaseException:
        os.remove(tmp)
        raise
    return subprocess.run(["bash", RELOAD_SCRIPT], capture_output=True).returncode == 0

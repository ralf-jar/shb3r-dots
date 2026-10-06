#!/usr/bin/env python3
"""Copia colores de colors.css a ~/.config/kdeglobals -- complementa a
sync_gtk3.py para apps KDE/Qt como Dolphin. El grueso del reskin viene
de QT_QPA_PLATFORMTHEME=gtk3 (~/.config/uwsm/env, plugin libqgtk3.so
toma el tema GTK3 tal cual); este script solo cubre los colores que KDE
Frameworks (KIO/Kirigami) lee directo de kdeglobals aparte de la paleta
Qt (confirmado: el fondo del panel de archivos de Dolphin sale de acá
incluso con el QPA theme roto). Sin alpha (kdeglobals no soporta canal
alfa). [WM] (bordes de ventana estilo KWin) se deja sin tocar -- acá los
dibuja Hyprland."""
import os
import re
import sys

sys.path.insert(0, os.path.expanduser("~/.config/flipfrog/scripts"))
import common

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
KDEGLOBALS_PATH = os.path.expanduser("~/.config/kdeglobals")

DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(rgba?)\(([^)]+)\)")


def parse_colors(css_content):
    colors = {}
    for match in DEFINE_COLOR_RE.finditer(css_content):
        name, kind, raw_values = match.groups()
        parts = [float(p.strip()) for p in raw_values.split(",")]
        r, g, b = parts[0], parts[1], parts[2]
        a = parts[3] if kind == "rgba" and len(parts) > 3 else 1.0
        colors[name] = (round(r), round(g), round(b), a)
    return colors


def to_kde_triplet(prop, colors):
    r, g, b, _a = colors[prop]
    return f"{r},{g},{b}"


# Mismas 4 claves en cada sección "de superficie" -- Colors:Window/View/
# Tooltip usan mybackground como superficie principal, Colors:Button/
# Header/Header][Inactive/Complementary usan mybackgroundhover (botones,
# barras, alternado). Colors:Selection es la excepción: ahí
# BackgroundNormal/Alternate son el acento, no una superficie, y suma el
# override puntual de texto legible encima (ver docstring).
def _surface(bg):
    return {
        "BackgroundNormal": bg, "BackgroundAlternate": "mybackgroundhover",
        "DecorationFocus": "myborders", "DecorationHover": "myborders2",
    }


KDEGLOBALS_SECTION_RULES = {
    "Colors:Window": _surface("mybackground"),
    "Colors:View": _surface("mybackground"),
    "Colors:Tooltip": _surface("mybackground"),
    "Colors:Button": _surface("mybackgroundhover"),
    "Colors:Header": _surface("mybackgroundhover"),
    "Colors:Header][Inactive": _surface("mybackgroundhover"),
    "Colors:Complementary": _surface("mybackgroundhover"),
    "Colors:Selection": {
        "BackgroundNormal": "myforegroundhover",
        "BackgroundAlternate": "myforegroundhover",
        "DecorationFocus": "myborders",
        "DecorationHover": "myborders2",
        # Foreground* es el texto ENCIMA del acento, no texto normal
        # (ese sigue excluido) -- sin esto quedaba ilegible con el
        # acento previo de Noctalia.
        "ForegroundActive": "myforeground",
        "ForegroundNormal": "myforeground",
    },
}


def sync_kdeglobals(colors):
    try:
        with open(KDEGLOBALS_PATH) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return

    section_re = re.compile(r"^\[(.*)\]\s*$")
    key_re = re.compile(r"^([A-Za-z]+)=")
    current_section = None
    for i, line in enumerate(lines):
        m = section_re.match(line)
        if m:
            current_section = m.group(1)
            continue
        rules = KDEGLOBALS_SECTION_RULES.get(current_section)
        if not rules:
            continue
        km = key_re.match(line)
        if not km or km.group(1) not in rules:
            continue
        prop = rules[km.group(1)]
        if prop not in colors:
            continue
        lines[i] = f"{km.group(1)}={to_kde_triplet(prop, colors)}\n"

    common.atomic_write(KDEGLOBALS_PATH, "".join(lines))


def main():
    with open(CSS_PATH) as f:
        css_content = f.read()
    colors = parse_colors(css_content)
    sync_kdeglobals(colors)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Copia colores de colors.css a ~/.config/gtk-3.0/gtk.css (named-color
overrides) -- GTK3 lo carga con prioridad más alta que cualquier tema
para TODA app GTK3 del sistema (Nautilus, GParted, etc.), sin tocarlas
una por una. Mismo patrón que sync_dunst.py. `myforeground` (texto)
queda fuera a propósito -- ver GTK3_NAMED_COLOR_TARGETS."""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.expanduser("~/.config/flipfrog/scripts"))
import common

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
GTK3_CSS_PATH = os.path.expanduser("~/.config/gtk-3.0/gtk.css")

DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(rgba?)\(([^)]+)\)")

# Cada @mi-propiedad pisa estos "named colors" públicos de Adwaita/
# adw-gtk3 -- lista armada leyendo gtk-3.0/gtk.css del tema activo (ver
# docstring del módulo).
GTK3_NAMED_COLOR_TARGETS = {
    "mybackground": [
        "window_bg_color", "theme_bg_color", "view_bg_color",
        "theme_base_color", "dialog_bg_color", "popover_bg_color",
        "theme_unfocused_bg_color", "theme_unfocused_base_color",
        "insensitive_base_color",
    ],
    "mybackgroundhover": [
        "sidebar_bg_color", "card_bg_color", "headerbar_bg_color",
        "thumbnail_bg_color", "panel_bg_color",
    ],
    "myborders": ["borders"],
    "myborders2": ["unfocused_borders"],
    "myforegroundhover": [
        "accent_bg_color", "accent_color", "theme_selected_bg_color",
        "theme_unfocused_selected_bg_color",
    ],
}


def parse_colors(css_content):
    colors = {}
    for match in DEFINE_COLOR_RE.finditer(css_content):
        name, kind, raw_values = match.groups()
        parts = [float(p.strip()) for p in raw_values.split(",")]
        r, g, b = parts[0], parts[1], parts[2]
        a = parts[3] if kind == "rgba" and len(parts) > 3 else 1.0
        colors[name] = (round(r), round(g), round(b), a)
    return colors


def build_gtk3_css(colors):
    lines = [
        "/* Generado por themer/sync_gtk3.py -- NO editar a mano, se",
        "   reescribe en cada cambio de tema. Fuente real: themer/colors.css */",
        "",
    ]
    for prop, targets in GTK3_NAMED_COLOR_TARGETS.items():
        if prop not in colors:
            continue
        r, g, b, a = colors[prop]
        value = f"rgba({r}, {g}, {b}, {a:.2f})" if a < 1.0 else f"rgb({r}, {g}, {b})"
        for target in targets:
            lines.append(f"@define-color {target} {value};")
    lines.append("")
    return "\n".join(lines)


def main():
    with open(CSS_PATH) as f:
        css_content = f.read()
    colors = parse_colors(css_content)
    content = build_gtk3_css(colors)

    os.makedirs(os.path.dirname(GTK3_CSS_PATH), exist_ok=True)
    common.atomic_write(GTK3_CSS_PATH, content)


if __name__ == "__main__":
    main()

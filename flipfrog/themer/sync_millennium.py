#!/usr/bin/env python3
"""Sincroniza el fondo de Steam (Millennium + Adwaita-for-Steam) con
`mybackground` -- edita config.json (themes.themeColors.<tema>), la
misma sección que usa el panel "Customize" nativo de Millennium. Sin
hot-reload -- el cambio se ve recién la próxima vez que Steam reinicia
(no se fuerza un restart automático acá). Solo actúa si el tema activo
es "Adwaita-for-Steam" (otros temas usan variables distintas). Sin
alpha (mismo motivo que sync_qt.py/sync_vesktop.py)."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.expanduser("~/.config/flipfrog/scripts"))
import common

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
MILLENNIUM_CONFIG_PATH = os.path.expanduser("~/.config/millennium/config.json")

DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(rgba?)\(([^)]+)\)")

ADWAITA_THEME_NAME = "Adwaita-for-Steam"
BACKGROUND_VARS = [
    "--adw-window-bg-rgb",
    "--adw-view-bg-rgb",
    "--adw-sidebar-bg-rgb",
    "--adw-secondary-sidebar-bg-rgb",
    "--adw-dialog-bg-rgb",
    "--adw-popover-bg-rgb",
    "--adw-headerbar-bg-rgb",
    "--adw-thumbnail-bg-rgb",
    "--adw-card-bg-rgb",
]


def parse_colors(css_content):
    colors = {}
    for match in DEFINE_COLOR_RE.finditer(css_content):
        name, kind, raw_values = match.groups()
        parts = [float(p.strip()) for p in raw_values.split(",")]
        r, g, b = parts[0], parts[1], parts[2]
        a = parts[3] if kind == "rgba" and len(parts) > 3 else 1.0
        colors[name] = (round(r), round(g), round(b), a)
    return colors


def sync_millennium(r, g, b):
    try:
        with open(MILLENNIUM_CONFIG_PATH) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return

    themes = config.get("themes", {})
    if themes.get("activeTheme") != ADWAITA_THEME_NAME:
        return
    theme_colors = themes.get("themeColors", {}).get(ADWAITA_THEME_NAME)
    if not isinstance(theme_colors, dict):
        return

    rgb = f"{r}, {g}, {b}"
    for var in BACKGROUND_VARS:
        if var in theme_colors:
            theme_colors[var] = rgb

    common.atomic_write(MILLENNIUM_CONFIG_PATH, json.dumps(config, indent=2))


def main():
    with open(CSS_PATH) as f:
        css_content = f.read()
    colors = parse_colors(css_content)
    if "mybackground" not in colors:
        return
    r, g, b, _a = colors["mybackground"]
    sync_millennium(r, g, b)


if __name__ == "__main__":
    main()

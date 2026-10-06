#!/usr/bin/env python3
"""Sincroniza el fondo de Vesktop (Discord) con `mybackground` -- vía el
color semilla del plugin `ClientTheme` de Vencord
(`settings/settings.json` -> `plugins.ClientTheme.color`), no QuickCSS
(el cliente actual ya no pinta superficies con las CSS vars clásicas de
BetterDiscord/Vencord, resuelve todo internamente vía `oklab()`). Sin
alpha (hex plano de 6 dígitos). Solo actualiza si el plugin ya existe en
el JSON -- no lo crea ni lo fuerza a `enabled`. Sin hot-reload: recién
aplica la próxima vez que se abre/reinicia Vesktop, por eso no está en
la lista de reinicios de `reload.sh`."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.expanduser("~/.config/flipfrog/scripts"))
import common

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
SETTINGS_PATH = os.path.expanduser("~/.config/vesktop/settings/settings.json")

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


def sync_vesktop(hex_color):
    try:
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return

    client_theme = settings.get("plugins", {}).get("ClientTheme")
    if not isinstance(client_theme, dict):
        return
    client_theme["color"] = hex_color

    common.atomic_write(SETTINGS_PATH, json.dumps(settings, indent=4))


def main():
    with open(CSS_PATH) as f:
        css_content = f.read()
    colors = parse_colors(css_content)
    if "mybackground" not in colors:
        return
    r, g, b, _a = colors["mybackground"]
    sync_vesktop(f"{r:02x}{g:02x}{b:02x}")


if __name__ == "__main__":
    main()

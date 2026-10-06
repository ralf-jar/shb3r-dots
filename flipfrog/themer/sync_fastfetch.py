#!/usr/bin/env python3
"""Sincroniza fastfetch con colors.css -- fastfetch no lee CSS ni conoce
`~/.config/kitty/current-theme.conf`, así que sin este script sus
colores quedan fijos pase lo que pase con el tema:
1. `frog-logo.txt` (logo tipo "file-raw", ANSI truecolor horneado línea
   por línea) -- se le pisa el triple `38;2;R;G;B` a `myforegroundhover2`
   (acento). Un logo "file-raw" es contenido crudo, no soporta
   `--logo-color-N`/placeholders como el tipo "file" -- por eso se
   reescribe el archivo entero acá en vez de un override en config.jsonc.
2. `config.jsonc` -- `display.color.keys`/`title` al mismo acento. Sin
   esto, fastfetch harda su color de key/title al slot ANSI "cyan" de la
   terminal (ver sync_kitty.py, que ahora sí lo sincroniza) -- fijarlo
   acá explícito evita depender de que el usuario abra fastfetch siempre
   desde una Kitty ya sincronizada."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.expanduser("~/.config/flipfrog/scripts"))
import common

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
FROG_LOGO_PATH = os.path.expanduser("~/.config/fastfetch/frog-logo.txt")
FASTFETCH_CONFIG_PATH = os.path.expanduser("~/.config/fastfetch/config.jsonc")

DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(rgba?)\(([^)]+)\)")
LOGO_TRUECOLOR_RE = re.compile(r"38;2;\d+;\d+;\d+")


def parse_colors(css_content):
    colors = {}
    for match in DEFINE_COLOR_RE.finditer(css_content):
        name, kind, raw_values = match.groups()
        parts = [float(p.strip()) for p in raw_values.split(",")]
        r, g, b = parts[0], parts[1], parts[2]
        a = parts[3] if kind == "rgba" and len(parts) > 3 else 1.0
        colors[name] = (round(r), round(g), round(b), a)
    return colors


def sync_frog_logo(r, g, b):
    try:
        with open(FROG_LOGO_PATH) as f:
            content = f.read()
    except FileNotFoundError:
        return
    content = LOGO_TRUECOLOR_RE.sub(f"38;2;{r};{g};{b}", content)
    common.atomic_write(FROG_LOGO_PATH, content)


def sync_config_display(hex_color):
    try:
        with open(FASTFETCH_CONFIG_PATH) as f:
            config = json.load(f)
    except FileNotFoundError:
        return
    config["display"] = {"color": {"keys": hex_color, "title": hex_color}}
    # reordena para que "display" quede junto a "logo", antes de "modules"
    ordered = {k: config[k] for k in ("$schema", "logo", "display") if k in config}
    ordered["modules"] = config["modules"]
    content = json.dumps(ordered, indent=2, ensure_ascii=False) + "\n"
    common.atomic_write(FASTFETCH_CONFIG_PATH, content)


def main():
    with open(CSS_PATH) as f:
        css_content = f.read()
    colors = parse_colors(css_content)
    if "myforegroundhover2" not in colors:
        return
    r, g, b, _alpha = colors["myforegroundhover2"]
    sync_frog_logo(r, g, b)
    sync_config_display(f"#{r:02x}{g:02x}{b:02x}")


if __name__ == "__main__":
    main()

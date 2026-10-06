#!/usr/bin/env python3
"""Sincroniza btop con colors.css: genera `btop/themes/flipfrog.theme`
desde los 8 roles, apunta `color_theme` de btop.conf a él y le manda
SIGUSR2 a cualquier btop abierto (recarga config y tema en caliente).
`theme_background = false` para que se vea el fondo translúcido de Kitty
en vez de un `main_bg` opaco."""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.expanduser("~/.config/flipfrog/scripts"))
import common

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
BTOP_DIR = os.path.expanduser("~/.config/btop")
BTOP_CONF_PATH = os.path.join(BTOP_DIR, "btop.conf")
BTOP_THEME_PATH = os.path.join(BTOP_DIR, "themes", "flipfrog.theme")
THEME_NAME = "flipfrog"

DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(rgba?)\(([^)]+)\)")

GRADIENTS = ("temp", "cpu", "free", "cached", "available", "used",
             "download", "upload", "process")

# (rol, mezclar alpha sobre negro)
TARGETS = {
    "main_bg": ("mybackground", True),
    "main_fg": ("myforeground", False),
    "title": ("myforegroundhover", False),
    "hi_fg": ("myforegroundhover2", False),
    "selected_bg": ("mybackgroundhover", False),
    "selected_fg": ("myforeground", False),
    "inactive_fg": ("myborders", False),
    "graph_text": ("myforeground", False),
    "meter_bg": ("myborderinactive", True),
    "proc_misc": ("myforegroundhover2", False),
    "cpu_box": ("myborders", False),
    "mem_box": ("myborders", False),
    "net_box": ("myborders", False),
    "proc_box": ("myborders", False),
    "div_line": ("myborderinactive", True),
}
GRADIENT_STOPS = {
    "start": ("myforegroundhover2", False),
    "mid": ("myforegroundhover", False),
    "end": ("myborders2", False),
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


def hexcolor(colors, name, blend):
    r, g, b, a = colors[name]
    if blend:
        r, g, b = (round(c * a) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def build_theme(colors):
    lines = [
        "# Generado por flipfrog/themer/sync_btop.py -- NO editar a mano,",
        "# se reescribe en cada cambio de tema. Fuente real: themer/colors.css",
        "",
    ]
    for key, (prop, blend) in TARGETS.items():
        if prop in colors:
            lines.append(f'theme[{key}]="{hexcolor(colors, prop, blend)}"')

    for gradient in GRADIENTS:
        for stop, (prop, blend) in GRADIENT_STOPS.items():
            if prop in colors:
                lines.append(f'theme[{gradient}_{stop}]="{hexcolor(colors, prop, blend)}"')

    lines.append("")
    return "\n".join(lines)


def set_conf_value(content, key, value):
    line = f"{key} = {value}"
    pattern = re.compile(rf"^{key}\s*=.*$", re.MULTILINE)
    if pattern.search(content):
        return pattern.sub(line, content, count=1)
    return content.rstrip("\n") + f"\n{line}\n"


def sync_conf():
    try:
        with open(BTOP_CONF_PATH) as f:
            content = f.read()
    except FileNotFoundError:
        return
    new = set_conf_value(content, "color_theme", f'"{THEME_NAME}"')
    new = set_conf_value(new, "theme_background", "false")
    if new != content:
        common.atomic_write(BTOP_CONF_PATH, new)


def main():
    if not os.path.isdir(BTOP_DIR):
        return
    with open(CSS_PATH) as f:
        colors = parse_colors(f.read())

    os.makedirs(os.path.dirname(BTOP_THEME_PATH), exist_ok=True)
    common.atomic_write(BTOP_THEME_PATH, build_theme(colors))
    sync_conf()
    subprocess.run(["pkill", "-USR2", "-x", "btop"], stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()

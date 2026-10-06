#!/usr/bin/env python3
"""Sincroniza Kitty con colors.css en dos frentes:
1. background/background_opacity de `mybackground` en kitty.conf (línea
   existente, ver más abajo) -- Kitty no es GTK3/Qt, no importa
   colors.css directo. Solo toca esas dos líneas, preservando el resto,
   DESPUÉS del `include current-theme.conf` para pisar por orden el
   `background` propio del tema de color de Kitty. Kitty recarga en
   caliente solo cuando kitty.conf mismo (no un include suyo) cambia de
   mtime -- por eso se edita ahí directo.
2. La paleta ANSI de 16 colores (color0-15) + foreground/selection/
   cursor/bordes/tabs en current-theme.conf -- regenerada entera desde
   los 8 roles de colors.css (mapeo en ANSI_TARGETS, sin semántica de
   rojo=error/verde=ok: el tema no trae un arcoíris, solo grises+acento).
   Reemplaza el tema "noctalia" que traía este archivo antes, ajeno al
   dashboard. Este es el que hace que fastfetch (keys/logo heredan del
   slot "cyan"/verde de la terminal) y cualquier otra app que use
   colores ANSI estándar se vean con la paleta del tema activo."""
import os
import re
import sys

sys.path.insert(0, os.path.expanduser("~/.config/flipfrog/scripts"))
import common

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
KITTY_CONF_PATH = os.path.expanduser("~/.config/kitty/kitty.conf")
KITTY_THEME_PATH = os.path.expanduser("~/.config/kitty/current-theme.conf")

DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(rgba?)\(([^)]+)\)")
OPACITY_LINE_RE = re.compile(r"^background_opacity[ \t]+[\d.]+[ \t]*$", re.MULTILINE)
BACKGROUND_LINE_RE = re.compile(r"^background[ \t]+#[0-9a-fA-F]{6}[ \t]*$", re.MULTILINE)

# color1/5 (rojo/magenta), color3/6 (amarillo/cian): el tema no define
# hues separados, solo el acento (myforegroundhover2) y el borde tibio
# (myborders2) -- se reusan a propósito en vez de inventar tonos nuevos.
ANSI_TARGETS = {
    "color0": ("mybackground", False),
    "color8": ("mybackgroundhover", False),
    "color1": ("myborders2", True),
    "color9": ("myborders2", False),
    "color2": ("myforegroundhover2", False),
    "color10": ("myforegroundhover2", False),
    "color3": ("myforegroundhover2", False),
    "color11": ("myforegroundhover2", False),
    "color4": ("myforegroundhover", False),
    "color12": ("myforeground", False),
    "color5": ("myborders2", True),
    "color13": ("myborders2", False),
    "color6": ("myforegroundhover2", False),
    "color14": ("myforegroundhover2", False),
    "color7": ("myforegroundhover", False),
    "color15": ("myforeground", False),
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


def sync_kitty_background(r, g, b, alpha):
    try:
        with open(KITTY_CONF_PATH) as f:
            content = f.read()
    except FileNotFoundError:
        return

    hex_color = f"#{r:02x}{g:02x}{b:02x}"
    new_bg_line = f"background {hex_color}"
    if BACKGROUND_LINE_RE.search(content):
        content = BACKGROUND_LINE_RE.sub(new_bg_line, content, count=1)
    elif OPACITY_LINE_RE.search(content):
        # Sin línea `background` propia todavía: se agrega justo antes de
        # `background_opacity` para que ambas queden juntas, en vez de al
        # final del archivo (después de config no relacionada del usuario).
        content = OPACITY_LINE_RE.sub(lambda m: f"{new_bg_line}\n{m.group(0)}", content, count=1)
    else:
        content = content.rstrip("\n") + f"\n{new_bg_line}\n"

    new_opacity_line = f"background_opacity {alpha:.2f}"
    if OPACITY_LINE_RE.search(content):
        content = OPACITY_LINE_RE.sub(new_opacity_line, content, count=1)
    else:
        content = content.rstrip("\n") + f"\n{new_opacity_line}\n"

    common.atomic_write(KITTY_CONF_PATH, content)


def build_theme_conf(colors):
    lines = [
        "# Generado por themer/sync_kitty.py -- NO editar a mano, se",
        "# reescribe en cada cambio de tema. Fuente real: themer/colors.css",
    ]
    for slot, (prop, blend) in ANSI_TARGETS.items():
        if prop not in colors:
            continue
        lines.append(f"{slot} {hexcolor(colors, prop, blend)}")

    if "mybackground" in colors:
        lines.append(f"background {hexcolor(colors, 'mybackground', True)}")
    if "mybackground" in colors and "myforeground" in colors:
        lines.append(f"selection_foreground {hexcolor(colors, 'mybackground', True)}")
        lines.append(f"selection_background {hexcolor(colors, 'myforeground', False)}")
    if "myforegroundhover2" in colors and "mybackground" in colors:
        lines.append(f"cursor {hexcolor(colors, 'myforegroundhover2', False)}")
        lines.append(f"cursor_text_color {hexcolor(colors, 'mybackground', True)}")
    if "myforeground" in colors:
        lines.append(f"foreground {hexcolor(colors, 'myforeground', False)}")
    if "myforegroundhover2" in colors:
        lines.append(f"active_border_color {hexcolor(colors, 'myforegroundhover2', False)}")
    if "myborders" in colors:
        lines.append(f"inactive_border_color {hexcolor(colors, 'myborders', False)}")

    if "mybackground" in colors and "myforegroundhover2" in colors:
        lines.append(f"active_tab_foreground   {hexcolor(colors, 'mybackground', True)}")
        lines.append(f"active_tab_background   {hexcolor(colors, 'myforegroundhover2', False)}")
    if "myforegroundhover" in colors and "mybackgroundhover" in colors:
        lines.append(f"inactive_tab_foreground {hexcolor(colors, 'myforegroundhover', False)}")
        lines.append(f"inactive_tab_background {hexcolor(colors, 'mybackgroundhover', False)}")
    if "myforegroundhover" in colors:
        lines.append(f"cursor_trail_color      {hexcolor(colors, 'myforegroundhover', False)}")

    if "mybackground" in colors:
        lines.append(f"background_opacity {colors['mybackground'][3]:.2f}")

    lines.append("")
    return "\n".join(lines)


def main():
    with open(CSS_PATH) as f:
        css_content = f.read()
    colors = parse_colors(css_content)

    if "mybackground" in colors:
        r, g, b, alpha = colors["mybackground"]
        sync_kitty_background(r, g, b, alpha)

    theme_content = build_theme_conf(colors)
    common.atomic_write(KITTY_THEME_PATH, theme_content)


if __name__ == "__main__":
    main()

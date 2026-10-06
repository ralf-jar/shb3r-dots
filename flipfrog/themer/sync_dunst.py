#!/usr/bin/env python3
"""Copia colores de colors.css a dunstrc y recarga dunst.

Mapeo (todas las urgencias usan el mismo, sin diferenciador especial
para critical):
    frame_color -> @myborders
    background  -> @mybackground
    foreground  -> @myforeground
"""
import os
import re
import subprocess

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
DUNSTRC_PATH = os.path.expanduser("~/.config/dunst/dunstrc")

DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(rgba?)\(([^)]+)\)")

DUNST_KEY_TO_PROPERTY = {
    "frame_color": "myborders",
    "background": "mybackground",
    "foreground": "myforeground",
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


def to_hex(color):
    r, g, b, a = color
    if a >= 1.0:
        return "#{:02x}{:02x}{:02x}".format(r, g, b)
    return "#{:02x}{:02x}{:02x}{:02x}".format(r, g, b, round(a * 255))


def sync_dunstrc(colors):
    with open(DUNSTRC_PATH) as f:
        content = f.read()

    pattern = re.compile(
        r"^([ \t]*)(frame_color|background|foreground)[ \t]*=[ \t]*\"[^\"]*\"",
        re.MULTILINE,
    )

    def replace(match):
        indent, key = match.group(1), match.group(2)
        prop = DUNST_KEY_TO_PROPERTY[key]
        if prop not in colors:
            return match.group(0)
        return '{}{} = "{}"'.format(indent, key, to_hex(colors[prop]))

    new_content = pattern.sub(replace, content)

    if new_content != content:
        with open(DUNSTRC_PATH, "w") as f:
            f.write(new_content)


def main():
    with open(CSS_PATH) as f:
        css_content = f.read()

    colors = parse_colors(css_content)
    sync_dunstrc(colors)
    subprocess.run(["dunstctl", "reload"], check=False)


if __name__ == "__main__":
    main()

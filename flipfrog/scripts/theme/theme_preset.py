#!/usr/bin/env python3
"""Configuración de la barra y de "Personalización" guardada dentro de un
.theme, en la línea `/* preset: {json} */` (debajo de la del wallpaper).
`null` entero = el tema no trae configuración; una clave en `null` = no
tocar ese ajuste. Sin GTK: lo corre apply-theme.sh.

    theme_preset.py dump            JSON del estado actual
    theme_preset.py line [--none]   línea `/* preset: ... */` para un .theme
    theme_preset.py apply <tema>    aplica la línea preset del .theme"""

import json
import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
SCRIPTS_DIR = os.path.dirname(SCRIPT_DIR)
THEMER_DIR = os.path.normpath(os.path.join(SCRIPTS_DIR, "..", "themer"))
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, os.path.join(SCRIPTS_DIR, "bar"))
sys.path.insert(0, os.path.join(SCRIPTS_DIR, "menu"))
sys.path.insert(0, THEMER_DIR)
import bar_settings
import common
import icon_packs
import look_settings
import sync_font_size
import sync_radius
from i18n import t

PREFIX = "/* preset:"
LINE_RE = re.compile(r"^/\*\s*preset:\s*(.+?)\s*\*/$")

GLOBAL_CSS = os.path.join(THEMER_DIR, "global.css")
BLUR_FILE = os.path.join(THEMER_DIR, "blur-enabled.json")
CLOCK_FORMAT_FILE = os.path.expanduser("~/.config/waybar/clock-format.json")
CLOCK_GRADIENT_FILE = os.path.expanduser("~/.config/waybar/clock-gradient.json")

FONT_FAMILY_RE = re.compile(r'font-family:\s*"([^"]+)"')
DEFAULT_FONT_FAMILY = "JetBrainsMono Nerd Font"

# `visible` es el estado de SUPER+W, no parte del look.
BAR_KEYS = [k for k in bar_settings.DEFAULTS if k != "visible"]
LOOK_KEYS = ["opacity", "gaps_in", "gaps_out", "border_size", "animations", "blur_size"]


def _read_json(path, key, default):
    try:
        with open(path) as f:
            return json.load(f).get(key, default)
    except (OSError, ValueError, AttributeError):
        return default


def current_font_family():
    try:
        with open(GLOBAL_CSS) as f:
            content = f.read()
    except FileNotFoundError:
        return DEFAULT_FONT_FAMILY
    m = FONT_FAMILY_RE.search(content)
    return m.group(1) if m else DEFAULT_FONT_FAMILY


def set_font_family(name):
    try:
        with open(GLOBAL_CSS) as f:
            content = f.read()
    except FileNotFoundError:
        return
    escaped = name.replace('"', '\\"')
    new_value = f'font-family: "{escaped}", "Symbols Nerd Font", sans-serif;'
    new_content, count = re.subn(r'font-family:\s*[^;]+;', new_value, content, count=1)
    if count == 0:
        return
    common.atomic_write(GLOBAL_CSS, new_content)


def _font_installed(name):
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return True
    return any(name in line.split(",") for line in out.splitlines())


def dump():
    bar = bar_settings.load()
    look = look_settings.load()
    preset = {
        "bar": {k: bar[k] for k in BAR_KEYS},
        "look": {k: look[k] for k in LOOK_KEYS},
    }
    preset["bar"]["clock_extended"] = bool(_read_json(CLOCK_FORMAT_FILE, "extended", True))
    preset["bar"]["clock_gradient"] = bool(_read_json(CLOCK_GRADIENT_FILE, "enabled", True))
    preset["look"].update(
        blur=bool(_read_json(BLUR_FILE, "enabled", True)),
        radius=sync_radius.load_radius(),
        font_family=current_font_family(),
        font_size=sync_font_size.load_font_size(),
        icon_pack=icon_packs.current_theme(),
        cursor_theme=look["cursor_theme"],
        cursor_size=look["cursor_size"],
    )
    return preset


def preset_line(preset):
    """`*/` dentro de un valor cerraría el comentario: `\\/` es un escape
    válido de JSON para `/`."""
    return f"{PREFIX} {json.dumps(preset, ensure_ascii=False).replace('*/', '*\\/')} */"


def read_preset(theme_path):
    """(preset, ok): preset None si el tema no trae línea o trae `null`;
    ok False si la línea existe pero no es JSON válido."""
    with open(theme_path) as f:
        for line in f.read().splitlines()[:2]:
            m = LINE_RE.match(line)
            if not m:
                continue
            try:
                data = json.loads(m.group(1))
            except ValueError:
                return None, False
            return (data if isinstance(data, dict) else None), True
    return None, True


def _set(values, key):
    v = values.get(key)
    return v is not None


def apply(preset):
    """Aplica lo que no sea null. No hace `hyprctl reload`: apply-theme.sh
    corre reload.sh después. Devuelve lo que se saltó por no estar
    instalado."""
    skipped = []
    bar = preset.get("bar") or {}
    look = preset.get("look") or {}

    changes = {k: bar[k] for k in BAR_KEYS if _set(bar, k)}
    if changes:
        bar_settings.update(**changes)
    if _set(bar, "clock_extended"):
        common.atomic_write(CLOCK_FORMAT_FILE, json.dumps({"extended": bool(bar["clock_extended"])}))
    if _set(bar, "clock_gradient"):
        common.atomic_write(CLOCK_GRADIENT_FILE, json.dumps({"enabled": bool(bar["clock_gradient"])}))

    changes = {k: look[k] for k in LOOK_KEYS if _set(look, k)}
    if changes:
        look_settings.save(**changes)
    if _set(look, "blur"):
        common.atomic_write(BLUR_FILE, json.dumps({"enabled": bool(look["blur"])}))
    if _set(look, "radius") and int(look["radius"]) != sync_radius.load_radius():
        sync_radius.apply_radius(look["radius"])
    if _set(look, "font_size") and int(look["font_size"]) != sync_font_size.load_font_size():
        sync_font_size.apply_font_size(look["font_size"])

    family = look.get("font_family")
    if family and family != current_font_family():
        if _font_installed(family):
            set_font_family(family)
        else:
            skipped.append(family)

    pack = look.get("icon_pack")
    if pack and pack != icon_packs.current_theme():
        if pack in icon_packs.installed_themes():
            icon_packs.apply_theme(pack)
        else:
            skipped.append(pack)

    cursor = look.get("cursor_theme")
    if cursor or _set(look, "cursor_size"):
        current = look_settings.load()
        theme = cursor or current["cursor_theme"]
        size = look["cursor_size"] if _set(look, "cursor_size") else current["cursor_size"]
        if theme not in look_settings.list_cursor_themes():
            skipped.append(theme)
            theme = current["cursor_theme"]
        if (theme, size) != (current["cursor_theme"], current["cursor_size"]):
            look_settings.apply_cursor(theme, size)
    return skipped


def main():
    if sys.argv[1:] == ["dump"]:
        print(json.dumps(dump(), ensure_ascii=False))
    elif sys.argv[1:2] == ["line"]:
        print(preset_line(None if "--none" in sys.argv else dump()))
    elif len(sys.argv) == 3 and sys.argv[1] == "apply":
        preset, ok = read_preset(sys.argv[2])
        if not ok:
            subprocess.run(["notify-send", "Themer", t("temas", "preset_invalido"), "-i", "dialog-warning"])
            return
        if preset is None:
            return
        skipped = apply(preset)
        if skipped:
            subprocess.run(["notify-send", "Themer",
                            t("temas", "preset_no_instalado", items=", ".join(skipped)),
                            "-i", "dialog-warning"])
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

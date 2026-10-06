"""Ajustes de apariencia de Hyprland para la pestaña "Personalización":
opacidad de ventanas, gaps, borde, animaciones y cursor. Sin GTK.

Estado en themer/hypr-look.json (un solo escritor: el dashboard), leído
por hypr/config/look.lua en cada `hyprctl reload` -- mismo patrón que
corner-radius.json/blur-enabled.json. Nunca `hyprctl keyword` suelto:
el próximo reload lo pisaría."""

import configparser
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
import common

THEMER_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "themer"))
STATE_FILE = os.path.join(THEMER_DIR, "hypr-look.json")
TIMEOUT = 3

DEFAULTS = {
    "opacity": 1.0,
    "gaps_in": 5,
    "gaps_out": 10,
    "border_size": 2,
    "animations": True,
    "blur_size": 8,
    "cursor_theme": None,
    "cursor_size": 24,
}

CURSOR_DIRS = (
    os.path.expanduser("~/.local/share/icons"),
    os.path.expanduser("~/.icons"),
    "/usr/share/icons",
)
GTK_SETTINGS_FILES = (
    os.path.expanduser("~/.config/gtk-3.0/settings.ini"),
    os.path.expanduser("~/.config/gtk-4.0/settings.ini"),
)


def load():
    data = dict(DEFAULTS)
    try:
        with open(STATE_FILE) as f:
            data.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if not data["cursor_theme"]:
        data["cursor_theme"] = _gsettings_get("cursor-theme") or "default"
    return data


def save(**changes):
    data = load()
    data.update(changes)
    common.atomic_write(STATE_FILE, json.dumps(data, indent=2))
    return data


def reload_hyprland():
    subprocess.run(["hyprctl", "reload"], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=10)


def apply_blur_size(size):
    """`hyprctl eval` en vez de reload completo (decorations.lua lo relee
    igual de hypr-look.json en el próximo reload)."""
    subprocess.run(["hyprctl", "eval", f"hl.config({{decoration={{blur={{size={int(size)}}}}}}})"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT)


# ---- cursor -----------------------------------------------------------------

def list_cursor_themes():
    """Temas con carpeta cursors/ (los de íconos puros no sirven de
    cursor). Nombre = nombre de carpeta, el que esperan XCURSOR_THEME y
    gsettings."""
    names = set()
    for base in CURSOR_DIRS:
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for name in entries:
            if os.path.isdir(os.path.join(base, name, "cursors")):
                names.add(name)
    return sorted(names, key=str.lower)


def _gsettings_get(key):
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", key],
                             capture_output=True, text=True, timeout=TIMEOUT).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.strip("'\"") or None


def _gsettings_set(key, value):
    try:
        subprocess.run(["gsettings", "set", "org.gnome.desktop.interface", key, str(value)],
                       capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _write_gtk_ini(theme, size):
    for path in GTK_SETTINGS_FILES:
        parser = configparser.ConfigParser(strict=False)
        parser.optionxform = str
        if os.path.exists(path):
            parser.read(path)
        if not parser.has_section("Settings"):
            parser.add_section("Settings")
        parser.set("Settings", "gtk-cursor-theme-name", theme)
        parser.set("Settings", "gtk-cursor-theme-size", str(size))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            parser.write(f, space_around_delimiters=False)
        os.replace(tmp, path)


def apply_cursor(theme, size):
    """gsettings (apps GTK en vivo) + settings.ini (respaldo) + `hyprctl
    setcursor` (compositor, sesión en curso) + JSON (environment.lua lo
    exporta como XCURSOR_* en el próximo inicio de sesión)."""
    save(cursor_theme=theme, cursor_size=size)
    _gsettings_set("cursor-theme", theme)
    _gsettings_set("cursor-size", size)
    _write_gtk_ini(theme, size)
    try:
        subprocess.run(["hyprctl", "setcursor", theme, str(size)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        pass

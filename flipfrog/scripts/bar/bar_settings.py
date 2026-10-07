#!/usr/bin/env python3
"""Ajustes de la barra propia (bar-settings.json, gitignored). Los
escriben los toggles del dashboard y la línea de comandos; bar.py vigila
el archivo y se rearma sola, sin reiniciar nada. Sin GTK.

    bar_settings.py set <clave> on|off
    bar_settings.py toggle <clave>      (visible: arranca la barra si no corre)"""

import fcntl
import json
import os
import subprocess
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "bar-settings.json")
LOCK_FILE = os.path.join(SCRIPT_DIR, ".bar-settings.lock")
BAR_LOCK = "/tmp/ff-bar.pid"

DEFAULTS = {
    "layout": "islas",
    "visible": True,
    "frog": True,
    "workspaces": False,
    "ws_icons": True,
    "group_apps": False,
    "compact_ws": False,
    "sysmon": False,
    "bandcamp": False,
    "bluetooth": True,
    "red": False,
    "transparent": True,
    "full_width": False,
}
LAYOUTS = ("capsula", "islas")


def load():
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    settings = dict(DEFAULTS)
    settings.update({k: v for k, v in data.items() if k in DEFAULTS})
    if settings["layout"] not in LAYOUTS:
        settings["layout"] = DEFAULTS["layout"]
    return settings


def get(key):
    return load()[key]


def update(**changes):
    """Lectura-modificación-escritura bajo flock: escriben el dashboard y
    procesos sueltos (clic medio en la isla de Bandcamp, SUPER+W)."""
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        settings = load()
        settings.update(changes)
        fd, tmp = tempfile.mkstemp(dir=SCRIPT_DIR, prefix=".bar-settings.")
        with os.fdopen(fd, "w") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    return settings


def set_value(key, value):
    return update(**{key: value})


def workspaces_mode(settings):
    """None | "numbered" | "grouped" -- "Espacios de Trabajo" apagado con
    "Iconos en Workspaces" prendido muestra la fila de íconos de todas
    las ventanas."""
    if settings["workspaces"]:
        return "numbered"
    return "grouped" if settings["ws_icons"] else None


def bar_running():
    try:
        with open(BAR_LOCK) as f:
            os.kill(int(f.read().strip()), 0)
        return True
    except (OSError, ValueError):
        return False


def start_bar():
    subprocess.Popen(["python3", os.path.join(SCRIPT_DIR, "bar.py")], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(argv):
    if len(argv) < 2 or argv[1] not in DEFAULTS:
        print(__doc__, file=sys.stderr)
        return 1
    action, key = argv[0], argv[1]
    if action == "toggle":
        if key == "visible" and not bar_running():
            set_value("visible", True)
            start_bar()
            return 0
        value = not get(key)
    elif action == "set" and len(argv) == 3 and argv[2] in ("on", "off"):
        value = argv[2] == "on"
    else:
        print(__doc__, file=sys.stderr)
        return 1
    set_value(key, value)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

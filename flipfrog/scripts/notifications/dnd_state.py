#!/usr/bin/env python3
"""No molestar persistente. dunst guarda la pausa solo en memoria y
arranca sin ella en cada login; los switches del dashboard y del
historial (SUPER+N) la guardan en dnd.json (gitignored) y el autostart
la restaura. El modo cine NO pasa por acá: pausa y regresa el valor
previo él mismo, para que un reinicio a media película no deje el No
molestar pegado. Sin GTK.

    dnd_state.py restore"""

import json
import os
import subprocess
import sys
import tempfile
import time

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "dnd.json")
TIMEOUT = 3
RESTORE_ATTEMPTS = 10
RESTORE_RETRY_S = 1


def _dunstctl(*args):
    try:
        r = subprocess.run(["dunstctl", *args], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def is_paused():
    return (_dunstctl("is-paused") or "").strip() == "true"


def saved_paused():
    try:
        with open(STATE_FILE) as f:
            return bool(json.load(f).get("paused", False))
    except (OSError, ValueError):
        return False


def _save(paused):
    # Dos escritores posibles (dashboard e historial, procesos aparte):
    # temporal con nombre aleatorio.
    fd, tmp = tempfile.mkstemp(dir=SCRIPT_DIR, prefix=".dnd.")
    with os.fdopen(fd, "w") as f:
        json.dump({"paused": paused}, f)
    os.replace(tmp, STATE_FILE)


def set_paused(paused):
    _dunstctl("set-paused", "true" if paused else "false")
    _save(paused)


def restore():
    """Al iniciar sesión dunst puede no estar listo todavía (lo levanta
    D-Bus con la primera llamada): reintenta hasta que responda."""
    if not saved_paused():
        return
    for _ in range(RESTORE_ATTEMPTS):
        if _dunstctl("set-paused", "true") is not None and is_paused():
            return
        time.sleep(RESTORE_RETRY_S)


if __name__ == "__main__":
    if sys.argv[1:] == ["restore"]:
        restore()
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

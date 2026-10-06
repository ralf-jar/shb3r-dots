#!/usr/bin/env python3
"""Revisa actualizaciones pendientes una vez y cachea en
updates-cache.json. Lo corre cada 2 h un timer transitorio de systemd
--user armado en autostart.lua (sin daemon vivo entre revisiones)."""

import concurrent.futures
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

PROCESS_NAME = "ff-updates-chk"

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
from i18n import t

WAYBAR_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "waybar"))
CACHE_FILE = os.path.join(WAYBAR_DIR, "updates-cache.json")
CHECK_TIMEOUT = 60  # por comando -- si tarda más (red caída, etc.) se descarta ese ciclo

def _count_lines(cmd):
    """Líneas no vacías de stdout (una por paquete pendiente). None
    (no 0) si falla/tarda -- para no pisar el último conteo válido con
    un falso "sin actualizaciones". No chequea returncode: checkupdates
    devuelve 2 cuando no hay nada pendiente."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CHECK_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    return len(lines)


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"count": None, "checked_at": None, "notified": False}


def notify(count):
    body = t("actualizaciones", "notif_cuerpo_singular" if count == 1 else "notif_cuerpo_plural", count=count)
    subprocess.run(
        [
            "notify-send", "-a", "Waybar", "-i", "software-update-available",
            t("actualizaciones", "notif_titulo"),
            body,
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def check_once():
    # En paralelo: checkupdates (~1s) y paru -Qua (~12s) no dependen
    # entre sí.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        official_future = pool.submit(_count_lines, ["checkupdates"])
        aur_future = pool.submit(_count_lines, ["paru", "-Qua"])
        official = official_future.result()
        aur = aur_future.result()

    # Si cualquiera falló, se descarta el ciclo entero en vez de
    # cachear un total parcial que subestime lo pendiente.
    if official is None or aur is None:
        return

    count = official + aur
    prev = load_cache()
    was_notified = bool(prev.get("notified", False))

    # Notifica una sola vez al pasar de "sin pendientes" a "hay pendientes".
    if count > 0 and not was_notified:
        notify(count)
        notified = True
    elif count == 0:
        notified = False
    else:
        notified = was_notified

    data = {
        "count": count,
        "official": official,
        "aur": aur,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "notified": notified,
    }
    common.atomic_write(CACHE_FILE, json.dumps(data))


def main():
    common.set_process_name(PROCESS_NAME)
    check_once()

if __name__ == "__main__":
    main()

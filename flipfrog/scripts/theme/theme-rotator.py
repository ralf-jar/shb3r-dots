#!/usr/bin/env python3
"""
theme-rotator.py
Rota temas (favoritos si hay alguno marcado, si no todos). Sin daemon:
cada corrida rota si ya tocaba y programa la siguiente con un timer
transitorio de systemd --user (TIMER_PREFIX), así entre rotaciones no
queda ningún proceso vivo.

El intervalo se cuenta contra "last_rotation" de rotation.json (hora
real, OnCalendar): si la PC estuvo suspendida o apagada, el tiempo ya
transcurrido cuenta. autostart.lua lo corre al iniciar sesión para
volver a armar el timer (los transitorios no sobreviven al logout).

  theme-rotator.py              rota si ya tocaba + programa la siguiente
  theme-rotator.py --schedule   solo programa (el dashboard, al activar)
  theme-rotator.py --stop       cancela el timer pendiente
"""

import json
import os
import random
import subprocess
import sys
import tempfile
import time

PROCESS_NAME = "ff-theme-rot"

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common

THEMER_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "themer"))
THEMES_DIR = os.path.join(THEMER_DIR, "themes")
APPLY_SCRIPT = os.path.join(THEMER_DIR, "apply-theme.sh")
FAVORITES_FILE = os.path.join(THEMER_DIR, "favorites.json")
ROTATION_CONFIG = os.path.join(THEMER_DIR, "rotation.json")

TIMER_PREFIX = "ff-theme-rot-"
# Variables de la sesión gráfica que apply-theme.sh necesita (hyprctl,
# awww, notify-send); se copian al servicio por si el gestor de systemd
# todavía no las tiene.
SESSION_ENV = ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP",
               "DISPLAY", "DBUS_SESSION_BUS_ADDRESS")


def load_favorites():
    try:
        with open(FAVORITES_FILE) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def theme_pool():
    """Temas favoritos si hay alguno marcado; si no, todos."""
    try:
        all_themes = sorted(f for f in os.listdir(THEMES_DIR) if f.endswith(".theme"))
    except FileNotFoundError:
        return []
    favorites = load_favorites()
    favored = [t for t in all_themes if t[: -len(".theme")] in favorites]
    return favored or all_themes


def load_config():
    try:
        with open(ROTATION_CONFIG) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(data):
    """mkstemp + os.replace(): theme_module.py también escribe este archivo."""
    fd, tmp_path = tempfile.mkstemp(dir=THEMER_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp_path, ROTATION_CONFIG)
    except BaseException:
        os.remove(tmp_path)
        raise


def interval_seconds(data):
    try:
        return max(1, int(float(data.get("interval_minutes", 60)))) * 60
    except (TypeError, ValueError):
        return 3600


def stop():
    subprocess.run(["systemctl", "--user", "stop", TIMER_PREFIX + "*.timer"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


def schedule(when):
    """Un nombre de unidad por programación: el servicio que está
    corriendo (esta misma corrida) todavía ocupa el nombre anterior."""
    stop()
    env = [f"--setenv={k}" for k in SESSION_ENV if k in os.environ]
    subprocess.run(
        ["systemd-run", "--user", "--quiet", "--collect",
         f"--unit={TIMER_PREFIX}{int(when)}",
         "--timer-property=RemainAfterElapse=no",
         "--on-calendar=" + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when)),
         *env, "python3", os.path.realpath(__file__)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
    )


def rotate():
    """Devuelve la hora de la rotación. Relee el archivo justo antes de
    escribir para no pisar un cambio del dashboard."""
    pool = theme_pool()
    data = load_config()
    data["last_rotation"] = time.time()
    chosen = None
    if len(pool) >= 2:
        chosen = random.choice([t for t in pool if t != data.get("last_theme")] or pool)
        data["last_theme"] = chosen
    save_config(data)
    if chosen:
        # Scope propio: al terminar el servicio del timer, systemd mata lo
        # que quede en su cgroup (mpvpaper de un fondo de video, awww-daemon).
        subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "--collect",
                        "bash", APPLY_SCRIPT, os.path.join(THEMES_DIR, chosen)])
    return data["last_rotation"]


def main():
    common.set_process_name(PROCESS_NAME)

    if "--stop" in sys.argv:
        stop()
        return

    data = load_config()
    if not data.get("enabled"):
        stop()
        return

    interval = interval_seconds(data)
    due = float(data.get("last_rotation") or time.time()) + interval
    # 5 s de holgura: el timer puede disparar un poco antes de la marca.
    if "--schedule" not in sys.argv and time.time() >= due - 5:
        due = rotate() + interval
    schedule(due)


if __name__ == "__main__":
    main()

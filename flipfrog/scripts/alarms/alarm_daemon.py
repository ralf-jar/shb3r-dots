#!/usr/bin/env python3
"""Vigilante de alarmas -- corre como hilo de core/ff_core.py, sondea
alarms.json cada ~20s y dispara notificación crítica + sonido cuando
coincide hora/minuto (y día de semana, si la alarma es recurrente).

`last_fired` (fecha ISO) es el guardia anti-repetición -- sin esto
sonaría varias veces dentro del mismo minuto de polling, y una
recurrente volvería a sonar apenas cambia el minuto otra vez ese mismo
día. Se persiste en alarms.json (dos escritores, ver alarms_store.py).

Sin chequeo de workspace "gaming" a propósito -- a diferencia de los
toggles que reinician Hyprland/Waybar, esto no dispara ningún reload;
es justo el tipo de aviso que el usuario quiere aunque esté jugando."""

import os
import subprocess
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from i18n import t

import alarms_store

POLL_SECONDS = 20
SOUND_FILE = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"
SOUND_REPEATS = 3
SOUND_GAP = 1.2

def _fire(alarm):
    titulo = t("alarmas", "notif_titulo")
    cuerpo = alarm.get("label") or t("alarmas", "notif_cuerpo_default")
    subprocess.run(
        ["notify-send", "-a", "Waybar", "-u", "critical", "-i", "alarm-clock-symbolic", titulo, cuerpo],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if os.path.exists(SOUND_FILE):
        for i in range(SOUND_REPEATS):
            subprocess.Popen(["pw-play", SOUND_FILE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if i < SOUND_REPEATS - 1:
                time.sleep(SOUND_GAP)


def check_alarms():
    now = datetime.now()
    today = now.date().isoformat()
    alarms = alarms_store.load_alarms()
    changed = False
    for alarm in alarms:
        if not alarm.get("enabled"):
            continue
        if alarm.get("last_fired") == today:
            continue
        if now.hour != alarm.get("hour") or now.minute != alarm.get("minute"):
            continue
        days = alarm.get("days") or []
        if days and now.weekday() not in days:
            continue
        _fire(alarm)
        alarm["last_fired"] = today
        if not days:
            alarm["enabled"] = False
        changed = True
    if changed:
        alarms_store.save_alarms(alarms)


def run(stop):
    while not stop.is_set():
        check_alarms()
        stop.wait(POLL_SECONDS)

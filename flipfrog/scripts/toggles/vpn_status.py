#!/usr/bin/env python3
"""Indicador de VPN de la barra, junto al tray (bar/bar_modules.py).
Solo se ve conectada. Usa la rutina activa (vpn/vpn_profiles.py): si
tiene interfaz, solo revisa que exista (sin correr el comando de
estado, `protonvpn status` gasta ~0.7 s de CPU). Sin GTK."""
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "vpn"))
import vpn_profiles
from i18n import t

ICON = "󰦝"  # nf-md-shield_lock
TIMEOUT = 3


def _connection_name(iface):
    try:
        r = subprocess.run(["nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active"],
                           capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in r.stdout.splitlines():
        name, _, device = line.rpartition(":")
        if device == iface:
            return name.replace("\\:", ":")
    return None


def status():
    profile = vpn_profiles.active_profile()
    if not vpn_profiles.is_connected(profile, fast=True):
        return {"text": "", "tooltip": "", "class": "off"}
    iface = profile["interface"].strip()
    detail = (_connection_name(iface) if iface else None) or profile["name"]
    return {"text": ICON, "tooltip": f"{t('sistema', 'vpn_conectada')}\n{detail}", "class": ""}


def main():
    print(json.dumps(status(), ensure_ascii=False))


if __name__ == "__main__":
    main()

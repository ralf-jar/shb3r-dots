#!/usr/bin/env python3
"""connect/disconnect de la rutina activa (vpn/vpn_profiles.py) en
proceso propio (lo lanza vpn_toggle.py con start_new_session) --
conectar tarda hasta ~1 min y la notificación con el resultado tiene
que llegar aunque el dashboard ya se haya cerrado. Sin GTK."""

import os
import re
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "vpn"))
import vpn_profiles
from i18n import t

TIMEOUT = 90
# Salida de protonvpn connect; en otras VPN simplemente no coincide.
CONNECTED_RE = re.compile(r"Connected to (.+?) in (.+?)\.\s*$", re.M)
IP_RE = re.compile(r"Your new IP address is (\d+(?:\.\d+){3})")


def _notify(summary, body, urgency="normal"):
    subprocess.run(["notify-send", "-a", "VPN", "-i", "network-vpn",
                    "-u", urgency, summary, body], timeout=5)


def _last_line(r):
    lines = ((r.stderr or "") + "\n" + (r.stdout or "")).strip().splitlines()
    return lines[-1] if lines else ""


def connect():
    profile = vpn_profiles.active_profile()
    r = vpn_profiles.run(profile["connect"], TIMEOUT)
    if r is None:
        _notify(t("sistema", "vpn_error"), t("sistema", "vpn_timeout"), "critical")
        return
    connected = vpn_profiles.status_connected(profile)
    if r.returncode != 0 or connected is False:
        _notify(t("sistema", "vpn_error"), _last_line(r), "critical")
        return

    match = CONNECTED_RE.search(r.stdout)
    if match:
        body = t("sistema", "vpn_servidor", server=match.group(1), location=match.group(2))
    else:
        body = profile["name"]
    ip = IP_RE.search(r.stdout)
    if ip:
        body += "\n" + t("sistema", "vpn_ip", ip=ip.group(1))
    _notify(t("sistema", "vpn_conectada"), body)


def disconnect():
    vpn_profiles.run(vpn_profiles.active_profile()["disconnect"], TIMEOUT)


if __name__ == "__main__":
    connect() if sys.argv[1:] == ["connect"] else disconnect()

#!/usr/bin/env python3
"""Rutinas de VPN: qué comandos usan el toggle del dashboard
(toggles/vpn_toggle.py), vpn_connect.py y el indicador de la barra
(toggles/vpn_status.py). Predeterminadas en código (solo lectura), las
del usuario y la rutina activa en vpn-config.json (un escritor: el
popup vpn_config_popup.py). Sin GTK."""
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common

CONFIG = os.path.join(SCRIPT_DIR, "vpn-config.json")
TIMEOUT = 10
FIELDS = ("connect", "disconnect", "status", "connected_text", "interface")

# connected_text: texto que aparece en la salida de `status` solo con la
# VPN conectada (vacío = basta el código de salida 0). interface: si
# existe en /sys/class/net, la barra la toma como conectada sin correr
# `status` (vacío = la barra corre `status`).
BUILTIN = [
    {"id": "protonvpn", "name": "ProtonVPN",
     "connect": "protonvpn connect", "disconnect": "protonvpn disconnect",
     "status": "protonvpn status", "connected_text": "Status: Connected",
     "interface": "proton0"},
    {"id": "mullvad", "name": "Mullvad",
     "connect": "mullvad connect --wait", "disconnect": "mullvad disconnect --wait",
     "status": "mullvad status", "connected_text": "Connected",
     "interface": "wg0-mullvad"},
    {"id": "nordvpn", "name": "NordVPN",
     "connect": "nordvpn connect", "disconnect": "nordvpn disconnect",
     "status": "nordvpn status", "connected_text": "Status: Connected",
     "interface": "nordlynx"},
    {"id": "warp", "name": "Cloudflare WARP",
     "connect": "warp-cli connect", "disconnect": "warp-cli disconnect",
     "status": "warp-cli status", "connected_text": "Connected",
     "interface": "CloudflareWARP"},
    {"id": "expressvpn", "name": "ExpressVPN",
     "connect": "expressvpnctl connect", "disconnect": "expressvpnctl disconnect",
     "status": "expressvpnctl get connectionstate", "connected_text": "Connected",
     "interface": ""},
    {"id": "tailscale", "name": "Tailscale",
     "connect": "tailscale up", "disconnect": "tailscale down",
     "status": "tailscale status --peers=false", "connected_text": "",
     "interface": ""},
    {"id": "nm-wireguard", "name": "WireGuard (NetworkManager)",
     "connect": "nmcli connection up wg0", "disconnect": "nmcli connection down wg0",
     "status": "nmcli -t -f DEVICE connection show --active", "connected_text": "wg0",
     "interface": "wg0"},
    {"id": "nm-openvpn", "name": "OpenVPN (NetworkManager)",
     "connect": "nmcli connection up MiVPN", "disconnect": "nmcli connection down MiVPN",
     "status": "nmcli -t -f NAME,TYPE connection show --active", "connected_text": "MiVPN:vpn",
     "interface": ""},
]
DEFAULT_ID = "protonvpn"


def load():
    try:
        with open(CONFIG) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    return {"active": data.get("active", DEFAULT_ID), "custom": data.get("custom", [])}


def save(data):
    common.atomic_write(CONFIG, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def all_profiles(data=None):
    data = data or load()
    return [dict(p, builtin=True) for p in BUILTIN] + [dict(p, builtin=False) for p in data["custom"]]


def active_profile():
    data = load()
    profiles = all_profiles(data)
    for p in profiles:
        if p["id"] == data["active"]:
            return p
    return profiles[0]


def run(cmd, timeout=TIMEOUT):
    """`sh -c` (admite tuberías y variables). Devuelve CompletedProcess o
    None si el comando no existe/no terminó a tiempo."""
    try:
        return subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def status_connected(profile):
    """Estado real vía el comando `status`; None si no se pudo correr."""
    if not profile["status"].strip():
        return None
    r = run(profile["status"])
    if r is None or r.returncode in (126, 127):  # sh: sin permiso / no existe
        return None
    if profile["connected_text"]:
        return profile["connected_text"] in r.stdout
    return r.returncode == 0


def is_connected(profile, fast=False):
    """fast: con `interface` configurada solo revisa si existe (la barra
    sondea cada pocos segundos)."""
    iface = profile["interface"].strip()
    if fast and iface:
        return os.path.exists(f"/sys/class/net/{iface}")
    return bool(status_connected(profile))

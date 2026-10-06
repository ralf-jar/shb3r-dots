"""Acciones que modifican estado Wifi. Sin pkexec -- NetworkManager ya
autoriza al usuario de la sesión activa vía polkit (confirmado a mano,
mismo comportamiento que services_actions.py con systemctl)."""

import os
import subprocess
import sys

import wifi_state

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from i18n import t

TIMEOUT = 15  # nmcli connect puede tardar negociando con el AP


def _clean_message(stdout, stderr):
    """nmcli reparte su salida: stdout trae el mensaje humano real (ya
    en español, menciona el SSID), stderr solo ruido técnico
    ("Advertencia: ..." con nombres de propiedad internos). Se combinan
    los dos, se descartan líneas "Advertencia:"/"Warning:", se toma la
    PRIMERA línea real -- al revés de bluetooth_actions.py (que toma la
    ÚLTIMA de bluetoothctl), porque acá la humana va primero y el
    resumen técnico al final."""
    combined = "\n".join(s for s in (stdout, stderr) if s)
    lines = [l.strip() for l in combined.strip().splitlines() if l.strip()]
    real_lines = [l for l in lines if not l.lower().startswith(("advertencia:", "warning:"))]
    if real_lines:
        return real_lines[0]
    return lines[0] if lines else t("red", "unknown_error")


def _run(args, timeout=TIMEOUT):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, t("red", "wifi_timeout")
    if r.returncode == 0:
        return True, ""
    return False, _clean_message(r.stdout, r.stderr)


def set_radio(enabled):
    return _run(["nmcli", "radio", "wifi", "on" if enabled else "off"], timeout=8)


def connect(ssid, password=None):
    args = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    return _run(args)


def disconnect():
    device = wifi_state.wifi_device()
    if device is None:
        return False, t("red", "no_wifi_adapter")
    return _run(["nmcli", "device", "disconnect", device], timeout=8)


def rescan():
    # Best-effort -- nmcli devuelve error si se pide de nuevo muy rápido
    # seguido (rate-limit propio de NetworkManager), no es un fallo real,
    # el caller solo lo usa para refrescar la lista después.
    return _run(["nmcli", "device", "wifi", "rescan"], timeout=8)

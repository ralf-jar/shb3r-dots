"""Acciones que modifican estado de Bluetooth. Ver CLAUDE.md, pestaña
"Bluetooth" (gotcha de bluetoothctl colgándose sin timeout propio)."""

import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common

import bluetooth_state

ACTION_TIMEOUT = 15
PAIR_TIMEOUT = 30
POWER_TIMEOUT = 8
SCAN_TIMEOUT = 6


def _run(args, timeout=ACTION_TIMEOUT):
    try:
        r = subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout)
        output = (r.stdout or "") + (r.stderr or "")
        failed = r.returncode != 0 or "not available" in output.lower()
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        failed = True
        if not output.strip():
            output = "Tiempo de espera agotado"

    lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
    message = lines[-1] if lines else ("Listo" if not failed else "Error")
    return not failed, message


def set_powered(powered):
    return _run(["power", "on" if powered else "off"], timeout=POWER_TIMEOUT)


def scan(timeout=SCAN_TIMEOUT):
    """Bloqueante -- pensado para llamarse desde un hilo. `--timeout` hace
    que bluetoothctl salga solo pasado ese tiempo (a diferencia de
    connect/pair, que no tienen esa opción, ver arriba)."""
    return _run(["--timeout", str(timeout), "scan", "on"], timeout=timeout + 5)


def connect(mac):
    return _run(["connect", mac])


def disconnect(mac):
    return _run(["disconnect", mac])


def pair(mac):
    ok, msg = _run(["pair", mac], timeout=PAIR_TIMEOUT)
    if ok:
        # Sin trust, algunos dispositivos vuelven a pedir confirmación
        # tras reconectar solos.
        _run(["trust", mac], timeout=POWER_TIMEOUT)
    return ok, msg


def remove(mac):
    return _run(["remove", mac])


def set_autoconnect(delay, devices):
    """Escritura atómica (temp + os.replace) -- mismo patrón que
    colors.css/rotation.json: bluetooth_autoconnect.py puede estar
    leyendo este archivo al mismo tiempo (arranque de sesión), un
    open(path, "w") directo podría exponerle un archivo truncado a
    mitad de escritura."""
    data = {"delay": delay, "devices": sorted(devices)}
    common.atomic_write(bluetooth_state.AUTOCONNECT_FILE, json.dumps(data, indent=2))

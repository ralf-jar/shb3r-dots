"""
bluetooth_usage.py
Conteo de conexiones por dispositivo -- alimenta el orden de la lista de
la pestaña "Bluetooth" del dashboard (más conectado primero). Sin GTK ni
subprocess: solo lee/escribe waybar/bluetooth-usage.json. Lo llaman los
dos procesos que ya sondean el estado (bluetooth_status.py en la barra
cada 5s y bluetooth_tab.py en el dashboard cada 4s), contando una
conexión cuando un MAC pasa de desconectado a conectado -- así entran
también las reconexiones que no salen del dashboard (los audífonos al
prenderse solos, bluetooth_autoconnect.py al iniciar sesión).
"""

import fcntl
import json
import os
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common

WAYBAR_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "waybar"))
USAGE_FILE = os.path.join(WAYBAR_DIR, "bluetooth-usage.json")
# Archivo aparte para el flock: common.atomic_write reemplaza el inodo del
# JSON (temp + os.replace), un lock sobre el propio JSON se quedaría
# apuntando al inodo viejo. En /tmp porque es efímero, no configuración.
LOCK_FILE = os.path.join(tempfile.gettempdir(), "flipfrog-bluetooth-usage.lock")


def _read():
    """{"counts": {mac: n}, "connected": {macs}} o None si todavía no hay
    historial (primer arranque, archivo borrado o corrupto)."""
    try:
        with open(USAGE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None

    raw_counts = data.get("counts")
    counts = {}
    if isinstance(raw_counts, dict):
        for mac, n in raw_counts.items():
            try:
                counts[str(mac)] = int(n)
            except (TypeError, ValueError):
                continue

    raw_connected = data.get("connected")
    connected = {str(mac) for mac in raw_connected} if isinstance(raw_connected, list) else set()
    return {"counts": counts, "connected": connected}


def _write(counts, connected):
    data = {"counts": dict(sorted(counts.items())), "connected": sorted(connected)}
    common.atomic_write(USAGE_FILE, json.dumps(data, indent=2))


def load_counts():
    """{mac: veces_conectado} para ordenar la lista -- nunca escribe."""
    state = _read()
    return state["counts"] if state else {}


def record_connected(macs):
    """Guarda `macs` (los conectados AHORA) como estado actual e
    incrementa el contador de cada uno que no estaba conectado la última
    vez que corrió. Devuelve los contadores ya actualizados.

    El flock es necesario porque la barra y el dashboard sondean a la vez:
    sin lock los dos leerían el mismo estado viejo, verían la misma
    transición y contarían esa conexión dos veces."""
    macs = set(macs)
    with open(LOCK_FILE, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _read()
        if state is None:
            # Sin historial previo solo se guarda el estado actual -- no se
            # inventa una conexión por cada dispositivo que ya estaba
            # conectado la primera vez que corre esto.
            _write({}, macs)
            return {}

        counts, previous = state["counts"], state["connected"]
        for mac in macs - previous:
            counts[mac] = counts.get(mac, 0) + 1
        if macs != previous:
            _write(counts, macs)
        return counts

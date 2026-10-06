"""
bluetooth_state.py
Lectura de estado de Bluetooth (bluetoothctl show/devices/info) -- sin
efectos secundarios, separado de bluetooth_actions.py (que sí modifica
estado), mismo criterio que network_info.py/ufw_state.py en firewall/.
"""

import json
import os
import subprocess

INFO_TIMEOUT = 5

WAYBAR_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "waybar"))
AUTOCONNECT_FILE = os.path.join(WAYBAR_DIR, "bluetooth-autoconnect.json")
DEFAULT_AUTOCONNECT_DELAY = 10


def adapter_info():
    """None si esta máquina no tiene un adaptador de Bluetooth --
    `bluetoothctl show` sale vacío en ese caso, sin fallar."""
    r = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=INFO_TIMEOUT)
    if not r.stdout.strip():
        return None

    info = {"powered": False, "name": None}
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            info["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Powered:"):
            info["powered"] = line.split(":", 1)[1].strip() == "yes"
    return info


def _parse_device_lines(output):
    devices = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("Device "):
            continue
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        devices.append((parts[1], parts[2]))
    return devices


def _device_macs(*filter_args):
    r = subprocess.run(["bluetoothctl", "devices", *filter_args],
                        capture_output=True, text=True, timeout=INFO_TIMEOUT)
    return {mac for mac, _ in _parse_device_lines(r.stdout)}


def device_icon(mac):
    """Ícono real reportado por BlueZ (ej. "audio-headset", "input-gaming")
    -- ya son nombres de ícono XDG válidos, resuelven directo en el tema
    de íconos del sistema. None si el dispositivo no reporta ninguno
    (típico en BLE sin perfil claro)."""
    r = subprocess.run(["bluetoothctl", "info", mac], capture_output=True, text=True, timeout=INFO_TIMEOUT)
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("Icon:"):
            return line.split(":", 1)[1].strip()
    return None


def connected_count():
    """Solo el conteo de conectados (2 subprocess en total junto con
    adapter_info: show + devices Connected) -- versión liviana de
    get_devices() para el ícono de Waybar (custom/bluetooth,
    bluetooth_status.py), que no necesita nombres/MACs."""
    return len(_device_macs("Connected"))


def get_devices():
    """Todos los dispositivos conocidos por bluetoothctl (emparejados o
    vistos en algún scan anterior), con su estado de pareo/conexión --
    conectados primero, luego emparejados, luego el resto por nombre."""
    r = subprocess.run(["bluetoothctl", "devices"], capture_output=True, text=True, timeout=INFO_TIMEOUT)
    all_devices = _parse_device_lines(r.stdout)
    paired = _device_macs("Paired")
    connected = _device_macs("Connected")

    devices = [
        {"mac": mac, "name": name, "paired": mac in paired, "connected": mac in connected}
        for mac, name in all_devices
    ]
    devices.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))
    return devices


def get_autoconnect():
    """(delay_segundos, {macs}) que bluetooth_autoconnect.py lee al
    iniciar sesión (invocado desde autostart.lua) -- antes era un MAC
    fijo con "sleep 10" hardcodeado ahí; ahora configurable desde la
    pestaña "Bluetooth" del dashboard (bluetooth_tab.py, que además es
    quien escribe este archivo vía bluetooth_actions.set_autoconnect)."""
    try:
        with open(AUTOCONNECT_FILE) as f:
            data = json.load(f)
        delay = float(data.get("delay", DEFAULT_AUTOCONNECT_DELAY))
        devices = set(data.get("devices", []))
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        delay, devices = DEFAULT_AUTOCONNECT_DELAY, set()
    return delay, devices

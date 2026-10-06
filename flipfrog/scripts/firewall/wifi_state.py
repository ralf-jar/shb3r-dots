"""
wifi_state.py
Lectura de estado Wifi para la sección "Wifi" de la pestaña "Red" del
dashboard -- todo vía `nmcli`, sin privilegios (mismo criterio que
network_info.py). wifi_actions.py hace los cambios.
"""

import subprocess

TIMEOUT = 4


def wifi_device():
    """Primer dispositivo TYPE=wifi que reporte nmcli -- no se hardcodea
    el nombre (wlan0 en esta máquina) por si cambia."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) == 2 and parts[1] == "wifi":
            return parts[0]
    return None


def radio_enabled():
    try:
        result = subprocess.run(
            ["nmcli", "radio", "wifi"], capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.stdout.strip() == "enabled"


def list_networks():
    """Una entrada por SSID (la señal más fuerte si se repite, ej. mesh).
    Redes abiertas (sin SECURITY) quedan afuera por completo -- pedido
    explícito del usuario, no debería ser un click conectarse a una red
    pública sin cifrado."""
    if not radio_enabled():
        return []
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    by_ssid = {}
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        ssid, signal, security, in_use = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue  # red oculta sin nombre -- no hay con qué mostrarla
        if not security:
            continue  # red abierta/pública -- nunca se muestra
        try:
            signal = int(signal)
        except ValueError:
            signal = 0
        existing = by_ssid.get(ssid)
        if existing is None or signal > existing["signal"]:
            by_ssid[ssid] = {
                "ssid": ssid,
                "signal": signal,
                "secured": bool(security),
                "connected": in_use == "*",
            }
    return sorted(by_ssid.values(), key=lambda e: (not e["connected"], -e["signal"]))

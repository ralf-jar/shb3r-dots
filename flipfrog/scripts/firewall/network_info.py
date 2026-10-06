"""Info de la conexión activa (pestaña "Red"). Ver CLAUDE.md "Info de
conexión"."""

import json
import os
import subprocess

import psutil


def _default_route():
    try:
        result = subprocess.run(
            ["ip", "-j", "route", "show", "default"],
            capture_output=True, text=True, timeout=2,
        )
        routes = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return None, None
    if not routes:
        return None, None
    return routes[0].get("dev"), routes[0].get("gateway")


def _is_wireless(iface):
    return os.path.isdir(f"/sys/class/net/{iface}/wireless")


def _nmcli_field(output, key):
    prefix = key + ":"
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


def _device_info(iface):
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS,IP4.DNS", "device", "show", iface],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    out = result.stdout
    ip_with_prefix = _nmcli_field(out, "IP4.ADDRESS[1]") or ""
    return {
        "connection_name": _nmcli_field(out, "GENERAL.CONNECTION"),
        "ip": ip_with_prefix.split("/")[0] or None,
        "dns": _nmcli_field(out, "IP4.DNS[1]"),
    }


def _active_wifi():
    """(ssid, signal) de la red wifi con active=yes -- None, None si no
    hay ninguna (conectados por cable, o wifi sin asociar)."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "active,ssid,signal", "dev", "wifi"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, None
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0] == "yes":
            return parts[1], parts[2]
    return None, None


def get_connection_info():
    """None si no hay ruta por defecto (sin conexión real) -- la pestaña
    "Red" lo trata como "Sin conexión" en vez de fingir datos."""
    iface, gateway = _default_route()
    if not iface:
        return None

    info = _device_info(iface)
    stats = psutil.net_if_stats().get(iface)
    wireless = _is_wireless(iface)
    ssid, signal = _active_wifi() if wireless else (None, None)

    return {
        "interface": iface,
        "connection_name": info.get("connection_name") or iface,
        "ip": info.get("ip"),
        "gateway": gateway,
        "dns": info.get("dns"),
        "wireless": wireless,
        "ssid": ssid,
        "signal": signal,
        "is_up": stats.isup if stats else False,
        "speed_mbps": stats.speed if (stats and stats.speed > 0) else None,
    }

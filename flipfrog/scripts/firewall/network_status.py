#!/usr/bin/env python3
"""Ícono de red de la barra -- glyph con el estado real de la conexión
(sin red / cableada / wifi con señal), junto al ícono de Bluetooth
(bar/bar_modules.py). Click abre el dashboard
directo en la pestaña "Red" (dashboard.py, argv[1] == "red"). Sin GTK,
reusa network_info.py -- mismo criterio que bluetooth_status.py."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import network_info

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
from i18n import t

ICON = ""  # nf-fa-wifi (JetBrainsMono Nerd Font)


def status():
    info = network_info.get_connection_info()
    if info is None:
        out = {"text": ICON, "tooltip": t("red", "waybar_tooltip_sin_conexion"), "class": "off"}
    elif info["wireless"] and info["ssid"]:
        tooltip = t("red", "waybar_tooltip_wifi", ssid=info["ssid"], signal=info["signal"] or "?")
        out = {"text": ICON, "tooltip": tooltip, "class": ""}
    else:
        tooltip = t("red", "waybar_tooltip_cableada", interface=info["interface"])
        out = {"text": ICON, "tooltip": tooltip, "class": ""}

    return out


def main():
    print(json.dumps(status(), ensure_ascii=False))


if __name__ == "__main__":
    main()

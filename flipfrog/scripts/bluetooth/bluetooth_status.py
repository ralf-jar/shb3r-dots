#!/usr/bin/env python3
"""Ícono de Bluetooth de la barra -- glyph con el estado real del
adaptador (apagado/encendido/dispositivos conectados), junto al ícono
de sonido (bar/bar_modules.py). Click abre el
dashboard directo en la pestaña "Bluetooth" (dashboard.py, argv[1] ==
"bluetooth"). Sin GTK, reusa bluetooth_state.py -- mismo criterio que
pulseaudio.py."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import bluetooth_state

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
from i18n import t

ICON = ""  # nf-fa-bluetooth (JetBrainsMono Nerd Font)


def status():
    adapter = bluetooth_state.adapter_info()
    if adapter is None:
        out = {"text": ICON, "tooltip": t("bluetooth", "waybar_tooltip_no_adapter"), "class": "off"}
    elif not adapter["powered"]:
        out = {"text": ICON, "tooltip": t("bluetooth", "waybar_tooltip_off"), "class": "off"}
    else:
        n = bluetooth_state.connected_count()
        if n == 0:
            tooltip = t("bluetooth", "waybar_tooltip_on")
        elif n == 1:
            tooltip = t("bluetooth", "waybar_tooltip_connected_one")
        else:
            tooltip = t("bluetooth", "waybar_tooltip_connected_many", n=n)
        out = {"text": ICON, "tooltip": tooltip, "class": ""}

    return out


def main():
    print(json.dumps(status(), ensure_ascii=False))


if __name__ == "__main__":
    main()

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
import bluetooth_usage

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
from i18n import t

ICON = ""  # nf-fa-bluetooth (JetBrainsMono Nerd Font)


def status():
    adapter = bluetooth_state.adapter_info()
    if adapter is None:
        # Con el adaptador apagado/ausente no hay nada conectado: se
        # limpia el estado que usa bluetooth_usage para detectar la
        # próxima transición desconectado -> conectado.
        bluetooth_usage.record_connected(set())
        out = {"text": ICON, "tooltip": t("bluetooth", "waybar_tooltip_no_adapter"), "class": "off"}
    elif not adapter["powered"]:
        bluetooth_usage.record_connected(set())
        out = {"text": ICON, "tooltip": t("bluetooth", "waybar_tooltip_off"), "class": "off"}
    else:
        # Este poll de 5s es el que alimenta el contador de conexiones por
        # dispositivo que ordena la pestaña "Bluetooth" del dashboard --
        # corre aunque el dashboard esté cerrado (bluetooth_tab.py lo
        # repite mientras está abierto; el flock de record_connected evita
        # el doble conteo).
        connected = bluetooth_state.connected_macs()
        bluetooth_usage.record_connected(connected)
        n = len(connected)
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

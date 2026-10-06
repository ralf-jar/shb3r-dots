#!/usr/bin/env python3
"""Indicador de No molestar de la barra, junto al tray
(bar/bar_modules.py). Solo se ve activo. Lee la pausa real de dunst
(también la del modo cine, que no pasa por dnd_state.py). Sin GTK."""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import dnd_state
from i18n import t

ICON = "󰂛"  # nf-md-bell_off


def status():
    if not dnd_state.is_paused():
        return {"text": "", "tooltip": "", "class": "off"}
    return {"text": ICON, "tooltip": t("notificaciones", "dnd_activo"), "class": ""}


def main():
    print(json.dumps(status(), ensure_ascii=False))


if __name__ == "__main__":
    main()

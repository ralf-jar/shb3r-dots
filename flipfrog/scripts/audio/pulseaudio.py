#!/usr/bin/env python3
"""Reemplaza al módulo nativo "pulseaudio" de Waybar -- ese calcula mal
el índice de ícono para un sink Bluetooth bajo PipeWire (confirmado en
vivo: 10/30/70/100% dan el mismo ícono). Lee el volumen directo de
`pactl` en cada intervalo. get_default_sink_name() resuelve vía eq_pw.py
-- ver CLAUDE.md "eq_pw.resolve_default_sink_name()"."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
try:
    import eq_pw
except ImportError:
    eq_pw = None

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
from i18n import t

ICONS = ["", "", ""]  # sin ondas, media, alta
MUTED_TEXT = ICONS[0] + "X"


def get_default_sink_name():
    if eq_pw is not None:
        resolved = eq_pw.resolve_default_sink_name()
        if resolved:
            return resolved
    r = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=2)
    return r.stdout.strip()


def get_sink_info(name):
    r = subprocess.run(["pactl", "-f", "json", "list", "sinks"], capture_output=True, text=True, timeout=2)
    for sink in json.loads(r.stdout):
        if sink["name"] == name:
            return sink
    return None


def pick_icon(volume):
    n = len(ICONS)
    idx = min(n - 1, int(volume / 100 * n))
    return ICONS[idx]


def status():
    sink = get_sink_info(get_default_sink_name())
    if sink is None:
        out = {"text": ICONS[0], "tooltip": t("sonido", "sin_salida_tooltip")}
    else:
        volume = round(next(iter(sink["volume"].values()))["value"] / 65536 * 100)
        muted = sink["mute"]
        desc = sink["description"]
        out = {
            "text": MUTED_TEXT if muted else pick_icon(volume),
            "tooltip": f"{volume}% - {desc}",
            "class": "muted" if muted else "",
            "percentage": volume,
        }

    return out


def main():
    print(json.dumps(status(), ensure_ascii=False))


if __name__ == "__main__":
    main()

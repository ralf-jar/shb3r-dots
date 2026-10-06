#!/usr/bin/env python3
"""Indicador de audiolibro de la barra, junto al tray (bar/bar_modules.py).
Solo se ve mientras suena (no en pausa). Sin daemon vivo no abre el socket.
Sin GTK."""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import audiobook_ipc
from i18n import t

ICON = "󱓷"  # nf-md-book_open_variant
OFF = {"text": "", "tooltip": "", "class": "off"}


def status():
    if audiobook_ipc.daemon_pid() is None:
        return OFF
    state = audiobook_ipc.send_command("get_state", timeout=1) or {}
    player = state.get("player")
    if not player or player.get("paused"):
        return OFF
    title = player.get("title")
    return {"text": ICON, "class": "",
            "tooltip": t("audiolibros", "reproduciendo", title=title) if title else ""}


def main():
    print(json.dumps(status(), ensure_ascii=False))


if __name__ == "__main__":
    main()

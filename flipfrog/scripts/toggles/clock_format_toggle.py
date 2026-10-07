#!/usr/bin/env python3
"""Toggle "Reloj extendido" del dashboard: alterna custom/clock entre
compacto y extendido reescribiendo waybar/clock-format.json (ver
CLAUDE.md "Reloj")."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
from i18n import t
from waybar_lib import build_switch_row, svg_icon_image

ICON = os.path.join(SCRIPT_DIR, "icons", "clock_format.svg")
STATE_FILE = os.path.expanduser("~/.config/waybar/clock-format.json")


def clock_extended_enabled():
    try:
        with open(STATE_FILE) as f:
            return bool(json.load(f).get("extended", True))
    except (FileNotFoundError, json.JSONDecodeError):
        return True


def set_clock_extended(enabled):
    common.atomic_write(STATE_FILE, json.dumps({"extended": enabled}))


def build_clock_format_toggle(row):
    icon = svg_icon_image(ICON)

    def on_toggle(state):
        common.run_async(lambda: set_clock_extended(state))

    build_switch_row(row, icon, t("sistema", "reloj_extendido"), clock_extended_enabled(), on_toggle)

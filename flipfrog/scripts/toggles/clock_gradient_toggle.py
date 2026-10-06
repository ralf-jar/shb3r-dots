#!/usr/bin/env python3
"""Toggle "Degradado en Reloj" del dashboard: prende/apaga el degradado
de custom/clock reescribiendo waybar/clock-gradient.json (ver CLAUDE.md
"Reloj")."""

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

ICON = os.path.join(SCRIPT_DIR, "icons", "clock_gradient.svg")
STATE_FILE = os.path.expanduser("~/.config/waybar/clock-gradient.json")


def clock_gradient_enabled():
    try:
        with open(STATE_FILE) as f:
            return bool(json.load(f).get("enabled", False))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def set_clock_gradient(enabled):
    common.atomic_write(STATE_FILE, json.dumps({"enabled": enabled}))


def build_clock_gradient_toggle(row):
    icon = svg_icon_image(ICON)

    def on_toggle(state):
        common.run_async(lambda: set_clock_gradient(state))

    build_switch_row(row, icon, t("sistema", "reloj_degradado"), clock_gradient_enabled(), on_toggle)

#!/usr/bin/env python3
"""Toggle "No molestar" del dashboard -- persistente entre reinicios
(notifications/dnd_state.py)."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "notifications"))
import common
import dnd_state
from i18n import t
from waybar_lib import build_switch_row, svg_icon_image

ICON = os.path.join(SCRIPT_DIR, "icons", "dnd.svg")


def build_dnd_toggle(row):
    icon = svg_icon_image(ICON)

    def on_toggle(state):
        common.run_async(lambda: dnd_state.set_paused(state))

    build_switch_row(row, icon, t("sistema", "no_molestar"), dnd_state.is_paused(), on_toggle)

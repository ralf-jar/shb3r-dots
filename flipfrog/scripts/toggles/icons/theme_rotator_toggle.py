#!/usr/bin/env python3

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
from i18n import t
from waybar_lib import build_switch_row, svg_icon_image, suppress_close

ICON = os.path.join(SCRIPT_DIR, "icons", "blur.svg")
THEMER_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "themer"))
STATE_FILE = os.path.join(THEMER_DIR, "blur-enabled.json")


def blur_enabled():
    try:
        with open(STATE_FILE) as f:
            return bool(json.load(f).get("enabled", True))
    except (FileNotFoundError, json.JSONDecodeError):
        return True


def set_blur(enabled):
    common.atomic_write(STATE_FILE, json.dumps({"enabled": enabled}))

    suppress_close(2.5)
    subprocess.run(["hyprctl", "reload"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build_blur_toggle(row):
    icon = svg_icon_image(ICON)

    def on_toggle(state):
        common.run_async(lambda: set_blur(state))

    build_switch_row(row, icon, t("sistema", "blur"), blur_enabled(), on_toggle)

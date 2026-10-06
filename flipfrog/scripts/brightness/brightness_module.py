#!/usr/bin/env python3
"""
brightness_module.py
Módulo "Brillo" embebido en el dashboard (build_brightness) -- separado
de brightness.py (lógica de bajo nivel get_brightness/set_brightness +
su propio popup standalone, run_popup) para que dashboard.py no tenga
que cargar UI ajena a él. Mismo patrón para theme/theme_module.py y los
archivos de toggles/ -- ver CLAUDE.md.
"""

import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from brightness import get_brightness, set_brightness

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from waybar_lib import load_module_css
from i18n import t

MODULE_CSS = os.path.join(SCRIPT_DIR, "brightness_module.css")


def build_brightness(container):
    load_module_css(MODULE_CSS)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.get_style_context().add_class("module")

    title = Gtk.Label(label=t("brillo", "titulo"))
    title.get_style_context().add_class("module-title")
    title.set_halign(Gtk.Align.START)
    box.pack_start(title, False, False, 0)

    brightness = get_brightness()
    pct_label = Gtk.Label(label=f"{brightness}%")
    pct_label.get_style_context().add_class("module-pct")
    pct_label.set_halign(Gtk.Align.END)
    box.pack_start(pct_label, False, False, 0)

    adj = Gtk.Adjustment(value=brightness, lower=10, upper=90,
                          step_increment=10, page_increment=10, page_size=0)
    slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
    slider.set_draw_value(False)
    slider.set_hexpand(True)

    def on_release(s, event):
        val = int(s.get_value())
        set_brightness(val)
        pct_label.set_text(f"{val}%")

    slider.connect("button-release-event", on_release)
    box.pack_start(slider, False, False, 0)

    # fill=True, no solo expand -- sin esto la tarjeta se queda en su
    # alto natural en vez de ocupar el slot de top_row ya estirado al
    # alto del módulo vecino (actualizaciones).
    container.pack_start(box, True, True, 0)

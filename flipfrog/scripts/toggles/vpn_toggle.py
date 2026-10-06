#!/usr/bin/env python3
"""Toggle "VPN" del dashboard: conectar/desconectar con la rutina activa
(vpn/vpn_profiles.py). Caso async de build_switch_row (ver CLAUDE.md
"Fila de toggle") -- el comando de estado tarda y "Sistema" no es una pestaña lazy, así que resuelve en
un hilo aparte. connect/disconnect van por vpn_connect.py en proceso
propio: puede tardar negociando servidor y avisa el resultado con
notify-send aunque el dashboard ya se haya cerrado."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import os
import subprocess
import sys
import threading

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "vpn"))
import common
import vpn_profiles
from i18n import t
from waybar_lib import build_switch_row, svg_icon_image

ICON = os.path.join(SCRIPT_DIR, "icons", "protonvpn.svg")


def vpn_is_connected():
    return vpn_profiles.is_connected(vpn_profiles.active_profile())


def set_vpn_connected(connected):
    subprocess.Popen(
        [sys.executable, os.path.join(SCRIPT_DIR, "vpn_connect.py"),
         "connect" if connected else "disconnect"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def build_vpn_toggle(row):
    icon = svg_icon_image(ICON)

    def on_toggle(state):
        common.run_async(lambda: set_vpn_connected(state))

    wrapper, switch, handler_id = build_switch_row(row, icon, t("sistema", "vpn"), False, on_toggle)
    switch.set_sensitive(False)
    # no_show_all -- si no, el show_all() de dashboard.py (corre después,
    # antes de Gtk.main()) revierte este hide() y el switch queda visible
    # igual mostrando "off" mientras se resuelve el estado real.
    switch.set_no_show_all(True)
    switch.hide()

    spinner = Gtk.Spinner()
    spinner.set_valign(Gtk.Align.CENTER)
    spinner.get_style_context().add_class("toggle-row-spinner")
    switch_pos = wrapper.get_children().index(switch)
    wrapper.pack_start(spinner, False, False, 0)
    wrapper.reorder_child(spinner, switch_pos)
    spinner.start()
    spinner.show()

    def _apply(connected):
        spinner.stop()
        spinner.destroy()
        switch.handler_block(handler_id)
        switch.set_active(connected)
        switch.handler_unblock(handler_id)
        switch.set_sensitive(True)
        switch.set_no_show_all(False)
        switch.show()
        return False  # GLib.idle_add: correr una sola vez

    def _resolve():
        connected = vpn_is_connected()
        GLib.idle_add(_apply, connected)

    threading.Thread(target=_resolve, daemon=True).start()

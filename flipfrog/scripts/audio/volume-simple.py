#!/usr/bin/env python3
"""Popup de volumen master para Waybar. Sink real resuelto vía eq_pw.py
(import opcional) -- ver CLAUDE.md "eq_pw.resolve_default_sink_name()"."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
import subprocess
import re
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_near_cursor
from i18n import t

SCRIPT_DIR  = os.path.dirname(os.path.realpath(__file__))
CSS_FILE    = os.path.join(SCRIPT_DIR, "volume-simple.css")
LOCK        = "/tmp/volume-simple.pid"

sys.path.insert(0, SCRIPT_DIR)
try:
    import eq_pw
except ImportError:
    eq_pw = None

def _target_sink():
    if eq_pw is not None:
        resolved = eq_pw.resolve_default_sink_name()
        if resolved:
            return resolved
    return "@DEFAULT_SINK@"

def get_volume():
    try:
        r = subprocess.run(["pactl", "get-sink-volume", _target_sink()],
                           capture_output=True, text=True)
        m = re.search(r"(\d+)%", r.stdout)
        return int(m.group(1)) if m else 50
    except Exception:
        return 50

def get_muted():
    try:
        r = subprocess.run(["pactl", "get-sink-mute", _target_sink()],
                           capture_output=True, text=True)
        return "yes" in r.stdout
    except Exception:
        return False

def set_volume(vol):
    subprocess.run(["pactl", "set-sink-volume", _target_sink(), f"{int(vol)}%"],
                   capture_output=True)

class VolumePopup:
    def __init__(self):
        self.window, container = build_layer_window("volume-simple", CSS_FILE)
        container.set_name("container")
        position_near_cursor(container)

        header = Gtk.Label(label=t("sonido", "volumen_header"))
        header.set_name("header")
        header.set_halign(Gtk.Align.CENTER)
        container.pack_start(header, False, False, 0)

        vol   = get_volume()
        muted = get_muted()
        self.pct_label = Gtk.Label(label=self._pct_text(vol, muted))
        self.pct_label.set_name("label-pct")
        self.pct_label.set_halign(Gtk.Align.END)
        container.pack_start(self.pct_label, False, False, 0)

        adj = Gtk.Adjustment(value=vol, lower=0, upper=100,
                             step_increment=5, page_increment=5, page_size=0)
        self.slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                adjustment=adj)
        self.slider.set_draw_value(False)
        self.slider.set_hexpand(True)
        self.slider.connect("value-changed", self.on_volume_changed)
        container.pack_start(self.slider, False, False, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

    def _pct_text(self, vol, muted):
        return f"🔇 {t('sonido', 'silenciado')}" if muted else f"{vol}%"

    def on_volume_changed(self, slider):
        vol = int(slider.get_value())
        set_volume(vol)
        self.pct_label.set_text(self._pct_text(vol, get_muted()))

    def _on_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    VolumePopup()
    Gtk.main()


if __name__ == "__main__":
    main()

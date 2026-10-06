#!/usr/bin/env python3
"""Selector de salida de audio para Waybar. Excluye el sink del
ecualizador de la lista -- ver CLAUDE.md "eq_pw.resolve_default_sink_name()"."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk
import subprocess
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_near_cursor
from i18n import t

SCRIPT_DIR  = os.path.dirname(os.path.realpath(__file__))
CSS_FILE    = os.path.join(SCRIPT_DIR, "sink-selector.css")
LOCK        = "/tmp/sink-selector.pid"

sys.path.insert(0, SCRIPT_DIR)
import eq_pw
import audio_devices

def get_default_sink_name():
    return eq_pw.resolve_default_sink_name() or ""

def set_default_sink(name):
    subprocess.run(["pactl", "set-default-sink", name])
    # Mueve los inputs activos a la nueva salida
    r = subprocess.run(["pactl", "list", "short", "sink-inputs"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.strip():
            inp_id = line.split()[0]
            subprocess.run(["pactl", "move-sink-input", inp_id, name])

class SinkSelectorPopup:
    def __init__(self):
        self.window, container = build_layer_window("sink-selector", CSS_FILE)
        container.set_name("container")
        position_near_cursor(container)

        header = Gtk.Label(label=t("sonido", "salidas_header"))
        header.set_name("header")
        header.set_halign(Gtk.Align.CENTER)
        container.pack_start(header, False, False, 0)

        # Generar un radiobutton por cada Sink, con el actual ya seleccionado
        sinks = audio_devices.list_sinks()
        current = get_default_sink_name()

        radios = []
        group = None
        for sink in sinks:
            desc = audio_devices.display_name(sink["name"], sink["description"])
            desc = desc[:25] + "…" if len(desc) > 25 else desc
            radio = Gtk.RadioButton.new_with_label_from_widget(group, desc)
            group = group or radio
            radio.set_halign(Gtk.Align.FILL)
            radio.get_child().set_halign(Gtk.Align.START)
            container.pack_start(radio, False, False, 0)
            radios.append((radio, sink["name"]))

        for radio, name in radios:
            if name == current:
                radio.set_active(True)
                break

        # Conectamos "toggled" después de fijar la selección inicial, para
        # que ese set_active(True) no dispare un cambio de sink al abrir.
        for radio, name in radios:
            radio.connect("toggled", self._on_sink_toggled, name)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

    def _on_sink_toggled(self, radio, name):
        if radio.get_active():
            set_default_sink(name)
            self.window.destroy()

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
    SinkSelectorPopup()
    Gtk.main()

if __name__ == "__main__":
    main()

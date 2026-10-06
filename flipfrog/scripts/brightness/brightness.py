#!/usr/bin/env python3
"""
brightness.py
Popup de brillo para Waybar + modo --status para el módulo custom (JSON).
El CSS se carga desde brightness.css en la misma carpeta.
"""

import json
import os
import subprocess
import sys

CACHE = os.path.expanduser("~/.cache/brightness_level")
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
from i18n import t

CSS_FILE = os.path.join(SCRIPT_DIR, "brightness.css")
LOCK = "/tmp/brightness.pid"
BUS0 = 4
BUS1 = 5


def read_cache():
    if not os.path.exists(CACHE):
        with open(CACHE, "w") as f:
            f.write("50")
    with open(CACHE) as f:
        return f.read().strip() or "50"


def print_status():
    current = read_cache()
    print(json.dumps({"text": "󰃟 ", "tooltip": t("brillo", "waybar_tooltip", pct=current)}))


def get_brightness():
    try:
        r = subprocess.run(
            ["ddcutil", "--bus", f"{int(BUS0)}", "--brief", "getvcp", "10"],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            print(f"ddcutil error: {r.stderr}", file=sys.stderr)
            return 50
        parts = r.stdout.strip().split()
        if len(parts) >= 4:
            return int(parts[3])
        return 50
    except Exception as e:
        print(f"Exception en get_brightness: {e}", file=sys.stderr)
        return 50


def set_brightness(brightness):
    subprocess.run(["ddcutil", "--bus", f"{int(BUS0)}", "--noverify", "--sleep-multiplier", "0.1", "setvcp", "10", f"{int(brightness)}"], capture_output=True)
    subprocess.run(["ddcutil", "--bus", f"{int(BUS1)}", "--noverify", "--sleep-multiplier", "0.1", "setvcp", "10", f"{int(brightness)}"], capture_output=True)

    with open(CACHE, "w") as f:
        f.write(str(int(brightness)))


# ---------- Ventana (import perezoso: no se necesita para --status) ----------

def run_popup():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, Gdk
    from waybar_lib import kill_group, kill_existing, build_layer_window, position_near_cursor

    class BrightnessPopup:
        def __init__(self):
            self.window, container = build_layer_window("brightness", CSS_FILE)
            container.set_name("container")
            position_near_cursor(container)

            title = Gtk.Label(label=t("brillo", "titulo"))
            title.set_name("header")
            title.set_halign(Gtk.Align.CENTER)
            container.pack_start(title, False, False, 0)

            brightness = get_brightness()
            self.pct_label = Gtk.Label(label=self._pct_text(brightness))
            self.pct_label.set_name("label-pct")
            self.pct_label.set_halign(Gtk.Align.END)
            container.pack_start(self.pct_label, False, False, 0)

            adj = Gtk.Adjustment(value=brightness, lower=10, upper=90,
                                 step_increment=10, page_increment=10, page_size=0)
            self.slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL,
                                    adjustment=adj)
            self.slider.set_draw_value(False)
            self.slider.set_hexpand(True)
            self.slider.connect("button-release-event", self.on_brightness_changed)
            container.pack_start(self.slider, False, False, 0)

            self.window.connect("key-press-event", self._on_key)
            self.window.connect("destroy", self._on_destroy)
            self.window.show_all()

        def _pct_text(self, brightness):
            return f"{str(brightness) + '%'}"

        def on_brightness_changed(self, slider, event):
            brightness = int(slider.get_value())
            set_brightness(brightness)
            self.pct_label.set_text(self._pct_text(brightness))

        def _on_key(self, _, event):
            if event.keyval == Gdk.KEY_Escape:
                self.window.destroy()

        def _on_destroy(self, *_):
            if os.path.exists(LOCK):
                os.remove(LOCK)
            Gtk.main_quit()

    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    BrightnessPopup()
    Gtk.main()


def main():
    if "--status" in sys.argv:
        print_status()
        return
    run_popup()


if __name__ == "__main__":
    main()

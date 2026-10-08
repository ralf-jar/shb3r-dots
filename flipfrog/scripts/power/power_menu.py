#!/usr/bin/env python3
"""Menú de energía (ícono de power de la barra): apagar / reiniciar /
cerrar sesión. Una
superficie layer-shell OVERLAY por monitor, todas con fondo
@mybackground + blur (regla "power-menu" en windowrules.lua); los
botones solo en el monitor con foco. Click afuera o Esc cierra, S/R/C
apagan/reinician/cierran sesión."""

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GdkPixbuf, Gio, GtkLayerShell

import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from waybar_lib import kill_group, kill_existing, load_module_css
from common import theme_color_hex
from i18n import t

LOCK = "/tmp/power-menu.pid"
NAMESPACE = "power-menu"
CSS_FILE = os.path.join(SCRIPT_DIR, "power_menu.css")
ICONS_DIR = os.path.join(SCRIPT_DIR, "icons")
ICON_SIZE = 64

# Cerrar sesión: lo mismo que el SUPER+M de la config de fábrica de
# Hyprland -- hyprshutdown (si está) cierra las apps con calma antes de
# salir; si no, hl.dsp.exit() directo.
LOGOUT = "command -v hyprshutdown >/dev/null 2>&1 && hyprshutdown || hyprctl dispatch 'hl.dsp.exit()'"

ACTIONS = [
    ("shutdown", "system-shutdown.svg", "myforegroundhover", ["systemctl", "poweroff"], Gdk.KEY_s),
    ("reboot", "system-reboot.svg", "myforegroundhover2", ["systemctl", "reboot"], Gdk.KEY_r),
    ("logout", "system-log-out.svg", "myforeground", ["sh", "-c", LOGOUT], Gdk.KEY_c),
]


def focused_monitor_origin():
    try:
        out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=2).stdout
        mon = next(m for m in json.loads(out) if m.get("focused"))
        return mon["x"], mon["y"]
    except (OSError, subprocess.TimeoutExpired, ValueError, StopIteration, KeyError):
        return None


def icon_image(file_name, color_name, scale):
    """SVG recoloreado y rasterizado a `scale` -- nítido en monitores
    con escala > 1 (svg_icon_image de waybar_lib rasteriza a 1x)."""
    with open(os.path.join(ICONS_DIR, file_name), encoding="utf-8") as f:
        svg = f.read().replace("#000000", theme_color_hex(color_name))

    stream = Gio.MemoryInputStream.new_from_data(svg.encode("utf-8"))
    pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, ICON_SIZE * scale, ICON_SIZE * scale, True)
    surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, None)
    return Gtk.Image.new_from_surface(surface)


class PowerMenu:
    def __init__(self):
        load_module_css(CSS_FILE)
        self.windows = []
        self.closing = False

        display = Gdk.Display.get_default()
        monitors = [display.get_monitor(i) for i in range(display.get_n_monitors())]
        origin = focused_monitor_origin()
        focused = next((m for m in monitors
                        if (m.get_geometry().x, m.get_geometry().y) == origin), monitors[0])

        for monitor in monitors:
            self.windows.append(self._build_window(monitor, monitor is focused))

        for window in self.windows:
            window.show_all()

    def _build_window(self, monitor, with_buttons):
        window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        window.set_name("power-menu")
        window.set_decorated(False)

        GtkLayerShell.init_for_window(window)
        GtkLayerShell.set_namespace(window, NAMESPACE)
        GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_monitor(window, monitor)
        GtkLayerShell.set_exclusive_zone(window, -1)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(window, edge, True)

        background = Gtk.EventBox()
        background.connect("button-press-event", lambda *_: self.close())
        window.add(background)

        if with_buttons:
            GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.EXCLUSIVE)
            window.connect("key-press-event", self._on_key)
            background.add(self._build_buttons(monitor.get_scale_factor()))

        window.connect("destroy", lambda *_: self.close())
        return window

    def _build_buttons(self, scale):
        anchor = Gtk.EventBox()
        anchor.set_halign(Gtk.Align.CENTER)
        anchor.set_valign(Gtk.Align.CENTER)
        anchor.connect("button-press-event", lambda *_: True)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_name("power-row")
        anchor.add(row)

        for code, icon, color, command, _key in ACTIONS:
            button = Gtk.Button()
            button.get_style_context().add_class("power-button")
            button.get_style_context().add_class(f"power-{code}")
            button.set_relief(Gtk.ReliefStyle.NONE)

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            content.set_valign(Gtk.Align.CENTER)
            content.pack_start(icon_image(icon, color, scale), False, False, 0)

            label = Gtk.Label(label=t("energia", code))
            label.get_style_context().add_class("power-label")
            content.pack_start(label, False, False, 0)

            button.add(content)
            button.connect("clicked", lambda _b, cmd=command: self.run(cmd))
            row.pack_start(button, False, False, 0)

        return anchor

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True

        for _code, _icon, _color, command, key in ACTIONS:
            if Gdk.keyval_to_lower(event.keyval) == key:
                self.run(command)
                return True
        return False

    def run(self, command):
        subprocess.Popen(command, start_new_session=True)
        self.close()

    def close(self):
        if self.closing:
            return
        self.closing = True
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))

    PowerMenu()
    Gtk.main()


if __name__ == "__main__":
    main()

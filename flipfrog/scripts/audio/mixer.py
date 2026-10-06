#!/usr/bin/env python3
"""
mixer.py
Mezclador de audio por aplicación para Waybar.
El CSS se carga desde mixer.css en la misma carpeta.
"""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import subprocess
import re
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_near_cursor
from i18n import t

SCRIPT_DIR  = os.path.dirname(os.path.realpath(__file__))
CSS_FILE    = os.path.join(SCRIPT_DIR, "mixer.css")
LOCK        = "/tmp/volume-mixer.pid"

IGNORED_APPS = [
    "mpvpaper",
    "mpv",
    "equalizer sink",  # ver CLAUDE.md, "Pestaña Sonido"
]

APP_NAMES = {
    "vesktop":  "Vesktop",
    "electron": "Electron",
    "firefox":  "Firefox",
    "spotify":  "Spotify",
    "bandcamp-radio": "Bandcamp Radio",
}

TIMEOUT = 4  # cota para pactl/hyprctl/ps -- sin esto un colgado se lleva
             # el hilo de refresh entero (ver _refresh)

ICON_NAMES = {
    "vesktop":   "discord",
    "zen-bin":   "zen-browser",
    "firefox":   "firefox",
    "chromium":  "discord",
    "Chromium":  "discord",
    "spotify":   "spotify",
    "bandcamp-radio": "mpv",
    "mpv":       "mpv",
    "steam":     "steam",
}

def get_window_title_for_pid(pid):
    import json
    try:
        r = subprocess.run(["hyprctl", "clients", "-j"],
                            capture_output=True, text=True, timeout=TIMEOUT)
        clients = json.loads(r.stdout)
        # Primero intenta PID exacto
        for c in clients:
            if str(c.get("pid")) == str(pid):
                return c.get("title") or c.get("class")
        # Si no encuentra, busca por PID en el árbol de procesos
        # (proceso padre del renderer)
        ppid_r = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
        ppid = ppid_r.stdout.strip()
        if ppid:
            for c in clients:
                if str(c.get("pid")) == ppid:
                    return c.get("title") or c.get("class")
    except Exception:
        pass
    return None

def get_sink_inputs():
    try:
        r = subprocess.run(["pactl", "list", "sink-inputs"],
                           capture_output=True, text=True, timeout=TIMEOUT)
    except Exception:
        return []

    inputs  = []
    current = {}

    for line in r.stdout.splitlines():
        line = line.strip()
        m = re.match(r"Sink Input #(\d+)", line)
        if m:
            if current.get("index") is not None:
                inputs.append(current)
            current = {"index": int(m.group(1)), "name": None,
                       "volume": 100, "corked": False}
            continue
        if "Corked: yes" in line:
            current["corked"] = True
        m = re.search(r"Volume:.*?(\d+)%", line)
        if m:
            current["volume"] = int(m.group(1))
        for key in ("application.name", "application.process.binary", "media.name"):
            m = re.search(rf'{key} = "([^"]+)"', line)
            if m and current.get("name") is None:
                current["name"] = m.group(1)

        m = re.match(r'application\.process\.id\s*=\s*"(\d+)"', line)
        if m:
            current["pid"] = m.group(1)

    if current.get("index") is not None:
        inputs.append(current)

    active = [i for i in inputs if not i["corked"]]
    result = active if active else inputs

    return [i for i in result if (i["name"] or "").lower() not in IGNORED_APPS]


def set_input_volume(index, vol):
    subprocess.run(["pactl", "set-sink-input-volume", str(index), f"{int(vol)}%"],
                   capture_output=True)

class MixerPopup:
    def __init__(self):
        self.window, self._box = build_layer_window("volume-mixer", CSS_FILE)
        self._box.set_name("container")
        position_near_cursor(self._box)

        header = Gtk.Label(label=t("sonido", "mixer_header"))
        header.set_name("header")
        header.set_halign(Gtk.Align.CENTER)
        self._box.pack_start(header, False, False, 0)

        self._rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._box.pack_start(self._rows_box, False, False, 0)
        self._sliders = {}

        self._refresh_in_flight = False
        self._build_rows()
        GLib.timeout_add(2000, self._refresh)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

    def _build_rows(self, inputs=None):
        for child in self._rows_box.get_children():
            self._rows_box.remove(child)
        self._sliders.clear()

        if inputs is None:
            inputs = get_sink_inputs()
        if not inputs:
            lbl = Gtk.Label(label=t("sonido", "sin_apps"))
            lbl.set_name("no-apps")
            lbl.set_halign(Gtk.Align.CENTER)
            self._rows_box.pack_start(lbl, False, False, 0)
            return

        # Agrupa streams que resuelven al mismo nombre mostrado (p.ej. dos
        # pestañas de Firefox reproduciendo audio a la vez) en una sola
        # fila con un slider que mueve el volumen de todos juntos.
        groups = {}
        order = []
        for inp in inputs:
            name = inp.get("display_name") or self._display_name(inp)
            if name not in groups:
                groups[name] = []
                order.append(name)
            groups[name].append(inp)

        for name in order:
            self._add_row(name, groups[name])

    def _display_name(self, inp):
        raw_name = inp["name"] or ""
        return APP_NAMES.get(raw_name.lower(), None) \
               or get_window_title_for_pid(inp.get("pid")) \
               or raw_name \
               or f"stream {inp['index']}"

    def _add_row(self, name, group):
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        wrap.get_style_context().add_class("app-row")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        first = group[0]
        raw_name = first["name"] or ""
        raw_binary = first.get("binary") or ""

        display_name = name[:25] + "…" if len(name) > 25 else name
        if len(group) > 1:
            display_name = f"{display_name} ×{len(group)}"

        icon_name = ICON_NAMES.get(raw_binary.lower()) or ICON_NAMES.get(raw_name.lower()) or "audio-x-generic"

        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
        icon.get_style_context().add_class("app-icon")
        top.pack_start(icon, False, False, 0)

        name_label = Gtk.Label(label=display_name)
        name_label.get_style_context().add_class("app-name")
        name_label.set_halign(Gtk.Align.START)
        name_label.set_hexpand(True)
        name_label.set_ellipsize(3)

        avg_vol = round(sum(i["volume"] for i in group) / len(group))
        pct_label = Gtk.Label(label=f"{avg_vol}%")
        pct_label.get_style_context().add_class("app-pct")
        pct_label.set_halign(Gtk.Align.END)

        top.pack_start(name_label, True, True, 0)
        top.pack_start(pct_label, False, False, 0)
        wrap.pack_start(top, False, False, 0)

        adj = Gtk.Adjustment(value=avg_vol, lower=0, upper=100,
                             step_increment=5, page_increment=5, page_size=0)
        slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        slider.set_draw_value(False)
        slider.set_hexpand(True)

        indices = [i["index"] for i in group]
        slider.connect("value-changed",
                       lambda s, idxs=indices, l=pct_label: self._on_vol(s, idxs, l))
        wrap.pack_start(slider, False, False, 0)

        self._rows_box.pack_start(wrap, False, False, 0)
        self._sliders[name] = (slider, pct_label, indices)

    def _on_vol(self, slider, indices, pct_label):
        vol = int(slider.get_value())
        for index in indices:
            set_input_volume(index, vol)
        pct_label.set_text(f"{vol}%")

    def _refresh(self):
        """Antes corría get_sink_inputs()/get_window_title_for_pid()
        (pactl + hyprctl + ps, sin timeout) directo en el hilo de GTK
        cada 2s -- un colgado en cualquiera de esos tres se llevaba el
        popup entero. Ahora el fetch va a un hilo aparte; _apply_refresh
        es lo único que toca widgets, vía GLib.idle_add."""
        if self._refresh_in_flight:
            return True
        self._refresh_in_flight = True

        def worker():
            inputs = get_sink_inputs()
            for inp in inputs:
                inp["display_name"] = self._display_name(inp)
            GLib.idle_add(self._apply_refresh, inputs)

        threading.Thread(target=worker, daemon=True).start()
        return True  # GLib.timeout_add: seguir repitiendo

    def _apply_refresh(self, inputs):
        self._refresh_in_flight = False
        current_idxs = set()
        for _, _, idxs in self._sliders.values():
            current_idxs.update(idxs)
        live_idxs = {i["index"] for i in inputs}
        if current_idxs != live_idxs:
            self._build_rows(inputs)
            self.window.show_all()
        return False  # GLib.idle_add: correr una sola vez

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
    MixerPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

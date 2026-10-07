#!/usr/bin/env python3
"""Barra propia de flipfrog (reemplazo de Waybar). Una ventana
layer-shell por monitor; el estilo (bar.css + tema) y la estructura
(bar-settings.json, toggles del dashboard) se recargan en vivo al
cambiar los archivos, sin reiniciar el proceso. SIGUSR1 oculta/muestra
(SUPER+W). Cambiar de idioma relanza el proceso (los textos se resuelven
al importar).

--test: debajo de Waybar y sin zona exclusiva, para probar conviviendo."""

import argparse
import os
import signal
import sys
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, GtkLayerShell, Gdk, Gio, GLib, GLibUnix

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
import bar_hypr
import bar_modules
import bar_settings
import bar_tray
import bar_workspaces

LOCK = "/tmp/ff-bar.pid"
NAMESPACE = "ff-bar"
CSS_FILE = os.path.join(SCRIPT_DIR, "bar.css")
THEMER_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "themer"))
LANGUAGE_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "waybar"))
WATCH_DIRS = (SCRIPT_DIR, THEMER_DIR, LANGUAGE_DIR)
SETTINGS_FILE = os.path.basename(bar_settings.SETTINGS_FILE)
LANGUAGE_FILE = "language.json"

MARGIN_TOP = 5
CAPSULA_HEIGHT = 29
ISLAS_HEIGHT = 33
FULL_WIDTH_HEIGHT = 26
TEST_MARGIN_TOP = 5
SPACING = 4
RELOAD_DEBOUNCE_MS = 150
REPLACE_WAIT_S = 3


def _replace_running():
    """Espera a que la barra anterior muera antes de seguir: si el nombre
    del StatusNotifierWatcher pasa directo de un dueño a otro, las apps
    Qt (ZapZap, Stremio) no se vuelven a registrar -- solo reaccionan
    cuando aparece desde cero."""
    try:
        with open(LOCK) as f:
            pid = int(f.read().strip())
        if pid != os.getpid():
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + REPLACE_WAIT_S
            while time.monotonic() < deadline:
                os.kill(pid, 0)
                time.sleep(0.05)
    except (OSError, ValueError):
        pass
    common.atomic_write(LOCK, str(os.getpid()))


def _add_class(widget, name):
    widget.get_style_context().add_class(name)
    return widget


class BarWindow(Gtk.Window):
    def __init__(self, monitor, hypr, tray, test):
        super().__init__()
        self.set_name("bar-window")
        self._monitor = monitor
        self._hypr = hypr
        self._tray = tray
        self._test = test

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, NAMESPACE)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_monitor(self, monitor)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, edge, True)
        self._exclusive_margin = None
        self.connect("size-allocate", lambda *_a: self._sync_exclusive_zone())

    def _sync_exclusive_zone(self):
        """Hyprland suma el margen superior a la zona exclusiva (Waybar lo
        compensaba con margin-bottom: -5): reservar alto real - margen deja
        el mismo espacio que antes."""
        if self._exclusive_margin is None:
            return
        zone = max(0, self.get_allocated_height() - self._exclusive_margin)
        if zone != GtkLayerShell.get_exclusive_zone(self):
            GtkLayerShell.set_exclusive_zone(self, zone)

    def build(self, state):
        child = self.get_child()
        if child is not None:
            child.destroy()

        ctx = self.get_style_context()
        for name in ("islas", "capsula", "full-width"):
            ctx.remove_class(name)
        ctx.add_class(state["layout"])
        if state["full_width"]:
            ctx.add_class("full-width")

        if self._test:
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, TEST_MARGIN_TOP)
            self._exclusive_margin = None
            GtkLayerShell.set_exclusive_zone(self, 0)
        else:
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, self._margin_top(state))
            self._exclusive_margin = self._margin_top(state)
            self._sync_exclusive_zone()

        self.set_size_request(-1, self._height(state))
        root = self._build_capsula(state) if state["layout"] == "capsula" else self._build_islas(state)

        self.add(root)
        root.show_all()
        self.resize(1, 1)
        self.set_visible(state["visible"])

    @staticmethod
    def _height(state):
        if state["full_width"]:
            return FULL_WIDTH_HEIGHT
        return CAPSULA_HEIGHT if state["layout"] == "capsula" else ISLAS_HEIGHT

    @staticmethod
    def _margin_top(state):
        return 0 if state["full_width"] else MARGIN_TOP

    def _scale(self):
        return self._monitor.get_scale_factor()

    def _workspaces(self, state):
        mode = bar_settings.workspaces_mode(state)
        if mode == "numbered":
            return bar_workspaces.WorkspacesWidget(self._hypr, self._scale(), state["ws_icons"],
                                                   state["group_apps"], state["compact_ws"])
        if mode == "grouped":
            return bar_workspaces.TaskbarWidget(self._hypr, self._scale(), state["group_apps"])
        return None

    def _right_cluster(self, state):
        box = Gtk.Box(spacing=0)
        box.set_name("right-cluster")
        _add_class(box, "pill")
        box.pack_start(bar_tray.TrayWidget(self._tray, self._scale()), False, False, 0)
        box.pack_start(bar_modules.vpn_module()[0], False, False, 0)
        box.pack_start(bar_modules.dnd_module()[0], False, False, 0)
        box.pack_start(bar_modules.audiobook_module()[0], False, False, 0)
        modules = []
        if state["bluetooth"]:
            modules.append(bar_modules.bluetooth_module())
        if state["red"]:
            modules.append(bar_modules.red_module())
        modules.append(bar_modules.pulseaudio_module())
        for event_box, _styled in modules:
            box.pack_start(event_box, False, False, 0)
        if len(modules) > 1:
            _add_class(modules[-1][1], "last")
        hover = Gtk.EventBox()
        hover.add(box)
        bar_modules.track_hover(hover, box)
        return hover, box

    def _build_islas(self, state):
        root = Gtk.Box(spacing=SPACING)
        left = Gtk.Box(spacing=SPACING)
        left.set_name("islas-left")
        right = Gtk.Box(spacing=SPACING)

        if state["frog"]:
            box, styled = bar_modules.frog_module(self._scale())
            _add_class(styled, "pill")
            left.pack_start(box, False, False, 0)
        workspaces = self._workspaces(state)
        if workspaces is not None:
            left.pack_start(_add_class(workspaces, "pill"), False, False, 0)
        if state["bandcamp"]:
            bandcamp = Gtk.Box(spacing=0)
            bandcamp.set_name("bandcamp")
            for box, _styled in bar_modules.bandcamp_modules(with_transport=True):
                bandcamp.pack_start(box, False, False, 0)
            left.pack_start(bandcamp, False, False, 0)
        if state["sysmon"]:
            box, styled = bar_modules.sysmon_module()
            _add_class(styled, "pill")
            left.pack_start(box, False, False, 0)

        clock, clock_label = bar_modules.clock_module()
        _add_class(clock_label, "pill")

        right.pack_start(self._right_cluster(state)[0], False, False, 0)
        power, power_label = bar_modules.power_module()
        _add_class(power_label, "pill")
        right.pack_start(power, False, False, 0)

        root.pack_start(left, False, False, 0)
        root.set_center_widget(clock)
        root.pack_end(right, False, False, 0)
        return root

    def _build_capsula(self, state):
        root = Gtk.Box(spacing=SPACING)
        core = Gtk.Box(spacing=SPACING)
        core.set_name("core-cluster")
        _add_class(core, "pill")

        if state["frog"]:
            core.pack_start(bar_modules.frog_module(self._scale())[0], False, False, 0)
        workspaces = self._workspaces(state)
        if workspaces is not None:
            core.pack_start(workspaces, False, False, 0)
        core.pack_start(bar_modules.clock_module()[0], False, False, 0)
        if state["bandcamp"]:
            core.pack_start(bar_modules.bandcamp_modules(with_transport=False)[0][0], False, False, 0)
        core.pack_start(bar_modules.power_module()[0], False, False, 0)

        left = Gtk.Box(spacing=SPACING)
        left.set_halign(Gtk.Align.END)
        if state["sysmon"]:
            box, styled = bar_modules.sysmon_module()
            _add_class(styled, "pill")
            left.pack_start(box, False, False, 0)

        right = Gtk.Box(spacing=SPACING)
        right.set_halign(Gtk.Align.START)
        right.pack_start(self._right_cluster(state)[0], False, False, 0)

        root.pack_start(left, True, True, 0)
        root.set_center_widget(core)
        root.pack_end(right, True, True, 0)
        return root


class App:
    def __init__(self, test):
        self._test = test
        self._hypr = bar_hypr.HyprState()
        self._tray = bar_tray.Host()
        self._state = bar_settings.load()
        self._windows = {}
        self._reload_timer = None
        self._pending_css = False
        self._pending_state = False
        self._force_rebuild = False

        screen = Gdk.Screen.get_default()
        self._provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(screen, self._provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self._colors = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(screen, self._colors, Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self._load_css()
        self._load_colors()

        display = Gdk.Display.get_default()
        display.connect("monitor-added", lambda _d, m: self._add_monitor(m))
        display.connect("monitor-removed", lambda _d, m: self._remove_monitor(m))
        for i in range(display.get_n_monitors()):
            self._add_monitor(display.get_monitor(i))

        Gtk.IconTheme.get_default().connect("changed", self._on_icon_theme_changed)

        self._file_monitors = []
        for path in WATCH_DIRS:
            monitor = Gio.File.new_for_path(path).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            monitor.connect("changed", self._on_file_changed)
            self._file_monitors.append(monitor)

    def _add_monitor(self, monitor):
        window = BarWindow(monitor, self._hypr, self._tray, self._test)
        self._windows[monitor] = window
        window.build(self._state)

    def _remove_monitor(self, monitor):
        window = self._windows.pop(monitor, None)
        if window is not None:
            window.destroy()

    def _on_icon_theme_changed(self, _theme):
        """Pack de íconos nuevo: rearmar tray e íconos de ventanas (bar_icons
        ya vació su caché en su propio handler)."""
        self._pending_state = True
        self._force_rebuild = True
        if self._reload_timer is None:
            self._reload_timer = GLib.timeout_add(RELOAD_DEBOUNCE_MS, self._reload)

    def _load_css(self):
        try:
            self._provider.load_from_path(CSS_FILE)
        except GLib.Error as e:
            print(f"ff-bar: CSS inválido, se queda el anterior: {e.message}", file=sys.stderr)

    def _load_colors(self):
        """Fondo de las píldoras (@mybarbackground) y de la tira de
        "Barra completa" (@mystripbackground) según los ajustes."""
        background = "transparent" if self._state["transparent"] else "@mybackground"
        pills = "transparent" if self._state["full_width"] else background
        self._colors.load_from_data(
            f"@define-color mybarbackground {pills};\n@define-color mystripbackground {background};\n".encode())

    def toggle_visible(self):
        bar_settings.set_value("visible", not self._state["visible"])

    def _on_file_changed(self, _monitor, file, other, _event):
        names = {f.get_basename() for f in (file, other) if f is not None}
        if LANGUAGE_FILE in names and file.get_parent().get_path() == LANGUAGE_DIR:
            GLib.timeout_add(RELOAD_DEBOUNCE_MS, _restart)
            return
        if any(n.endswith(".css") for n in names):
            self._pending_css = True
        elif SETTINGS_FILE in names:
            self._pending_state = True
        else:
            return
        if self._reload_timer is None:
            self._reload_timer = GLib.timeout_add(RELOAD_DEBOUNCE_MS, self._reload)

    def _reload(self):
        self._reload_timer = None
        if self._pending_css:
            self._pending_css = False
            self._load_css()
            bar_modules.reset_frog_color()
        if self._pending_state:
            self._pending_state = False
            state = bar_settings.load()
            if state != self._state or self._force_rebuild:
                self._force_rebuild = False
                self._state = state
                self._load_colors()
                for window in self._windows.values():
                    window.build(state)
        return False


def _restart():
    os.execv(sys.executable, [sys.executable, os.path.realpath(__file__), *sys.argv[1:]])


def _quit(*_args):
    try:
        with open(LOCK) as f:
            if int(f.read().strip()) == os.getpid():
                os.remove(LOCK)
    except (OSError, ValueError):
        pass
    Gtk.main_quit()
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    common.set_process_name("ff-bar")
    _replace_running()
    if not bar_settings.get("visible"):
        bar_settings.set_value("visible", True)
    app = App(args.test)
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, _quit)
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, lambda: app.toggle_visible() or True)
    Gtk.main()


if __name__ == "__main__":
    main()

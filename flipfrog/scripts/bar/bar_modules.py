"""Módulos de texto/imagen de la barra (reloj, sysmon, sonido, Bluetooth,
red, indicadores de VPN, No molestar y audiolibro, Bandcamp, ranita, power). Cada
dato sale de la misma función status() que usaba Waybar, corrida en un
hilo y compartida por las barras de todos los monitores."""

import os
import subprocess
import sys
import threading
import warnings

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
SCRIPTS_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
for sub in ("clock", "process", "bluetooth", "firewall", "audio", "bandcamp", "toggles", "notifications", "audiobook"):
    sys.path.insert(0, os.path.join(SCRIPTS_DIR, sub))

import audiobook_status
import bandcamp_ipc
import bluetooth_status
import clock
import common
import dnd_status
import island
import network_status
import playpause
import pulseaudio
import sysmon
import vpn_status

FROG_GIF = os.path.normpath(os.path.join(SCRIPTS_DIR, "..", "themer", "105651-Transparent.gif"))
FROG_SIZE = 24
FROG_ORANGE = b"\xff\x63\x00"
POWER_ICON = ""
PREV_ICON = "⏮"
NEXT_ICON = "⏭"
SCROLL_THRESHOLD = 1.0

# La API de animación de GdkPixbuf está marcada obsoleta pero es la que
# GTK3 usa para gifs; sin alternativa sin PIL.
warnings.filterwarnings("ignore", category=DeprecationWarning, message=r"GdkPixbuf\.PixbufAnimation")


def _py(rel, *args):
    return ["python3", os.path.join(SCRIPTS_DIR, rel), *args]


def run(cmd):
    try:
        subprocess.Popen(cmd, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def clickable(child, actions, scroll=None):
    """EventBox con acciones por botón ({1: fn, 2: fn, 3: fn}) y rueda
    (scroll(+1|-1)). Devuelve True al manejar el clic para que no llegue
    al EventBox de afuera (ícono dentro de un workspace)."""
    box = Gtk.EventBox()
    box.add(child)
    box.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
    accum = [0.0]

    def on_press(_w, event):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return True
        fn = actions.get(event.button)
        if fn is None:
            return False
        fn()
        return True

    def on_scroll(_w, event):
        if scroll is None:
            return False
        if event.direction == Gdk.ScrollDirection.UP:
            scroll(1)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            scroll(-1)
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            accum[0] += event.delta_y
            while abs(accum[0]) >= SCROLL_THRESHOLD:
                scroll(-1 if accum[0] > 0 else 1)
                accum[0] -= SCROLL_THRESHOLD if accum[0] > 0 else -SCROLL_THRESHOLD
        return True

    box.connect("button-press-event", on_press)
    box.connect("scroll-event", on_scroll)
    return box


def styled_box(child):
    """Caja intermedia para estilos: un Gtk.EventBox ignora margin/padding
    del CSS en GTK3, un Gtk.Box no."""
    box = Gtk.Box()
    box.pack_start(child, True, True, 0)
    return box


def track_hover(event_box, target, css_class="hover"):
    """Clase "hover" a mano: un Gtk.Box sin ventana propia no recibe
    :hover confiable, y salir hacia un hijo (INFERIOR) no cuenta."""
    def on_enter(_w, _e):
        target.get_style_context().add_class(css_class)

    def on_leave(_w, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            target.get_style_context().remove_class(css_class)

    event_box.connect("enter-notify-event", on_enter)
    event_box.connect("leave-notify-event", on_leave)


class Source:
    """fn() en un hilo cada `interval_ms`, solo mientras haya algún widget
    suscrito; sin apilar corridas si la anterior sigue colgada."""

    def __init__(self, fn, interval_ms):
        self._fn = fn
        self._interval = interval_ms
        self._subs = []
        self._timer = None
        self._busy = False
        self._again = False
        self.value = None

    def subscribe(self, callback, widget):
        entry = (callback, widget)
        self._subs.append(entry)
        widget.connect("destroy", lambda _w: self._unsubscribe(entry))
        if self.value is not None:
            callback(self.value)
        if self._timer is None:
            self._timer = GLib.timeout_add(self._interval, self._tick)
            self.poke()

    def _unsubscribe(self, entry):
        self._subs.remove(entry)
        if not self._subs and self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
            self.stopped()

    def stopped(self):
        pass

    def poke(self):
        if self._busy:
            self._again = True
            return
        self._busy = True
        threading.Thread(target=self._run, daemon=True).start()

    def _tick(self):
        self.poke()
        return True

    def _run(self):
        try:
            value = self._fn()
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            value = None
        GLib.idle_add(self._done, value)

    def _done(self, value):
        self._busy = False
        if value is not None:
            self.value = value
            for callback, _widget in list(self._subs):
                callback(value)
        if self._again:
            self._again = False
            self.poke()
        return False


class PulseSource(Source):
    """Además del sondeo lento, `pactl subscribe` refresca al momento en
    cualquier cambio de sink/servidor (volumen, mute, salida default)."""

    def __init__(self):
        super().__init__(pulseaudio.status, 10000)
        self._proc = None

    def subscribe(self, callback, widget):
        super().subscribe(callback, widget)
        if self._proc is None:
            threading.Thread(target=self._watch, daemon=True).start()

    def stopped(self):
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None

    def _watch(self):
        try:
            self._proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, text=True,
                                          env={**os.environ, "LC_ALL": "C"})
        except OSError:
            return
        for line in self._proc.stdout:
            if "sink" in line or "server" in line:
                GLib.idle_add(self._poke_once)

    def _poke_once(self):
        self.poke()
        return False


def _bandcamp_status():
    state = bandcamp_ipc.get_state()
    return {"info": island.status(state or {}), "playpause": playpause.status(state or {})}


SOURCES = {
    "clock": Source(clock.status, 2000),
    "sysmon": Source(lambda: sysmon.status(cpu_sample=None), 2000),
    "bluetooth": Source(bluetooth_status.status, 5000),
    "red": Source(network_status.status, 5000),
    "vpn": Source(vpn_status.status, 5000),
    "dnd": Source(dnd_status.status, 2000),
    "audiobook": Source(audiobook_status.status, 2000),
    "pulseaudio": PulseSource(),
    "bandcamp": Source(_bandcamp_status, 1000),
}


def _set_markup(label, text):
    try:
        Pango.parse_markup(text, -1, "\0")
        label.set_markup(text)
    except GLib.Error:
        label.set_text(text)


def _apply(label, box, data, prev_class):
    _set_markup(label, data.get("text", ""))
    box.set_tooltip_text(data.get("tooltip") or None)
    ctx = label.get_style_context()
    if prev_class[0]:
        ctx.remove_class(prev_class[0])
    prev_class[0] = data.get("class") or ""
    if prev_class[0]:
        ctx.add_class(prev_class[0])


def text_module(name, source=None, actions=None, scroll=None, text="", key=None):
    label = Gtk.Label(label=text)
    label.set_name(name)
    box = clickable(label, actions or {}, scroll)
    if source is not None:
        prev_class = [""]
        SOURCES[source].subscribe(
            lambda value: _apply(label, box, value[key] if key else value, prev_class), box)
    return box, label


def clock_module():
    return text_module("clock", "clock", {
        1: lambda: run(_py("calendar/calendar_popup.py")),
        3: lambda: run(_py("alarms/alarm_popup.py")),
    })


def sysmon_module():
    return text_module("sysmon", "sysmon", {3: lambda: run(_py("dashboard.py"))})


def pulseaudio_module():
    return text_module("pulseaudio", "pulseaudio", {
        1: lambda: run(_py("audio/volume-simple.py")),
        2: lambda: run(_py("audio/sink-selector.py")),
        3: lambda: run(_py("audio/mixer.py")),
    }, scroll=lambda d: run(_py("audio/scroll-volume.py", "up" if d > 0 else "down")))


def bluetooth_module():
    return text_module("bluetooth", "bluetooth", {1: lambda: run(_py("dashboard.py", "bluetooth"))})


def red_module():
    return text_module("red", "red", {1: lambda: run(_py("dashboard.py", "red"))})


def _indicator(name, actions):
    """Ícono que solo existe mientras su fuente trae texto; no_show_all
    para que el show_all() de la barra no lo vuelva a mostrar."""
    box, label = text_module(name, name, actions)
    box.set_no_show_all(True)
    label.show()
    SOURCES[name].subscribe(lambda value: box.set_visible(bool(value.get("text"))), box)
    return box, label


def vpn_module():
    return _indicator("vpn", {1: lambda: run(_py("dashboard.py"))})


def dnd_module():
    return _indicator("dnd", {1: lambda: run(_py("notifications/notification_history.py"))})


def audiobook_module():
    return _indicator("audiobook", {1: lambda: run(_py("audiobook/audiobook_popup.py"))})


def power_module():
    return text_module("power", actions={1: lambda: run(_py("power/power_menu.py"))}, text=POWER_ICON)


def _bandcamp_ctl(cmd):
    def fn():
        run(_py("bandcamp/bandcamp_ctl.py", cmd))
        GLib.timeout_add(300, lambda: SOURCES["bandcamp"].poke() and False)
    return fn


def bandcamp_modules(with_transport):
    off = lambda: run(_py("bar/bar_settings.py", "set", "bandcamp", "off"))
    widgets = [text_module("bandcamp-info", "bandcamp", {
        1: lambda: run(_py("bandcamp/bandcamp_popup.py")), 2: off,
    }, key="info")]
    if with_transport:
        widgets += [
            text_module("bandcamp-prev", actions={1: _bandcamp_ctl("prev"), 2: off}, text=PREV_ICON),
            text_module("bandcamp-playpause", "bandcamp", {1: _bandcamp_ctl("toggle_pause"), 2: off},
                        key="playpause"),
            text_module("bandcamp-next", actions={1: _bandcamp_ctl("skip"), 2: off}, text=NEXT_ICON),
        ]
    return widgets


_frog_anim = []
_frog_surfaces = {}


def reset_frog_color():
    """colors.css cambió: cada ranita toma el color nuevo en su próximo frame."""
    _frog_surfaces.clear()


def _timeval(ms):
    tv = GLib.TimeVal()
    tv.tv_sec, tv.tv_usec = ms // 1000, (ms % 1000) * 1000
    return tv


def _frog_surface(pixbuf, scale):
    """Frame ya recoloreado y escalado, cacheado por contenido (el gif
    repite frames). Sprite pixel art de 2 colores planos: el naranja se
    cambia por @myforegroundhover por comparación exacta."""
    pixels = pixbuf.get_pixels()
    key = (hash(pixels), scale)
    surface = _frog_surfaces.get(key)
    if surface is not None:
        return surface

    width, height, stride = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
    target = bytes(int(c) for c in common.theme_color_rgba("myforegroundhover")[:3])
    data = bytearray(pixels)
    i = data.find(FROG_ORANGE)
    while i != -1:
        col = i % stride
        if col % 4 == 0 and col < width * 4:
            data[i:i + 3] = target
        i = data.find(FROG_ORANGE, i + 1)

    recolored = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(data)), GdkPixbuf.Colorspace.RGB, True, 8, width, height, stride)
    factor = FROG_SIZE * scale / max(width, height)
    scaled = recolored.scale_simple(round(width * factor), round(height * factor), GdkPixbuf.InterpType.BILINEAR)
    surface = Gdk.cairo_surface_create_from_pixbuf(scaled, scale, None)
    _frog_surfaces[key] = surface
    return surface


def frog_module(scale):
    image = Gtk.Image()
    styled = styled_box(image)
    styled.set_name("frog")
    box = clickable(styled, {
        1: lambda: run(_py("launcher/launcher.py")),
        3: lambda: run(_py("dashboard.py")),
    })
    if not _frog_anim:
        try:
            _frog_anim.append(GdkPixbuf.PixbufAnimation.new_from_file(FROG_GIF))
        except GLib.Error:
            return box, styled

    # Reloj propio en ms: el iterador avanza frame por frame sin
    # depender de la hora real.
    frames = _frog_anim[0].get_iter(_timeval(0))
    state = {"ms": 0, "timer": None}

    def show():
        image.set_from_surface(_frog_surface(frames.get_pixbuf(), scale))
        delay = frames.get_delay_time()
        if delay >= 0:
            state["ms"] += delay
            state["timer"] = GLib.timeout_add(max(delay, 20), step)

    def step():
        state["timer"] = None
        frames.advance(_timeval(state["ms"]))
        show()
        return False

    def on_destroy(_w):
        if state["timer"] is not None:
            GLib.source_remove(state["timer"])

    show()
    box.connect("destroy", on_destroy)
    return box, styled

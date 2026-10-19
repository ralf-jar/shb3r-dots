import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GtkLayerShell, Pango, Gio, GdkPixbuf, GLib, GLibUnix
import cairo
import json
import math
import sys
import os
import signal
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from common import theme_color_hex as _theme_color_hex

_LOADED_CSS_PATHS = set()

_suppress_close_until = 0.0


def suppress_close(seconds):
    """Ignora el próximo click-afuera de esta ventana por `seconds` --
    usado al reiniciar Waybar en caliente (ver theme_module.py), evita
    que la recreación de superficie dispare un cierre espurio."""
    global _suppress_close_until
    _suppress_close_until = time.time() + seconds

_ICON_PIXBUF_CACHE = {}


def svg_icon_image(svg_path, size=18, color_name="myforeground"):
    """Gtk.Image desde un .svg con fill fijo en #000000, recoloreado en
    memoria al hex real de `color_name` (mismo contrato que
    connect.svg/disconnect.svg en bluetooth_tab.py). Cacheado por
    (ruta, tamaño, color)."""
    key = (svg_path, size, color_name)
    if key not in _ICON_PIXBUF_CACHE:
        try:
            with open(svg_path, "r", encoding="utf-8") as f:
                svg = f.read().replace("#000000", _theme_color_hex(color_name))
            stream = Gio.MemoryInputStream.new_from_data(svg.encode("utf-8"))
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, size, size, True)
        except (OSError, GLib.Error):
            pixbuf = None
        _ICON_PIXBUF_CACHE[key] = pixbuf
    pixbuf = _ICON_PIXBUF_CACHE[key]
    return Gtk.Image.new_from_pixbuf(pixbuf) if pixbuf else Gtk.Image()


def _icon_text_button(icon_path, label_text, tooltip, color_name="myforegroundhover",
                       hover_color_name="myforeground", icon_size=15):
    """Botón ícono + texto con espaciado real (Gtk.Box propio, el layout
    nativo de Gtk.Button para ícono+label los deja pegados). El ícono
    cambia de color en :hover junto con el texto -- CSS no puede resolver
    esto solo (el color de un Gtk.Image viene de un pixbuf ya horneado,
    no de una propiedad `color`), así que se alternan dos pixbufs
    precalculados vía enter/leave-notify-event."""
    btn = Gtk.Button()
    btn.set_tooltip_text(tooltip)
    content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    content.set_halign(Gtk.Align.CENTER)

    icon = svg_icon_image(icon_path, size=icon_size, color_name=color_name)
    normal_pixbuf = icon.get_pixbuf()
    hover_pixbuf = svg_icon_image(icon_path, size=icon_size, color_name=hover_color_name).get_pixbuf()

    content.pack_start(icon, False, False, 0)
    content.pack_start(Gtk.Label(label=label_text), False, False, 0)
    btn.add(content)

    def on_enter(_widget, _event):
        icon.set_from_pixbuf(hover_pixbuf)

    def on_leave(_widget, _event):
        icon.set_from_pixbuf(normal_pixbuf)

    btn.connect("enter-notify-event", on_enter)
    btn.connect("leave-notify-event", on_leave)
    return btn


def load_module_css(css_path):
    """Suma un Gtk.CssProvider para `css_path` sobre la pantalla default
    -- para módulos embebidos en el dashboard con su propio .css hermano
    (a diferencia de build_layer_window, no abre ventana). Idempotente
    por ruta."""
    if css_path in _LOADED_CSS_PATHS:
        return
    css_provider = Gtk.CssProvider()
    css_provider.load_from_path(css_path)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _LOADED_CSS_PATHS.add(css_path)


def sync_toggle_label_active(label, active):
    """Agrega/quita la clase .active en la etiqueta según el estado del
    toggle -- ver toggle_common.css para el color real."""
    ctx = label.get_style_context()
    if active:
        ctx.add_class("active")
    else:
        ctx.remove_class("active")


def wrap_toggle_row(content, switch):
    """Envuelve la fila en un Gtk.EventBox para que un click en cualquier
    espacio de la fila togglee el switch, no solo el switch mismo."""
    event_box = Gtk.EventBox()
    event_box.add(content)

    def _on_click(_widget, event):
        if event.button != 1 or not switch.get_sensitive():
            return False
        switch.set_active(not switch.get_active())
        return True

    event_box.connect("button-press-event", _on_click)
    return event_box


def build_switch_row(row, icon, label_text, active, on_toggle):
    """Fila ícono-en-chip + etiqueta + Gtk.Switch, compartida por los
    toggles del dashboard. `on_toggle(state)` corre el efecto real.
    Devuelve (wrapper, switch, handler_id) -- para el caso async, bloquear
    `handler_id` antes de un switch.set_active() programático (ver
    vpn_toggle.py)."""
    wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    wrapper.get_style_context().add_class("toggle-list-row")

    chip = Gtk.Box()
    chip.get_style_context().add_class("toggle-icon-chip")
    # expand=True + fill=False centra el ícono en el chip -- con
    # expand=False el sobrante queda pegado a la izquierda (confirmado).
    icon.set_halign(Gtk.Align.CENTER)
    icon.set_valign(Gtk.Align.CENTER)
    chip.pack_start(icon, True, False, 0)
    wrapper.pack_start(chip, False, False, 0)

    label = Gtk.Label(label=label_text)
    label.set_halign(Gtk.Align.START)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.get_style_context().add_class("toggle-row-label")
    sync_toggle_label_active(label, active)
    wrapper.pack_start(label, True, True, 0)

    switch = Gtk.Switch()
    switch.set_active(active)
    switch.set_valign(Gtk.Align.CENTER)
    switch.get_style_context().add_class("toggle-row-switch")
    # notify::active (no state-set) -- se dispara incluso con un
    # set_active() bloqueado, así la etiqueta siempre refleja el switch.
    switch.connect("notify::active", lambda sw, _p: sync_toggle_label_active(label, sw.get_active()))

    def _on_state_set(_sw, state):
        on_toggle(state)
        return False

    handler_id = switch.connect("state-set", _on_state_set)
    wrapper.pack_start(switch, False, False, 0)

    row.pack_start(wrap_toggle_row(wrapper, switch), True, True, 0)
    return wrapper, switch, handler_id


GROUP_LOCKS = [
    "/tmp/volume-simple.pid",
    "/tmp/volume-mixer.pid",
    "/tmp/sink-selector.pid",
    "/tmp/brightness.pid",
    "/tmp/dashboard.pid",
    "/tmp/calendar.pid",
    "/tmp/calendar-sync.pid",
    "/tmp/theme-editor.pid",
    "/tmp/google-fonts.pid",
    "/tmp/cursor-picker.pid",
    "/tmp/icon-picker.pid",
    "/tmp/firewall-log-viewer.pid",
    "/tmp/firewall-popup.pid",
    "/tmp/services-popup.pid",
    "/tmp/bandcamp-radio-popup.pid",
    "/tmp/keybinds-popup.pid",
    "/tmp/app-launcher.pid",
    "/tmp/alarm-popup.pid",
    "/tmp/download-popup.pid",
    "/tmp/audiobook-popup.pid",
    "/tmp/notification-history.pid",
    "/tmp/disk-usage.pid",
    "/tmp/power-menu.pid",
    "/tmp/vpn-config.pid"
]

def kill_group(LOCK):
    """Cierra cualquier otro popup del grupo que esté abierto."""
    for lock in GROUP_LOCKS:
        if lock == LOCK:          # no matarse a sí mismo
            continue
        if os.path.exists(lock):
            try:
                with open(lock) as f:
                    pid = int(f.read().strip())
                os.kill(pid, signal.SIGTERM)
                os.remove(lock)
            except (ProcessLookupError, ValueError, FileNotFoundError):
                try:
                    os.remove(lock)
                except FileNotFoundError:
                    pass

def kill_existing(LOCK):
    if os.path.exists(LOCK):
        try:
            with open(LOCK) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
            os.remove(LOCK)
            sys.exit(0)
        except (ProcessLookupError, ValueError):
            os.remove(LOCK)

def build_layer_window(namespace, css_file):
    """Ventana layer-shell transparente a pantalla completa; click afuera
    del contenido cierra (salvo con un grab de GTK activo). Sin
    Gtk.Overlay -- ignora halign/valign/margin de sus hijos en este
    entorno; se posiciona con Bin/Box normales en su lugar. Devuelve
    (window, content_box) -- posicionar con position_near_cursor()/
    position_fixed_top() sobre content_box. Cada `namespace` nuevo
    necesita su hl.layer_rule de blur en hypr/config/windowrules.lua
    (ver CLAUDE.md, "Línea de diseño")."""
    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_decorated(False)
    window.set_resizable(False)

    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, namespace)
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.TOP)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.ON_DEMAND)
    for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                 GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
        GtkLayerShell.set_anchor(window, edge, True)

    load_module_css(css_file)

    def _on_background_click(_widget, _event):
        # Grab de GTK activo (menú/combo abierto) -> no cerrar. No cubre
        # popovers, ver theme-editor.py (ColorButton/show-editor).
        if Gtk.grab_get_current() is not None:
            return True
        if time.time() < _suppress_close_until:
            return True
        window.destroy()

    background = Gtk.EventBox()
    background.set_hexpand(True)
    background.set_vexpand(True)
    background.connect("button-press-event", _on_background_click)

    anchor = Gtk.EventBox()
    anchor.connect("button-press-event", lambda *_: True)
    background.add(anchor)

    content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    anchor.add(content_box)

    window.add(background)
    if _animations_enabled():
        _PopupAnimation(window, anchor)
    return window, content_box


LOOK_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "themer", "hypr-look.json")
ANIM_MS = 400
ANIM_RISE = 60
ANIM_MIN_SCALE = 0.6
ANIM_TILT = 0.4
ANIM_FLATTEN = 0.3


def _animations_enabled():
    """Mismo switch "Animaciones" de Personalización que Hyprland."""
    try:
        with open(LOOK_FILE) as f:
            return json.load(f).get("animations", True) is not False
    except (OSError, ValueError):
        return True


class _PopupAnimation:
    """Entrada y salida estilo Star Wars: el panel aparece subiendo desde
    abajo mientras crece, y se va hacia arriba encogiéndose, inclinado
    hacia atrás (borde de arriba más angosto). GTK3 no tiene transform en
    CSS: cada cuadro se dibuja el panel en una superficie aparte y se pinta
    deformado, franja por franja. Reemplaza window.destroy (y SIGTERM, el
    cierre de kill_existing/kill_group) para animar antes de cerrar."""

    def __init__(self, window, panel):
        self.window = window
        self.panel = panel
        self.progress = 0.0
        self.closing = False
        self.running = True
        self.last_time = None

        window.connect("draw", self._on_draw)
        window.add_tick_callback(self._on_tick)
        window.destroy = self.close
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_sigterm)

    def _on_sigterm(self):
        self.close()
        return GLib.SOURCE_REMOVE

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.last_time = None
        if not self.window.get_mapped():
            Gtk.Window.destroy(self.window)
            return
        if not self.running:
            self.running = True
            self.window.add_tick_callback(self._on_tick)

    def _panel_rect(self):
        coords = self.panel.translate_coordinates(self.window, 0, 0)
        width, height = self.panel.get_allocated_width(), self.panel.get_allocated_height()
        if coords is None or width <= 1 or height <= 1:
            return None
        return coords[0], coords[1], width, height

    def _on_tick(self, _widget, clock):
        rect = self._panel_rect()
        if rect is None:
            return GLib.SOURCE_CONTINUE
        now = clock.get_frame_time()
        if self.last_time is not None:
            step = (now - self.last_time) / 1000 / ANIM_MS
            self.progress += -step if self.closing else step
        self.last_time = now
        self.progress = max(0.0, min(1.0, self.progress))

        x, y, width, height = rect
        self.window.queue_draw_area(x, y - ANIM_RISE, width, height + 2 * ANIM_RISE)
        if self.closing and self.progress <= 0:
            Gtk.Window.destroy(self.window)
            return GLib.SOURCE_REMOVE
        if not self.closing and self.progress >= 1:
            self.running = False
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _on_draw(self, widget, cr):
        if not self.running:
            return False
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        rect = self._panel_rect()
        if rect is None or self.progress <= 0:
            return True

        x, y, width, height = rect
        scale = widget.get_scale_factor()
        snapshot = cairo.ImageSurface(cairo.FORMAT_ARGB32, width * scale, height * scale)
        snapshot.set_device_scale(scale, scale)
        self.panel.draw(cairo.Context(snapshot))

        eased = 1 - (1 - self.progress) ** 3
        rest = 1 - eased
        size = ANIM_MIN_SCALE + (1 - ANIM_MIN_SCALE) * eased
        out_height = height * size * (1 - ANIM_FLATTEN * rest)
        shift = ANIM_RISE * rest * (-1 if self.closing else 1)
        top = y + (height - out_height) / 2 + shift
        center = x + width / 2
        window_width = widget.get_allocated_width()

        band = 1 / scale
        rows = math.ceil(out_height / band)
        for i in range(rows):
            t = i / rows
            row_scale = size * (1 - ANIM_TILT * rest * (1 - t))
            cr.save()
            cr.rectangle(0, top + i * band, window_width, band)
            cr.clip()
            cr.translate(center, top + i * band)
            cr.scale(row_scale, out_height / height)
            cr.translate(-width / 2, -t * height)
            cr.set_source_surface(snapshot, 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            cr.paint_with_alpha(eased)
            cr.restore()
        return True


# Ícono de la barra que abrió este popup, "mx,my,cx,bottom": origen del
# monitor, centro del ícono y borde inferior de la barra, relativos al
# monitor. Se quita del entorno para que los popups que abra este no lo hereden.
BAR_ANCHOR_ENV = "FF_BAR_ANCHOR"
_BAR_ANCHOR = os.environ.pop(BAR_ANCHOR_ENV, None)
BAR_GAP = 6
SCREEN_EDGE = 8


def _bar_anchor():
    try:
        mx, my, cx, bottom = (int(v) for v in _BAR_ANCHOR.split(","))
    except (AttributeError, ValueError):
        return None
    display = Gdk.Display.get_default()
    for i in range(display.get_n_monitors()):
        monitor = display.get_monitor(i)
        geo = monitor.get_geometry()
        if (geo.x, geo.y) == (mx, my):
            return monitor, cx, bottom
    return None


def position_at_bar(container):
    """Ubica `container` justo debajo del ícono de la barra que lo abrió,
    centrado en él y sin salirse del monitor. False si no lo abrió la
    barra (atajo, .desktop, otro popup)."""
    found = _bar_anchor()
    if found is None:
        return False
    monitor, cx, bottom = found
    geo = monitor.get_geometry()
    window = container.get_toplevel()
    GtkLayerShell.set_monitor(window, monitor)

    anchor = container.get_parent()
    anchor.set_halign(Gtk.Align.START)
    anchor.set_valign(Gtk.Align.START)

    def on_allocate(_widget, alloc):
        # La superficie empieza debajo de la zona exclusiva de la barra.
        top_offset = geo.height - window.get_allocated_height()
        x = max(SCREEN_EDGE, min(cx - alloc.width // 2, geo.width - alloc.width - SCREEN_EDGE))
        y = max(SCREEN_EDGE, bottom + BAR_GAP - top_offset)
        if (anchor.get_margin_start(), anchor.get_margin_top()) != (x, y):
            anchor.set_margin_start(x)
            anchor.set_margin_top(y)

    anchor.connect("size-allocate", on_allocate)
    return True


def position_near_cursor(container, top_offset=10):
    """Ubica `container` centrado horizontalmente y cerca de la altura del
    cursor: arriba si el cursor está en la mitad superior de la pantalla,
    abajo si está en la inferior."""
    if position_at_bar(container):
        return
    anchor = container.get_parent()
    display = Gdk.Display.get_default()
    seat = display.get_default_seat()
    _, cx, cy = seat.get_pointer().get_position()
    monitor = display.get_monitor_at_point(cx, cy)
    geo = monitor.get_geometry()
    ry = cy - geo.y + top_offset
    sh = geo.height

    anchor.set_halign(Gtk.Align.CENTER)
    if ry < sh // 2:
        anchor.set_valign(Gtk.Align.START)
        anchor.set_margin_top(ry)
    else:
        anchor.set_valign(Gtk.Align.END)
        anchor.set_margin_bottom(sh - ry)


def position_fixed_top(container, margin=60):
    """Ubica `container` centrado horizontalmente, a `margin` px del borde
    superior (para popups que no dependen de la posición del cursor)."""
    if position_at_bar(container):
        return
    anchor = container.get_parent()
    anchor.set_halign(Gtk.Align.CENTER)
    anchor.set_valign(Gtk.Align.START)
    anchor.set_margin_top(margin)

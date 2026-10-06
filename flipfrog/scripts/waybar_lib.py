import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GtkLayerShell, Pango, Gio, GdkPixbuf, GLib
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
    "/tmp/theme-editor.pid",
    "/tmp/google-fonts.pid",
    "/tmp/firewall-log-viewer.pid",
    "/tmp/firewall-popup.pid",
    "/tmp/services-popup.pid",
    "/tmp/voice-assistant.pid",
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
    return window, content_box


def position_near_cursor(container, top_offset=10):
    """Ubica `container` centrado horizontalmente y cerca de la altura del
    cursor: arriba si el cursor está en la mitad superior de la pantalla,
    abajo si está en la inferior."""
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
    anchor = container.get_parent()
    anchor.set_halign(Gtk.Align.CENTER)
    anchor.set_valign(Gtk.Align.START)
    anchor.set_margin_top(margin)

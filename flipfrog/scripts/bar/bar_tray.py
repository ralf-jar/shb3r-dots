"""Bandeja del sistema (StatusNotifierItem). Si nadie tiene el
StatusNotifierWatcher (sin Waybar), la barra lo implementa; si ya lo
tiene otro (Waybar en --test), se registra como host en el suyo y toma
el nombre en cuanto se libere. Menús por com.canonical.dbusmenu,
armados como Gtk.Menu al abrirlos. Todo asíncrono en el hilo de GTK."""

import os
import re

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_IFACE = "org.kde.StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
DEFAULT_ITEM_PATH = "/StatusNotifierItem"

ITEM_SIGNALS = ("NewIcon", "NewAttentionIcon", "NewOverlayIcon", "NewStatus", "NewTitle", "NewToolTip")
CALL_TIMEOUT_MS = 2000
ITEM_REFRESH_DEBOUNCE_MS = 50
ICON_SIZE = 16
ITEM_SPACING = 10

# Íconos a color que rompen con el resto de la barra -> versión
# simbólica propia (bar/icons/, pintada con el `color` del CSS), por Id
# del ítem.
ICONS_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "icons")
ICON_OVERRIDES = {"openrgb": "ff-openrgb-symbolic"}

WATCHER_XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
    <method name="RegisterStatusNotifierHost"><arg type="s" direction="in"/></method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
    <signal name="StatusNotifierItemUnregistered"><arg type="s"/></signal>
    <signal name="StatusNotifierHostRegistered"/>
    <signal name="StatusNotifierHostUnregistered"/>
  </interface>
</node>
"""


def _split_item_id(item_id):
    slash = item_id.find("/")
    if slash < 0:
        return item_id, DEFAULT_ITEM_PATH
    return item_id[:slash], item_id[slash:]


class Watcher:
    """org.kde.StatusNotifierWatcher propio. El objeto se exporta ANTES
    de pedir el nombre -- un ítem que reacciona al cambio de dueño puede
    llamar en cuanto lo tenemos."""

    def __init__(self, conn):
        self._conn = conn
        self._items = {}
        self._hosts = {}
        self.owned = False

        info = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]
        conn.register_object(WATCHER_PATH, info, self._on_method, self._on_get_property, None)
        Gio.bus_own_name_on_connection(conn, WATCHER_NAME, Gio.BusNameOwnerFlags.NONE,
                                       self._on_acquired, self._on_lost)

    def _on_acquired(self, _conn, _name):
        self.owned = True

    def _on_lost(self, _conn, _name):
        self.owned = False

    def _emit(self, signal, params=None):
        try:
            self._conn.emit_signal(None, WATCHER_PATH, WATCHER_IFACE, signal, params)
        except GLib.Error:
            pass

    def _on_method(self, _conn, sender, _path, _iface, method, params, invocation):
        service = params.unpack()[0]
        if method == "RegisterStatusNotifierItem":
            bus, path = (sender, service) if service.startswith("/") else (service, DEFAULT_ITEM_PATH)
            item_id = bus + path
            if item_id not in self._items:
                watch = Gio.bus_watch_name_on_connection(
                    self._conn, bus, Gio.BusNameWatcherFlags.NONE, None,
                    lambda *_a, i=item_id: self._drop_item(i))
                self._items[item_id] = watch
                self._emit("StatusNotifierItemRegistered", GLib.Variant("(s)", (item_id,)))
        elif method == "RegisterStatusNotifierHost":
            if service not in self._hosts:
                self._hosts[service] = Gio.bus_watch_name_on_connection(
                    self._conn, service, Gio.BusNameWatcherFlags.NONE, None,
                    lambda *_a, s=service: self._drop_host(s))
                self._emit("StatusNotifierHostRegistered")
        invocation.return_value(None)

    def _drop_item(self, item_id):
        watch = self._items.pop(item_id, None)
        if watch is not None:
            Gio.bus_unwatch_name(watch)
            self._emit("StatusNotifierItemUnregistered", GLib.Variant("(s)", (item_id,)))

    def _drop_host(self, service):
        watch = self._hosts.pop(service, None)
        if watch is not None:
            Gio.bus_unwatch_name(watch)
            self._emit("StatusNotifierHostUnregistered")

    def _on_get_property(self, _conn, _sender, _path, _iface, prop):
        if prop == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", list(self._items))
        if prop == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", bool(self._hosts))
        if prop == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return None


def _parse_props(result):
    """GetAll a mano: los pixmaps (a(iiay)) como bytes, sin pasar por
    unpack() (que los vuelve listas de enteros, lento con íconos de
    64x64)."""
    props = {}
    entries = result.get_child_value(0)
    for i in range(entries.n_children()):
        entry = entries.get_child_value(i)
        key = entry.get_child_value(0).get_string()
        value = entry.get_child_value(1).get_variant()
        if key.endswith("Pixmap"):
            props[key] = _parse_pixmaps(value)
        elif key == "ToolTip" and value.get_type_string() == "(sa(iiay)ss)":
            props[key] = (value.get_child_value(2).get_string(), value.get_child_value(3).get_string())
        else:
            props[key] = value.unpack()
    return props


def _parse_pixmaps(value):
    pixmaps = []
    for i in range(value.n_children()):
        child = value.get_child_value(i)
        width = child.get_child_value(0).get_int32()
        height = child.get_child_value(1).get_int32()
        data = child.get_child_value(2).get_data_as_bytes().get_data()
        if width > 0 and height > 0 and len(data) >= width * height * 4:
            pixmaps.append((width, height, data))
    return pixmaps


class TrayItem:
    def __init__(self, conn, item_id):
        self.item_id = item_id
        self.bus, self.path = _split_item_id(item_id)
        self.props = None
        self.conn = conn
        self._subs = []
        self._owner = None
        self._debounce = None
        self._signal_ids = [
            conn.signal_subscribe(None, ITEM_IFACE, name, self.path, None,
                                  Gio.DBusSignalFlags.NONE, self._on_signal)
            for name in ITEM_SIGNALS
        ]
        if self.bus.startswith(":"):
            self._owner = self.bus
        else:
            conn.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                      "GetNameOwner", GLib.Variant("(s)", (self.bus,)), GLib.VariantType("(s)"),
                      Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None, self._on_owner)
        self.refresh()

    def _on_owner(self, conn, result):
        try:
            self._owner = conn.call_finish(result).unpack()[0]
        except GLib.Error:
            pass

    def destroy(self):
        for sid in self._signal_ids:
            self.conn.signal_unsubscribe(sid)
        self._signal_ids = []
        self._subs = []

    def subscribe(self, callback, widget):
        entry = (callback, widget)
        self._subs.append(entry)
        widget.connect("destroy", lambda _w: entry in self._subs and self._subs.remove(entry))
        if self.props is not None:
            callback(self)

    def _on_signal(self, _conn, sender, *_args):
        if sender not in (self._owner, self.bus):
            return
        if self._debounce is None:
            self._debounce = GLib.timeout_add(ITEM_REFRESH_DEBOUNCE_MS, self._debounced_refresh)

    def _debounced_refresh(self):
        self._debounce = None
        self.refresh()
        return False

    def refresh(self):
        self.conn.call(self.bus, self.path, PROPS_IFACE, "GetAll",
                        GLib.Variant("(s)", (ITEM_IFACE,)), GLib.VariantType("(a{sv})"),
                        Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None, self._on_props)

    def _on_props(self, conn, result):
        try:
            self.props = _parse_props(conn.call_finish(result))
        except GLib.Error:
            return
        for callback, _widget in list(self._subs):
            callback(self)

    def call(self, method, params, on_error=None):
        def done(conn, result):
            try:
                conn.call_finish(result)
            except GLib.Error:
                if on_error is not None:
                    on_error()

        self.conn.call(self.bus, self.path, ITEM_IFACE, method, params, None,
                        Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None, done)

    @property
    def menu_path(self):
        path = (self.props or {}).get("Menu")
        return path if path and path != "/" else None


class Host:
    """Lista de ítems compartida por las barras de todos los monitores."""

    def __init__(self):
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.items = {}
        self._subs = []
        self._name = f"org.kde.StatusNotifierHost-{os.getpid()}"
        Gio.bus_own_name_on_connection(self.conn, self._name, Gio.BusNameOwnerFlags.NONE, None, None)

        self.conn.signal_subscribe(None, WATCHER_IFACE, "StatusNotifierItemRegistered", WATCHER_PATH,
                                   None, Gio.DBusSignalFlags.NONE, self._on_registered)
        self.conn.signal_subscribe(None, WATCHER_IFACE, "StatusNotifierItemUnregistered", WATCHER_PATH,
                                   None, Gio.DBusSignalFlags.NONE, self._on_unregistered)
        self._watcher = Watcher(self.conn)
        Gio.bus_watch_name_on_connection(self.conn, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE,
                                         self._on_watcher_appeared, self._on_watcher_vanished)

    def subscribe(self, callback, widget):
        entry = (callback, widget)
        self._subs.append(entry)
        widget.connect("destroy", lambda _w: self._subs.remove(entry))
        callback()

    def _notify(self):
        for callback, _widget in list(self._subs):
            callback()

    def _on_watcher_appeared(self, conn, _name, _owner):
        conn.call(WATCHER_NAME, WATCHER_PATH, WATCHER_IFACE, "RegisterStatusNotifierHost",
                  GLib.Variant("(s)", (self._name,)), None, Gio.DBusCallFlags.NONE,
                  CALL_TIMEOUT_MS, None, None)
        conn.call(WATCHER_NAME, WATCHER_PATH, PROPS_IFACE, "Get",
                  GLib.Variant("(ss)", (WATCHER_IFACE, "RegisteredStatusNotifierItems")),
                  GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None,
                  self._on_item_list)

    def _on_item_list(self, conn, result):
        try:
            ids = conn.call_finish(result).unpack()[0]
        except GLib.Error:
            return
        # El watcher de Waybar antepone cada ítem nuevo; el nuestro los
        # agrega al final. Orden de la barra: el más reciente al final.
        if not self._watcher.owned:
            ids = list(reversed(ids))
        for item_id in ids:
            self._add(item_id)

    def _on_watcher_vanished(self, _conn, _name):
        for item in self.items.values():
            item.destroy()
        self.items.clear()
        self._notify()

    def _on_registered(self, _conn, _sender, _path, _iface, _signal, params):
        self._add(params.unpack()[0])

    def _on_unregistered(self, _conn, _sender, _path, _iface, _signal, params):
        item = self.items.pop(params.unpack()[0], None)
        if item is not None:
            item.destroy()
            self._notify()

    def _add(self, item_id):
        if item_id in self.items:
            return
        self.items[item_id] = TrayItem(self.conn, item_id)
        self._notify()


# ---- Íconos ----

_theme_cache = {}


def _theme_for(search_path):
    if not search_path:
        return Gtk.IconTheme.get_default()
    theme = _theme_cache.get(search_path)
    if theme is None:
        # Solo la carpeta de la app, consultada antes que el tema del
        # usuario (mismo orden que Waybar: Steam trae su propio ícono ahí).
        theme = Gtk.IconTheme()
        theme.set_search_path([search_path])
        _theme_cache[search_path] = theme
    return theme


def _surface_from_name(name, theme_path, size, scale):
    px = size * scale
    if name.startswith("/"):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(name, px, px, True)
        except GLib.Error:
            return None
        return Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, None)
    for theme in (_theme_for(theme_path), Gtk.IconTheme.get_default()):
        info = theme.lookup_icon_for_scale(name, size, scale, Gtk.IconLookupFlags.FORCE_SIZE)
        if info is not None:
            try:
                return info.load_surface(None)
            except GLib.Error:
                pass
    return None


def _surface_from_pixmaps(pixmaps, size, scale):
    px = size * scale
    bigger = [p for p in pixmaps if p[0] >= px]
    width, height, data = min(bigger, key=lambda p: p[0]) if bigger else max(pixmaps, key=lambda p: p[0])
    argb = data[:width * height * 4]
    rgba = bytearray(len(argb))
    rgba[0::4] = argb[1::4]
    rgba[1::4] = argb[2::4]
    rgba[2::4] = argb[3::4]
    rgba[3::4] = argb[0::4]
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(rgba)), GdkPixbuf.Colorspace.RGB,
                                             True, 8, width, height, width * 4)
    if width != px:
        pixbuf = pixbuf.scale_simple(px, px * height // width, GdkPixbuf.InterpType.BILINEAR)
    return Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, None)


def item_surface(props, size, scale):
    attention = props.get("Status") == "NeedsAttention"
    candidates = []
    if attention:
        candidates.append((props.get("AttentionIconName"), props.get("AttentionIconPixmap")))
    candidates.append((props.get("IconName"), props.get("IconPixmap")))
    for name, pixmaps in candidates:
        if name:
            surface = _surface_from_name(name, props.get("IconThemePath"), size, scale)
            if surface is not None:
                return surface
        if pixmaps:
            return _surface_from_pixmaps(pixmaps, size, scale)
    return None


# ---- Menús (com.canonical.dbusmenu) ----

def _menu_event(item, node_id, event_id):
    item.conn.call(item.bus, item.menu_path, MENU_IFACE, "Event",
                    GLib.Variant("(isvu)", (node_id, event_id, GLib.Variant("i", 0), Gtk.get_current_event_time())),
                    None, Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None, None)


def _build_menu(item, children):
    menu = Gtk.Menu()
    for node_id, props, grandchildren in children:
        if not props.get("visible", True):
            continue
        if props.get("type") == "separator":
            menu.append(Gtk.SeparatorMenuItem())
            continue

        label = props.get("label", "")
        toggle = props.get("toggle-type", "")
        if toggle in ("checkmark", "radio"):
            entry = Gtk.CheckMenuItem.new_with_mnemonic(label)
            entry.set_draw_as_radio(toggle == "radio")
            entry.set_active(props.get("toggle-state", 0) == 1)
        else:
            entry = Gtk.MenuItem.new_with_mnemonic(label)
        entry.set_sensitive(props.get("enabled", True))

        if grandchildren or props.get("children-display") == "submenu":
            submenu = _build_menu(item, grandchildren)
            submenu.connect("show", lambda _m, i=node_id: _menu_event(item, i, "opened"))
            entry.set_submenu(submenu)
        else:
            entry.connect("activate", lambda _e, i=node_id: _menu_event(item, i, "clicked"))
        menu.append(entry)
    return menu


def popup_menu(item, widget, event):
    path = item.menu_path
    if path is None:
        return
    trigger = event.copy()

    def on_layout(conn, result):
        try:
            _revision, (_root_id, _props, children) = conn.call_finish(result).unpack()
        except GLib.Error:
            return
        if not children:
            return
        menu = _build_menu(item, children)
        menu.attach_to_widget(widget, None)
        menu.connect("deactivate", lambda m: GLib.idle_add(m.destroy))
        menu.show_all()
        menu.popup_at_widget(widget, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, trigger)

    def on_about_to_show(conn, result):
        try:
            conn.call_finish(result)
        except GLib.Error:
            pass
        conn.call(item.bus, path, MENU_IFACE, "GetLayout", GLib.Variant("(iias)", (0, -1, [])),
                  GLib.VariantType("(u(ia{sv}av))"), Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS,
                  None, on_layout)

    item.conn.call(item.bus, path, MENU_IFACE, "AboutToShow", GLib.Variant("(i)", (0,)),
                    None, Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None, on_about_to_show)


# ---- Widget ----

def _tooltip(props):
    tooltip = props.get("ToolTip")
    if isinstance(tooltip, tuple) and (tooltip[0] or tooltip[1]):
        text = "\n".join(p for p in tooltip if p)
    else:
        text = props.get("Title") or props.get("Id") or ""
    return re.sub(r"<[^>]+>", "", text)


Gtk.IconTheme.get_default().append_search_path(ICONS_DIR)


class TrayWidget(Gtk.Box):
    STATUS_CLASSES = {"Passive": "passive", "Active": "active", "NeedsAttention": "needs-attention"}

    def __init__(self, host, scale):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=ITEM_SPACING)
        self.set_name("tray")
        self._host = host
        self._scale = scale
        self._buttons = {}
        host.subscribe(self._on_items, self)

    def _on_items(self):
        for item_id in list(self._buttons):
            if item_id not in self._host.items:
                self._buttons.pop(item_id).destroy()
        for item_id, item in self._host.items.items():
            if item_id not in self._buttons:
                self._buttons[item_id] = self._make_button(item)
                self.pack_start(self._buttons[item_id], False, False, 0)
        self.set_visible(bool(self._buttons))

    def _make_button(self, item):
        image = Gtk.Image()
        styled = Gtk.Box()
        styled.pack_start(image, True, True, 0)
        styled.get_style_context().add_class("tray-item")
        button = Gtk.EventBox()
        button.add(styled)
        button.add_events(Gdk.EventMask.SCROLL_MASK)
        button.connect("button-press-event", self._on_press, item)
        button.connect("scroll-event", self._on_scroll, item)
        item.subscribe(lambda it: self._update(button, styled, image, it), button)
        button.show_all()
        return button

    def _update(self, button, styled, image, item):
        props = item.props
        override = ICON_OVERRIDES.get(props.get("Id"))
        surface = None if override else item_surface(props, ICON_SIZE, self._scale)
        if override:
            image.set_from_icon_name(override, Gtk.IconSize.MENU)
            image.set_pixel_size(ICON_SIZE)
        elif surface is not None:
            image.set_from_surface(surface)
        else:
            image.set_from_icon_name("image-missing", Gtk.IconSize.MENU)
        button.set_tooltip_text(_tooltip(props) or None)
        ctx = styled.get_style_context()
        for css in self.STATUS_CLASSES.values():
            ctx.remove_class(css)
        ctx.add_class(self.STATUS_CLASSES.get(props.get("Status"), "active"))

    def _on_press(self, widget, event, item):
        if event.type != Gdk.EventType.BUTTON_PRESS or item.props is None:
            return True
        coords = GLib.Variant("(ii)", (int(event.x_root), int(event.y_root)))
        if event.button == 1:
            if item.props.get("ItemIsMenu") and item.menu_path:
                popup_menu(item, widget, event)
            else:
                trigger = event.copy()
                item.call("Activate", coords, on_error=lambda: popup_menu(item, widget, trigger))
        elif event.button == 2:
            item.call("SecondaryActivate", coords)
        elif event.button == 3:
            if item.menu_path:
                popup_menu(item, widget, event)
            else:
                item.call("ContextMenu", coords)
        return True

    def _on_scroll(self, _widget, event, item):
        if event.direction == Gdk.ScrollDirection.UP:
            item.call("Scroll", GLib.Variant("(is)", (1, "vertical")))
        elif event.direction == Gdk.ScrollDirection.DOWN:
            item.call("Scroll", GLib.Variant("(is)", (-1, "vertical")))
        return True

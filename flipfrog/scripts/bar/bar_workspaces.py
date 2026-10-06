"""Workspaces numerados (con o sin íconos de ventanas adentro) y la fila
única de íconos de todas las ventanas ("Iconos en Workspaces" con
"Espacios de Trabajo" apagado).

- "Agrupar apps": un ícono por app con el número de ventanas al lado.
  Clic izquierdo enfoca (con varias, cada clic pasa a la siguiente),
  medio cierra la más reciente, derecho abre un menú con cada ventana
  (izquierdo ahí = ir, medio = cerrar).
- "Compactar workspaces": un workspace que no se ve en ningún monitor
  muestra solo su número y cuántas ventanas tiene; sus íconos aparecen
  al pasar el mouse."""

import re

from gi.repository import Gdk, Gtk

import bar_hypr
import bar_icons
from bar_modules import clickable, styled_box, track_hover

ICON_SIZE = 18
PERSISTENT = ["1", "2"]
FORMAT_ICONS = {"gaming": "👾"}
IGNORE = [re.compile(p) for p in (
    "^$", ".*anticheat.*", ".*battleye.*", ".*gameguard.*",
    ".*punkbuster.*", ".*vanguard.*", ".*xigncode.*",
)]


def _ignored(client):
    return any(r.search(client.get("class") or "") or r.search(client.get("title") or "")
               for r in IGNORE)


def _visible_clients(snap):
    return [c for c in snap["clients"]
            if c.get("mapped", True) and not c.get("hidden")
            and not c["workspace"]["name"].startswith("special:")
            and not _ignored(c)]


def _workspace_list(snap):
    by_name = {w["name"]: w for w in snap["workspaces"] if not w["name"].startswith("special:")}
    for name in PERSISTENT:
        by_name.setdefault(name, {"id": int(name), "name": name})
    return sorted(by_name.values(),
                  key=lambda w: (not w["name"].isdigit(), int(w["name"]) if w["name"].isdigit() else w["id"]))


def _class_of(client):
    return client.get("class") or client.get("initialClass") or ""


def _groups(clients, group_apps):
    """[[cliente, ...], ...] en orden de aparición: una lista por app si
    se agrupa, una por ventana si no."""
    if not group_apps:
        return [[c] for c in clients]
    groups = {}
    for c in clients:
        groups.setdefault(_class_of(c), []).append(c)
    return list(groups.values())


def _most_recent(group):
    return min(group, key=lambda c: c.get("focusHistoryID", 1 << 30))


def _focus_group(group, active_address):
    """Una ventana: enfocarla. Varias: si ya tiene el foco una del grupo,
    pasar a la siguiente; si no, a la usada más recientemente."""
    addresses = [c["address"] for c in group]
    if active_address in addresses:
        target = addresses[(addresses.index(active_address) + 1) % len(addresses)]
    else:
        target = _most_recent(group)["address"]
    bar_hypr.focus_window(target)


def _group_menu(group, widget, event):
    menu = Gtk.Menu()
    for c in group:
        item = Gtk.MenuItem(label=c.get("title") or _class_of(c))
        item.connect("button-release-event", _on_menu_item_release, c["address"])
        menu.append(item)
    menu.attach_to_widget(widget, None)
    menu.connect("deactivate", lambda m: m.destroy())
    menu.show_all()
    menu.popup_at_widget(widget, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, event)


def _on_menu_item_release(item, event, address):
    if event.button == 2:
        bar_hypr.close_window(address)
    else:
        bar_hypr.focus_window(address)
    item.get_parent().deactivate()
    return True


def _tooltip(group):
    return "\n".join(c.get("title") or _class_of(c) for c in group)


class _HyprBox(Gtk.Box):
    """Base: se suscribe al estado de Hyprland y rearma solo cuando cambia
    la estructura; el foco solo cambia clases y tooltips."""

    def __init__(self, hypr, scale, name, group_apps):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_name(name)
        self._scale = scale
        self._group_apps = group_apps
        self._key = None
        self._items = {}
        self._tooltips = {}
        self._snap = None
        hypr.subscribe(self._on_snapshot, self)

    def _app_icon(self, group, css_class):
        """Ícono de una app (o de una ventana suelta) con sus acciones."""
        image = bar_icons.image_for_class(_class_of(group[0]), ICON_SIZE, self._scale)
        styled = styled_box(image)
        styled.get_style_context().add_class(css_class)
        if len(group) > 1:
            badge = Gtk.Label(label=str(len(group)))
            badge.get_style_context().add_class("app-count")
            styled.pack_start(badge, False, False, 0)

        addresses = [c["address"] for c in group]

        def current_group():
            clients = (self._snap or {}).get("clients", [])
            live = [c for c in clients if c["address"] in addresses]
            return live or group

        def on_right():
            event = Gtk.get_current_event()
            _group_menu(current_group(), box, event)

        box = clickable(styled, {
            1: lambda: _focus_group(current_group(), (self._snap or {}).get("active_window")),
            2: lambda: bar_hypr.close_window(_most_recent(current_group())["address"]),
            3: on_right,
        })
        self._tooltips[tuple(addresses)] = box
        return box, styled

    def _on_snapshot(self, snap):
        self._snap = snap
        clients = _visible_clients(snap)
        key = self._structure_key(snap, clients)
        if key != self._key:
            self._key = key
            for child in self.get_children():
                child.destroy()
            self._items.clear()
            self._tooltips.clear()
            self._build(snap, clients)
            self.show_all()
            self._after_show()

        by_address = {c["address"]: c for c in clients}
        for addresses, box in self._tooltips.items():
            text = _tooltip([by_address[a] for a in addresses if a in by_address])
            if box.get_tooltip_text() != text:
                box.set_tooltip_text(text)

        active = self._active_keys(snap)
        for item_key, widget in self._items.items():
            ctx = widget.get_style_context()
            if item_key in active:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

    def _after_show(self):
        pass


class WorkspacesWidget(_HyprBox):
    def __init__(self, hypr, scale, show_icons, group_apps, compact):
        self._show_icons = show_icons
        self._compact = compact
        self._collapsible = []
        super().__init__(hypr, scale, "workspaces", group_apps)

    def _structure_key(self, snap, clients):
        shown = snap["shown_workspaces"] if self._compact else ()
        return tuple(
            (w["name"], w["name"] in shown,
             tuple(tuple((c["address"], _class_of(c)) for c in g)
                   for g in _groups([c for c in clients if c["workspace"]["name"] == w["name"]],
                                    self._group_apps)) if self._show_icons else ())
            for w in _workspace_list(snap))

    def _active_keys(self, snap):
        return {snap["active_workspace"]}

    def _build(self, snap, clients):
        self._collapsible = []
        for ws in _workspace_list(snap):
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            inner.get_style_context().add_class("ws-button")
            label = Gtk.Label(label=FORMAT_ICONS.get(ws["name"], ws["name"]))
            label.get_style_context().add_class("ws-label")
            inner.pack_start(label, False, False, 0)

            ws_clients = [c for c in clients if c["workspace"]["name"] == ws["name"]]
            icons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            if self._show_icons:
                for group in _groups(ws_clients, self._group_apps):
                    icon, _styled = self._app_icon(group, "ws-icon")
                    icons.pack_start(icon, False, False, 0)
            inner.pack_start(icons, False, False, 0)

            box = clickable(inner, {1: lambda ws=ws: bar_hypr.focus_workspace(ws)})
            track_hover(box, inner)

            if (self._compact and self._show_icons and ws_clients
                    and ws["name"] not in snap["shown_workspaces"]):
                count = Gtk.Label(label=str(len(ws_clients)))
                count.get_style_context().add_class("ws-count")
                inner.pack_start(count, False, False, 0)
                self._collapsible.append((icons, count))
                box.connect("enter-notify-event", lambda _w, _e, i=icons, c=count: self._expand(i, c, True))
                box.connect("leave-notify-event", self._on_compact_leave, icons, count)

            self._items[ws["name"]] = inner
            self.pack_start(box, False, False, 0)

    def _after_show(self):
        for icons, count in self._collapsible:
            self._expand(icons, count, False)

    @staticmethod
    def _expand(icons, count, expanded):
        icons.set_visible(expanded)
        count.set_visible(not expanded)

    def _on_compact_leave(self, _widget, event, icons, count):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self._expand(icons, count, False)


class TaskbarWidget(_HyprBox):
    def __init__(self, hypr, scale, group_apps):
        super().__init__(hypr, scale, "taskbar", group_apps)

    def _sorted_groups(self, clients):
        ordered = sorted(clients, key=lambda c: _class_of(c).lower())
        return _groups(ordered, self._group_apps)

    def _structure_key(self, snap, clients):
        return tuple(tuple((c["address"], _class_of(c)) for c in g) for g in self._sorted_groups(clients))

    def _active_keys(self, snap):
        active = snap["active_window"]
        return {key for key in self._items if active in key}

    def _build(self, snap, clients):
        for group in self._sorted_groups(clients):
            icon, styled = self._app_icon(group, "taskbar-button")
            track_hover(icon, styled)
            self._items[tuple(c["address"] for c in group)] = styled
            self.pack_start(icon, False, False, 0)

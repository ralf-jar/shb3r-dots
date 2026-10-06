"""Ícono de una ventana a partir de su clase: StartupWMClass de los
.desktop, luego el nombre del .desktop, luego el tema de íconos."""

from gi.repository import Gio, GLib, Gtk

FALLBACK_ICON = "application-x-executable"

_index = None
_surfaces = {}


def _build_index():
    by_wm_class = {}
    by_id = {}
    for app in Gio.AppInfo.get_all():
        if not isinstance(app, Gio.DesktopAppInfo) or app.get_icon() is None:
            continue
        icon = app.get_icon()
        wm_class = app.get_startup_wm_class()
        if wm_class:
            by_wm_class.setdefault(wm_class.lower(), icon)
        stem = (app.get_id() or "").lower()
        if stem.endswith(".desktop"):
            stem = stem[:-len(".desktop")]
        by_id.setdefault(stem, icon)
        by_id.setdefault(stem.rsplit(".", 1)[-1], icon)
    return by_wm_class, by_id


def _invalidate(*_args):
    global _index
    _index = None
    _surfaces.clear()


Gio.AppInfoMonitor.get().connect("changed", _invalidate)
Gtk.IconTheme.get_default().connect("changed", _invalidate)


def _gicon_for_class(window_class):
    global _index
    if _index is None:
        _index = _build_index()
    by_wm_class, by_id = _index
    key = (window_class or "").lower()
    for candidate in (by_wm_class.get(key), by_id.get(key), by_id.get(key.rsplit(".", 1)[-1])):
        if candidate is not None:
            return candidate
    if key and Gtk.IconTheme.get_default().has_icon(key):
        return Gio.ThemedIcon.new(key)
    return Gio.ThemedIcon.new(FALLBACK_ICON)


def image_for_class(window_class, size, scale):
    key = (window_class, size, scale)
    surface = _surfaces.get(key)
    if surface is None:
        theme = Gtk.IconTheme.get_default()
        flags = Gtk.IconLookupFlags.FORCE_SIZE
        info = theme.lookup_by_gicon_for_scale(_gicon_for_class(window_class), size, scale, flags)
        if info is None:
            info = theme.lookup_icon_for_scale(FALLBACK_ICON, size, scale, flags)
        try:
            surface = info.load_surface(None) if info is not None else None
        except GLib.Error:
            surface = None
        _surfaces[key] = surface
    if surface is None:
        return Gtk.Image.new_from_icon_name(FALLBACK_ICON, Gtk.IconSize.LARGE_TOOLBAR)
    return Gtk.Image.new_from_surface(surface)

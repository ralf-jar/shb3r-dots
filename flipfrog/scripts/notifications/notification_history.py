#!/usr/bin/env python3
"""Popup "Notificaciones" (SUPER+N) -- historial de dunst (`dunstctl
history`), agrupado por día. Anclado arriba a la derecha, la misma
esquina donde dunst muestra las notificaciones (origin=top-right en
dunstrc). Volver a mostrar = `history-pop`, borrar = `history-rm`,
"Limpiar todas" = `history-clear`. Sin botón de cerrar (pedido explícito
del usuario) -- se cierra con click afuera/Esc como el resto de popups.
Lo de más de MAX_AGE_S se borra de dunst en cada sondeo; el tope de 100
es `history_length` de dunstrc."""

import html
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
from waybar_lib import kill_group, kill_existing, build_layer_window, load_module_css
from i18n import t
import dnd_state

CSS_FILE = os.path.join(SCRIPT_DIR, "notification_history.css")
# Switch "No molestar" con el mismo estilo que los toggles del dashboard
# (sigue el redondeo global de sync_radius.py).
TOGGLE_CSS = os.path.join(os.path.dirname(SCRIPT_DIR), "toggles", "toggle_common.css")
LOCK = "/tmp/notification-history.pid"
POLL_MS = 2000
TIMEOUT = 2
ICON_SIZE = 32
LIST_HEIGHT = 560
MAX_AGE_S = 24 * 3600
MARKUP_RE = re.compile(r"<[^>]+>")


def _dunstctl(*args):
    try:
        return subprocess.run(["dunstctl", *args], capture_output=True, text=True,
                              timeout=TIMEOUT).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None


def _plain(text):
    return html.unescape(MARKUP_RE.sub("", text or "")).strip()


_FILE_ICONS = {}


def _file_icon(path):
    """Pixbuf de un ícono que la app mandó como ruta -- seguro en el hilo
    worker (solo GdkPixbuf), cacheado por ruta."""
    if path not in _FILE_ICONS:
        try:
            _FILE_ICONS[path] = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, ICON_SIZE, ICON_SIZE, True)
        except GLib.Error:
            _FILE_ICONS[path] = None
    return _FILE_ICONS[path]


def _theme_icon(names):
    """Ícono del tema por nombre -- solo en el hilo de GTK."""
    theme = Gtk.IconTheme.get_default()
    for name in names:
        if name and theme.has_icon(name):
            try:
                return theme.load_icon(name, ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
            except GLib.Error:
                pass
    return None


def fetch_history():
    """[{"id", "app", "summary", "body", "when", "critical", "icon"}],
    lo más nuevo primero. None si dunst no responde. `timestamp` de
    dunst es monotónico en microsegundos (g_get_monotonic_time) -- se
    pasa a hora real con el mismo reloj de Python."""
    out = _dunstctl("history")
    if out is None:
        return None
    try:
        entries = json.loads(out)["data"][0]
    except (ValueError, KeyError, IndexError):
        return None

    offset = time.time() - time.monotonic()
    items = []
    for e in entries:
        get = lambda k, d="": e.get(k, {}).get("data", d)
        items.append({
            "id": get("id", 0),
            "app": get("appname"),
            "summary": _plain(get("summary")),
            "body": _plain(get("body")),
            "when": get("timestamp", 0) / 1e6 + offset,
            "critical": get("urgency") == "CRITICAL",
            "icon_path": get("icon_path"),
        })
    cutoff = time.time() - MAX_AGE_S
    for item in items:
        if item["when"] < cutoff:
            _dunstctl("history-rm", str(item["id"]))
    items = [i for i in items if i["when"] >= cutoff]
    items.sort(key=lambda i: i["when"], reverse=True)
    return items


def _day_label(when):
    day = datetime.fromtimestamp(when).date()
    today = datetime.now().date()
    if day == today:
        return t("notificaciones", "hoy")
    if day == today - timedelta(days=1):
        return t("notificaciones", "ayer")
    return day.strftime("%d/%m/%Y")


def _relative(when):
    delta = time.time() - when
    if delta < 60:
        return t("notificaciones", "ahora")
    if delta < 3600:
        return t("notificaciones", "hace_min", n=int(delta // 60))
    return datetime.fromtimestamp(when).strftime("%H:%M")


def _icon_button(icon, tooltip):
    btn = Gtk.Button()
    btn.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR))
    btn.set_tooltip_text(tooltip)
    btn.set_valign(Gtk.Align.START)
    btn.get_style_context().add_class("nh-icon-btn")
    return btn


class NotificationHistory:
    def __init__(self):
        self.window, container = build_layer_window("notification-history", CSS_FILE)
        container.set_name("nh-container")
        anchor = container.get_parent()
        anchor.set_halign(Gtk.Align.END)
        anchor.set_valign(Gtk.Align.START)
        anchor.set_margin_top(60)
        anchor.set_margin_end(25)

        self.signature = None
        self._poll_in_flight = False
        self._dnd_lock = False

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=t("notificaciones", "titulo"))
        title.set_name("nh-title")
        header.pack_start(title, False, False, 0)
        self.count_label = Gtk.Label(label="0")
        self.count_label.set_name("nh-count")
        self.count_label.set_valign(Gtk.Align.CENTER)
        header.pack_start(self.count_label, False, False, 0)
        self.clear_btn = Gtk.Button(label=t("notificaciones", "limpiar_todas"))
        self.clear_btn.set_valign(Gtk.Align.CENTER)
        self.clear_btn.set_sensitive(False)
        self.clear_btn.get_style_context().add_class("nh-clear-btn")
        self.clear_btn.connect("clicked", lambda _b: self._run("history-clear"))
        header.pack_start(self.clear_btn, False, False, 0)

        load_module_css(TOGGLE_CSS)
        self.dnd_switch = Gtk.Switch()
        self.dnd_switch.get_style_context().add_class("toggle-row-switch")
        self.dnd_switch.set_valign(Gtk.Align.CENTER)
        self.dnd_switch.set_sensitive(False)
        self.dnd_switch.connect("state-set", self._on_dnd)
        header.pack_end(self.dnd_switch, False, False, 0)
        dnd_label = Gtk.Label(label=t("notificaciones", "no_molestar"))
        dnd_label.set_tooltip_text(t("notificaciones", "no_molestar_tooltip"))
        dnd_label.get_style_context().add_class("nh-dnd-label")
        header.pack_end(dnd_label, False, False, 0)
        container.pack_start(header, False, False, 0)

        self.listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.empty_label = Gtk.Label(label=t("notificaciones", "vacio"))
        self.empty_label.get_style_context().add_class("nh-empty")
        self.empty_label.set_no_show_all(True)

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("nh-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(LIST_HEIGHT)
        scroller.set_max_content_height(LIST_HEIGHT)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        inner.pack_start(self.empty_label, False, False, 0)
        inner.pack_start(self.listbox, False, False, 0)
        scroller.add(inner)
        container.pack_start(scroller, True, True, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

        threading.Thread(target=self._fetch_dnd, daemon=True).start()
        self._poll()
        GLib.timeout_add(POLL_MS, self._poll)

    # ---- historial -------------------------------------------------------

    def _poll(self):
        if not self._poll_in_flight:
            self._poll_in_flight = True
            threading.Thread(target=self._fetch, daemon=True).start()
        return True

    def _fetch(self):
        items = fetch_history()
        for item in items or []:
            path = item["icon_path"]
            item["pixbuf"] = _file_icon(path) if path and os.path.isfile(path) else None
        GLib.idle_add(self._apply, items)

    def _apply(self, items):
        self._poll_in_flight = False
        if items is None:
            items = []
        sig = [(i["id"], _relative(i["when"])) for i in items]
        if sig == self.signature:
            return False
        self.signature = sig

        for child in self.listbox.get_children():
            self.listbox.remove(child)
        current_day = None
        for item in items:
            day = _day_label(item["when"])
            if day != current_day:
                current_day = day
                label = Gtk.Label(label=day.upper(), xalign=0)
                label.get_style_context().add_class("nh-day")
                self.listbox.pack_start(label, False, False, 0)
            self.listbox.pack_start(self._row(item), False, False, 0)

        self.count_label.set_text(str(len(items)))
        self.clear_btn.set_sensitive(bool(items))
        self.empty_label.set_visible(not items)
        self.listbox.show_all()
        return False

    def _row(self, item):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("nh-row")
        if item["critical"]:
            row.get_style_context().add_class("nh-critical")

        pixbuf = item["pixbuf"] or _theme_icon((item["icon_path"], (item["app"] or "").lower()))
        if pixbuf:
            icon = Gtk.Image.new_from_pixbuf(pixbuf)
        else:
            icon = Gtk.Image.new_from_icon_name("preferences-system-notifications-symbolic", Gtk.IconSize.DND)
            icon.get_style_context().add_class("nh-icon-placeholder")
        icon.set_valign(Gtk.Align.START)
        icon.set_size_request(ICON_SIZE, ICON_SIZE)
        row.pack_start(icon, False, False, 0)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        app = Gtk.Label(label=item["app"] or "", xalign=0)
        app.set_ellipsize(Pango.EllipsizeMode.END)
        app.set_max_width_chars(24)
        app.get_style_context().add_class("nh-app")
        meta.pack_start(app, True, True, 0)
        when = Gtk.Label(label=_relative(item["when"]), xalign=1.0)
        when.set_width_chars(10)
        when.set_tooltip_text(datetime.fromtimestamp(item["when"]).strftime("%d/%m/%Y %H:%M:%S"))
        when.get_style_context().add_class("nh-time")
        meta.pack_start(when, False, False, 0)
        text.pack_start(meta, False, False, 0)

        if item["summary"]:
            summary = Gtk.Label(label=item["summary"], xalign=0)
            summary.set_line_wrap(True)
            summary.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            summary.set_max_width_chars(1)
            summary.get_style_context().add_class("nh-summary")
            text.pack_start(summary, False, False, 0)
        if item["body"] and item["body"] != item["summary"]:
            body = Gtk.Label(label=item["body"], xalign=0)
            body.set_line_wrap(True)
            body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body.set_max_width_chars(1)
            body.set_lines(4)
            body.set_ellipsize(Pango.EllipsizeMode.END)
            body.set_tooltip_text(item["body"] if len(item["body"]) > 160 else None)
            body.get_style_context().add_class("nh-body")
            text.pack_start(body, False, False, 0)
        row.pack_start(text, True, True, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        show = _icon_button("view-reveal-symbolic", t("notificaciones", "volver_mostrar"))
        show.connect("clicked", lambda _b: self._run("history-pop", str(item["id"])))
        actions.pack_start(show, False, False, 0)
        remove = _icon_button("window-close-symbolic", t("notificaciones", "borrar"))
        remove.connect("clicked", lambda _b: self._run("history-rm", str(item["id"])))
        actions.pack_start(remove, False, False, 0)
        row.pack_start(actions, False, False, 0)
        return row

    def _run(self, *args):
        def work():
            _dunstctl(*args)
            GLib.idle_add(self._poll)
        threading.Thread(target=work, daemon=True).start()

    # ---- no molestar -----------------------------------------------------------

    def _fetch_dnd(self):
        paused = (_dunstctl("is-paused") or "").strip() == "true"
        GLib.idle_add(self._apply_dnd, paused)

    def _apply_dnd(self, paused):
        self._dnd_lock = True
        self.dnd_switch.set_active(paused)
        self._dnd_lock = False
        self.dnd_switch.set_sensitive(True)
        return False

    def _on_dnd(self, _switch, state):
        if not self._dnd_lock:
            def work():
                dnd_state.set_paused(state)
                GLib.idle_add(self._poll)
            threading.Thread(target=work, daemon=True).start()
        return False

    # ---- ciclo de vida -----------------------------------------------------------

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_args):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    NotificationHistory()
    Gtk.main()


if __name__ == "__main__":
    main()

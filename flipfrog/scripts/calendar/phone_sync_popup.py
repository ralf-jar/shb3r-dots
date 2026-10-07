#!/usr/bin/env python3
"""Asistente "Calendario en tu celular": guía para sincronizar las notas
del calendario con Android (DAVx⁵ + Etar) y switch que abre Radicale a
la red local (phone_sync.py). Se abre desde el calendario o el lanzador."""

import os
import subprocess
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from waybar_lib import (kill_group, kill_existing, build_layer_window, position_fixed_top,
                        build_switch_row, svg_icon_image, load_module_css, suppress_close)
from i18n import t
import phone_sync

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CSS_FILE = os.path.join(SCRIPT_DIR, "phone_sync_popup.css")
TOGGLE_CSS = os.path.join(SCRIPT_DIR, "..", "toggles", "toggle_common.css")
NETWORK_ICON = os.path.join(SCRIPT_DIR, "..", "toggles", "icons", "network.svg")
LOCK = "/tmp/calendar-sync.pid"

APPS = (
    ("DAVx⁵", "sync_davx5_desc", "https://play.google.com/store/apps/details?id=at.bitfire.davdroid"),
    ("Etar", "sync_etar_desc", "https://play.google.com/store/apps/details?id=ws.xsoh.etar"),
)
QR_SCALE = 3


def qr_image(text):
    """Código QR negro sobre blanco (los lectores fallan con colores del
    tema). qrencode viene con CachyOS."""
    try:
        png = subprocess.run(["qrencode", "-t", "PNG", "-o", "-", "-s", str(QR_SCALE), "-m", "1", text],
                             capture_output=True, timeout=5, check=True).stdout
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(png)
        loader.close()
        image = Gtk.Image.new_from_pixbuf(loader.get_pixbuf())
    except (OSError, subprocess.SubprocessError, GLib.Error):
        image = Gtk.Image.new_from_icon_name("image-missing-symbolic", Gtk.IconSize.DIALOG)
    box = Gtk.Box()
    box.get_style_context().add_class("qr")
    box.set_halign(Gtk.Align.CENTER)
    box.pack_start(image, False, False, 0)
    return box


def copy(text):
    subprocess.Popen(["wl-copy", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class PhoneSyncPopup:
    def __init__(self):
        self.window, container = build_layer_window("calendar-sync", CSS_FILE)
        load_module_css(TOGGLE_CSS)
        container.set_name("sync-container")
        position_fixed_top(container, margin=60)

        self.ip, _subnet = phone_sync.network()

        title = Gtk.Label(label=t("calendario", "sync_titulo"))
        title.set_name("sync-title")
        container.pack_start(title, False, False, 0)

        subtitle = Gtk.Label(label=t("calendario", "sync_subtitulo"))
        subtitle.set_name("sync-subtitle")
        subtitle.set_line_wrap(True)
        subtitle.set_max_width_chars(1)
        subtitle.set_justify(Gtk.Justification.CENTER)
        container.pack_start(subtitle, False, False, 0)

        # 2×2: en una sola columna no cabe en una pantalla de 1080 de alto.
        grid = Gtk.Grid(column_spacing=22, row_spacing=6)
        grid.set_column_homogeneous(True)
        grid.attach(self._step(1, t("calendario", "sync_paso1"), self._build_apps()), 0, 0, 1, 1)
        grid.attach(self._step(2, t("calendario", "sync_paso2"), self._build_switch()), 1, 0, 1, 1)
        self.login_step = self._step(3, t("calendario", "sync_paso3"), self._build_login())
        grid.attach(self.login_step, 0, 1, 1, 1)
        self.final_step = self._step(4, t("calendario", "sync_paso4"), self._text(t("calendario", "sync_paso4_desc")))
        grid.attach(self.final_step, 1, 1, 1, 1)
        container.pack_start(grid, False, False, 0)

        self._sync_sensitivity(phone_sync.lan_enabled())

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

    def _step(self, number, title, body):
        step = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        step.get_style_context().add_class("sync-step")

        badge = Gtk.Label(label=str(number))
        badge.get_style_context().add_class("sync-badge")
        badge.set_valign(Gtk.Align.START)
        step.pack_start(badge, False, False, 0)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        heading = Gtk.Label(label=title)
        heading.get_style_context().add_class("sync-step-title")
        heading.set_xalign(0)
        heading.set_line_wrap(True)
        heading.set_max_width_chars(1)
        column.pack_start(heading, False, False, 0)
        column.pack_start(body, False, False, 0)
        step.pack_start(column, True, True, 0)
        return step

    def _text(self, text):
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("sync-text")
        label.set_xalign(0)
        label.set_line_wrap(True)
        label.set_max_width_chars(1)
        return label

    def _build_apps(self):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_homogeneous(True)
        for name, desc_code, link in APPS:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.get_style_context().add_class("sync-card")
            card.pack_start(qr_image(link), False, False, 0)
            label = Gtk.Label(label=name)
            label.get_style_context().add_class("sync-app-name")
            card.pack_start(label, False, False, 0)
            desc = Gtk.Label(label=t("calendario", desc_code))
            desc.get_style_context().add_class("sync-text")
            desc.set_line_wrap(True)
            desc.set_max_width_chars(1)
            desc.set_justify(Gtk.Justification.CENTER)
            card.pack_start(desc, False, False, 0)
            row.pack_start(card, True, True, 0)
        return row

    def _build_switch(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        row = Gtk.Box()
        _wrapper, self.switch, self.handler = build_switch_row(
            row, svg_icon_image(NETWORK_ICON), t("calendario", "sync_switch"),
            phone_sync.lan_enabled(), self._on_toggle)
        box.pack_start(row, False, False, 0)
        self.status = self._text(t("calendario", "sync_switch_desc"))
        box.pack_start(self.status, False, False, 0)
        return box

    def _build_login(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        address = phone_sync.url(self.ip) if self.ip else t("calendario", "sync_sin_red")
        rows = (
            (t("calendario", "sync_url"), address, bool(self.ip)),
            (t("calendario", "sync_usuario"), phone_sync.USER, True),
            (t("calendario", "sync_contrasena"), t("calendario", "sync_contrasena_valor"), False),
        )
        for i, (key, value, copyable) in enumerate(rows):
            k = Gtk.Label(label=key)
            k.get_style_context().add_class("sync-key")
            k.set_xalign(1)
            grid.attach(k, 0, i, 1, 1)
            v = Gtk.Label(label=value)
            v.get_style_context().add_class("sync-value")
            v.set_xalign(0)
            v.set_selectable(True)
            grid.attach(v, 1, i, 1, 1)
            if copyable:
                btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.BUTTON)
                btn.get_style_context().add_class("sync-copy")
                btn.set_tooltip_text(t("calendario", "sync_copiar"))
                btn.connect("clicked", lambda _b, text=value: copy(text))
                grid.attach(btn, 2, i, 1, 1)
        box.pack_start(grid, False, False, 0)

        if self.ip:
            qr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            qr_row.pack_start(qr_image(phone_sync.url(self.ip)), False, False, 0)
            hint = Gtk.Label(label=t("calendario", "sync_qr_prueba"))
            hint.get_style_context().add_class("sync-hint")
            hint.set_xalign(0)
            hint.set_line_wrap(True)
            hint.set_max_width_chars(1)
            qr_row.pack_start(hint, True, True, 0)
            box.pack_start(qr_row, False, False, 0)
        return box

    def _sync_sensitivity(self, enabled):
        self.login_step.set_sensitive(enabled)
        self.final_step.set_sensitive(enabled)

    def _on_toggle(self, state):
        # pkexec abre su diálogo en otro proceso: la ventana layer-shell se
        # lo comería si sigue a la vista.
        self.switch.set_sensitive(False)
        self.status.set_text(t("calendario", "sync_aplicando"))
        suppress_close(60)
        self.window.hide()
        threading.Thread(target=self._apply, args=(state,), daemon=True).start()

    def _apply(self, state):
        ok, msg = phone_sync.set_lan(state)
        GLib.idle_add(self._apply_done, state, ok, msg)

    def _apply_done(self, state, ok, msg):
        self.window.show()
        suppress_close(1)
        enabled = state if ok else not state
        self.switch.handler_block(self.handler)
        self.switch.set_active(enabled)
        self.switch.handler_unblock(self.handler)
        self.switch.set_sensitive(True)
        self.status.set_text(t("calendario", "sync_switch_desc") if ok
                             else t("calendario", "sync_error", msg=msg))
        self._sync_sensitivity(enabled)
        return False

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
    PhoneSyncPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Popup "Configurar VPN" (botón en la pestaña "Red"): elegir la rutina
de comandos que usa el switch de VPN y crear/editar las propias. Las
predeterminadas son de solo lectura (se duplican para editarlas). Los
cambios viven en memoria hasta "Guardar y usar"."""

import os
import sys
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
import vpn_profiles
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top
from i18n import t

CSS_FILE = os.path.join(SCRIPT_DIR, "vpn_config_popup.css")
LOCK = "/tmp/vpn-config.pid"

# (campo, código de etiqueta, código de tooltip)
FORM = [
    ("name", "campo_nombre", None),
    ("connect", "campo_conectar", "tip_conectar"),
    ("disconnect", "campo_desconectar", "tip_desconectar"),
    ("status", "campo_estado", "tip_estado"),
    ("connected_text", "campo_texto", "tip_texto"),
    ("interface", "campo_interfaz", "tip_interfaz"),
]


class VpnConfigPopup:
    def __init__(self):
        self.window, container = build_layer_window("vpn-config", CSS_FILE)
        container.set_name("vpn-container")
        position_fixed_top(container, margin=60)

        self.data = vpn_profiles.load()
        self.profiles = vpn_profiles.all_profiles(self.data)
        self.current = None
        self._test_in_flight = False

        title = Gtk.Label(label=t("vpn", "titulo"))
        title.set_name("vpn-title")
        container.pack_start(title, False, False, 0)

        self.in_use = Gtk.Label()
        self.in_use.set_name("vpn-in-use")
        container.pack_start(self.in_use, False, False, 0)

        top = Gtk.Box(spacing=8)
        top.set_name("vpn-top")
        top.pack_start(self._field_label(t("vpn", "rutina")), False, False, 0)

        self.combo = Gtk.ComboBoxText()
        self.combo.set_hexpand(True)
        self.combo.connect("changed", self._on_select)
        top.pack_start(self.combo, True, True, 0)

        self.dup_btn = self._button(t("vpn", "duplicar"), self._on_duplicate)
        self.del_btn = self._button(t("vpn", "eliminar"), self._on_delete)
        top.pack_start(self._button(t("vpn", "nueva"), self._on_new), False, False, 0)
        top.pack_start(self.dup_btn, False, False, 0)
        top.pack_start(self.del_btn, False, False, 0)
        container.pack_start(top, False, False, 0)

        self.readonly = Gtk.Label(label=t("vpn", "predeterminada"))
        self.readonly.set_name("vpn-readonly")
        self.readonly.set_halign(Gtk.Align.START)
        self.readonly.set_no_show_all(True)
        container.pack_start(self.readonly, False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        grid.set_name("vpn-form")
        self.entries = {}
        for row, (key, label_code, tip_code) in enumerate(FORM):
            label = self._field_label(t("vpn", label_code))
            entry = Gtk.Entry()
            entry.set_hexpand(True)
            entry.set_width_chars(44)
            if tip_code:
                label.set_tooltip_text(t("vpn", tip_code))
                entry.set_tooltip_text(t("vpn", tip_code))
            grid.attach(label, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
            self.entries[key] = entry
        container.pack_start(grid, False, False, 0)

        bottom = Gtk.Box(spacing=8)
        bottom.set_name("vpn-bottom")
        self.result = Gtk.Label()
        self.result.set_name("vpn-result")
        self.result.set_halign(Gtk.Align.START)
        self.result.set_ellipsize(Pango.EllipsizeMode.END)
        self.result.set_max_width_chars(40)
        bottom.pack_start(self.result, True, True, 0)

        self.test_btn = self._button(t("vpn", "probar"), self._on_test)
        self.save_btn = self._button(t("vpn", "guardar"), self._on_save)
        self.save_btn.get_style_context().add_class("vpn-primary-btn")
        bottom.pack_start(self.test_btn, False, False, 0)
        bottom.pack_start(self.save_btn, False, False, 0)
        container.pack_start(bottom, False, False, 0)

        self._fill_combo(self.data["active"])
        self._update_in_use()
        self.window.connect("destroy", Gtk.main_quit)
        self.window.connect("key-press-event", self._on_key)
        self.window.show_all()

    # ---- widgets ----

    @staticmethod
    def _field_label(text):
        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        label.get_style_context().add_class("vpn-field-label")
        return label

    @staticmethod
    def _button(text, callback):
        btn = Gtk.Button(label=text)
        btn.get_style_context().add_class("vpn-btn")
        btn.connect("clicked", lambda _b: callback())
        return btn

    def _fill_combo(self, select_id):
        self.combo.remove_all()
        for p in self.profiles:
            self.combo.append(p["id"], p["name"] or t("vpn", "sin_nombre"))
        if not self.combo.set_active_id(select_id):
            self.combo.set_active(0)

    def _update_in_use(self):
        name = next((p["name"] for p in self.profiles if p["id"] == self.data["active"]),
                    self.profiles[0]["name"])
        self.in_use.set_text(t("vpn", "en_uso", name=name))

    def _profile(self, pid):
        return next((p for p in self.profiles if p["id"] == pid), None)

    # ---- edición en memoria ----

    def _flush(self):
        """Pasa lo escrito en el formulario a la rutina en pantalla."""
        p = self.current
        if p is None or p["builtin"]:
            return
        for key, entry in self.entries.items():
            p[key] = entry.get_text().strip() if key != "connected_text" else entry.get_text()

    def _on_select(self, _combo):
        pid = self.combo.get_active_id()
        if pid is None:
            return
        if self.current is not None and self.current["id"] != pid:
            self._flush()
        self.current = self._profile(pid)
        builtin = self.current["builtin"]
        for key, entry in self.entries.items():
            entry.set_text(self.current.get(key, ""))
            entry.set_editable(not builtin)
            ctx = entry.get_style_context()
            (ctx.add_class if builtin else ctx.remove_class)("readonly")
        self.readonly.set_visible(builtin)
        self.del_btn.set_sensitive(not builtin)
        self.save_btn.set_label(t("vpn", "usar") if builtin else t("vpn", "guardar"))
        self.result.set_text("")

    def _add_custom(self, base):
        self._flush()
        profile = {k: base.get(k, "") for k in ("name",) + vpn_profiles.FIELDS}
        profile["id"] = f"custom-{int(time.time() * 1000)}"
        profile["builtin"] = False
        self.profiles.append(profile)
        self._fill_combo(profile["id"])
        self.entries["name"].grab_focus()

    def _on_new(self):
        self._add_custom({"name": t("vpn", "nueva_nombre")})

    def _on_duplicate(self):
        self._flush()
        base = dict(self.current, name=t("vpn", "copia", name=self.current["name"]))
        self._add_custom(base)

    def _on_delete(self):
        if self.current is None or self.current["builtin"]:
            return
        self.profiles.remove(self.current)
        self.current = None
        if self.data["active"] not in (p["id"] for p in self.profiles):
            self.data["active"] = vpn_profiles.DEFAULT_ID
        self._write()
        self._fill_combo(self.data["active"])
        self._update_in_use()

    def _write(self):
        self.data["custom"] = [{k: v for k, v in p.items() if k != "builtin"}
                               for p in self.profiles if not p["builtin"]]
        vpn_profiles.save(self.data)

    def _on_save(self):
        self._flush()
        if not self.current["builtin"] and not self.current["name"]:
            self.current["name"] = t("vpn", "sin_nombre")
        self.data["active"] = self.current["id"]
        self._write()
        self._fill_combo(self.current["id"])
        self._update_in_use()
        self.result.set_text(t("vpn", "guardado"))

    # ---- probar estado ----

    def _on_test(self):
        if self._test_in_flight:
            return
        self._flush()
        profile = dict(self.current)
        self._test_in_flight = True
        self.test_btn.set_sensitive(False)
        self.result.set_text(t("vpn", "probando"))

        def worker():
            connected = vpn_profiles.status_connected(profile)
            GLib.idle_add(self._apply_test, connected)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_test(self, connected):
        self._test_in_flight = False
        self.test_btn.set_sensitive(True)
        code = {True: "resultado_conectada", False: "resultado_desconectada",
                None: "resultado_error"}[connected]
        self.result.set_text(t("vpn", code))
        return False

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        return False


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    VpnConfigPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

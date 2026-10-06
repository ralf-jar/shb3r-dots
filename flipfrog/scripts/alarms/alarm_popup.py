#!/usr/bin/env python3
"""Popup "Alarmas" -- click derecho en el reloj de la barra
(bar/bar_modules.py). Lista + editor en un
Gtk.Stack, mismo patrón que el editor de categorías de launcher.py.
Estado real en alarms.json (alarms_store.py, compartido con
alarm_daemon.py en autostart -- ver ese módulo para el porqué del
mkstemp de dos escritores).

Cada fila tiene switch/editar/borrar como botones explícitos en vez de
"click en la fila abre el editor" -- evita cualquier ambigüedad de
bubbling de eventos entre el switch/botones hijos y un click-handler de
fila (services_popup.py/bluetooth_tab.py combinan switch+botón en la
misma fila con este mismo criterio, sin click-en-fila)."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_near_cursor
from i18n import t

import alarms_store

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CSS_FILE = os.path.join(SCRIPT_DIR, "alarm_module.css")
LOCK = "/tmp/alarm-popup.pid"

DAY_CODES = ["dia_lun", "dia_mar", "dia_mie", "dia_jue", "dia_vie", "dia_sab", "dia_dom"]


class AlarmPopup:
    def __init__(self):
        self.window, container = build_layer_window("alarms", CSS_FILE)
        container.set_name("container")
        position_near_cursor(container)

        self.editing_id = None

        header = Gtk.Label(label=t("alarmas", "titulo"))
        header.set_name("header")
        header.set_halign(Gtk.Align.CENTER)
        container.pack_start(header, False, False, 0)

        self.list_page = self._build_list_page()
        self.editor_page = self._build_editor_page()

        self.stack = Gtk.Stack()
        self.stack.add_named(self.list_page, "list")
        self.stack.add_named(self.editor_page, "editor")
        container.pack_start(self.stack, True, True, 0)

        self._refresh_list()

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

    # ---- Página lista -------------------------------------------------

    def _build_list_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(160)
        scroller.set_max_content_height(320)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.get_style_context().add_class("listbox-alarms")
        scroller.add(self.listbox)
        box.pack_start(scroller, True, True, 0)

        add_btn = Gtk.Button()
        add_icon = Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        add_btn.set_image(add_icon)
        add_btn.set_always_show_image(True)
        add_btn.set_label(t("alarmas", "agregar_tooltip"))
        add_btn.get_style_context().add_class("alarm-add-btn")
        add_btn.connect("clicked", lambda *_: self._open_editor(None))
        box.pack_start(add_btn, False, False, 0)

        return box

    def _refresh_list(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)

        alarms = sorted(alarms_store.load_alarms(), key=lambda a: (a["hour"], a["minute"]))
        if not alarms:
            empty = Gtk.Label(label=t("alarmas", "vacio"))
            empty.get_style_context().add_class("alarm-empty")
            row = Gtk.ListBoxRow()
            row.set_selectable(False)
            row.add(empty)
            self.listbox.add(row)
        else:
            for alarm in alarms:
                self.listbox.add(self._make_row(alarm))
        self.listbox.show_all()

    def _make_row(self, alarm):
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.get_style_context().add_class("alarm-row")
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(8)
        box.set_margin_end(8)

        time_label = Gtk.Label(label=f"{alarm['hour']:02d}:{alarm['minute']:02d}")
        time_label.get_style_context().add_class("alarm-time")
        box.pack_start(time_label, False, False, 0)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_hexpand(True)
        info_box.set_halign(Gtk.Align.START)

        days = alarm.get("days") or []
        if days:
            days_text = " ".join(t("alarmas", DAY_CODES[d]) for d in sorted(days))
        else:
            days_text = t("alarmas", "una_vez")
        days_label = Gtk.Label(label=days_text)
        days_label.set_halign(Gtk.Align.START)
        days_label.get_style_context().add_class("alarm-days")
        info_box.pack_start(days_label, False, False, 0)

        if alarm.get("label"):
            name_label = Gtk.Label(label=alarm["label"])
            name_label.set_halign(Gtk.Align.START)
            name_label.set_ellipsize(Pango.EllipsizeMode.END)
            name_label.set_max_width_chars(18)
            name_label.get_style_context().add_class("alarm-label")
            info_box.pack_start(name_label, False, False, 0)

        box.pack_start(info_box, True, True, 0)

        switch = Gtk.Switch()
        switch.set_active(alarm.get("enabled", True))
        switch.set_valign(Gtk.Align.CENTER)
        switch.connect("state-set", self._on_toggle_enabled, alarm["id"])
        box.pack_start(switch, False, False, 0)

        edit_btn = Gtk.Button()
        edit_btn.add(Gtk.Image.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
        edit_btn.set_tooltip_text(t("alarmas", "editar_alarma"))
        edit_btn.get_style_context().add_class("alarm-edit-btn")
        edit_btn.connect("clicked", lambda _b, aid=alarm["id"]: self._open_editor(aid))
        box.pack_start(edit_btn, False, False, 0)

        delete_btn = Gtk.Button()
        delete_btn.add(Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
        delete_btn.set_tooltip_text(t("alarmas", "borrar_tooltip"))
        delete_btn.get_style_context().add_class("alarm-delete-btn")
        delete_btn.connect("clicked", self._on_delete_clicked, alarm["id"])
        box.pack_start(delete_btn, False, False, 0)

        row.add(box)
        return row

    def _on_toggle_enabled(self, _switch, state, alarm_id):
        alarms = alarms_store.load_alarms()
        for a in alarms:
            if a["id"] == alarm_id:
                a["enabled"] = state
                if state:
                    # Rearmar -- si se prende a mano una que se
                    # autoapagó hoy después de sonar (alarma "de una
                    # vez"), que no quede pegada al last_fired de hoy.
                    a["last_fired"] = None
                break
        alarms_store.save_alarms(alarms)
        return False

    def _on_delete_clicked(self, btn, alarm_id):
        win = btn.get_toplevel()
        dialog = Gtk.MessageDialog(
            transient_for=win, flags=0, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=t("alarmas", "borrar_confirm"),
        )
        # Workaround de layer-shell (ver bluetooth_tab.py/CLAUDE.md): la
        # ventana es Layer.TOP a pantalla completa y se come los clicks
        # del diálogo si no se oculta antes de dialog.run().
        original_prgname = GLib.get_prgname()
        GLib.set_prgname("alarm-delete-dialog")
        dialog.show_all()
        GLib.set_prgname(original_prgname)

        win.hide()
        response = dialog.run()
        dialog.destroy()
        win.show()

        if response == Gtk.ResponseType.YES:
            alarms = [a for a in alarms_store.load_alarms() if a["id"] != alarm_id]
            alarms_store.save_alarms(alarms)
            self._refresh_list()

    # ---- Página editor --------------------------------------------------

    def _build_editor_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_name("alarm-editor")

        self.editor_title = Gtk.Label(label="")
        self.editor_title.set_halign(Gtk.Align.CENTER)
        self.editor_title.get_style_context().add_class("alarm-editor-title")
        box.pack_start(self.editor_title, False, False, 0)

        time_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        time_row.set_halign(Gtk.Align.CENTER)
        time_row.get_style_context().add_class("alarm-time-row")

        self.hour_spin = Gtk.SpinButton.new_with_range(0, 23, 1)
        self.hour_spin.set_wrap(True)
        self.hour_spin.set_numeric(True)
        self.hour_spin.set_valign(Gtk.Align.CENTER)
        self.hour_spin.connect("output", self._on_spin_output)

        colon = Gtk.Label(label=":")
        colon.get_style_context().add_class("alarm-time-colon")

        self.minute_spin = Gtk.SpinButton.new_with_range(0, 59, 1)
        self.minute_spin.set_wrap(True)
        self.minute_spin.set_numeric(True)
        self.minute_spin.set_valign(Gtk.Align.CENTER)
        self.minute_spin.connect("output", self._on_spin_output)

        time_row.pack_start(self.hour_spin, False, False, 0)
        time_row.pack_start(colon, False, False, 0)
        time_row.pack_start(self.minute_spin, False, False, 0)
        box.pack_start(time_row, False, False, 0)

        self.label_entry = Gtk.Entry()
        self.label_entry.set_placeholder_text(t("alarmas", "label_placeholder"))
        self.label_entry.set_alignment(0.5)
        box.pack_start(self.label_entry, False, False, 0)

        days_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        days_row.set_halign(Gtk.Align.CENTER)
        self.day_toggles = []
        for code in DAY_CODES:
            btn = Gtk.ToggleButton(label=t("alarmas", code))
            btn.get_style_context().add_class("alarm-day-toggle")
            days_row.pack_start(btn, False, False, 0)
            self.day_toggles.append(btn)
        box.pack_start(days_row, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_row.set_halign(Gtk.Align.CENTER)
        cancel_btn = Gtk.Button(label=t("alarmas", "cancelar"))
        cancel_btn.get_style_context().add_class("alarm-cancel-btn")
        cancel_btn.connect("clicked", lambda *_: self._show_list())
        save_btn = Gtk.Button(label=t("alarmas", "guardar"))
        save_btn.get_style_context().add_class("alarm-save-btn")
        save_btn.connect("clicked", self._on_save)
        btn_row.pack_start(cancel_btn, False, False, 0)
        btn_row.pack_start(save_btn, False, False, 0)
        box.pack_start(btn_row, False, False, 0)

        return box

    def _on_spin_output(self, spin):
        spin.set_text(f"{int(spin.get_value()):02d}")
        return True

    def _open_editor(self, alarm_id):
        self.editing_id = alarm_id
        if alarm_id is None:
            now = datetime.now()
            self.editor_title.set_text(t("alarmas", "nueva_alarma"))
            self.hour_spin.set_value(now.hour)
            self.minute_spin.set_value(now.minute)
            self.label_entry.set_text("")
            for btn in self.day_toggles:
                btn.set_active(False)
        else:
            alarm = next((a for a in alarms_store.load_alarms() if a["id"] == alarm_id), None)
            if alarm is None:
                return
            self.editor_title.set_text(t("alarmas", "editar_alarma"))
            self.hour_spin.set_value(alarm["hour"])
            self.minute_spin.set_value(alarm["minute"])
            self.label_entry.set_text(alarm.get("label", ""))
            days = set(alarm.get("days") or [])
            for i, btn in enumerate(self.day_toggles):
                btn.set_active(i in days)
        self.stack.set_visible_child_name("editor")

    def _show_list(self):
        self.stack.set_visible_child_name("list")
        self._refresh_list()

    def _on_save(self, _btn):
        hour = int(self.hour_spin.get_value())
        minute = int(self.minute_spin.get_value())
        label = self.label_entry.get_text().strip()
        days = [i for i, btn in enumerate(self.day_toggles) if btn.get_active()]

        alarms = alarms_store.load_alarms()
        if self.editing_id is None:
            alarms.append({
                "id": alarms_store.new_alarm_id(),
                "hour": hour, "minute": minute, "label": label,
                "days": days, "enabled": True, "last_fired": None,
            })
        else:
            for a in alarms:
                if a["id"] == self.editing_id:
                    a["hour"] = hour
                    a["minute"] = minute
                    a["label"] = label
                    a["days"] = days
                    # Si se reprograma hora/días de una que ya sonó hoy,
                    # se rearma -- si no, quedaría "pegada" sin sonar
                    # hasta mañana por el guardia last_fired.
                    a["last_fired"] = None
                    break
        alarms_store.save_alarms(alarms)
        self._show_list()

    # ---- Ciclo de vida ----------------------------------------------------

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
    AlarmPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

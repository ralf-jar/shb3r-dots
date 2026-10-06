#!/usr/bin/env python3
"""Ventana aparte del administrador de UFW. Ver CLAUDE.md "Administrador
de UFW"."""

import os
import subprocess
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Gdk, GLib, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
import ufw_state
import ufw_actions
from waybar_lib import (
    kill_group, kill_existing, build_layer_window, position_fixed_top,
    svg_icon_image, load_module_css, sync_toggle_label_active, wrap_toggle_row,
    _icon_text_button,
)
from i18n import t

CSS_FILE = os.path.join(SCRIPT_DIR, "firewall_popup.css")
LOCK = "/tmp/firewall-popup.pid"
LOG_VIEWER = os.path.join(SCRIPT_DIR, "firewall_log_viewer.py")

# Proceso/pantalla aparte -- necesita su propio Gtk.CssProvider para
# toggle_common.css, dashboard.py lo carga una sola vez pero no comparte
# pantalla con esta ventana.
TOGGLE_COMMON_CSS = os.path.join(SCRIPT_DIR, "..", "toggles", "toggle_common.css")

ICONS_DIR = os.path.join(SCRIPT_DIR, "icons")
LOG_ICON = os.path.join(ICONS_DIR, "log.svg")
FIREWALL_ICON = os.path.join(ICONS_DIR, "firewall.svg")


PROTO_OPTIONS = ["tcp", "udp", "any"]
ACTION_OPTIONS = ["allow", "deny", "reject", "limit"]
DIRECTION_OPTIONS = [("in", t("red", "direction_in")), ("out", t("red", "direction_out"))]


class FirewallPopup:
    def __init__(self):
        self.window, container = build_layer_window("firewall-popup", CSS_FILE)
        container.set_name("firewall-container")
        position_fixed_top(container, margin=60)
        load_module_css(TOGGLE_COMMON_CSS)

        self._busy_widgets = []

        title = Gtk.Label(label=t("red", "popup_title"))
        title.set_name("firewall-title")
        title.set_halign(Gtk.Align.CENTER)
        container.pack_start(title, False, False, 0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        header.set_name("firewall-header")
        header.set_homogeneous(True)

        # No usa waybar_lib.build_switch_row -- necesita la referencia al
        # Gtk.Switch para resincronizar su estado en cada refresh() (ej.
        # si falla pkexec y el estado no cambió). Misma estructura/clases
        # CSS que el helper.
        toggle_chip = Gtk.Box()
        toggle_chip.get_style_context().add_class("toggle-icon-chip")
        toggle_icon = svg_icon_image(FIREWALL_ICON)
        toggle_icon.set_halign(Gtk.Align.CENTER)
        toggle_icon.set_valign(Gtk.Align.CENTER)
        toggle_chip.pack_start(toggle_icon, True, False, 0)

        toggle_label = Gtk.Label(label=t("red", "toggle_label"))
        toggle_label.set_halign(Gtk.Align.START)
        toggle_label.get_style_context().add_class("toggle-row-label")
        sync_toggle_label_active(toggle_label, False)

        self.enable_switch = Gtk.Switch()
        self.enable_switch.set_valign(Gtk.Align.CENTER)
        self.enable_switch.get_style_context().add_class("toggle-row-switch")
        self.enable_switch.connect(
            "notify::active",
            lambda sw, _p: sync_toggle_label_active(toggle_label, sw.get_active()))
        self._enable_handler = self.enable_switch.connect("state-set", self._on_enable_toggled)

        toggle_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        toggle_row.get_style_context().add_class("toggle-list-row")
        toggle_row.pack_start(toggle_chip, False, False, 0)
        toggle_row.pack_start(toggle_label, True, True, 0)
        toggle_row.pack_start(self.enable_switch, False, False, 0)
        header.pack_start(wrap_toggle_row(toggle_row, self.enable_switch), True, True, 0)

        log_btn = _icon_text_button(LOG_ICON, t("red", "log_btn_label"), t("red", "log_btn_tooltip"))
        log_btn.get_style_context().add_class("firewall-log-btn")
        log_btn.set_halign(Gtk.Align.CENTER)
        log_btn.connect("clicked", self._on_open_log)
        header.pack_start(log_btn, False, True, 0)

        container.pack_start(header, False, False, 0)

        # homogeneous -- pedido explícito del usuario: sin esto, la
        # columna "Bloqueados" (nombres de regla más largos ahí, se ven
        # en las reglas reales de esta máquina) ganaba más ancho natural
        # que "Abiertos" en vez de partir el espacio 50/50 real.
        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        columns.set_name("firewall-columns")
        columns.set_homogeneous(True)
        self.allowed_box, self.allowed_list = self._build_column(t("red", "col_allowed"))
        self.blocked_box, self.blocked_list = self._build_column(t("red", "col_blocked"))
        columns.pack_start(self.allowed_box, True, True, 0)
        columns.pack_start(self.blocked_box, True, True, 0)
        container.pack_start(columns, True, True, 0)

        container.pack_start(self._build_add_form(), False, False, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

        self.refresh()

    # ---- Columnas de reglas --------------------------------------------

    def _build_column(self, title):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_name("firewall-column")

        label = Gtk.Label(label=title)
        label.set_name("firewall-column-title")
        label.set_halign(Gtk.Align.CENTER)
        box.pack_start(label, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(0)

        listbox = Gtk.ListBox()
        listbox.set_name("firewall-rule-list")
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.add(listbox)
        box.pack_start(scroller, True, True, 0)
        return box, listbox

    def _make_rule_row(self, rule):
        port, direction, src = ufw_state.rule_label(rule)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("firewall-rule-row")

        text = f"{port}  {direction}  ·  {src}"
        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        # ellipsize -- una regla larga forzaría el ancho de columna en
        # vez de respetar el 50/50 homogeneous de `columns`.
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(28)
        if rule.get("comment"):
            label.set_tooltip_text(rule["comment"])
        else:
            label.set_tooltip_text(text)
        row.pack_start(label, True, True, 0)

        del_btn = Gtk.Button()
        del_btn.add(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
        del_btn.get_style_context().add_class("firewall-delete-btn")
        del_btn.set_tooltip_text(t("red", "delete_rule_tooltip"))
        del_btn.connect("clicked", self._on_delete_clicked, rule, text)
        row.pack_start(del_btn, False, False, 0)

        wrap = Gtk.ListBoxRow()
        wrap.set_selectable(False)
        wrap.add(row)
        return wrap

    # ---- Formulario "agregar regla" ------------------------------------

    def _build_add_form(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_name("firewall-add-form")

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_row.set_name("firewall-form-title-row")

        title = Gtk.Label(label=t("red", "add_form_title"))
        title.get_style_context().add_class("firewall-form-title")
        title_row.pack_start(title, False, False, 0)

        title_divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        title_divider.get_style_context().add_class("firewall-form-title-divider")
        title_divider.set_valign(Gtk.Align.CENTER)
        title_row.pack_start(title_divider, True, True, 0)

        box.pack_start(title_row, False, False, 0)

        fields = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.action_combo = Gtk.ComboBoxText()
        for opt in ACTION_OPTIONS:
            self.action_combo.append_text(opt)
        self.action_combo.set_active(0)
        fields.pack_start(self._labeled(self.action_combo, t("red", "field_action")), False, False, 0)

        self.port_entry = Gtk.Entry()
        self.port_entry.set_placeholder_text(t("red", "port_placeholder"))
        self.port_entry.set_width_chars(14)
        fields.pack_start(self._labeled(self.port_entry, t("red", "field_port")), False, False, 0)

        self.proto_combo = Gtk.ComboBoxText()
        for opt in PROTO_OPTIONS:
            self.proto_combo.append_text(opt)
        self.proto_combo.set_active(0)
        fields.pack_start(self._labeled(self.proto_combo, t("red", "field_proto")), False, False, 0)

        self.direction_combo = Gtk.ComboBoxText()
        for value, caption in DIRECTION_OPTIONS:
            self.direction_combo.append(value, caption)
        self.direction_combo.set_active(0)
        fields.pack_start(self._labeled(self.direction_combo, t("red", "field_direction")), False, False, 0)

        self.source_entry = Gtk.Entry()
        self.source_entry.set_placeholder_text(t("red", "source_placeholder"))
        self.source_entry.set_text("any")
        self.source_entry.set_width_chars(16)
        fields.pack_start(self._labeled(self.source_entry, t("red", "field_source")), True, True, 0)

        box.pack_start(fields, False, False, 0)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_btn = Gtk.Button(label=t("red", "add_btn_label"))
        self.add_btn.get_style_context().add_class("firewall-add-btn")
        self.add_btn.connect("clicked", self._on_add_clicked)
        btn_row.pack_start(self.add_btn, False, False, 0)

        self.form_status = Gtk.Label(label="")
        self.form_status.set_halign(Gtk.Align.START)
        self.form_status.get_style_context().add_class("firewall-form-status")
        btn_row.pack_start(self.form_status, True, True, 0)

        box.pack_start(btn_row, False, False, 0)

        self._busy_widgets += [
            self.action_combo, self.port_entry, self.proto_combo,
            self.direction_combo, self.source_entry, self.add_btn,
        ]
        return box

    def _labeled(self, widget, caption):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        label = Gtk.Label(label=caption)
        label.set_halign(Gtk.Align.START)
        label.get_style_context().add_class("firewall-field-label")
        col.pack_start(label, False, False, 0)
        col.pack_start(widget, False, False, 0)
        return col

    # ---- Datos ------------------------------------------------------------

    def refresh(self):
        state = ufw_state.read_state()

        self.enable_switch.handler_block(self._enable_handler)
        self.enable_switch.set_active(state["enabled"])
        self.enable_switch.handler_unblock(self._enable_handler)

        for listbox in (self.allowed_list, self.blocked_list):
            for child in listbox.get_children():
                listbox.remove(child)

        for rule in state["rules"]:
            row = self._make_rule_row(rule)
            target = self.allowed_list if rule["action"] == "allow" else self.blocked_list
            target.add(row)

        self.allowed_list.show_all()
        self.blocked_list.show_all()

    # ---- Acciones (hilo aparte, pkexec de por medio) -------------------

    def _set_busy(self, busy):
        for w in self._busy_widgets:
            w.set_sensitive(not busy)
        self.enable_switch.set_sensitive(not busy)

    def _run_action(self, fn, on_done):
        """self.window (layer-shell Layer.TOP pantalla completa) se come
        el click del diálogo de contraseña de polkit y lo tapa -- oculto
        hasta _finish_action, no solo mientras el diálogo está abierto
        (lo abre un proceso aparte, en un momento que no controlamos)."""
        self.window.hide()
        self._set_busy(True)

        def worker():
            ok, msg = fn()
            GLib.idle_add(self._finish_action, ok, msg, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_action(self, ok, msg, on_done):
        self.window.show()
        self._set_busy(False)
        on_done(ok, msg)
        self.refresh()
        return False  # GLib.idle_add: correr una sola vez

    def _on_enable_toggled(self, _switch, target):
        self._run_action(
            lambda: ufw_actions.set_enabled(target),
            lambda ok, msg: None if ok else self._show_error(msg),
        )
        return False  # deja que el manejador default sincronice el switch

    def _on_add_clicked(self, _btn):
        action = self.action_combo.get_active_text()
        proto = self.proto_combo.get_active_text()
        direction = self.direction_combo.get_active_id()
        port = self.port_entry.get_text().strip()
        source = self.source_entry.get_text().strip() or "any"

        self.form_status.set_text("")
        self._run_action(
            lambda: ufw_actions.add_rule(action, proto, port, source, direction),
            self._on_add_finished,
        )

    def _on_add_finished(self, ok, msg):
        if ok:
            self.port_entry.set_text("")
            self.source_entry.set_text("any")
            self.form_status.set_text(t("red", "rule_added"))
        else:
            self.form_status.set_text(t("red", "error", msg=msg)[:120])

    def _on_delete_clicked(self, btn, rule, text):
        win = btn.get_toplevel()
        dialog = Gtk.MessageDialog(
            transient_for=win, flags=0, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=t("red", "delete_rule_confirm"),
        )
        dialog.format_secondary_text(text)

        original_prgname = GLib.get_prgname()
        GLib.set_prgname("firewall-delete-dialog")
        dialog.show_all()
        GLib.set_prgname(original_prgname)

        win.hide()
        response = dialog.run()
        dialog.destroy()
        win.show()

        if response == Gtk.ResponseType.YES:
            self._run_action(
                lambda: ufw_actions.delete_rule(rule["index"]),
                lambda ok, msg: None if ok else self._show_error(msg),
            )

    def _show_error(self, msg):
        self.form_status.set_text(t("red", "error", msg=msg)[:120])

    # ---- Registro -------------------------------------------------------

    def _on_open_log(self, _btn):
        subprocess.Popen(["python3", LOG_VIEWER])

    # ---- Ciclo de vida --------------------------------------------------

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_a):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    FirewallPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

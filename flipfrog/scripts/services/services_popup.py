#!/usr/bin/env python3

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
import services_state
import services_actions
import services_wiki
import autostart_state
import autostart_actions
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top
from i18n import t

CSS_FILE = os.path.join(SCRIPT_DIR, "services_popup.css")
LOCK = "/tmp/services-popup.pid"


class ServicesPopup:
    def __init__(self):
        self.window, container = build_layer_window("services-popup", CSS_FILE)
        container.set_name("services-container")
        position_fixed_top(container, margin=60)

        notebook = Gtk.Notebook()
        notebook.set_name("services-notebook")
        notebook.append_page(self._build_services_page(), Gtk.Label(label=t("servicios", "tab_servicios")))
        notebook.append_page(self._build_autostart_page(), Gtk.Label(label=t("servicios", "tab_autostart")))
        container.pack_start(notebook, True, True, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

        self.refresh_services()
        self.refresh_autostart()

    # ==== Pestaña "Servicios" (systemd) ====================================

    def _build_services_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_name("services-page")

        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text(t("servicios", "buscar_servicio"))
        self.search_entry.connect("changed", lambda _e: self.refresh_services())
        page.pack_start(self.search_entry, False, False, 0)

        hint = Gtk.Label(
            label=t("servicios", "hint_servicios")
        )
        hint.set_name("services-hint")
        hint.set_halign(Gtk.Align.START)
        hint.set_line_wrap(True)
        page.pack_start(hint, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("services-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(320)
        scroller.set_max_content_height(320)

        self.services_listbox = Gtk.ListBox()
        self.services_listbox.set_name("services-list")
        self.services_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.add(self.services_listbox)
        page.pack_start(scroller, True, True, 0)

        self.services_status_label = Gtk.Label(label="")
        self.services_status_label.set_halign(Gtk.Align.START)
        self.services_status_label.get_style_context().add_class("services-status-label")
        page.pack_start(self.services_status_label, False, False, 0)

        return page

    def _make_service_row(self, svc):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("services-row")

        name_label = Gtk.Label(label=svc["name"])
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_max_width_chars(34)
        name_label.get_style_context().add_class("services-name")
        name_label.set_tooltip_text(services_wiki.describe(svc["name"], svc["description"]))
        row.pack_start(name_label, True, True, 0)

        enable_btn = Gtk.ToggleButton(label=t("servicios", "habilitado") if svc["enabled"] else t("servicios", "deshabilitado"))
        enable_btn.get_style_context().add_class("services-enable-btn")
        enable_btn.set_active(svc["enabled"])
        if svc["can_toggle_enable"]:
            enable_btn.set_tooltip_text(t("servicios", "tooltip_requiere_admin"))
            enable_btn.connect("toggled", self._on_enable_toggled, svc["name"])
        else:
            enable_btn.set_sensitive(False)
            enable_btn.set_tooltip_text(t("servicios", "tooltip_estado_no_toggle", estado=svc["enabled_state"]))
        row.pack_start(enable_btn, False, False, 0)

        active_btn = Gtk.ToggleButton(label=t("servicios", "activo") if svc["active"] else t("servicios", "inactivo"))
        active_btn.get_style_context().add_class("services-active-btn")
        active_btn.set_active(svc["active"])
        active_btn.set_tooltip_text(t("servicios", "tooltip_activa_inactiva"))
        active_btn.connect("toggled", self._on_active_toggled, svc["name"])
        row.pack_start(active_btn, False, False, 0)

        copy_btn = Gtk.Button()
        copy_btn.add(Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
        copy_btn.get_style_context().add_class("services-copy-btn")
        copy_btn.set_tooltip_text(t("servicios", "tooltip_copiar_comandos"))
        copy_btn.connect("clicked", self._on_copy_clicked, svc["name"])
        row.pack_start(copy_btn, False, False, 0)

        wrap = Gtk.ListBoxRow()
        wrap.set_selectable(False)
        wrap.add(row)
        return wrap

    def _visible_services(self):
        query = self.search_entry.get_text().strip().lower()
        services = services_state.list_services()
        if query:
            return [s for s in services if query in s["name"].lower()]
        return [s for s in services if s["enabled"] or s["active"]]

    def refresh_services(self):
        for child in self.services_listbox.get_children():
            self.services_listbox.remove(child)
        for svc in self._visible_services():
            self.services_listbox.add(self._make_service_row(svc))
        self.services_listbox.show_all()

    def _set_services_busy(self, busy):
        self.services_listbox.set_sensitive(not busy)
        self.search_entry.set_sensitive(not busy)

    def _run_action(self, fn):
        self._set_services_busy(True)

        def worker():
            ok, msg = fn()
            GLib.idle_add(self._finish_action, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_action(self, ok, msg):
        self._set_services_busy(False)
        self.services_status_label.set_text("" if ok else t("servicios", "error_msg", msg=msg)[:160])
        self.refresh_services()
        return False  # GLib.idle_add: correr una sola vez

    def _on_enable_toggled(self, btn, name):
        target = btn.get_active()
        self._run_action(lambda: services_actions.set_enabled(name, target))

    def _on_active_toggled(self, btn, name):
        target = btn.get_active()
        fn = services_actions.start if target else services_actions.stop
        self._run_action(lambda: fn(name))

    def _on_copy_clicked(self, btn, name):
        commands = (
            f"systemctl start {name}\n"
            f"systemctl stop {name}\n"
            f"systemctl enable {name}\n"
            f"systemctl disable {name}"
        )
        subprocess.run(["wl-copy"], input=commands, text=True)
        btn.set_tooltip_text(t("servicios", "tooltip_comando_copiado"))

    # ==== Pestaña "Autostart" (Hyprland) ====================================

    def _build_autostart_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_name("autostart-page")

        hint = Gtk.Label(
            label=t("servicios", "hint_autostart")
        )
        hint.set_name("autostart-hint")
        hint.set_halign(Gtk.Align.START)
        hint.set_line_wrap(True)
        page.pack_start(hint, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("services-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(320)
        scroller.set_max_content_height(320)

        self.autostart_listbox = Gtk.ListBox()
        self.autostart_listbox.set_name("services-list")
        self.autostart_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.add(self.autostart_listbox)
        page.pack_start(scroller, True, True, 0)

        self.autostart_status_label = Gtk.Label(label="")
        self.autostart_status_label.set_halign(Gtk.Align.START)
        self.autostart_status_label.get_style_context().add_class("services-status-label")
        page.pack_start(self.autostart_status_label, False, False, 0)

        return page

    def _make_autostart_row(self, entry):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("services-row")

        name_label = Gtk.Label(label=entry["name"])
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_max_width_chars(34)
        name_label.get_style_context().add_class("services-name")
        name_label.set_tooltip_text(entry["command"])
        row.pack_start(name_label, True, True, 0)

        enable_btn = Gtk.ToggleButton(label=t("servicios", "habilitado") if entry["enabled"] else t("servicios", "deshabilitado"))
        enable_btn.get_style_context().add_class("services-enable-btn")
        enable_btn.set_active(entry["enabled"])
        enable_btn.set_tooltip_text(t("servicios", "tooltip_deshabilitar"))
        enable_btn.connect("toggled", self._on_autostart_toggled, entry["command"])
        row.pack_start(enable_btn, False, False, 0)

        wrap = Gtk.ListBoxRow()
        wrap.set_selectable(False)
        wrap.add(row)
        return wrap

    def refresh_autostart(self):
        for child in self.autostart_listbox.get_children():
            self.autostart_listbox.remove(child)
        for entry in autostart_state.list_entries():
            self.autostart_listbox.add(self._make_autostart_row(entry))
        self.autostart_listbox.show_all()

    def _on_autostart_toggled(self, btn, command):
        target = btn.get_active()
        ok, msg = autostart_actions.set_enabled(command, target)
        self.autostart_status_label.set_text("" if ok else t("servicios", "error_msg", msg=msg)[:160])
        self.refresh_autostart()

    # ==== Ciclo de vida ======================================================

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
    ServicesPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

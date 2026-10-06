"""Pestaña "Bluetooth" del dashboard. Ver CLAUDE.md, sección
homónima."""

import os
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, GLib, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import bluetooth_state
import bluetooth_actions

sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from waybar_lib import (
    load_module_css, svg_icon_image, sync_toggle_label_active, wrap_toggle_row, _icon_text_button,
)
from i18n import t

MODULE_CSS = os.path.join(SCRIPT_DIR, "bluetooth_tab.css")

ICONS_DIR = os.path.join(SCRIPT_DIR, "icons")
SCAN_ICON = os.path.join(ICONS_DIR, "scan.svg")
BLUETOOTH_ICON = os.path.join(ICONS_DIR, "bluetooth.svg")
CLOCK_ICON = os.path.join(ICONS_DIR, "clock.svg")

ICON_SIZE = 22
BACKGROUND_REFRESH_MS = 4000



class BluetoothTab:
    def __init__(self, container):
        self._icon_cache = {}
        self._busy = False
        self._refresh_in_flight = False
        self._timeout_id = None
        self._autoconnect_delay, self._autoconnect_macs = bluetooth_state.get_autoconnect()

        container.pack_start(self._build_top_grid(), False, False, 0)

        self.status_label = Gtk.Label(label="")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.get_style_context().add_class("bluetooth-status-label")
        self.status_label.set_no_show_all(True)
        container.pack_start(self.status_label, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("bluetooth-device-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.device_list = Gtk.ListBox()
        self.device_list.set_name("bluetooth-device-list")
        self.device_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.add(self.device_list)
        container.pack_start(scroller, True, True, 0)

        container.connect("destroy", self._on_destroy)

        self.refresh()
        self._schedule_background_refresh()

    # ---- Grid 2x2: power / autoconectar / (vacío) / buscar ---------------

    def _build_top_grid(self):
        """Grid 2x2: power (0,0), autoconectar (1,0), vacío (0,1),
        "Buscar dispositivos" ocupando el ancho completo (1,1, width=2).
        column_homogeneous para 50/50 real, mismo criterio que los
        demás headers del dashboard."""
        grid = Gtk.Grid()
        grid.set_name("bluetooth-top-grid")
        grid.set_column_homogeneous(True)
        grid.set_column_spacing(20)
        grid.set_row_spacing(12)

        grid.attach(self._build_power_row(), 0, 0, 1, 1)
        grid.attach(self._build_autoconnect_row(), 1, 0, 1, 1)

        # width=2 (no 1) -- pedido explícito del usuario: centrado en el
        # ANCHO TOTAL de la fila (las dos columnas juntas), no confinado
        # a la mitad derecha como el resto de los slots 50/50.
        self.scan_btn = _icon_text_button(
            SCAN_ICON, t("bluetooth", "scan_label"), t("bluetooth", "scan_tooltip"))
        self.scan_btn.get_style_context().add_class("bluetooth-scan-btn")
        self.scan_btn.set_halign(Gtk.Align.CENTER)
        self.scan_btn.connect("clicked", self._on_scan_clicked)
        grid.attach(self.scan_btn, 0, 1, 2, 1)

        return grid

    def _build_power_row(self):
        """Mismo componente toggle_common.css que los 9 de "Sistema"/
        "Panel" -- ya cargado por dashboard.py, esta pestaña comparte
        esa pantalla."""
        power_chip = Gtk.Box()
        power_chip.get_style_context().add_class("toggle-icon-chip")
        power_icon = svg_icon_image(BLUETOOTH_ICON)
        power_icon.set_halign(Gtk.Align.CENTER)
        power_icon.set_valign(Gtk.Align.CENTER)
        power_chip.pack_start(power_icon, True, False, 0)

        power_label = Gtk.Label(label=t("bluetooth", "power_label"))
        power_label.set_halign(Gtk.Align.START)
        power_label.get_style_context().add_class("toggle-row-label")
        sync_toggle_label_active(power_label, False)

        self.power_switch = Gtk.Switch()
        self.power_switch.set_valign(Gtk.Align.CENTER)
        self.power_switch.get_style_context().add_class("toggle-row-switch")
        self.power_switch.connect(
            "notify::active",
            lambda sw, _p: sync_toggle_label_active(power_label, sw.get_active()))
        self._power_handler = self.power_switch.connect("state-set", self._on_power_toggled)

        power_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        power_row.get_style_context().add_class("toggle-list-row")
        power_row.pack_start(power_chip, False, False, 0)
        power_row.pack_start(power_label, True, True, 0)
        power_row.pack_start(self.power_switch, False, False, 0)
        return wrap_toggle_row(power_row, self.power_switch)

    # ---- Fila "Autoconectar al iniciar sesión" (retraso global) ---------

    def _build_autoconnect_row(self):
        """Mismo patrón que rotate_group/radius_group en theme_module.py
        (pedido explícito del usuario) -- ícono + etiqueta + Gtk.Entry +
        unidad + botón "Aplicar" (check), en vez de la fila plana de
        antes (label expandido + entry + "s" sueltos)."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.set_name("bluetooth-autoconnect-row")
        # Centrado en su celda del grid (slot 2, pedido explícito del
        # usuario) -- mismo criterio que "Buscar dispositivos" en el
        # slot 4.
        row.set_halign(Gtk.Align.CENTER)

        icon = svg_icon_image(CLOCK_ICON, size=14, color_name="myforegroundhover")
        icon.set_margin_end(7)
        icon.set_valign(Gtk.Align.CENTER)
        row.pack_start(icon, False, False, 0)

        label = Gtk.Label(label=t("bluetooth", "autoconnect_delay_label"))
        label.get_style_context().add_class("bluetooth-autoconnect-label")
        row.pack_start(label, False, False, 0)

        # Entry en vez de SpinButton, mismo criterio que interval_entry en
        # theme_module.py (rotación de temas) -- solo dígitos (sin punto
        # decimal: el retraso es en segundos enteros), confirma con Enter,
        # al perder foco, o clickeando el check (no en cada tecla, evita
        # reescribir el archivo a mitad de tipear un número de dos cifras).
        self.delay_entry = Gtk.Entry()
        self.delay_entry.set_text(str(int(self._autoconnect_delay)))
        self.delay_entry.set_width_chars(3)
        self.delay_entry.set_max_length(3)
        self.delay_entry.set_alignment(1.0)
        self.delay_entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        # valign(CENTER) + margin_top(10) -- mismo gotcha que
        # interval_entry/radius_entry en theme_module.py: Gtk.Entry
        # reserva ~10px invisibles solo abajo de su propio recuadro
        # pintado, este margen lo compensa para que quede centrado en el
        # mismo eje que el botón "Aplicar" (confirmado a mano ahí).
        self.delay_entry.set_valign(Gtk.Align.CENTER)
        self.delay_entry.set_margin_top(10)
        self.delay_entry.set_tooltip_text(t("bluetooth", "autoconnect_delay_tooltip"))
        self.delay_entry.connect("insert-text", self._on_delay_insert_text)
        self.delay_entry.connect("activate", self._on_delay_committed)
        self.delay_entry.connect("focus-out-event", self._on_delay_committed)
        row.pack_start(self.delay_entry, False, False, 0)

        seconds_label = Gtk.Label(label=t("bluetooth", "seconds_unit"))
        seconds_label.get_style_context().add_class("bluetooth-autoconnect-unit-label")
        seconds_label.set_margin_start(6)
        row.pack_start(seconds_label, False, False, 0)

        # Botón "Aplicar" -- misma clase ".apply-btn" que rotate_apply_btn/
        # radius_apply_btn en theme_module.py, look idéntico a propósito.
        apply_btn = Gtk.Button()
        apply_icon = Gtk.Image.new_from_icon_name("object-select-symbolic", Gtk.IconSize.BUTTON)
        apply_btn.set_image(apply_icon)
        apply_btn.set_always_show_image(True)
        apply_btn.set_tooltip_text(t("bluetooth", "apply_tooltip"))
        apply_btn.get_style_context().add_class("apply-btn")
        apply_btn.connect("clicked", self._on_delay_committed)
        apply_btn.set_valign(Gtk.Align.CENTER)
        apply_btn.set_margin_start(8)
        row.pack_start(apply_btn, False, False, 0)

        return row

    def _on_delay_insert_text(self, entry, text, _length, _position):
        for ch in text:
            if not ch.isdigit():
                entry.stop_emission_by_name("insert-text")
                return

    def _on_delay_committed(self, *_args):
        text = self.delay_entry.get_text().strip()
        try:
            delay = int(text)
            if delay < 0:
                raise ValueError
        except ValueError:
            delay = int(self._autoconnect_delay)
        self.delay_entry.set_text(str(delay))

        if delay == self._autoconnect_delay:
            return
        self._autoconnect_delay = delay
        bluetooth_actions.set_autoconnect(self._autoconnect_delay, self._autoconnect_macs)

    # ---- Ciclo de vida ---------------------------------------------------

    def _on_destroy(self, *_a):
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _schedule_background_refresh(self):
        self._timeout_id = GLib.timeout_add(BACKGROUND_REFRESH_MS, self._on_background_tick)

    def _on_background_tick(self):
        if not self._busy:
            self.refresh(resolve_icons=False)
        self._schedule_background_refresh()
        return False  # se reprograma a sí mismo, mismo patrón que NetworkTab

    # ---- Estado / acciones (hilo aparte) ---------------------------------

    def set_status(self, text):
        self.status_label.set_text(text)
        self.status_label.set_no_show_all(not bool(text))
        self.status_label.set_visible(bool(text))

    def _set_busy(self, busy):
        self._busy = busy
        self.scan_btn.set_sensitive(not busy)
        self.power_switch.set_sensitive(not busy)
        self.device_list.set_sensitive(not busy)

    def _run_action(self, fn, busy_text, on_done):
        self._set_busy(True)
        self.set_status(busy_text)

        def worker():
            ok, msg = fn()
            GLib.idle_add(self._finish_action, ok, msg, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_action(self, ok, msg, on_done):
        self._set_busy(False)
        on_done(ok, msg)
        self.refresh()
        return False  # GLib.idle_add: correr una sola vez

    def _on_power_toggled(self, _switch, target):
        self._run_action(
            lambda: bluetooth_actions.set_powered(target),
            t("bluetooth", "powering_on") if target else t("bluetooth", "powering_off"),
            lambda ok, msg: self.set_status("" if ok else t("bluetooth", "error", msg=msg)[:120]),
        )
        return False  # deja que el manejador default sincronice el switch

    def _on_scan_clicked(self, _btn):
        self._run_action(
            bluetooth_actions.scan,
            t("bluetooth", "scanning"),
            lambda ok, msg: self.set_status("" if ok else t("bluetooth", "error", msg=msg)[:120]),
        )

    # ---- Refresh -------------------------------------------------------

    def refresh(self, resolve_icons=True):
        """Dispara `bluetoothctl` (2-3 subprocess por llamada, hasta
        INFO_TIMEOUT=5s cada uno) en un hilo aparte -- antes corría
        directo acá, tanto desde el poll de 4s (_on_background_tick)
        como al terminar cada acción (_finish_action), y podía trabar
        el dashboard ENTERO si bluetoothctl se colgaba. `_refresh_in_flight`
        evita apilar polls mientras uno anterior sigue esperando."""
        if self._refresh_in_flight:
            return False
        self._refresh_in_flight = True

        def worker():
            adapter = bluetooth_state.adapter_info()
            devices = bluetooth_state.get_devices() if adapter and adapter["powered"] else []
            GLib.idle_add(self._apply_refresh, adapter, devices, resolve_icons)

        threading.Thread(target=worker, daemon=True).start()
        return False

    def _apply_refresh(self, adapter, devices, resolve_icons):
        self._refresh_in_flight = False

        if adapter is None:
            self.scan_btn.set_sensitive(False)
            self.power_switch.set_sensitive(False)
            self._render_devices([], t("bluetooth", "no_adapter"))
            return False

        self.power_switch.handler_block(self._power_handler)
        self.power_switch.set_active(adapter["powered"])
        self.power_switch.handler_unblock(self._power_handler)
        self.power_switch.set_sensitive(True)
        self.scan_btn.set_sensitive(adapter["powered"])

        if not adapter["powered"]:
            self._render_devices([], t("bluetooth", "powered_off"))
            return False

        empty_text = t("bluetooth", "empty_devices") if not devices else ""
        self._render_devices(devices, empty_text, resolve_icons=resolve_icons)
        return False  # GLib.idle_add: correr una sola vez

    def _render_devices(self, devices, empty_text, resolve_icons=True):
        for child in self.device_list.get_children():
            self.device_list.remove(child)

        if not devices:
            if empty_text:
                empty = Gtk.Label(label=empty_text)
                empty.get_style_context().add_class("bluetooth-empty")
                self.device_list.add(self._wrap_row(empty))
        else:
            for device in devices:
                self.device_list.add(self._make_device_row(device, resolve_icons))

        self.device_list.show_all()

    def _wrap_row(self, widget):
        wrap = Gtk.ListBoxRow()
        wrap.set_selectable(False)
        wrap.add(widget)
        return wrap

    # ---- Fila de dispositivo --------------------------------------------

    def _make_device_row(self, device, resolve_icons):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("bluetooth-device-row")
        # La MAC solo en tooltip (pedido explícito del usuario) -- ya no
        # hay un label aparte para ella, ver .bluetooth-device-mac
        # (removido de bluetooth_tab.css).
        row.set_tooltip_text(device["mac"])

        row.pack_start(self._make_icon_slot(device["mac"], resolve_icons), False, False, 0)

        name_label = Gtk.Label(label=device["name"])
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_max_width_chars(26)
        name_label.get_style_context().add_class("bluetooth-device-name")
        row.pack_start(name_label, True, True, 0)

        # Solo emparejados tienen sentido como candidatos a autoconectar
        # -- uno "Disponible" no tiene credenciales guardadas.
        if device["paired"]:
            row.pack_start(self._make_autoconnect_toggle(device), False, False, 0)

        row.pack_start(self._make_connection_switch(device), False, False, 0)

        if device["paired"]:
            forget_btn = Gtk.Button()
            forget_btn.add(Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
            forget_btn.set_tooltip_text(t("bluetooth", "forget_tooltip"))
            forget_btn.connect("clicked", self._on_forget_clicked, device["mac"], device["name"])
            forget_btn.get_style_context().add_class("bluetooth-forget-btn")
            row.pack_start(forget_btn, False, False, 0)
        else:
            # Espaciador -- mismo botón real (no un margen a mano) para
            # que el switch quede alineado con el de una fila emparejada
            # que sí tiene forget_btn empujándolo. Mismo criterio que
            # close_spacer en process_module.py.
            spacer = Gtk.Button()
            spacer.add(Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
            spacer.get_style_context().add_class("bluetooth-forget-btn")
            spacer.set_sensitive(False)
            spacer.set_opacity(0)
            row.pack_start(spacer, False, False, 0)

        return self._wrap_row(row)

    def _make_autoconnect_toggle(self, device):
        mac = device["mac"]
        btn = Gtk.ToggleButton(label=t("bluetooth", "auto_btn_label"))
        btn.get_style_context().add_class("bluetooth-autoconnect-btn")
        btn.set_tooltip_text(t("bluetooth", "auto_btn_tooltip"))
        is_auto = mac in self._autoconnect_macs
        btn.set_active(is_auto)
        if is_auto:
            btn.get_style_context().add_class("active")
        btn.connect("toggled", self._on_autoconnect_toggled, mac)
        return btn

    def _on_autoconnect_toggled(self, btn, mac):
        ctx = btn.get_style_context()
        if btn.get_active():
            self._autoconnect_macs.add(mac)
            ctx.add_class("active")
        else:
            self._autoconnect_macs.discard(mac)
            ctx.remove_class("active")
        bluetooth_actions.set_autoconnect(self._autoconnect_delay, self._autoconnect_macs)

    def _make_connection_switch(self, device):
        """Un switch para las tres acciones (conectar/desconectar/
        emparejar) -- sin emparejar, refleja el pedido de emparejar;
        no hay "desemparejar" simétrico (eso es el botón de basura)."""
        mac, name = device["mac"], device["name"]
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.get_style_context().add_class("toggle-row-switch")
        switch.set_active(device["connected"])
        if device["paired"]:
            switch.set_tooltip_text(
                t("bluetooth", "disconnect_tooltip") if device["connected"] else t("bluetooth", "connect_tooltip"))
        else:
            switch.set_tooltip_text(t("bluetooth", "pair_tooltip"))

        def on_state_set(_sw, state):
            if device["paired"]:
                if state:
                    self._on_connect_clicked(switch, mac, name)
                else:
                    self._on_disconnect_clicked(switch, mac, name)
            elif state:
                self._on_pair_clicked(switch, mac, name)
            return False

        switch.connect("state-set", on_state_set)
        return switch

    # ---- Acciones por dispositivo ----------------------------------------

    def _on_connect_clicked(self, _btn, mac, name):
        self._run_action(
            lambda: bluetooth_actions.connect(mac),
            t("bluetooth", "connecting", name=name),
            lambda ok, msg: self.set_status("" if ok else t("bluetooth", "error", msg=msg)[:120]),
        )

    def _on_disconnect_clicked(self, _btn, mac, name):
        self._run_action(
            lambda: bluetooth_actions.disconnect(mac),
            t("bluetooth", "disconnecting", name=name),
            lambda ok, msg: self.set_status("" if ok else t("bluetooth", "error", msg=msg)[:120]),
        )

    def _on_pair_clicked(self, _btn, mac, name):
        self._run_action(
            lambda: bluetooth_actions.pair(mac),
            t("bluetooth", "pairing", name=name),
            lambda ok, msg: self.set_status("" if ok else t("bluetooth", "error", msg=msg)[:120]),
        )

    def _on_forget_clicked(self, btn, mac, name):
        win = btn.get_toplevel()
        dialog = Gtk.MessageDialog(
            transient_for=win, flags=0, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=t("bluetooth", "forget_confirm_title", name=name),
        )
        dialog.format_secondary_text(mac)

        # Mismo workaround de layer-shell que ProcessTab._on_close_clicked:
        # la ventana del dashboard es Layer.TOP a pantalla completa y se
        # come los clicks del diálogo si no se oculta antes de dialog.run().
        original_prgname = GLib.get_prgname()
        GLib.set_prgname("bluetooth-forget-dialog")
        dialog.show_all()
        GLib.set_prgname(original_prgname)

        win.hide()
        response = dialog.run()
        dialog.destroy()
        win.show()

        if response == Gtk.ResponseType.YES:
            write_autoconnect = mac in self._autoconnect_macs
            if write_autoconnect:
                self._autoconnect_macs.discard(mac)

            def forget():
                # Sin esto, un MAC olvidado seguiría en
                # bluetooth-autoconnect.json -- bluetooth_autoconnect.py
                # llamaría connect contra un dispositivo que ya no está
                # emparejado en cada inicio de sesión. La escritura del
                # archivo va acá adentro (hilo de _run_action), no en el
                # click, para no bloquear el hilo de GTK con I/O.
                if write_autoconnect:
                    bluetooth_actions.set_autoconnect(self._autoconnect_delay, self._autoconnect_macs)
                return bluetooth_actions.remove(mac)

            self._run_action(
                forget,
                t("bluetooth", "forgetting", name=name),
                lambda ok, msg: self.set_status("" if ok else t("bluetooth", "error", msg=msg)[:120]),
            )

    # ---- Ícono (async, cacheado por MAC) --------------------------------

    def _make_icon_slot(self, mac, resolve_icons):
        slot = Gtk.Box()
        slot.set_size_request(ICON_SIZE, ICON_SIZE)

        cached = self._icon_cache.get(mac)
        icon_name = cached if cached and cached != "none" else "bluetooth"
        slot.add(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR))

        if cached is not None or not resolve_icons:
            return slot

        def worker():
            icon = bluetooth_state.device_icon(mac) or "none"
            GLib.idle_add(self._apply_icon, slot, mac, icon)

        threading.Thread(target=worker, daemon=True).start()
        return slot

    def _apply_icon(self, slot, mac, icon):
        self._icon_cache[mac] = icon
        if icon == "none":
            return False
        # El slot puede haber quedado desprendido si un refresco reconstruyó
        # la lista antes de que el hilo terminara -- mismo criterio que
        # ProcessTab._apply_icon: set_from_pixbuf/add sobre un widget sin
        # padre no rompe nada, el cache ya quedó completo para la próxima fila.
        for child in slot.get_children():
            slot.remove(child)
        slot.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR))
        slot.show_all()
        return False  # GLib.idle_add: correr una sola vez


def build_bluetooth_tab(container):
    load_module_css(MODULE_CSS)
    BluetoothTab(container)

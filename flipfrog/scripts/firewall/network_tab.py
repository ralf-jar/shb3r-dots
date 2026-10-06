"""Pestaña "Red" (build_network_tab): conexión activa (network_info.py),
actividad por proceso (nethogs, network_monitor.py) y Wifi
(wifi_state.py/wifi_actions.py, nmcli sin privilegios). Ver CLAUDE.md,
"Pestaña Red y ventana Firewall"."""

import json
import os
import signal
import subprocess
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, GLib, Gdk, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import network_info
import wifi_state
import wifi_actions

sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
from waybar_lib import load_module_css, build_switch_row, svg_icon_image, _icon_text_button
from i18n import t

MODULE_CSS = os.path.join(SCRIPT_DIR, "network_tab.css")
ICONS_DIR = os.path.join(SCRIPT_DIR, "icons")
ACTIVITY_ICON = os.path.join(ICONS_DIR, "activity.svg")
FIREWALL_ICON = os.path.join(ICONS_DIR, "firewall.svg")
VPN_ICON = os.path.join(ICONS_DIR, "vpn.svg")
WIFI_ICON = os.path.join(ICONS_DIR, "wifi.svg")
WIFI_SCAN_ICON = os.path.join(ICONS_DIR, "wifi-scan.svg")

# Umbrales calcados de los que usa el indicador de red de GNOME --
# nmcli ya da el % de señal (0-100), no hace falta resolver ningún
# ícono por red, solo elegir el nombre simbólico del tema según rango.
_SIGNAL_LEVELS = (
    (80, "network-wireless-signal-excellent-symbolic"),
    (60, "network-wireless-signal-good-symbolic"),
    (40, "network-wireless-signal-ok-symbolic"),
    (20, "network-wireless-signal-weak-symbolic"),
    (0, "network-wireless-signal-none-symbolic"),
)


def _signal_icon_name(signal_pct):
    for threshold, name in _SIGNAL_LEVELS:
        if signal_pct >= threshold:
            return name
    return _SIGNAL_LEVELS[-1][1]


def _section_title_row(text):
    """Subtítulo + línea que se estira sola. Devuelve (row, title_label)
    -- el caller puede seguir empacando algo más a la derecha."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.get_style_context().add_class("network-title-row")

    title = Gtk.Label(label=text)
    title.get_style_context().add_class("network-section-title")
    row.pack_start(title, False, False, 0)

    divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    divider.get_style_context().add_class("network-title-divider")
    divider.set_valign(Gtk.Align.CENTER)
    row.pack_start(divider, True, True, 0)

    return row, title


def _make_empty_state_label(text, tooltip=None):
    """Label de estado vacío -- wrap+max_width_chars, no ancho natural
    (scroller de policy NEVER en un eje, ver Gotchas transversales en
    CLAUDE.md). `tooltip`: la explicación va ahí, nunca pegada al
    label (ver feedback_tooltip_not_inline_text)."""
    label = Gtk.Label(label=text)
    label.get_style_context().add_class("network-activity-empty")
    label.set_line_wrap(True)
    label.set_max_width_chars(28)
    label.set_justify(Gtk.Justification.CENTER)
    if tooltip:
        label.set_tooltip_text(tooltip)
    return label


WAYBAR_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "waybar"))
NETWORK_MONITOR_SCRIPT = os.path.join(SCRIPT_DIR, "network_monitor.py")
NETWORK_CACHE = os.path.join(WAYBAR_DIR, "network-usage.json")
NETWORK_LOCK = "/tmp/network-monitor.pid"
NETWORK_REFRESH_MS = 2000

FIREWALL_POPUP = os.path.join(SCRIPT_DIR, "firewall_popup.py")
VPN_POPUP = os.path.join(SCRIPT_DIR, "..", "vpn", "vpn_config_popup.py")


def network_monitor_pid():
    """Mismo patrón que rotator_pid() en theme_module.py -- os.kill(pid, 0)
    solo chequea que el proceso exista, no lo mata."""
    try:
        with open(NETWORK_LOCK) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError):
        return None


def start_network_monitor():
    """start_new_session=True: el daemon sigue vivo con el dashboard
    cerrado, igual que theme-rotator.py -- se apaga a mano desde el
    mismo toggle, no cuando se cierra esta pestaña."""
    subprocess.Popen(
        ["python3", NETWORK_MONITOR_SCRIPT],
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def stop_network_monitor():
    pid = network_monitor_pid()
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if os.path.exists(NETWORK_LOCK):
        try:
            os.remove(NETWORK_LOCK)
        except FileNotFoundError:
            pass


def read_network_usage():
    try:
        with open(NETWORK_CACHE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


WIFI_BACKGROUND_REFRESH_MS = 4000


class NetworkTab:
    def __init__(self, container):
        self._net_timeout_id = None
        self._wifi_timeout_id = None
        self._wifi_busy = False
        self._wifi_refresh_in_flight = False
        # SSID con el input de contraseña inline abierto (ver
        # _make_wifi_row/_build_password_input); None = ninguna.
        self._wifi_password_ssid = None
        self._wifi_password_entry = None

        # homogeneous, mismo spacing que `columns` abajo -- cada mitad
        # alinea con su columna correspondiente.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        header.set_homogeneous(True)

        activity_icon = svg_icon_image(ACTIVITY_ICON)
        activity_running = network_monitor_pid() is not None
        build_switch_row(header, activity_icon, t("red", "monitor_label"),
                          activity_running, self._on_network_toggled)

        firewall_btn = _icon_text_button(
            FIREWALL_ICON, t("red", "firewall_btn_label"), t("red", "firewall_btn_tooltip"))
        firewall_btn.get_style_context().add_class("network-firewall-btn")
        firewall_btn.connect("clicked", self._on_open_firewall)

        vpn_btn = _icon_text_button(
            VPN_ICON, t("red", "vpn_btn_label"), t("red", "vpn_btn_tooltip"))
        vpn_btn.get_style_context().add_class("network-firewall-btn")
        vpn_btn.connect("clicked", lambda _b: subprocess.Popen(["python3", VPN_POPUP]))

        buttons = Gtk.Box(spacing=56)
        buttons.set_halign(Gtk.Align.CENTER)
        buttons.pack_start(firewall_btn, False, False, 0)
        buttons.pack_start(vpn_btn, False, False, 0)
        # fill=True (no False) -- en un Gtk.Box homogeneous, fill=False
        # centra el widget sin importar halign (ver Gotchas
        # transversales en CLAUDE.md).
        header.pack_start(buttons, False, True, 0)

        container.pack_start(header, False, False, 0)

        # homogeneous=True: las dos secciones tienen anchos naturales
        # muy distintos (tabla de texto vs. columnas KB/s), sin esto no
        # quedan 50/50 real.
        columns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        columns.set_name("network-columns")
        columns.set_homogeneous(True)
        columns.pack_start(self._build_activity_section(), True, True, 0)
        columns.pack_start(self._build_info_section(), True, True, 0)
        # expand=False: que `columns` mida su alto natural y "Wifi"
        # quede pegado debajo, sin hueco en el medio.
        container.pack_start(columns, False, False, 0)

        # expand=True acá sí -- el scroller de redes es quien debe
        # absorber el alto sobrante de la página.
        container.pack_start(self._build_wifi_section(), True, True, 0)

        container.connect("destroy", self._on_destroy)

        self.refresh_info()
        self._refresh_activity()
        self._refresh_wifi()
        self._schedule_wifi_background_refresh()

    # ---- Ciclo de vida --------------------------------------------------

    def _on_destroy(self, *_a):
        if self._net_timeout_id is not None:
            GLib.source_remove(self._net_timeout_id)
            self._net_timeout_id = None
        if self._wifi_timeout_id is not None:
            GLib.source_remove(self._wifi_timeout_id)
            self._wifi_timeout_id = None

    # ---- Info de la conexión activa ------------------------------------

    def _build_info_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_name("network-info-section")

        title_row, _title = _section_title_row(t("red", "section_conexion"))
        box.pack_start(title_row, False, False, 0)

        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.pack_start(self.info_box, False, False, 0)

        return box

    def refresh_info(self):
        for child in self.info_box.get_children():
            self.info_box.remove(child)

        info = network_info.get_connection_info()
        if info is None:
            empty = Gtk.Label(label=t("red", "sin_conexion"))
            empty.set_halign(Gtk.Align.START)
            empty.get_style_context().add_class("network-info-empty")
            self.info_box.pack_start(empty, False, False, 0)
            self.info_box.show_all()
            return

        rows = [(t("red", "info_interfaz"), f"{info['interface']} ({info['connection_name']})")]
        if info["wireless"]:
            if info["ssid"]:
                rows.append((t("red", "info_wifi"), t(
                    "red", "wifi_ssid_signal", ssid=info["ssid"], signal=info["signal"])))
            else:
                rows.append((t("red", "info_wifi"), t("red", "wifi_sin_asociar")))
        else:
            rows.append((t("red", "info_tipo"), t("red", "tipo_cable")))
        if info["speed_mbps"]:
            rows.append((t("red", "info_velocidad"), f"{info['speed_mbps']} Mbps"))
        rows.append((t("red", "info_ip_local"), info["ip"] or "?"))
        rows.append((t("red", "info_gateway"), info["gateway"] or "?"))
        if info["dns"]:
            rows.append((t("red", "info_dns"), info["dns"]))

        for label_text, value_text in rows:
            self.info_box.pack_start(self._make_info_row(label_text, value_text), False, False, 0)
        self.info_box.show_all()

    def _make_info_row(self, label_text, value_text):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("network-info-row")

        label = Gtk.Label(label=label_text)
        label.set_halign(Gtk.Align.START)
        label.get_style_context().add_class("network-info-label")
        row.pack_start(label, False, False, 0)

        value = Gtk.Label(label=value_text)
        value.set_halign(Gtk.Align.START)
        row.pack_start(value, True, True, 0)

        return row

    def _on_open_firewall(self, _btn):
        subprocess.Popen(["python3", FIREWALL_POPUP])

    # ---- Actividad de red (nethogs) ------------------------------------

    def _build_activity_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_name("network-activity-section")

        title_row, _title = _section_title_row(t("red", "section_actividad"))
        box.pack_start(title_row, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("network-activity-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.network_list = Gtk.ListBox()
        self.network_list.set_name("network-activity-list")
        self.network_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.add(self.network_list)
        box.pack_start(scroller, True, True, 0)

        return box

    def _on_network_toggled(self, running):
        # common.run_async (mismo worker FIFO que toggles/*.py): Popen/
        # os.kill acá son rápidos, pero se encola igual por consistencia
        # y para no bloquear el hilo de GTK si el arranque/apagado tarda.
        common.run_async(lambda: self._apply_network_toggle(running))

    def _apply_network_toggle(self, running):
        if running:
            start_network_monitor()
        else:
            stop_network_monitor()
        GLib.idle_add(self._refresh_activity)

    def _refresh_activity(self):
        # Se reprograma a sí mismo (en vez de GLib.timeout_add fijo desde
        # __init__) para poder frenar sin condiciones de carrera al
        # destruir la pestaña -- mismo patrón que ProcessTab._on_tick.
        self._net_timeout_id = GLib.timeout_add(NETWORK_REFRESH_MS, self._on_network_tick)

        for child in self.network_list.get_children():
            self.network_list.remove(child)

        if network_monitor_pid() is None:
            empty = _make_empty_state_label(
                t("red", "monitor_off"), tooltip=t("red", "monitor_off_tooltip"))
            self.network_list.add(self._wrap_row(empty))
            self.network_list.show_all()
            return

        entries = read_network_usage()
        if not entries:
            empty = _make_empty_state_label(t("red", "sin_actividad"))
            self.network_list.add(self._wrap_row(empty))
        else:
            entries = sorted(entries, key=lambda e: e["sent_kbps"] + e["recv_kbps"], reverse=True)
            for entry in entries:
                self.network_list.add(self._make_network_row(entry))

        self.network_list.show_all()

    def _on_network_tick(self):
        self._refresh_activity()
        return False  # el propio _refresh_activity reprograma el siguiente

    def _wrap_row(self, widget):
        wrap = Gtk.ListBoxRow()
        wrap.set_selectable(False)
        wrap.add(widget)
        return wrap

    def _make_network_row(self, entry):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("network-activity-row")

        name_label = Gtk.Label(label=entry["name"])
        name_label.set_halign(Gtk.Align.START)
        name_label.get_style_context().add_class("network-activity-name")
        row.pack_start(name_label, True, True, 0)

        sent_label = Gtk.Label(label=t("red", "sent_kbps", kbps=entry["sent_kbps"]))
        sent_label.get_style_context().add_class("network-activity-sent")
        sent_label.set_width_chars(12)
        sent_label.set_xalign(1.0)
        row.pack_start(sent_label, False, False, 0)

        recv_label = Gtk.Label(label=t("red", "recv_kbps", kbps=entry["recv_kbps"]))
        recv_label.get_style_context().add_class("network-activity-recv")
        recv_label.set_width_chars(12)
        recv_label.set_xalign(1.0)
        row.pack_start(recv_label, False, False, 0)

        return self._wrap_row(row)

    # ---- Sección "Wifi" --------------------------------------------------

    def _build_wifi_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_name("network-wifi-section")

        title_row, _title = _section_title_row(t("red", "section_wifi"))
        box.pack_start(title_row, False, False, 0)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        top_row.set_homogeneous(True)

        wifi_icon = svg_icon_image(WIFI_ICON)
        _wrapper, self.wifi_radio_switch, self._wifi_radio_handler = build_switch_row(
            top_row, wifi_icon, t("red", "wifi_radio_label"),
            wifi_state.radio_enabled(), self._on_wifi_radio_toggled)

        self.wifi_scan_btn = _icon_text_button(
            WIFI_SCAN_ICON, t("red", "wifi_scan_label"), t("red", "wifi_scan_tooltip"))
        self.wifi_scan_btn.get_style_context().add_class("network-wifi-scan-btn")
        self.wifi_scan_btn.set_halign(Gtk.Align.CENTER)
        self.wifi_scan_btn.connect("clicked", self._on_wifi_scan_clicked)
        top_row.pack_start(self.wifi_scan_btn, False, True, 0)

        box.pack_start(top_row, False, False, 0)

        self.wifi_status_label = Gtk.Label(label="")
        self.wifi_status_label.set_halign(Gtk.Align.START)
        self.wifi_status_label.get_style_context().add_class("network-wifi-status-label")
        self.wifi_status_label.set_no_show_all(True)
        box.pack_start(self.wifi_status_label, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("network-wifi-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Piso, no tope -- expand=True en el pack_start de abajo deja
        # que absorba el alto sobrante real de la página.
        scroller.set_min_content_height(90)
        self.wifi_list = Gtk.ListBox()
        self.wifi_list.set_name("network-wifi-list")
        self.wifi_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroller.add(self.wifi_list)
        box.pack_start(scroller, True, True, 0)

        return box

    def set_wifi_status(self, text):
        self.wifi_status_label.set_text(text)
        self.wifi_status_label.set_no_show_all(not bool(text))
        self.wifi_status_label.set_visible(bool(text))

    def _set_wifi_busy(self, busy):
        self._wifi_busy = busy
        self.wifi_radio_switch.set_sensitive(not busy)
        self.wifi_scan_btn.set_sensitive(not busy)
        self.wifi_list.set_sensitive(not busy)

    def _run_wifi_action(self, fn, busy_text):
        self._set_wifi_busy(True)
        self.set_wifi_status(busy_text)

        def worker():
            ok, msg = fn()
            GLib.idle_add(self._finish_wifi_action, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_wifi_action(self, ok, msg):
        self._set_wifi_busy(False)
        self.set_wifi_status("" if ok else t("red", "error", msg=msg)[:120])
        self._refresh_wifi()
        return False  # GLib.idle_add: correr una sola vez

    def _on_wifi_radio_toggled(self, state):
        self._run_wifi_action(
            lambda: wifi_actions.set_radio(state),
            t("red", "wifi_on") if state else t("red", "wifi_off"),
        )

    def _on_wifi_scan_clicked(self, _btn):
        self._run_wifi_action(wifi_actions.rescan, t("red", "wifi_scanning"))

    def _schedule_wifi_background_refresh(self):
        """Poll liviano (mismo patrón/cadencia que BACKGROUND_REFRESH_MS
        en bluetooth_tab.py) -- apagar y volver a encender el Wifi
        dispara un reconecto automático de NetworkManager a la última
        red guardada, pero eso pasa en segundo plano DESPUÉS de que
        `set_radio` ya devolvió éxito (asociación + DHCP tardan unos
        segundos más) -- sin este poll, el switch de esa red seguía
        mostrándose apagado hasta que el usuario tocaba algo más (ej.
        "Buscar redes") que disparara un refresh nuevo. Se reprograma a
        sí mismo, mismo criterio que _on_network_tick, para poder
        frenarlo sin condiciones de carrera al destruir la pestaña."""
        self._wifi_timeout_id = GLib.timeout_add(
            WIFI_BACKGROUND_REFRESH_MS, self._on_wifi_background_tick)

    def _on_wifi_background_tick(self):
        if not self._wifi_busy:
            self._refresh_wifi()
        self._schedule_wifi_background_refresh()
        return False  # se reprograma a sí mismo

    def _refresh_wifi(self):
        """Dispara `nmcli` (2-3 subprocess por llamada, hasta TIMEOUT=4s
        cada uno) en un hilo aparte -- antes corría directo acá, tanto
        desde el poll de 4s (_on_wifi_background_tick) como al abrir la
        pestaña, y podía trabar el dashboard ENTERO si nmcli se colgaba.
        `_wifi_refresh_in_flight` evita apilar polls mientras uno
        anterior sigue esperando."""
        if self._wifi_refresh_in_flight:
            return False
        self._wifi_refresh_in_flight = True

        def worker():
            enabled = wifi_state.radio_enabled()
            networks = wifi_state.list_networks() if enabled else []
            GLib.idle_add(self._apply_wifi_refresh, enabled, networks)

        threading.Thread(target=worker, daemon=True).start()
        return False  # permite usar esta función directo con GLib.idle_add

    def _apply_wifi_refresh(self, enabled, networks):
        self._wifi_refresh_in_flight = False

        self.wifi_radio_switch.handler_block(self._wifi_radio_handler)
        self.wifi_radio_switch.set_active(enabled)
        self.wifi_radio_switch.handler_unblock(self._wifi_radio_handler)
        self.wifi_scan_btn.set_sensitive(enabled)

        for child in self.wifi_list.get_children():
            self.wifi_list.remove(child)

        if not enabled:
            empty = _make_empty_state_label(t("red", "wifi_radio_off"))
            self.wifi_list.add(self._wrap_row(empty))
            self.wifi_list.show_all()
            return False

        if not networks:
            empty = _make_empty_state_label(
                t("red", "sin_redes"), tooltip=t("red", "sin_redes_tooltip"))
            self.wifi_list.add(self._wrap_row(empty))
        else:
            for entry in networks:
                self.wifi_list.add(self._make_wifi_row(entry))
        self.wifi_list.show_all()
        return False  # GLib.idle_add: correr una sola vez

    def _make_wifi_row(self, entry):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("network-wifi-row")

        signal_icon = Gtk.Image.new_from_icon_name(
            _signal_icon_name(entry["signal"]), Gtk.IconSize.SMALL_TOOLBAR)
        signal_icon.get_style_context().add_class("network-wifi-signal-icon")
        signal_icon.set_tooltip_text(t("red", "senal_tooltip", pct=entry["signal"]))
        row.pack_start(signal_icon, False, False, 0)

        name_label = Gtk.Label(label=entry["ssid"])
        name_label.set_halign(Gtk.Align.START)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_label.set_max_width_chars(22)
        name_label.get_style_context().add_class("network-wifi-name")
        row.pack_start(name_label, True, True, 0)

        # Fila en modo "pidiendo contraseña" (ver _on_wifi_network_switch)
        # -- input + botón [check] en vez del switch normal, pedido
        # explícito del usuario en vez de un Gtk.Dialog aparte.
        if entry["ssid"] == self._wifi_password_ssid:
            row.pack_start(self._build_password_input(entry["ssid"]), False, False, 0)
            return self._wrap_row(row)

        if entry["secured"]:
            lock_icon = Gtk.Image.new_from_icon_name(
                "changes-prevent-symbolic", Gtk.IconSize.SMALL_TOOLBAR)
            lock_icon.set_tooltip_text(t("red", "red_protegida"))
            row.pack_start(lock_icon, False, False, 0)

        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.get_style_context().add_class("toggle-row-switch")
        switch.set_active(entry["connected"])
        switch.set_tooltip_text(
            t("red", "wifi_disconnect_tooltip") if entry["connected"] else t("red", "wifi_connect_tooltip"))
        switch.connect("state-set", self._on_wifi_network_switch, entry)
        row.pack_start(switch, False, False, 0)

        return self._wrap_row(row)

    def _build_password_input(self, ssid):
        """Input + botón [check] inline en la fila -- Escape cancela
        (ver _on_wifi_password_key), sin botón de cancelar propio."""
        wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_width_chars(14)
        entry.set_placeholder_text(t("red", "password_placeholder"))
        entry.get_style_context().add_class("network-wifi-password-entry")
        # margin_top compensa que Gtk.Entry reserva px invisibles solo
        # abajo de su recuadro (mismo gotcha que delay_entry en
        # bluetooth_tab.py) -- si no, el texto queda corrido hacia
        # arriba con valign(CENTER).
        entry.set_valign(Gtk.Align.CENTER)
        entry.set_margin_top(4)
        entry.connect("activate", self._on_wifi_password_confirm, ssid)
        entry.connect("key-press-event", self._on_wifi_password_key)
        wrap.pack_start(entry, False, False, 0)
        self._wifi_password_entry = entry

        confirm_btn = Gtk.Button()
        confirm_btn.set_image(
            Gtk.Image.new_from_icon_name("object-select-symbolic", Gtk.IconSize.BUTTON))
        confirm_btn.set_always_show_image(True)
        confirm_btn.set_tooltip_text(t("red", "wifi_confirm_tooltip"))
        confirm_btn.get_style_context().add_class("network-wifi-confirm-btn")
        confirm_btn.set_valign(Gtk.Align.CENTER)
        confirm_btn.connect("clicked", self._on_wifi_password_confirm, ssid)
        wrap.pack_start(confirm_btn, False, False, 0)

        # idle_add: grab_focus no funciona hasta que el widget esté
        # mapeado, recién después de que _refresh_wifi lo muestre.
        GLib.idle_add(entry.grab_focus)
        return wrap

    def _on_wifi_password_confirm(self, _widget, ssid):
        password = self._wifi_password_entry.get_text()
        self._wifi_password_ssid = None
        self._run_wifi_action(
            lambda: wifi_actions.connect(ssid, password), t("red", "wifi_connecting", ssid=ssid))

    def _on_wifi_password_key(self, _entry, event):
        if event.keyval == Gdk.KEY_Escape:
            self._wifi_password_ssid = None
            self._refresh_wifi()
            return True
        return False

    def _on_wifi_network_switch(self, switch, state, entry):
        ssid = entry["ssid"]
        if not state:
            self._run_wifi_action(wifi_actions.disconnect, t("red", "wifi_disconnecting", ssid=ssid))
            return False

        if entry["secured"]:
            # idle_add, no sincrónico: seguimos dentro del handler de
            # "state-set" de este switch, y _refresh_wifi reconstruye
            # toda la lista (este switch incluido).
            switch.handler_block_by_func(self._on_wifi_network_switch)
            switch.set_active(False)
            switch.handler_unblock_by_func(self._on_wifi_network_switch)
            self._wifi_password_ssid = ssid
            GLib.idle_add(self._refresh_wifi)
            return True  # no dejar que el manejador default lo prenda

        self._run_wifi_action(
            lambda: wifi_actions.connect(ssid, None), t("red", "wifi_connecting", ssid=ssid))
        return False


def build_network_tab(container):
    load_module_css(MODULE_CSS)
    NetworkTab(container)

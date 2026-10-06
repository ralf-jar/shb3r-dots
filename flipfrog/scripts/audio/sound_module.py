"""Pestaña "Sonido" (build_sound_tab) -- ecualizador de 6 bandas.
Ver CLAUDE.md, "Pestaña 'Sonido'" para el mecanismo completo
(filter-chain.service, integración con Autoecualizador de Bandcamp,
etc.)."""

import json
import os
import socket
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import eq_pw
import eq_actions
import eq_presets
import audio_devices

sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
from waybar_lib import load_module_css, svg_icon_image, _icon_text_button
from i18n import t

MODULE_CSS = os.path.join(SCRIPT_DIR, "sound_module.css")

ICONS_DIR = os.path.join(SCRIPT_DIR, "icons")
RESET_ICON = os.path.join(ICONS_DIR, "reset.svg")
SAVE_ICON = os.path.join(ICONS_DIR, "save.svg")
DEVICE_ICON = os.path.join(ICONS_DIR, "device.svg")

BANDCAMP_AUTO_EQ_FILE = os.path.join(SCRIPT_DIR, "..", "bandcamp", "auto-eq.json")
BANDCAMP_SOCKET = "/tmp/bandcamp-radio-ctl.sock"


def _bandcamp_auto_eq_enabled():
    try:
        with open(BANDCAMP_AUTO_EQ_FILE) as f:
            return bool(json.load(f).get("enabled", False))
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _disable_bandcamp_auto_eq():
    try:
        common.atomic_write(BANDCAMP_AUTO_EQ_FILE, json.dumps({"enabled": False}))
    except OSError:
        pass
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(BANDCAMP_SOCKET)
        sock.sendall((json.dumps({"cmd": "set_auto_eq", "enabled": False}) + "\n").encode())
        sock.close()
    except OSError:
        pass


def _make_band_column(band, gain, on_commit):
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    column.get_style_context().add_class("eq-band")

    value_label = Gtk.Label(label=f"{gain:+.1f}")
    value_label.get_style_context().add_class("eq-value-label")
    column.pack_start(value_label, False, False, 0)

    adj = Gtk.Adjustment(
        value=gain, lower=eq_pw.MIN_GAIN, upper=eq_pw.MAX_GAIN,
        step_increment=0.5, page_increment=2, page_size=0,
    )
    scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, adjustment=adj)
    scale.set_inverted(True)  # +12 arriba, -12 abajo
    scale.set_draw_value(False)
    scale.set_vexpand(True)
    scale.get_style_context().add_class("eq-scale")
    scale.add_mark(0, Gtk.PositionType.RIGHT, None)

    def on_value_changed(s, band=band, value_label=value_label):
        gain_db = s.get_value()
        value_label.set_text(f"{gain_db:+.1f}")
        eq_pw.set_band_gain(band, gain_db)

    def on_release(s, _event, band=band):
        eq_actions.set_band_gain(band, s.get_value())
        on_commit()

    scale.connect("value-changed", on_value_changed)
    scale.connect("button-release-event", on_release)
    column.pack_start(scale, True, True, 0)

    freq_label = Gtk.Label(label=eq_pw.BAND_LABELS[band])
    freq_label.get_style_context().add_class("eq-freq-label")
    column.pack_start(freq_label, False, False, 0)

    return column, scale, value_label


def _build_unavailable_notice():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.set_valign(Gtk.Align.CENTER)
    box.set_vexpand(True)
    label = Gtk.Label(label=t("sonido", "eq_no_disponible"))
    label.set_name("tab-loading")
    label.set_justify(Gtk.Justification.CENTER)
    label.set_halign(Gtk.Align.CENTER)
    box.pack_start(label, True, True, 0)
    return box


def build_sound_tab(container):
    load_module_css(MODULE_CSS)

    if not eq_pw.eq_sink_loaded():
        container.pack_start(_build_unavailable_notice(), True, True, 0)
        return

    auto_state = {"active": _bandcamp_auto_eq_enabled()}
    PLACEHOLDER_IDLE = t("sonido", "preset_placeholder_idle")
    PLACEHOLDER_AUTO = t("sonido", "preset_placeholder_auto")

    gains = eq_pw.get_band_gains()

    preset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    preset_row.set_name("eq-preset-row")

    preset_label = Gtk.Label(label=t("sonido", "preajustes_label"))
    preset_label.get_style_context().add_class("eq-preset-label")
    preset_row.pack_start(preset_label, False, False, 0)

    preset_combo = Gtk.ComboBoxText()
    preset_combo.set_hexpand(True)
    preset_combo.get_style_context().add_class("eq-preset-combo")
    preset_row.pack_start(preset_combo, True, True, 0)

    delete_preset_btn = Gtk.Button()
    delete_preset_btn.add(Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.SMALL_TOOLBAR))
    delete_preset_btn.set_tooltip_text(t("sonido", "borrar_preajuste_tooltip"))
    delete_preset_btn.get_style_context().add_class("eq-delete-preset-btn")
    preset_row.pack_start(delete_preset_btn, False, False, 0)

    container.pack_start(preset_row, False, False, 0)

    def _all_presets():
        return {**eq_presets.PRESETS, **eq_presets.load_custom_presets()}

    def _sync_delete_sensitivity(_combo=None):
        name = preset_combo.get_active_text()
        delete_preset_btn.set_sensitive(name in eq_presets.load_custom_presets())

    def _populate_presets(select_name=None):
        preset_combo.handler_block(preset_changed_handler)
        preset_combo.remove_all()
        preset_combo.append_text(PLACEHOLDER_AUTO if auto_state["active"] else PLACEHOLDER_IDLE)
        custom_names = list(eq_presets.load_custom_presets().keys())
        all_names = eq_presets.PRESET_ORDER + custom_names
        for name in all_names:
            preset_combo.append_text(name)

        if select_name is not None and select_name in all_names:
            matched_index = all_names.index(select_name) + 1
        else:
            matched_index = 0
            if not auto_state["active"]:
                current = tuple(round(gains.get(b, 0.0), 1) for b in range(1, eq_pw.BAND_COUNT + 1))
                for i, name in enumerate(all_names, start=1):
                    preset_vals = tuple(round(float(v), 1) for v in _all_presets()[name])
                    if preset_vals == current:
                        matched_index = i
                        break
        preset_combo.set_active(matched_index)
        preset_combo.handler_unblock(preset_changed_handler)
        _sync_delete_sensitivity()

    def _maybe_disable_auto():
        if not auto_state["active"]:
            return
        auto_state["active"] = False
        _disable_bandcamp_auto_eq()
        was_placeholder = preset_combo.get_active() == 0
        preset_combo.remove(0)
        preset_combo.prepend_text(PLACEHOLDER_IDLE)
        if was_placeholder:
            preset_combo.set_active(0)

    bands_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    bands_row.set_name("eq-bands-row")
    bands_row.set_homogeneous(True)
    bands_row.set_vexpand(True)

    scales = []
    for band in range(1, eq_pw.BAND_COUNT + 1):
        column, scale, value_label = _make_band_column(band, gains.get(band, 0.0), _maybe_disable_auto)
        bands_row.pack_start(column, True, True, 0)
        scales.append((band, scale, value_label))

    container.pack_start(bands_row, True, True, 0)

    def _apply_gains(gains_by_band):
        for band, scale, _value_label in scales:
            eq_actions.set_band_gain(band, gains_by_band[band - 1])
            # set_value() ya dispara "value-changed" (actualiza el label
            # y reaplica el gain en vivo) -- set_band_gain de arriba ya
            # lo persistió a disco, no hace falta duplicar nada acá.
            scale.set_value(gains_by_band[band - 1])

    def on_preset_changed(combo):
        name = combo.get_active_text()
        gains_by_band = _all_presets().get(name)
        _sync_delete_sensitivity()
        if gains_by_band is None:
            return  # el placeholder "Elegir preajuste..."/"Automático..." no hace nada
        _maybe_disable_auto()
        _apply_gains(gains_by_band)

    preset_changed_handler = preset_combo.connect("changed", on_preset_changed)
    _populate_presets()

    def _on_delete_preset(_btn):
        name = preset_combo.get_active_text()
        eq_presets.delete_custom_preset(name)
        _populate_presets()

    delete_preset_btn.connect("clicked", _on_delete_preset)

    actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)
    actions_row.set_name("eq-actions-row")
    actions_row.set_halign(Gtk.Align.CENTER)

    reset_btn = _icon_text_button(
        RESET_ICON, t("sonido", "restablecer_btn"), t("sonido", "restablecer_tooltip"))
    reset_btn.get_style_context().add_class("eq-action-btn")

    def on_reset(_btn):
        _maybe_disable_auto()
        _apply_gains((0, 0, 0, 0, 0, 0))
        _populate_presets()

    reset_btn.connect("clicked", on_reset)
    actions_row.pack_start(reset_btn, False, False, 0)

    save_preset_btn = _icon_text_button(
        SAVE_ICON, t("sonido", "guardar_preajuste_btn"), t("sonido", "guardar_preajuste_tooltip"))
    save_preset_btn.get_style_context().add_class("eq-action-btn")

    def _on_save_preset(btn):
        win = btn.get_toplevel()
        dialog = Gtk.Dialog(title=t("sonido", "dialogo_guardar_titulo"), transient_for=win, modal=True)
        dialog.add_button(t("sonido", "dialogo_cancelar"), Gtk.ResponseType.CANCEL)
        dialog.add_button(t("sonido", "dialogo_guardar"), Gtk.ResponseType.OK)
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text(t("sonido", "nombre_preajuste_placeholder"))
        name_entry.set_activates_default(True)
        dialog.get_content_area().pack_start(name_entry, True, True, 8)
        dialog.set_default_response(Gtk.ResponseType.OK)

        # workaround layer-shell, ver CLAUDE.md "Editor de temas"
        original_prgname = GLib.get_prgname()
        GLib.set_prgname("sound-save-preset-dialog")
        dialog.show_all()
        GLib.set_prgname(original_prgname)

        win.hide()
        response = dialog.run()
        name = name_entry.get_text().strip()
        dialog.destroy()
        win.show()

        if response != Gtk.ResponseType.OK or not name:
            return
        gains_tuple = tuple(scale.get_value() for _band, scale, _label in scales)
        eq_presets.save_custom_preset(name, gains_tuple)
        _populate_presets(select_name=name)

    save_preset_btn.connect("clicked", _on_save_preset)
    actions_row.pack_start(save_preset_btn, False, False, 0)

    save_device_btn = _icon_text_button(
        DEVICE_ICON, t("sonido", "guardar_dispositivo_btn"),
        t("sonido", "guardar_dispositivo_tooltip"))
    save_device_btn.get_style_context().add_class("eq-action-btn")

    def _on_save_device(_btn):
        hw_sink = eq_pw.current_output_target()
        if not hw_sink:
            return
        eq_actions.save_gains_for_device(hw_sink, eq_pw.get_band_gains())

    save_device_btn.connect("clicked", _on_save_device)
    actions_row.pack_start(save_device_btn, False, False, 0)

    container.pack_start(actions_row, False, False, 0)

    container.pack_start(_build_device_names_section(), False, False, 0)


def _build_device_names_section():
    section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    section.set_name("eq-devices-section")

    title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    title_row.set_name("eq-devices-title-row")

    title = Gtk.Label(label=t("sonido", "dispositivos_titulo"))
    title.get_style_context().add_class("eq-devices-title")
    title_row.pack_start(title, False, False, 0)

    title_divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    title_divider.get_style_context().add_class("eq-devices-title-divider")
    title_divider.set_valign(Gtk.Align.CENTER)
    title_row.pack_start(title_divider, True, True, 0)

    section.pack_start(title_row, False, False, 0)

    sinks = audio_devices.list_sinks()
    if not sinks:
        empty = Gtk.Label(label=t("sonido", "sin_salidas"))
        empty.get_style_context().add_class("eq-devices-empty")
        empty.set_halign(Gtk.Align.START)
        section.pack_start(empty, False, False, 0)
        return section

    for sink in sinks:
        section.pack_start(_make_device_name_row(sink), False, False, 0)

    return section


def _make_device_name_row(sink):
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.get_style_context().add_class("eq-device-row")

    icon = svg_icon_image(DEVICE_ICON, size=14, color_name="myforegroundhover")
    icon.set_valign(Gtk.Align.CENTER)
    row.pack_start(icon, False, False, 0)

    entry = Gtk.Entry()
    entry.set_text(audio_devices.display_name(sink["name"], sink["description"]))
    entry.set_tooltip_text(sink["description"])
    entry.set_hexpand(True)
    entry.set_valign(Gtk.Align.CENTER)
    entry.set_margin_top(10)  # mismo gotcha de Gtk.Entry que interval_entry en theme_module.py
    row.pack_start(entry, True, True, 0)

    apply_btn = Gtk.Button()
    apply_icon = Gtk.Image.new_from_icon_name("object-select-symbolic", Gtk.IconSize.BUTTON)
    apply_btn.set_image(apply_icon)
    apply_btn.set_always_show_image(True)
    apply_btn.set_tooltip_text(t("sonido", "guardar_nombre_tooltip"))
    apply_btn.get_style_context().add_class("apply-btn")
    apply_btn.set_valign(Gtk.Align.CENTER)

    def on_apply(*_args, sink_name=sink["name"]):
        audio_devices.set_name(sink_name, entry.get_text())

    apply_btn.connect("clicked", on_apply)
    entry.connect("activate", on_apply)
    row.pack_start(apply_btn, False, False, 0)

    return row

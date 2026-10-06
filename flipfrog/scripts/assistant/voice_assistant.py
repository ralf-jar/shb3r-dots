#!/usr/bin/env python3
"""Popup tipo Spotlight (SUPER+V), entry point del asistente de voz.
Ver CLAUDE.md "Asistente de voz" para el flujo completo.

Máquina de estados, un hilo daemon por paso bloqueante (grabar/
transcribir/pensar/hablar), cada uno termina con GLib.idle_add.
self._closed evita tocar widgets ya destruidos si se cierra a mitad
de un paso."""

import json
import os
import subprocess
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, Gdk, GLib, GtkLayerShell

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
import recorder
import stt
import brain
import tts
import assistant_config
import calendar_actions
from i18n import t
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top

CSS_FILE = os.path.join(SCRIPT_DIR, "voice_assistant.css")
LOCK = "/tmp/voice-assistant.pid"

STATUS_LISTENING = t("asistente", "status_listening")
STATUS_TRANSCRIBING = t("asistente", "status_transcribing")
STATUS_THINKING = t("asistente", "status_thinking")
STATUS_SETTINGS = t("asistente", "status_settings")
STATUS_CHECKING_CALENDAR = t("asistente", "status_checking_calendar")
STATUS_CONFIRM_CALENDAR = t("asistente", "status_confirm_calendar")
STATUS_SAVING_CALENDAR = t("asistente", "status_saving_calendar")
STATUS_SAVED_CALENDAR = t("asistente", "status_saved_calendar")
STATUS_QUERYING_CALENDAR = t("asistente", "status_querying_calendar")

CLOSE_DELAY_MS = 1800  # tiempo para leer el mensaje de error antes de cerrar solo

SETTINGS_FIELDS = (
    ("ciudad", t("asistente", "campo_ciudad")),
    ("sistema_operativo", t("asistente", "campo_sistema_operativo")),
    ("notas", t("asistente", "campo_notas")),
)

# Duplicado a propósito en vez de importar calendar/calendar_popup.py --
# mismo criterio que calendar_actions.py con CALDAV_URL/uid_for, ver su
# docstring: aquí solo hace falta un formato de fecha legible para la
# confirmación, no vale la pena arrastrar ese módulo entero por eso.
_WEEKDAY_NAMES_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTH_NAMES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _format_date_es(d):
    return f"{_WEEKDAY_NAMES_ES[d.weekday()]} {d.day} de {_MONTH_NAMES_ES[d.month - 1]} de {d.year}"


def _gaming_target_monitor():
    """Monitor donde debe aparecer el popup si "gaming" está en el OTRO
    monitor -- ver CLAUDE.md "Monitor destino con el workspace gaming
    activo". None si no aplica -- GtkLayerShell usa su default."""
    try:
        out = subprocess.run(
            ["hyprctl", "monitors", "-j"],
            capture_output=True, text=True, timeout=2, check=True,
        ).stdout
        monitors = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None

    if len(monitors) < 2:
        return None
    gaming_mon = next(
        (m for m in monitors if m.get("activeWorkspace", {}).get("name") == "gaming"), None
    )
    if gaming_mon is None:
        return None
    other_mons = [m for m in monitors if m is not gaming_mon]
    if len(other_mons) != 1:
        return None
    other = other_mons[0]

    display = Gdk.Display.get_default()
    return display.get_monitor_at_point(other["x"] + 1, other["y"] + 1)


class VoiceAssistant:
    def __init__(self):
        self.window, container = build_layer_window("voice-assistant", CSS_FILE)
        container.set_name("voice-assistant-container")
        position_fixed_top(container, margin=140)

        gaming_target = _gaming_target_monitor()
        if gaming_target is not None:
            GtkLayerShell.set_monitor(self.window, gaming_target)

        self._closed = False
        self._in_settings = False  # ver _open_settings
        self._pending_event = None  # ver _start_calendar_confirmation
        self._recorder_proc = None  # ver _listen/_on_destroy
        self._tts_proc = None  # ver _speak/_on_destroy

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_row.set_name("voice-assistant-header")
        container.pack_start(header_row, False, False, 0)

        self.status_label = Gtk.Label(label=STATUS_LISTENING)
        self.status_label.set_name("voice-assistant-status")
        self.status_label.set_halign(Gtk.Align.START)
        header_row.pack_start(self.status_label, True, True, 0)

        self.settings_btn = Gtk.Button()
        self.settings_btn.set_name("voice-assistant-settings-btn")
        settings_icon = Gtk.Image.new_from_icon_name(
            "preferences-system-symbolic", Gtk.IconSize.LARGE_TOOLBAR
        )
        self.settings_btn.set_image(settings_icon)
        self.settings_btn.set_tooltip_text(t("asistente", "tooltip_preferencias"))
        self.settings_btn.connect("clicked", lambda _b: self._open_settings())
        header_row.pack_start(self.settings_btn, False, False, 0)

        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        container.pack_start(self.main_box, True, True, 0)

        self.query_entry = Gtk.Entry()
        self.query_entry.set_name("voice-assistant-query")
        self.query_entry.set_editable(False)
        self.query_entry.set_can_focus(False)
        self.main_box.pack_start(self.query_entry, False, False, 0)

        self.answer_scroller = Gtk.ScrolledWindow()
        self.answer_scroller.set_name("voice-assistant-answer-scroller")
        self.answer_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.answer_scroller.set_max_content_height(360)
        self.answer_scroller.set_propagate_natural_height(True)
        self.answer_scroller.set_no_show_all(True)  # oculto hasta que hay respuesta

        self.answer_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        self.answer_view = Gtk.TextView()
        self.answer_view.set_name("voice-assistant-answer")
        self.answer_view.set_editable(False)
        self.answer_view.set_cursor_visible(False)
        self.answer_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.answer_buffer = self.answer_view.get_buffer()
        self.mono_tag = self.answer_buffer.create_tag("voice-assistant-code")
        self.mono_tag.set_property("family", "monospace")
        self.answer_content.pack_start(self.answer_view, False, False, 0)

        # Lugares recomendados (brain.extract_locations) y música
        # (brain.extract_music): listas de botones reales en vez de meter
        # la URL como texto plano en el TextView -- un link largo con
        # word-wrap se corta a la mitad y no es clickeable, ver
        # _show_answer/_make_link_row.
        self.locations_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.locations_box.set_name("voice-assistant-locations")
        self.locations_box.set_no_show_all(True)
        self.answer_content.pack_start(self.locations_box, False, False, 0)

        self.music_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.music_box.set_name("voice-assistant-music")
        self.music_box.set_no_show_all(True)
        self.answer_content.pack_start(self.music_box, False, False, 0)

        self.answer_scroller.add(self.answer_content)
        self.main_box.pack_start(self.answer_scroller, True, True, 0)

        self.settings_box = self._build_settings_box()
        self.settings_box.set_no_show_all(True)
        container.pack_start(self.settings_box, True, True, 0)

        self.confirm_box = self._build_confirm_box()
        self.confirm_box.set_no_show_all(True)
        container.pack_start(self.confirm_box, True, True, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()
        self.answer_scroller.hide()
        self.settings_box.hide()
        self.confirm_box.hide()

        threading.Thread(target=self._listen, daemon=True).start()

    # ---- Flujo: grabar -> transcribir -> pensar -> responder ------------

    def _listen(self):
        wav_path = recorder.record_until_silence(on_start=self._on_recorder_started)
        GLib.idle_add(self._on_recorded, wav_path)

    def _on_recorder_started(self, proc):
        # Llamado desde el hilo daemon de _listen -- solo guarda la
        # referencia, no toca widgets de GTK.
        self._recorder_proc = proc

    def _on_recorded(self, wav_path):
        if self._closed or self._in_settings:
            # La grabación pudo haber alcanzado a producir un WAV real
            # entre que se abrieron las preferencias y que el hilo de
            # _listen terminó de cortar solo -- no se usa, pero tampoco
            # se deja tirado en /tmp.
            if wav_path is not None:
                os.remove(wav_path)
            return False
        if wav_path is None:
            self._fail(t("asistente", "fail_sin_audio"))
            return False
        self._set_status(STATUS_TRANSCRIBING)
        threading.Thread(target=self._transcribe, args=(wav_path,), daemon=True).start()
        return False

    def _transcribe(self, wav_path):
        text = stt.transcribe(wav_path)
        os.remove(wav_path)
        GLib.idle_add(self._on_transcribed, text)

    def _on_transcribed(self, text):
        if self._closed or self._in_settings:
            return False
        if not text:
            self._fail(t("asistente", "fail_no_entendi"))
            return False
        self.query_entry.set_text(text)
        self._set_status(STATUS_THINKING)
        threading.Thread(target=self._think, args=(text,), daemon=True).start()
        return False

    def _think(self, question):
        answer = brain.ask(question)
        GLib.idle_add(self._on_answered, answer)

    def _on_answered(self, answer):
        if self._closed or self._in_settings:
            return False
        if not answer:
            self._fail(t("asistente", "fail_sin_respuesta"))
            return False

        if brain.is_calendar_action(answer):
            parsed = brain.parse_calendar_action(answer)
            if parsed is None:
                self._fail(t("asistente", "fail_fecha_evento"))
                return False
            self._start_calendar_confirmation(*parsed)
            return False

        if brain.is_calendar_query(answer):
            event_date = brain.parse_calendar_query(answer)
            if event_date is None:
                self._fail(t("asistente", "fail_fecha_consulta"))
                return False
            self._start_calendar_query(event_date)
            return False

        technical = brain.is_technical(answer)
        body = brain.strip_marker(answer) if technical else answer
        # Las secciones "Ubicaciones:"/"Música:" (links reales armados en
        # brain.py, ver _render_locations/_render_music) se separan del
        # cuerpo para mostrarse como listas de links de verdad, no texto
        # plano -- y nunca se leen en voz, leer una URL en voz alta no
        # sirve de nada.
        display_text, locations = brain.extract_locations(body)
        display_text, music = brain.extract_music(display_text)
        spoken_text = brain.MARKER if technical else brain.strip_music(brain.strip_locations(answer))

        self._set_status("")
        self._show_answer(display_text, technical, locations, music)
        threading.Thread(target=self._speak, args=(spoken_text,), daemon=True).start()
        return False

    def _speak(self, text):
        tts.speak(text, on_start=self._on_tts_started)

    def _on_tts_started(self, proc):
        # Llamado desde el hilo daemon de _speak -- solo guarda la
        # referencia, no toca widgets de GTK (mismo patrón que
        # _on_recorder_started).
        self._tts_proc = proc

    # ---- UI ---------------------------------------------------------

    def _set_status(self, text):
        self.status_label.set_text(text)

    def _fail(self, message):
        self._set_status(message)
        GLib.timeout_add(CLOSE_DELAY_MS, self._close)

    def _show_answer(self, text, technical, locations, music):
        self.answer_buffer.set_text("")
        if technical:
            self._insert_with_code_blocks(text)
        else:
            self.answer_buffer.insert(self.answer_buffer.get_end_iter(), text)

        self._fill_link_box(self.locations_box, locations, "mark-location-symbolic", self._open_url)
        self._fill_link_box(self.music_box, music, "audio-x-generic-symbolic", self._copy_link)

        self.answer_scroller.set_no_show_all(False)
        self.answer_scroller.show_all()
        if not locations:
            self.locations_box.hide()
        if not music:
            self.music_box.hide()

    def _fill_link_box(self, box, items, icon_name, on_click):
        for child in box.get_children():
            box.remove(child)
        for label, url in items:
            box.pack_start(self._make_link_row(label, url, icon_name, on_click), False, False, 0)
        box.set_no_show_all(not items)

    def _make_link_row(self, label, url, icon_name, on_click):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.get_style_context().add_class("voice-assistant-link-row")

        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.SMALL_TOOLBAR)
        row.pack_start(icon, False, False, 0)

        # Gtk.LinkButton NUNCA -- tira la conexión con el compositor
        # desde esta ventana layer-shell, ver CLAUDE.md "Gtk.LinkButton".
        btn = Gtk.Button(label=label)
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_halign(Gtk.Align.START)
        btn.get_style_context().add_class("voice-assistant-link-btn")
        btn.connect("clicked", lambda _b: on_click(url))
        row.pack_start(btn, True, True, 0)

        return row

    def _open_url(self, url):
        subprocess.Popen(
            ["xdg-open", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )

    def _copy_link(self, url):
        subprocess.run(["wl-copy"], input=url, text=True)
        self._flash_status(t("asistente", "enlace_copiado"))

    def _flash_status(self, message, duration_ms=1200):
        previous = self.status_label.get_text()
        self._set_status(message)
        GLib.timeout_add(duration_ms, lambda: self._set_status(previous) or False)

    def _insert_with_code_blocks(self, text):
        # Parser mínimo a mano (sin markdown completo ni resaltado de
        # sintaxis, ver plan): separa por las cercas ``` y alterna tag
        # monoespaciado / texto normal -- las cercas quedan en índices
        # impares de la lista resultante de split().
        end_iter = self.answer_buffer.get_end_iter
        for i, part in enumerate(text.split("```")):
            if i % 2 == 1:
                lines = part.split("\n", 1)
                code = lines[1] if len(lines) > 1 else part
                self.answer_buffer.insert_with_tags(
                    end_iter(), code.strip("\n") + "\n", self.mono_tag
                )
            else:
                self.answer_buffer.insert(end_iter(), part)

    # ---- Preferencias (engranaje) ------------------------------------

    def _build_settings_box(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_name("voice-assistant-settings")

        self.settings_entries = {}
        for field, label_text in SETTINGS_FIELDS:
            label = Gtk.Label(label=label_text)
            label.set_halign(Gtk.Align.START)
            label.get_style_context().add_class("voice-assistant-settings-label")
            box.pack_start(label, False, False, 0)

            entry = Gtk.Entry()
            entry.set_name("voice-assistant-settings-entry")
            box.pack_start(entry, False, False, 0)
            self.settings_entries[field] = entry

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_row.set_halign(Gtk.Align.END)
        box.pack_start(button_row, False, False, 4)

        cancel_btn = Gtk.Button(label=t("asistente", "boton_cancelar"))
        cancel_btn.get_style_context().add_class("voice-assistant-cancel-btn")
        cancel_btn.connect("clicked", lambda _b: self._cancel_settings())
        button_row.pack_start(cancel_btn, False, False, 0)

        save_btn = Gtk.Button(label=t("asistente", "boton_guardar"))
        save_btn.get_style_context().add_class("voice-assistant-settings-save")
        save_btn.connect("clicked", lambda _b: self._save_settings())
        button_row.pack_start(save_btn, False, False, 0)

        return box

    def _stop_active_audio(self):
        # Micrófono/parlante son los únicos recursos "en vivo" que
        # importa cortar de verdad (privacidad/UX) -- un `claude -p` o
        # `whisper-cli` que siga corriendo de fondo tras abrir
        # preferencias no tiene nada que cancelar, simplemente su
        # callback no hace nada gracias a `self._in_settings`.
        if self._recorder_proc is not None and self._recorder_proc.poll() is None:
            recorder._stop_cleanly(self._recorder_proc)
        if self._tts_proc is not None and self._tts_proc.poll() is None:
            tts.stop_playback(self._tts_proc)

    def _open_settings(self):
        if self._in_settings:
            return
        self._in_settings = True
        self._stop_active_audio()

        values = assistant_config.load()
        for field, entry in self.settings_entries.items():
            entry.set_text(values[field])

        self.status_label.set_text(STATUS_SETTINGS)
        self.settings_btn.set_sensitive(False)
        self.main_box.hide()
        self.settings_box.set_no_show_all(False)
        self.settings_box.show_all()
        self.settings_entries["ciudad"].grab_focus()

    def _save_settings(self):
        values = {field: entry.get_text() for field, entry in self.settings_entries.items()}
        assistant_config.save(values)
        self.window.destroy()

    def _cancel_settings(self):
        self.window.destroy()

    # ---- Agregar al calendario (con confirmación) --------------------

    def _build_confirm_box(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_name("voice-assistant-confirm")

        self.confirm_summary_label = Gtk.Label(label="")
        self.confirm_summary_label.set_line_wrap(True)
        self.confirm_summary_label.set_halign(Gtk.Align.START)
        self.confirm_summary_label.get_style_context().add_class("voice-assistant-confirm-summary")
        box.pack_start(self.confirm_summary_label, False, False, 0)

        self.confirm_existing_label = Gtk.Label(label="")
        self.confirm_existing_label.set_line_wrap(True)
        self.confirm_existing_label.set_halign(Gtk.Align.START)
        self.confirm_existing_label.get_style_context().add_class("voice-assistant-confirm-existing")
        self.confirm_existing_label.set_no_show_all(True)
        box.pack_start(self.confirm_existing_label, False, False, 0)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_row.set_halign(Gtk.Align.END)
        box.pack_start(button_row, False, False, 4)

        no_btn = Gtk.Button(label=t("asistente", "boton_no"))
        no_btn.get_style_context().add_class("voice-assistant-cancel-btn")
        no_btn.connect("clicked", lambda _b: self._cancel_calendar())
        button_row.pack_start(no_btn, False, False, 0)

        yes_btn = Gtk.Button(label=t("asistente", "boton_si_agregar"))
        yes_btn.get_style_context().add_class("voice-assistant-settings-save")
        yes_btn.connect("clicked", lambda _b: self._confirm_calendar())
        button_row.pack_start(yes_btn, False, False, 0)

        return box

    def _start_calendar_confirmation(self, event_date, event_text):
        self._pending_event = (event_date, event_text)
        self.settings_btn.set_sensitive(False)
        self._set_status(STATUS_CHECKING_CALENDAR)
        threading.Thread(target=self._fetch_existing_note, args=(event_date,), daemon=True).start()

    def _fetch_existing_note(self, event_date):
        existing = calendar_actions.get_existing_note(event_date)
        GLib.idle_add(self._show_calendar_confirmation, existing)

    def _show_calendar_confirmation(self, existing_text):
        if self._closed or self._in_settings:
            return False
        event_date, event_text = self._pending_event

        self._set_status(STATUS_CONFIRM_CALENDAR)
        self.confirm_summary_label.set_text(
            t("asistente", "resumen_confirmacion", evento=event_text, fecha=_format_date_es(event_date))
        )
        if existing_text:
            self.confirm_existing_label.set_text(
                t("asistente", "nota_existente", nota=existing_text)
            )
            self.confirm_existing_label.set_no_show_all(False)
        else:
            self.confirm_existing_label.hide()

        self.main_box.hide()
        self.confirm_box.set_no_show_all(False)
        self.confirm_box.show_all()
        if not existing_text:
            self.confirm_existing_label.hide()

        spoken = f"¿Agrego \"{event_text}\" el {_format_date_es(event_date)}? Confirma en pantalla."
        threading.Thread(target=self._speak, args=(spoken,), daemon=True).start()
        return False

    def _cancel_calendar(self):
        self.window.destroy()

    def _confirm_calendar(self):
        event_date, event_text = self._pending_event
        self._set_status(STATUS_SAVING_CALENDAR)
        self.confirm_box.hide()
        threading.Thread(
            target=self._save_calendar_event, args=(event_date, event_text), daemon=True
        ).start()

    def _save_calendar_event(self, event_date, event_text):
        result = calendar_actions.save_event(event_date, event_text)
        GLib.idle_add(self._on_calendar_saved, result, event_date, event_text)

    def _on_calendar_saved(self, result, event_date, event_text):
        if self._closed:
            return False
        if result is None:
            self._fail(t("asistente", "fail_guardar_calendario"))
            return False
        self._set_status(STATUS_SAVED_CALENDAR)
        spoken = f'Listo, agregué "{event_text}" el {_format_date_es(event_date)}.'
        threading.Thread(target=self._speak, args=(spoken,), daemon=True).start()
        GLib.timeout_add(CLOSE_DELAY_MS, self._close)
        return False

    # ---- Consultar el calendario (solo lectura, sin confirmación) ----

    def _start_calendar_query(self, event_date):
        self._set_status(STATUS_QUERYING_CALENDAR)
        threading.Thread(target=self._fetch_query_note, args=(event_date,), daemon=True).start()

    def _fetch_query_note(self, event_date):
        existing = calendar_actions.get_existing_note(event_date)
        GLib.idle_add(self._show_calendar_query_result, event_date, existing)

    def _show_calendar_query_result(self, event_date, existing_text):
        if self._closed or self._in_settings:
            return False
        date_str = _format_date_es(event_date)
        if existing_text:
            text = f'El {date_str} tienes: "{existing_text}".'
        else:
            text = f"No tienes nada pendiente el {date_str}."

        self._set_status("")
        self._show_answer(text, False, [], [])
        threading.Thread(target=self._speak, args=(text,), daemon=True).start()
        return False

    def _close(self):
        if not self._closed:
            self.window.destroy()
        return False

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        self._closed = True
        # Ver CLAUDE.md "Interrumpir grabación/audio al cerrar".
        self._stop_active_audio()
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    VoiceAssistant()
    Gtk.main()


if __name__ == "__main__":
    main()

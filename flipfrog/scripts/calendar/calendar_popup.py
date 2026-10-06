#!/usr/bin/env python3
"""Calendario propio para Waybar (grid de días a mano, sin
Gtk.Calendar). Se abre con click en el módulo "clock". Nombre NO puede
ser "calendar.py" (colisión con el módulo estándar, ver CLAUDE.md
sección "Calendario" -- también documenta CalDAV/Radicale)."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import os
import sys
import threading
from datetime import date, datetime, timedelta

import caldav

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_near_cursor
from i18n import t

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CSS_FILE = os.path.join(SCRIPT_DIR, "calendar_popup.css")
LOCK = "/tmp/calendar.pid"

CALDAV_URL = "http://127.0.0.1:5232/"
CALDAV_USER = "waybar"
CALDAV_PASSWORD = "waybar"
CALENDAR_NAME = "Notas"

WEEKDAY_LABELS = [
    t("calendario", "wd_dom"), t("calendario", "wd_lun"), t("calendario", "wd_mar"),
    t("calendario", "wd_mie"), t("calendario", "wd_jue"), t("calendario", "wd_vie"),
    t("calendario", "wd_sab"),
]
MONTH_NAMES = [
    t("calendario", "mes_01"), t("calendario", "mes_02"), t("calendario", "mes_03"),
    t("calendario", "mes_04"), t("calendario", "mes_05"), t("calendario", "mes_06"),
    t("calendario", "mes_07"), t("calendario", "mes_08"), t("calendario", "mes_09"),
    t("calendario", "mes_10"), t("calendario", "mes_11"), t("calendario", "mes_12"),
]


def format_display_date(d):
    """"Vie 17 jul 2026" -- mismo formato que clock.py."""
    weekday = WEEKDAY_LABELS[(d.weekday() + 1) % 7]
    weekday = weekday[0].upper() + weekday[1:]
    month = MONTH_NAMES[d.month - 1][:3]
    return f"{weekday} {d.day} {month} {d.year}"


def uid_for(d):
    return f"note-{d.strftime('%Y%m%d')}@waybar-calendar"


def connect_calendar():
    client = caldav.DAVClient(url=CALDAV_URL, username=CALDAV_USER, password=CALDAV_PASSWORD)
    principal = client.principal()
    for cal in principal.calendars():
        if cal.name == CALENDAR_NAME:
            return cal
    return principal.make_calendar(name=CALENDAR_NAME)


def fetch_notes(cal):
    """Trae todos los eventos de la coleccion y arma (fecha_iso -> texto,
    fecha_iso -> Event de caldav) -- dataset chico, se carga entero de una
    misma que el JSON viejo."""
    notes = {}
    note_events = {}
    for ev in cal.events():
        comp = ev.icalendar_component
        dtstart = comp.get("dtstart").dt
        d = dtstart.date() if isinstance(dtstart, datetime) else dtstart
        key = d.isoformat()
        notes[key] = str(comp.get("summary"))
        note_events[key] = ev
    return notes, note_events


def days_in_month(year, month):
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return (nxt - date(year, month, 1)).days


class CalendarPopup:
    def __init__(self):
        self.window, container = build_layer_window("calendar", CSS_FILE)
        container.set_name("container")
        position_near_cursor(container)

        self._destroyed = False
        self.cal = None
        self.notes = {}
        self.note_events = {}

        today = date.today()
        self.view_year, self.view_month = today.year, today.month
        self.selected = today

        header = Gtk.Label(label=t("calendario", "header"))
        header.set_name("header")
        header.set_halign(Gtk.Align.CENTER)
        container.pack_start(header, False, False, 0)

        self.date_label = Gtk.Label(label="")
        self.date_label.set_name("date-label")
        self.date_label.set_halign(Gtk.Align.CENTER)
        container.pack_start(self.date_label, False, False, 0)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        nav.set_name("nav")
        prev_btn = Gtk.Button(label="‹")
        prev_btn.get_style_context().add_class("nav-btn")
        prev_btn.connect("clicked", lambda *_: self._change_month(-1))
        self.month_label = Gtk.Label()
        self.month_label.set_name("month-label")
        self.month_label.set_hexpand(True)
        self.month_label.set_halign(Gtk.Align.CENTER)
        next_btn = Gtk.Button(label="›")
        next_btn.get_style_context().add_class("nav-btn")
        next_btn.connect("clicked", lambda *_: self._change_month(1))
        nav.pack_start(prev_btn, False, False, 0)
        nav.pack_start(self.month_label, True, True, 0)
        nav.pack_start(next_btn, False, False, 0)
        container.pack_start(nav, False, False, 0)

        weekday_row = Gtk.Grid()
        weekday_row.set_column_homogeneous(True)
        for col, name in enumerate(WEEKDAY_LABELS):
            lbl = Gtk.Label(label=name)
            lbl.get_style_context().add_class("weekday")
            weekday_row.attach(lbl, col, 0, 1, 1)
        container.pack_start(weekday_row, False, False, 0)

        self.grid = Gtk.Grid()
        self.grid.set_row_homogeneous(True)
        self.grid.set_column_homogeneous(True)
        self.day_buttons = []
        self.day_dates = [None] * 42
        for i in range(42):
            btn = Gtk.Button(label="")
            btn.get_style_context().add_class("day")
            btn.connect("clicked", self._on_day_clicked, i)
            row, col = divmod(i, 7)
            self.grid.attach(btn, col, row, 1, 1)
            self.day_buttons.append(btn)
        container.pack_start(self.grid, False, False, 0)

        self.note_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.note_row.set_name("note-row")
        self.entry = None
        self._build_note_row_loading()
        container.pack_start(self.note_row, False, False, 0)

        self._rebuild_grid()
        self._sync_entry_for_selected_day()

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

        threading.Thread(target=self._load_calendar_async, daemon=True).start()

    def _clear_note_row(self):
        for child in self.note_row.get_children():
            self.note_row.remove(child)

    def _build_note_row_loading(self):
        self._clear_note_row()
        self.entry = None
        label = Gtk.Label(label=t("calendario", "cargando"))
        label.set_name("error-label")
        label.set_hexpand(True)
        self.note_row.pack_start(label, True, True, 0)
        self.note_row.show_all()

    def _build_note_row_error(self):
        self._clear_note_row()
        self.entry = None
        error_label = Gtk.Label(label=t("calendario", "error_conexion"))
        error_label.set_name("error-label")
        error_label.set_hexpand(True)
        self.note_row.pack_start(error_label, True, True, 0)
        self.note_row.show_all()

    def _build_note_row_ready(self):
        self._clear_note_row()
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text(t("calendario", "descripcion_placeholder"))
        self.entry.set_hexpand(True)
        self.entry.set_has_frame(False)
        self.entry.set_size_request(-1, 20)
        self.entry.connect("activate", self._on_save_note)
        self.note_row.pack_start(self.entry, True, True, 0)

        save_btn = Gtk.Button(label="+")
        save_btn.get_style_context().add_class("save-btn")
        save_btn.set_valign(Gtk.Align.CENTER)
        save_btn.set_size_request(36, 20)
        save_btn.connect("clicked", self._on_save_note)
        self.note_row.pack_start(save_btn, False, False, 0)

        self.note_row.show_all()
        self._sync_entry_for_selected_day()

    def _load_calendar_async(self):
        try:
            cal = connect_calendar()
            notes, note_events = fetch_notes(cal)
        except Exception:
            GLib.idle_add(self._on_calendar_load_error)
            return
        GLib.idle_add(self._on_calendar_load_success, cal, notes, note_events)

    def _on_calendar_load_success(self, cal, notes, note_events):
        if self._destroyed:
            return False
        self.cal = cal
        self.notes = notes
        self.note_events = note_events
        self._build_note_row_ready()
        self._rebuild_grid()
        return False

    def _on_calendar_load_error(self):
        if self._destroyed:
            return False
        self._build_note_row_error()
        return False

    def _change_month(self, delta):
        m = self.view_month + delta
        y = self.view_year
        if m > 12:
            m, y = 1, y + 1
        elif m < 1:
            m, y = 12, y - 1
        self.view_month, self.view_year = m, y
        self._rebuild_grid()

    def _rebuild_grid(self):
        first_dow = (date(self.view_year, self.view_month, 1).weekday() + 1) % 7
        ndays = days_in_month(self.view_year, self.view_month)

        if self.view_month == 1:
            prev_y, prev_m = self.view_year - 1, 12
        else:
            prev_y, prev_m = self.view_year, self.view_month - 1
        prev_ndays = days_in_month(prev_y, prev_m)

        if self.view_month == 12:
            next_y, next_m = self.view_year + 1, 1
        else:
            next_y, next_m = self.view_year, self.view_month + 1

        cells = []
        for i in range(first_dow):
            d = prev_ndays - first_dow + 1 + i
            cells.append(date(prev_y, prev_m, d))
        for d in range(1, ndays + 1):
            cells.append(date(self.view_year, self.view_month, d))
        i = 1
        while len(cells) < 42:
            cells.append(date(next_y, next_m, i))
            i += 1

        today = date.today()
        for idx, btn in enumerate(self.day_buttons):
            d = cells[idx]
            self.day_dates[idx] = d
            btn.set_label(str(d.day))

            ctx = btn.get_style_context()
            for cls in ("other-month", "has-note", "selected", "today"):
                ctx.remove_class(cls)

            if d.month != self.view_month:
                ctx.add_class("other-month")
            if d.isoformat() in self.notes:
                ctx.add_class("has-note")
            if d == self.selected:
                ctx.add_class("selected")
            if d == today:
                ctx.add_class("today")

        self.month_label.set_text(f"{MONTH_NAMES[self.view_month - 1]} {self.view_year}")

    def _on_day_clicked(self, _btn, idx):
        d = self.day_dates[idx]
        self.selected = d
        if (d.year, d.month) != (self.view_year, self.view_month):
            self.view_year, self.view_month = d.year, d.month
        self._rebuild_grid()
        self._sync_entry_for_selected_day()

    def _sync_entry_for_selected_day(self):
        key = self.selected.isoformat()
        self.date_label.set_text(format_display_date(self.selected))
        if self.entry is not None:
            self.entry.set_text(self.notes.get(key, ""))

    def _on_save_note(self, _widget):
        key = self.selected.isoformat()
        text = self.entry.get_text().strip()
        existing = self.note_events.get(key)
        if text:
            if existing is not None:
                existing.icalendar_component["summary"] = text
                existing.save()
            else:
                self.note_events[key] = self.cal.save_event(
                    dtstart=self.selected,
                    dtend=self.selected + timedelta(days=1),
                    summary=text,
                    uid=uid_for(self.selected),
                )
            self.notes[key] = text
        else:
            if existing is not None:
                existing.delete()
                self.note_events.pop(key, None)
            self.notes.pop(key, None)
        self._rebuild_grid()

    def _on_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        self._destroyed = True
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    CalendarPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

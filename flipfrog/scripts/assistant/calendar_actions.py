"""Escribe en el calendario CalDAV (Radicale local, mismo que
calendar/calendar_popup.py) cuando el usuario pide agregar algo por
voz. Constantes/UID duplicadas de calendar_popup.py a propósito -- ver
CLAUDE.md sección "Calendario"."""

from datetime import timedelta

import caldav

CALDAV_URL = "http://127.0.0.1:5232/"
CALDAV_USER = "waybar"
CALDAV_PASSWORD = "waybar"
CALENDAR_NAME = "Notas"


def uid_for(d):
    return f"note-{d.strftime('%Y%m%d')}@waybar-calendar"


def _connect():
    client = caldav.DAVClient(url=CALDAV_URL, username=CALDAV_USER, password=CALDAV_PASSWORD)
    principal = client.principal()
    for cal in principal.calendars():
        if cal.name == CALENDAR_NAME:
            return cal
    return principal.make_calendar(name=CALENDAR_NAME)


def _find_event(cal, uid):
    for ev in cal.events():
        if str(ev.icalendar_component.get("uid")) == uid:
            return ev
    return None


def get_existing_note(event_date):
    """Texto ya guardado en esa fecha, o None si no hay o si falló la
    conexión. Nunca excepciona."""
    try:
        cal = _connect()
        ev = _find_event(cal, uid_for(event_date))
        return str(ev.icalendar_component.get("summary")) if ev is not None else None
    except Exception:
        return None


def save_event(event_date, event_text):
    """Combina con lo que ya haya guardado esa fecha (relee fresco) en
    vez de sobreescribir. Texto final guardado, o None si falló. Nunca
    excepciona."""
    try:
        cal = _connect()
        uid = uid_for(event_date)
        existing_event = _find_event(cal, uid)

        if existing_event is not None:
            existing_text = str(existing_event.icalendar_component.get("summary"))
            final_text = f"{existing_text}; {event_text}" if existing_text else event_text
            existing_event.icalendar_component["summary"] = final_text
            existing_event.save()
        else:
            final_text = event_text
            cal.save_event(
                dtstart=event_date,
                dtend=event_date + timedelta(days=1),
                summary=final_text,
                uid=uid,
            )
    except Exception:
        return None
    return final_text

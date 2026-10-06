#!/usr/bin/env python3
import datetime
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
from i18n import t as _t

CLOCK_FORMAT_FILE = os.path.expanduser("~/.config/waybar/clock-format.json")
CLOCK_GRADIENT_FILE = os.path.expanduser("~/.config/waybar/clock-gradient.json")

WEEKDAY_LABELS = [
    _t("reloj", "wd_dom"), _t("reloj", "wd_lun"), _t("reloj", "wd_mar"),
    _t("reloj", "wd_mie"), _t("reloj", "wd_jue"), _t("reloj", "wd_vie"),
    _t("reloj", "wd_sab"),
]
MONTH_NAMES = [
    _t("reloj", "mes_01"), _t("reloj", "mes_02"), _t("reloj", "mes_03"),
    _t("reloj", "mes_04"), _t("reloj", "mes_05"), _t("reloj", "mes_06"),
    _t("reloj", "mes_07"), _t("reloj", "mes_08"), _t("reloj", "mes_09"),
    _t("reloj", "mes_10"), _t("reloj", "mes_11"), _t("reloj", "mes_12"),
]


def lerp(a, b, t):
    return a + (b - a) * t


def format_display_date(d):
    weekday = WEEKDAY_LABELS[(d.weekday() + 1) % 7]
    weekday = weekday[0].upper() + weekday[1:]
    month = MONTH_NAMES[d.month - 1][:3]
    return f"{weekday} {d.day} {month} {d.year}"


def _ampm(dt):
    """A mano, no strftime("%p"): en un proceso GTK (la barra propia) el
    locale ya es es_MX y %p sale vacío."""
    return "AM" if dt.hour < 12 else "PM"


def format_extended_text(dt):
    """"Lun 27 de jul 3:32 PM" -- fecha+hora en un solo texto, toggle
    en toggles/clock_format_toggle.py."""
    weekday = WEEKDAY_LABELS[(dt.weekday() + 1) % 7]
    weekday = weekday[0].upper() + weekday[1:]
    month = MONTH_NAMES[dt.month - 1][:3]
    hour12 = dt.hour % 12 or 12
    ampm = _ampm(dt)
    return f"{weekday} {dt.day} de {month} {hour12}:{dt.minute:02d} {ampm}"


def is_extended_clock():
    try:
        with open(CLOCK_FORMAT_FILE) as f:
            return bool(json.load(f).get("extended", False))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def is_gradient_clock():
    try:
        with open(CLOCK_GRADIENT_FILE) as f:
            return bool(json.load(f).get("enabled", False))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def gradient_markup(text):
    css = open(common.COLORS_CSS).read()
    r1, g1, b1, a1 = common.theme_color_rgba("myforegroundhover", css)
    r2, g2, b2, a2 = common.theme_color_rgba("myforegroundhover2", css)

    n = len(text)
    out = []
    for i, ch in enumerate(text):
        if ch == " ":
            out.append(" ")
            continue
        t = i / (n - 1) if n > 1 else 0
        r = round(lerp(r1, r2, t))
        g = round(lerp(g1, g2, t))
        b = round(lerp(b1, b2, t))
        pct = round(lerp(a1, a2, t) * 100)
        out.append(f"<span foreground='#{r:02x}{g:02x}{b:02x}' alpha='{pct}%'>{ch}</span>")
    return "".join(out)


def status():
    now = datetime.datetime.now()

    bar_text = format_extended_text(now) if is_extended_clock() else f"{now.hour % 12 or 12:02d}:{now.minute:02d} {_ampm(now)}"

    return {
        "text": gradient_markup(bar_text) if is_gradient_clock() else bar_text,
        "tooltip": format_display_date(now.date()),
    }


def main():
    print(json.dumps(status()))


if __name__ == "__main__":
    main()

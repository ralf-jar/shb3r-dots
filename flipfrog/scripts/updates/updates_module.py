#!/usr/bin/env python3
"""Módulo "Actualizaciones" embebido en el dashboard -- badge simple,
solo lee el cache de updates-checker.py, nunca corre checkupdates/paru.
Click copia "paru -Syu" al portapapeles vía wl-copy (no Gtk.Clipboard,
no sobrevive el cierre del proceso bajo Wayland). Ver CLAUDE.md
"Notificador de actualizaciones"."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
import json
import os
import subprocess
import sys

UPDATE_COMMAND = "paru -Syu"
COPIED_FEEDBACK_MS = 1500

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from waybar_lib import load_module_css
from i18n import t

MODULE_CSS = os.path.join(SCRIPT_DIR, "updates_module.css")
WAYBAR_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "waybar"))
CACHE_FILE = os.path.join(WAYBAR_DIR, "updates-cache.json")


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"count": None}


def build_updates_badge(container):
    load_module_css(MODULE_CSS)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.get_style_context().add_class("module")

    title = Gtk.Label(label=t("actualizaciones", "titulo"))
    title.get_style_context().add_class("module-title")
    title.set_halign(Gtk.Align.START)
    box.pack_start(title, False, False, 0)

    cache = load_cache()
    count = cache.get("count")
    aur = cache.get("aur")
    up_to_date = count == 0

    if count is None:
        text = t("actualizaciones", "sin_datos")
    elif up_to_date:
        text = t("actualizaciones", "todo_actualizado")
    else:
        text = (t("actualizaciones", "una_actualizacion") if count == 1
                else t("actualizaciones", "n_actualizaciones", count=count))

    label = Gtk.Label(label=text)

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row.get_style_context().add_class("updates-badge")
    if not up_to_date:
        row.get_style_context().add_class("alert")
    row.pack_start(label, False, False, 0)

    if aur:
        aur_label = Gtk.Label(label=t("actualizaciones", "aur_extra", aur=aur))
        aur_label.get_style_context().add_class("updates-badge-aur")
        row.pack_start(aur_label, False, False, 0)

    cachy_icon = Gtk.Image.new_from_icon_name("cachyos", Gtk.IconSize.SMALL_TOOLBAR)
    row.pack_start(cachy_icon, False, False, 0)

    status_icon_name = (
        "software-update-available-symbolic" if count else "emblem-ok-symbolic"
    )
    status_icon = Gtk.Image.new_from_icon_name(status_icon_name, Gtk.IconSize.SMALL_TOOLBAR)
    row.pack_start(status_icon, False, False, 0)

    btn = Gtk.Button()
    btn.get_style_context().add_class("updates-badge-btn")
    btn.set_tooltip_text(t("actualizaciones", "tooltip_copiar", cmd=UPDATE_COMMAND))
    btn.add(row)

    def on_click(_btn):
        subprocess.run(["wl-copy"], input=UPDATE_COMMAND, text=True)
        label.set_text(t("actualizaciones", "comando_copiado"))
        GLib.timeout_add(COPIED_FEEDBACK_MS, lambda: label.set_text(text) or False)

    btn.connect("clicked", on_click)

    box.pack_start(btn, False, False, 0)
    container.pack_start(box, True, True, 0)

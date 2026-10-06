#!/usr/bin/env python3
"""Popup "Ver registro" de firewall_popup.py. Ver CLAUDE.md "Logger de
firewall"."""

import json
import os
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top
from i18n import t

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CSS_FILE = os.path.join(SCRIPT_DIR, "firewall_log_viewer.css")
LOCK = "/tmp/firewall-log-viewer.pid"

WAYBAR_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "waybar"))
LOG_FILE = os.path.join(WAYBAR_DIR, "firewall-log.jsonl")

KIND_LABELS = {
    "BLOCK": t("red", "kind_block"),
    "ALLOW": t("red", "kind_allow"),
    "LIMIT": t("red", "kind_limit"),
}


def load_entries():
    entries = []
    try:
        with open(LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    entries.reverse()  # más reciente arriba
    return entries


def format_entry(entry):
    time_part = entry.get("time", "")[11:19]  # HH:MM:SS del ISO-8601
    proto = entry.get("proto", "?")
    dpt = entry.get("dpt", "")
    port_part = f":{dpt}" if dpt else ""
    return (
        f"{time_part}  {entry.get('src', '?')} → {entry.get('dst', '?')}{port_part}/{proto}"
        f"  ({entry.get('iface', '?')})"
    )


class LogViewer:
    def __init__(self):
        self.window, container = build_layer_window("firewall-log", CSS_FILE)
        container.set_name("firewall-log-container")
        position_fixed_top(container, margin=60)

        title = Gtk.Label(label=t("red", "log_title"))
        title.set_name("firewall-log-title")
        title.set_halign(Gtk.Align.START)
        container.pack_start(title, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("firewall-log-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # Alto fijo (min Y max) -- ver CLAUDE.md "Logger de firewall".
        scroller.set_min_content_height(450)
        scroller.set_max_content_height(450)

        listbox = Gtk.ListBox()
        listbox.set_name("firewall-log-list")
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        entries = load_entries()
        if not entries:
            empty = Gtk.Label(label=t("red", "log_empty"))
            empty.get_style_context().add_class("firewall-log-empty")
            container.pack_start(empty, True, True, 0)
        else:
            for entry in entries:
                listbox.add(self._make_row(entry))
            scroller.add(listbox)
            container.pack_start(scroller, True, True, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

    def _make_row(self, entry):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("firewall-log-row")

        kind = entry.get("kind", "?")
        tag = Gtk.Label(label=KIND_LABELS.get(kind, kind))
        tag.get_style_context().add_class(f"firewall-log-tag-{kind.lower()}")
        row.pack_start(tag, False, False, 0)

        label = Gtk.Label(label=format_entry(entry))
        label.set_halign(Gtk.Align.START)
        row.pack_start(label, True, True, 0)

        wrap = Gtk.ListBoxRow()
        wrap.set_selectable(False)
        wrap.add(row)
        return wrap

    def _on_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    LogViewer()
    Gtk.main()


if __name__ == "__main__":
    main()

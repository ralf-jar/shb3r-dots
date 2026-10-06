#!/usr/bin/env python3
"""Popup "Atajos de Teclado" (SUPER+H) -- lista los binds de
hypr/config/keybinds.lua leyendo el formato `-- section:`/
`-- description:`, ver CLAUDE.md "Atajos de teclado".

`--welcome` (autostart): solo abre si welcome.json lo pide, con un switch
para dejar de mostrarlo al iniciar sesión."""

import json
import os
import re
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from waybar_lib import (kill_group, kill_existing, build_layer_window, position_fixed_top,
                        build_switch_row, svg_icon_image, load_module_css)
from common import atomic_write
from i18n import t

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CSS_FILE = os.path.join(SCRIPT_DIR, "keybinds_popup.css")
LOCK = "/tmp/keybinds-popup.pid"
WELCOME_FILE = os.path.join(SCRIPT_DIR, "welcome.json")
TOGGLE_CSS = os.path.join(SCRIPT_DIR, "..", "toggles", "toggle_common.css")
WELCOME_ICON = os.path.join(SCRIPT_DIR, "..", "toggles", "icons", "menu.svg")
SCROLLER_HEIGHT = 760

KEYBINDS_LUA = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "..", "..", "hypr", "config", "keybinds.lua"))

KEY_DISPLAY = {
    "left": "←", "right": "→", "up": "↑", "down": "↓",
    "SPACE": "Espacio", "plus": "+",
    "mouse_down": "Rueda ↓", "mouse_up": "Rueda ↑",
}
MOUSE_BUTTONS = {"272": "Click izq.", "273": "Click der.", "274": "Click medio"}

SECTION_RE = re.compile(r'--\s*section:\s*(.+?)\s*$')
DESCRIPTION_RE = re.compile(r'--\s*description:\s*(.+?)\s*$')
BIND_RE = re.compile(r'hl\.bind\(mainMod \.\. " \+ ([^"]+)"\s*,\s*(.*)\)\s*$')


def _format_key(raw):
    """Una entrada de lista por tecla -- se renderiza como una cápsula
    aparte por tecla (`[SUPER] [SHIFT] [C]`), no un solo combo de texto."""
    chips = ["SUPER"]
    for part in (p.strip() for p in raw.split(" + ")):
        if part.startswith("mouse:"):
            code = part.split(":", 1)[1]
            chips.append(MOUSE_BUTTONS.get(code, f"Mouse {code}"))
        else:
            chips.append(KEY_DISPLAY.get(part, part))
    return chips


def _humanize(action):
    """Red de seguridad si falta `-- description:`."""
    if "window.fullscreen" in action:
        return "Pantalla completa"
    if "window.close" in action:
        return "Cerrar ventana"
    if "window.drag" in action:
        return "Mover ventana (arrastrar)"
    if "focus(" in action and "direction" in action:
        return "Mover foco"

    m = re.search(r'workspace\s*=\s*"([^"]+)"', action)
    if m:
        verb = "Mover ventana a" if "window.move" in action else "Ir a"
        return f'{verb} workspace "{m.group(1)}"'

    m = re.search(r'workspace\s*=\s*(\d+)', action)
    if m:
        verb = "Mover ventana al" if "window.move" in action else "Ir al"
        return f"{verb} workspace {m.group(1)}"

    m = re.search(r'exec_cmd\(([^)]*)\)', action)
    if m:
        return "Ejecutar: " + m.group(1).strip().strip("\"'")

    return action.strip()


def _truncate(text, limit=76):
    """`-- description:` debería ser corto, pero por si alguien escribe
    de más -- se recorta a algo hojeable en la lista."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _loop_workspace_chips(stripped):
    """Las dos líneas dentro de `for i = 1, 9 do ... end` usan una
    variable de loop en la tecla (`mainMod .. " + " .. i`), que `BIND_RE`
    no puede resolver -- se reconocen por texto en vez de parsear la
    tecla real."""
    if "hl.bind(" not in stripped or ".. i" not in stripped:
        return None
    return ["SUPER", "SHIFT", "1..9"] if "SHIFT" in stripped else ["SUPER", "1..9"]


def load_keybinds():
    """Cada entrada: [chips, description, section, action]. `description`
    queda en `None` hasta que se resuelve -- por el tag `-- description:`
    que sigue al bind, o al final por `_humanize(action)` de respaldo."""
    entries = []
    current_section = t("atajos", "seccion_otros")
    awaiting = None  # última entry agregada, esperando su "-- description:"
    in_workspace_loop = False

    try:
        with open(KEYBINDS_LUA) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    for line in lines:
        stripped = line.strip()

        if awaiting is not None:
            m_desc = DESCRIPTION_RE.match(stripped)
            if m_desc:
                awaiting[1] = _truncate(m_desc.group(1))
                awaiting = None
                continue
            awaiting = None  # cualquier otra cosa corta la espera

        m_section = SECTION_RE.match(stripped)
        if m_section:
            current_section = m_section.group(1)
            continue

        if stripped.startswith("for i = 1, 9 do"):
            in_workspace_loop = True
            continue
        if in_workspace_loop:
            if stripped == "end":
                in_workspace_loop = False
                continue
            chips = _loop_workspace_chips(stripped)
            if chips is not None:
                entry = [chips, None, current_section, ""]
                entries.append(entry)
                awaiting = entry
            continue

        m_bind = BIND_RE.search(stripped)
        if m_bind:
            entry = [_format_key(m_bind.group(1).strip()), None,
                      current_section, m_bind.group(2).strip()]
            entries.append(entry)
            awaiting = entry
            continue

    for entry in entries:
        if entry[1] is None:
            entry[1] = _truncate(_humanize(entry[3]))

    return entries


def grouped_keybinds():
    """Agrupa por sección, en el orden en que aparecen en keybinds.lua
    (`-- section: ...`) -- no hay lista de categorías fija, sale directo
    del archivo."""
    groups = {}
    for chips, description, section, _action in load_keybinds():
        groups.setdefault(section, []).append((chips, description))
    return list(groups.items())


def show_on_login():
    try:
        with open(WELCOME_FILE) as f:
            return bool(json.load(f).get("show_on_login"))
    except (FileNotFoundError, ValueError):
        return False


def set_show_on_login(value):
    atomic_write(WELCOME_FILE, json.dumps({"show_on_login": value}) + "\n")


def _scroller_height():
    """760 px no cabe en una laptop de 768 de alto."""
    display = Gdk.Display.get_default()
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    if monitor is None:
        return SCROLLER_HEIGHT
    return max(300, min(SCROLLER_HEIGHT, monitor.get_geometry().height - 260))


class KeybindsPopup:
    def __init__(self, welcome=False):
        self.window, container = build_layer_window("keybinds-popup", CSS_FILE)
        container.set_name("keybinds-container")
        position_fixed_top(container, margin=60)

        groups = grouped_keybinds()

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_halign(Gtk.Align.CENTER)
        title = Gtk.Label(label=t("atajos", "titulo"))
        title.set_name("keybinds-title")
        header.pack_start(title, False, False, 0)
        count = Gtk.Label(label=str(sum(len(rows) for _, rows in groups)))
        count.set_name("keybinds-count")
        count.set_valign(Gtk.Align.CENTER)
        header.pack_start(count, False, False, 0)
        container.pack_start(header, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("keybinds-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        height = _scroller_height()
        scroller.set_min_content_height(height)
        scroller.set_max_content_height(height)

        if not groups:
            empty = Gtk.Label(label=t("atajos", "error_leer_keybinds"))
            empty.get_style_context().add_class("keybinds-empty")
            container.pack_start(empty, True, True, 0)
        else:
            # Una columna por categoría -- pack_start(column, True, True, 0)
            # en un Box horizontal reparte el ancho parejo y estira cada
            # columna al alto de la más alta.
            columns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            for section, rows in groups:
                column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                column.get_style_context().add_class("keybinds-column")

                section_title = Gtk.Label(label=section.upper())
                section_title.set_halign(Gtk.Align.CENTER)
                section_title.get_style_context().add_class("keybinds-section-title")
                column.pack_start(section_title, False, False, 0)

                listbox = Gtk.ListBox()
                listbox.set_name("keybinds-list")
                listbox.set_selection_mode(Gtk.SelectionMode.NONE)
                for key, description in rows:
                    listbox.add(self._make_row(key, description))
                column.pack_start(listbox, False, False, 0)

                columns_row.pack_start(column, True, True, 0)

            scroller.add(columns_row)
            container.pack_start(scroller, True, True, 0)

        if welcome:
            container.pack_start(self._build_welcome_footer(), False, False, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

    def _build_welcome_footer(self):
        load_module_css(TOGGLE_CSS)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        footer.set_name("keybinds-welcome")

        hint = Gtk.Label(label=t("atajos", "bienvenida_hint"))
        hint.set_halign(Gtk.Align.START)
        hint.get_style_context().add_class("keybinds-welcome-hint")
        footer.pack_start(hint, True, True, 0)

        row = Gtk.Box()
        build_switch_row(row, svg_icon_image(WELCOME_ICON), t("atajos", "mostrar_al_iniciar"),
                         True, set_show_on_login)
        footer.pack_end(row, False, False, 0)
        return footer

    def _make_row(self, chips, description):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row.get_style_context().add_class("keybinds-row")

        keycap_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        keycap_row.set_halign(Gtk.Align.CENTER)
        for chip in chips:
            keycap = Gtk.Label(label=chip)
            keycap.get_style_context().add_class("keybinds-keycap")
            keycap_row.pack_start(keycap, False, False, 0)
        row.pack_start(keycap_row, False, False, 0)

        desc_label = Gtk.Label(label=description)
        desc_label.set_halign(Gtk.Align.CENTER)
        desc_label.set_justify(Gtk.Justification.CENTER)
        desc_label.set_xalign(0.5)
        desc_label.set_line_wrap(True)
        desc_label.set_max_width_chars(32)
        desc_label.get_style_context().add_class("keybinds-desc")
        row.pack_start(desc_label, False, False, 0)

        wrap = Gtk.ListBoxRow()
        wrap.set_selectable(False)
        wrap.get_style_context().add_class("keybinds-listrow")
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
    welcome = "--welcome" in sys.argv[1:]
    if welcome and not show_on_login():
        return
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    KeybindsPopup(welcome=welcome)
    Gtk.main()


if __name__ == "__main__":
    main()

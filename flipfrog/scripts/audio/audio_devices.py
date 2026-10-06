"""Alias de sinks -- pactl no permite renombrar persistente, así que es
un mapeo propio (NAMES_FILE) que nunca toca el sistema. Compartido por
sink-selector.py y sound_module.py para no duplicar el parseo de
`pactl list sinks`."""

import json
import os
import re
import subprocess
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
NAMES_FILE = os.path.join(SCRIPT_DIR, "sink-names.json")


def list_sinks():
    """[{"name": ..., "description": ...}] -- excluye el sink virtual
    del ecualizador (effect_input.eq6/effect_output.eq6), igual que
    antes en sink-selector.py."""
    r = subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True)
    sinks = []
    current = {}
    for line in r.stdout.splitlines():
        m = re.match(r"^Sink #(\d+)", line)
        if m:
            if current:
                sinks.append(current)
            current = {"name": "", "description": ""}
            continue
        m_name = re.match(r"^\s+Name: (.*)", line)
        if m_name and current:
            current["name"] = m_name.group(1)
        m_desc = re.match(r"^\s+Description: (.*)", line)
        if m_desc and current:
            current["description"] = m_desc.group(1)
    if current:
        sinks.append(current)
    return [s for s in sinks if not s["name"].startswith("effect_")]


def load_names():
    try:
        with open(NAMES_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _save_names(names):
    fd, tmp = tempfile.mkstemp(dir=SCRIPT_DIR)
    with os.fdopen(fd, "w") as f:
        json.dump(names, f)
    os.replace(tmp, NAMES_FILE)


def set_name(sink_name, label):
    """label vacío (o solo espacios) borra el alias -- vuelve a mostrar
    la Description real de pactl."""
    names = load_names()
    label = label.strip()
    if label:
        names[sink_name] = label
    else:
        names.pop(sink_name, None)
    _save_names(names)


def display_name(sink_name, fallback):
    return load_names().get(sink_name) or fallback

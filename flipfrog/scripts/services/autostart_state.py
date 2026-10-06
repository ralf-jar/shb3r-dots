"""Parsea hypr/config/autostart.lua directo -- ver CLAUDE.md
sección "Autostart" para el formato de tags."""

import os
import re

AUTOSTART_LUA = os.path.expanduser("~/.config/hypr/config/autostart.lua")

NAME_TAG_RE = re.compile(r'^\s*--\s*name:\s*(.+?)\s*$')
EXEC_RE = re.compile(r'^(\s*)(-- )?(hl\.exec_cmd\(.*\))\s*$')


def list_entries():
    try:
        with open(AUTOSTART_LUA) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []

    entries = []
    pending_name = None
    for line in lines:
        name_match = NAME_TAG_RE.match(line)
        if name_match:
            pending_name = name_match.group(1)
            continue

        exec_match = EXEC_RE.match(line)
        if exec_match:
            if pending_name:
                _indent, disabled_prefix, command = exec_match.groups()
                entries.append({
                    "name": pending_name,
                    "command": command,
                    "enabled": disabled_prefix is None,
                })
            pending_name = None
            continue

        # El tag tiene que estar pegado justo arriba de SU exec_cmd.
        if line.strip() and not line.strip().startswith("--"):
            pending_name = None

    return entries

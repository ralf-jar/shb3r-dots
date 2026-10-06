"""Prende/apaga una entrada de autostart.lua comentando/descomentando
su `hl.exec_cmd(...)`, atómico. Sin pkexec (archivo propio del
usuario). Aplica recién en el PRÓXIMO login -- ver CLAUDE.md
"Autostart"."""

import os
import tempfile

from autostart_state import AUTOSTART_LUA, EXEC_RE


def set_enabled(command, enabled):
    try:
        with open(AUTOSTART_LUA) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return False, "No se encontró autostart.lua"

    changed = False
    for i, line in enumerate(lines):
        match = EXEC_RE.match(line)
        if not match:
            continue
        indent, _disabled_prefix, cmd = match.groups()
        if cmd != command:
            continue
        lines[i] = f"{indent}{cmd}\n" if enabled else f"{indent}-- {cmd}\n"
        changed = True
        break

    if not changed:
        return False, "No se encontró esa entrada en autostart.lua"

    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(AUTOSTART_LUA))
    with os.fdopen(fd, "w") as f:
        f.writelines(lines)
    os.replace(tmp, AUTOSTART_LUA)
    return True, ""

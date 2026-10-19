"""Extras opcionales para la página "Extras" del launcher, sin GTK. Se
leen de `EXTRAS_LIST`/`EXTRA_DEFAULTS` del propio install.sh (una sola
fuente con el menú de `ff-extras`), e instalar corre `install.sh
--extras` con `FLIPFROG_EXTRAS=<ids>` en Kitty: sin el menú, con la barra
de progreso y la contraseña de sudo a la vista."""

import os
import re
import shutil
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
INSTALL_SH = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "install.sh"))
TERMINAL_CLASS = "flipfrog-extras"
TIMEOUT = 5

ROW_RE = re.compile(r'^\s*"([^"|]+)\|([^"|]*)\|([^"|]*)\|([^"|]*)\|([^"|]*)"\s*$')
DEFAULTS_RE = re.compile(r'^EXTRA_DEFAULTS=\(([^)]*)\)', re.M)


def _installed(cmd):
    if cmd.startswith("pkg:"):
        try:
            return subprocess.run(["pacman", "-Q", cmd[4:]], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=TIMEOUT).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return shutil.which(cmd) is not None


def load():
    """[{"id", "section", "name", "desc", "installed", "default"}] en el
    orden de install.sh. Lista vacía si no se encuentra install.sh."""
    try:
        with open(INSTALL_SH, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    m = DEFAULTS_RE.search(text)
    defaults = set(m.group(1).split()) if m else set()

    extras = []
    inside = False
    for line in text.splitlines():
        if line.startswith("EXTRAS_LIST=("):
            inside = True
        elif inside and line.strip() == ")":
            break
        elif inside:
            row = ROW_RE.match(line)
            if row:
                extra_id, section, name, desc, cmd = row.groups()
                # El aviso de DaVinci solo existe en el menú de la terminal.
                desc = desc.replace(", ver aviso", "")
                extras.append({"id": extra_id, "section": section, "name": name, "desc": desc,
                               "installed": _installed(cmd), "default": extra_id in defaults})
    return extras


def install(ids):
    """Abre Kitty con la instalación; `--hold` deja la ventana abierta al
    terminar para leer el resultado."""
    env = dict(os.environ, FLIPFROG_EXTRAS=",".join(ids))
    subprocess.Popen(["kitty", "--class", TERMINAL_CLASS, "--hold", "bash", INSTALL_SH, "--extras"],
                     env=env, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

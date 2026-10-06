"""Modificaciones al firewall, siempre vía pkexec. Ver CLAUDE.md
"Administrador de UFW"."""

import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from i18n import t

TIMEOUT = 30


def _run(args):
    """Corre `pkexec ufw ...`, devuelve (ok, mensaje). Nunca excepciona:
    pkexec ausente, timeout (usuario tarda/cancela el diálogo) o
    cualquier otra falla se reportan como error legible en vez de tirar
    una excepción que rompería el hilo de fondo que llama a esto."""
    try:
        result = subprocess.run(
            ["pkexec", "ufw"] + args,
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except FileNotFoundError:
        return False, t("red", "pkexec_missing")
    except subprocess.TimeoutExpired:
        return False, t("red", "ufw_timeout")

    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        return False, msg or t("red", "ufw_bad_returncode", code=result.returncode)
    return True, (result.stdout or "").strip()


def set_enabled(enabled):
    return _run(["--force", "enable"] if enabled else ["disable"])


def add_rule(action, proto, port, source, direction):
    """action: allow/deny/reject/limit. proto: tcp/udp/any. port: puerto
    o rango (ej. "5232" o "5232:5300"), puede venir vacío para una regla
    que solo restringe por origen. source: IP/CIDR o "any". direction:
    in/out."""
    args = [action]
    if direction == "out":
        args.append("out")
    if source and source != "any":
        args += ["from", source]
    else:
        args += ["from", "any"]
    if port:
        args += ["to", "any", "port", port]
    else:
        args += ["to", "any"]
    if proto and proto != "any":
        args += ["proto", proto]
    return _run(args)


def delete_rule(index):
    return _run(["--force", "delete", str(index)])

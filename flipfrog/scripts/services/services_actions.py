"""Todo lo que MODIFICA un servicio de SISTEMA -- nunca excepciona,
siempre devuelve (ok, mensaje). Llama `systemctl` DIRECTO, sin pkexec
-- ver CLAUDE.md sección servicios systemd (allow_active=auth_admin_keep
vs. la acción genérica de pkexec)."""

import subprocess

TIMEOUT = 30


def _run(action_args):
    cmd = ["systemctl"] + action_args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        return False, "systemctl no está instalado"
    except subprocess.TimeoutExpired:
        return False, "Se agotó el tiempo de espera (¿quedó el diálogo de contraseña sin responder?)"

    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        return False, msg or f"systemctl devolvió código {result.returncode}"
    return True, (result.stdout or "").strip()


def start(name):
    return _run(["start", name])


def stop(name):
    return _run(["stop", name])


def set_enabled(name, enabled):
    return _run(["enable" if enabled else "disable", name])

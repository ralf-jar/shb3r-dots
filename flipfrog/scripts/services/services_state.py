"""Lectura sin privilegios de unidades systemd de SISTEMA -- ver
CLAUDE.md "Configurador de servicios systemd y Autostart"."""

import subprocess

TIMEOUT = 10

# static/generated/alias/indirect/masked no tienen un enable/disable
# real (confirmado con man systemd.unit).
NON_TOGGLEABLE_STATES = {"static", "generated", "alias", "indirect", "masked", "masked-runtime", "transient"}


def _run(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT)
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _list_unit_files():
    cmd = ["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager", "--plain"]
    files = {}
    for line in _run(cmd).splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        files[name] = state
    return files


def _list_units():
    cmd = ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"]
    units = {}
    for line in _run(cmd).splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        name, _load, active, sub = parts[0], parts[1], parts[2], parts[3]
        desc = parts[4] if len(parts) > 4 else ""
        units[name] = {"active": active, "sub": sub, "description": desc}
    return units


def list_services():
    """Todas las unidades .service de sistema, ordenadas por nombre --
    el filtro de qué mostrar por default es responsabilidad de quien
    llama (services_popup.py)."""
    files = _list_unit_files()
    units = _list_units()

    services = []
    for name, enabled_state in files.items():
        info = units.get(name, {})
        services.append({
            "name": name,
            "enabled_state": enabled_state,
            "enabled": enabled_state.startswith("enabled"),
            "can_toggle_enable": enabled_state not in NON_TOGGLEABLE_STATES,
            "active": info.get("active") == "active",
            "sub": info.get("sub", "dead"),
            "description": info.get("description", ""),
        })

    services.sort(key=lambda s: s["name"].lower())
    return services

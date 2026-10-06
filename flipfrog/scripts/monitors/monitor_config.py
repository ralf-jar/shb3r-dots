"""monitor_config.py
Helpers sin GTK del configurador de monitores (monitor_module.py, tab
"Monitores" del dashboard): estado en vivo vía `hyprctl monitors -j`,
serialización a `hypr/monitor-config.lua` (leído por
`hypr/config/monitors.lua` en cada `hyprctl reload`) y el cálculo de
tamaño lógico (post-scale/rotación) para el canvas de posición.

No hay parser Lua en Python -- el dashboard nunca necesita leer de
vuelta lo último guardado ahí, siempre parte del estado REAL vía
hyprctl (igual que el resto del dashboard parte de estado real: brillo,
volumen, bluetooth, etc.), así que monitor-config.lua es de escritura
única desde acá."""

import json
import os
import subprocess
import sys

MODULE_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(MODULE_DIR, ".."))
import common

HYPR_DIR = os.path.normpath(os.path.join(MODULE_DIR, "..", "..", "..", "hypr"))
CONFIG_LUA = os.path.join(HYPR_DIR, "monitor-config.lua")

# Touch (pantallas táctiles, ver "Ancla la configuración touch al de los
# monitores"): dos archivos por el mismo motivo que layout-mode.json --
# TOUCH_CONFIG_LUA es lo que consume monitors.lua/Hyprland (fuente real
# de aplicación), TOUCH_STATE_JSON es un espejo liviano para que este
# módulo sepa qué mostrar preseleccionado sin parsear Lua (nunca hay
# forma de leer en vivo el mapeo touch->output actual: `hyprctl devices
# -j` solo da name/address del touch, sin su output asignado).
TOUCH_CONFIG_LUA = os.path.join(HYPR_DIR, "touch-config.lua")
TOUCH_STATE_JSON = os.path.join(HYPR_DIR, "touch-config.json")

# 0/1/2/3 -- ver monitors.lua: se pasan tal cual a hl.monitor()/al
# transform real de Hyprland (wlr_output_transform). El mapeo
# horizontal/vertical es una convención propia de esta UI, no hay una
# única forma "correcta" de nombrar los 4 casos sin flip.
TRANSFORM_LABELS = {
    0: "Horizontal",
    1: "Vertical",
    2: "Horizontal invertido",
    3: "Vertical invertido",
}

MONITOR_FIELDS = ("output", "mode", "position", "scale", "transform", "disabled", "mirror_of")


def live_monitors():
    """Estado real de TODOS los monitores (activos y deshabilitados,
    `-a`) -- si un monitor apagado desapareciera de la lista, la UI no
    podría volver a encenderlo. [] si hyprctl no responde."""
    try:
        out = subprocess.run(
            ["hyprctl", "monitors", "-j", "-a"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout
        return json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError):
        return []


def live_touch_devices():
    """Nombres de touchscreens conectados (`hyprctl devices -j`, campo
    "touch") -- [] si no hay ninguno o hyprctl no responde, mismo
    criterio fail-open que live_monitors()."""
    try:
        out = subprocess.run(
            ["hyprctl", "devices", "-j"],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout
        return [d["name"] for d in json.loads(out).get("touch", [])]
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError, KeyError):
        return []


def load_touch_assignments():
    """{device: output} guardado la última vez -- {} sin estado previo
    o archivo corrupto (el touch queda sin forzar, comportamiento
    default de Hyprland)."""
    try:
        with open(TOUCH_STATE_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _serialize_touch(assignments):
    lines = [
        "-- Generado por monitor_module.py (dashboard, tab \"Monitores\") --",
        "-- NO editar a mano, se pisa completo en cada \"Guardar y aplicar\".",
        "return {",
    ]
    for device, output in assignments.items():
        if output:
            lines.append(f"    {{ device = {_lua_value(device)}, output = {_lua_value(output)} }},")
    lines.append("}")
    return "\n".join(lines) + "\n"


def save_touch(assignments):
    common.atomic_write(TOUCH_CONFIG_LUA, _serialize_touch(assignments))
    common.atomic_write(TOUCH_STATE_JSON, json.dumps(assignments))


def _hz_to_int(hz_text):
    """"140.00Hz" / "60.00" -> 140 / 60 -- monitors.lua ya usa enteros
    sin unidad ("2560x1080@140"), replicado acá para no mezclar dos
    formatos de `mode` distintos en el mismo archivo."""
    return round(float(hz_text.rstrip("Hz")))


def mode_keyword(resolution_hz):
    """"2160x1440@60.00Hz" (formato de availableModes) ->
    "2160x1440@60" (formato de mode= real de Hyprland/monitors.lua)."""
    res, hz = resolution_hz.split("@")
    return f"{res}@{_hz_to_int(hz)}"


def current_mode_keyword(mon):
    return f"{mon['width']}x{mon['height']}@{round(mon['refreshRate'])}"


def available_mode_keywords(mon):
    """Modos únicos (orden estable) en formato "WxH@R", con el modo
    ACTUAL siempre presente aunque no matchee ningún string de
    availableModes al pixel/Hz exacto (pasa con modos custom -- ver
    CLAUDE.md, "DP-3 usa un modo de refresh custom no listado")."""
    seen = []
    for m in mon.get("availableModes", []):
        kw = mode_keyword(m)
        if kw not in seen:
            seen.append(kw)
    current = current_mode_keyword(mon)
    if current not in seen:
        seen.insert(0, current)
    return seen


def mode_wh(mode):
    """"2560x1080@144" -> (2560, 1080), sin el refresh."""
    res, _hz = mode.split("@")
    w, h = res.split("x")
    return int(w), int(h)


def logical_size(mode, scale, transform):
    """Ancho/alto lógico (post-scale, post-rotación) -- lo que ocupa
    realmente en el espacio de posiciones x/y de Hyprland (ver
    monitors.lua, comentario de `position`: confirmado contra la config
    real ya existente, HDMI-A-2 2160px físicos a scale 1.5 = 1440
    lógicos = donde arrancaba DP-3). transform 1/3 (Vertical/Vertical
    invertido) rota 90°, así que ancho y alto se intercambian --
    Hyprland reporta "width"/"height" siempre sin rotar, el swap para
    el layout lógico lo hace el compositor aparte."""
    w, h = mode_wh(mode)
    lw, lh = w / scale, h / scale
    if transform in (1, 3):
        lw, lh = lh, lw
    return lw, lh


def _lua_value(value):
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def serialize(monitors):
    """`monitors`: lista de dicts (uno por output, en el orden en que
    se le van a pasar a Hyprland -- el PRIMERO es el "monitor
    principal" elegido desde la UI, ver monitors.lua). Genera un
    archivo .lua (no JSON): este repo no tiene confirmado que el
    entorno de Lua de Hyprland traiga una librería de JSON, `dofile()`
    en cambio es Lua estándar, sin esa duda."""
    lines = [
        "-- Generado por monitor_module.py (dashboard, tab \"Monitores\") --",
        "-- NO editar a mano, se pisa completo en cada \"Guardar y aplicar\".",
        "return {",
    ]
    for m in monitors:
        fields = ", ".join(f"{k} = {_lua_value(m.get(k))}" for k in MONITOR_FIELDS)
        lines.append(f"    {{ {fields} }},")
    lines.append("}")
    return "\n".join(lines) + "\n"


def save_and_apply(monitors, touch_assignments=None):
    common.atomic_write(CONFIG_LUA, serialize(monitors))
    if touch_assignments is not None:
        save_touch(touch_assignments)
    subprocess.Popen(["hyprctl", "reload"], start_new_session=True,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

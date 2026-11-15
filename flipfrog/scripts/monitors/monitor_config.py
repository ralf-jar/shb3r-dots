"""monitor_config.py
Helpers sin GTK del configurador de monitores (monitor_module.py, tab
"Monitores" del dashboard): estado en vivo vía
`hyprctl monitors -j all` (el `all` es obligatorio para que un monitor
apagado siga apareciendo), serialización a `hypr/monitor-config.lua` (leído por
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

# Espejo en JSON de lo último guardado, mismo patrón que touch-config.json
# y por el MISMO motivo: Hyprland NO retiene el estado de un monitor
# apagado. `hyprctl monitors -j all` devuelve al monitor deshabilitado con
# width/height 0, x/y -1 y scale 1 (confirmado en vivo 2026-11-15), así
# que sin este espejo el dashboard no tiene con qué reponer resolución,
# escala ni posición al volver a encenderlo -- y peor, heredaba el modo
# "0x0@60" y lo volvía a guardar, dejando el monitor apagado para siempre
# (bug real 2026-11-15). Estado personal, gitignored como
# monitor-config.lua.
SAVED_STATE_JSON = os.path.join(HYPR_DIR, "monitor-config.json")

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
    """Estado real de TODOS los monitores (activos y deshabilitados) --
    si un monitor apagado desapareciera de la lista, la UI no podría
    volver a encenderlo. `all` es un ARGUMENTO posicional, no un flag:
    `-a` no existe como alias, Hyprland lo ignora en silencio y omite
    del listado al monitor apagado (bug real 2026-11-13: al deshabilitar
    uno y reabrir el dashboard ya no aparecía, y no había forma de
    reactivarlo sin editar el .lua a mano). [] si hyprctl no responde."""
    try:
        out = subprocess.run(
            ["hyprctl", "monitors", "-j", "all"],
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


def load_saved_state():
    """{output: {mode, position, scale, transform, disabled, mirror_of}}
    de la última vez que se guardó desde la UI -- {} sin estado previo o
    archivo corrupto (mismo criterio fail-open que
    load_touch_assignments()). Solo se usa para reponer lo que Hyprland
    no reporta de un monitor APAGADO (ver SAVED_STATE_JSON)."""
    try:
        with open(SAVED_STATE_JSON, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def save_state(monitors):
    """Espejo JSON de `monitors` (las mismas entradas que van a
    monitor-config.lua). El .lua sigue siendo la única fuente que lee
    Hyprland; esto es solo para que el dashboard pueda reponer
    resolución/escala/posición de un monitor apagado, que `hyprctl` ya
    no reporta."""
    state = {
        m["output"]: {k: m.get(k) for k in MONITOR_FIELDS}
        for m in monitors
    }
    common.atomic_write(SAVED_STATE_JSON, json.dumps(state, indent=4))


def live_position(mon):
    """(x, y) reales del monitor, o None si no tiene posición -- un
    monitor apagado viene con x/y = -1/-1 en `hyprctl monitors -j all`
    (NO con su última posición). Ojo: coordenadas negativas SÍ son
    válidas (un monitor a la izquierda del principal), por eso el
    sentinel se detecta como ese -1/-1 exacto y no como "cualquier
    negativo"."""
    x, y = mon.get("x", -1), mon.get("y", -1)
    if x == -1 and y == -1:
        return None
    return x, y


def saved_mode(saved):
    """mode del espejo JSON, solo si es un "WxH@R" aplicable (no
    "preferred" ni "0x0@R"); None si no sirve y hay que caer al modo
    nativo de availableModes."""
    mode = (saved or {}).get("mode") or ""
    try:
        w, h = mode_wh(mode)
    except (ValueError, AttributeError):
        return None
    return mode if w and h else None


def saved_position(saved):
    """"XxY" del espejo JSON -> (x, y); None si no existe o es el -1/-1
    del sentinel de monitor apagado."""
    try:
        x, y = (saved or {}).get("position", "").split("x")
        x, y = int(x), int(y)
    except (ValueError, AttributeError):
        return None
    if x == -1 and y == -1:
        return None
    return x, y


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
    """Modo "WxH@R" del monitor. Un monitor APAGADO viene de hyprctl con
    width/height 0 (no con los retenidos, ver SAVED_STATE_JSON), así que
    ahí se usa el primer modo de `availableModes` -- Hyprland los ordena
    de mayor a menor, o sea el nativo/preferido. Sin este fallback el
    modo del monitor apagado es "0x0@60" y el dashboard lo volvía a
    guardar tal cual al encenderlo (Hyprland no puede aplicar 0x0 y el
    monitor seguía apagado, bug real 2026-11-15)."""
    if mon.get("width") and mon.get("height"):
        return f"{mon['width']}x{mon['height']}@{round(mon['refreshRate'])}"
    for available in mon.get("availableModes") or []:
        try:
            return mode_keyword(available)
        except ValueError:
            continue
    return "1920x1080@60"


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
    # Mínimo 1: un monitor apagado reporta 0x0 y un rect de área cero no
    # solo es invisible, tampoco se puede clicar (el hit-test del canvas
    # pide mx/my == el borde exacto) -- bug real 2026-11-15, el HDMI
    # apagado no se podía volver a seleccionar para encenderlo.
    lw, lh = max(1.0, w / scale), max(1.0, h / scale)
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


def _normalized(spec):
    """Un monitor que se va a ENCENDER no puede salir con un modo que
    Hyprland no puede aplicar: "preferred" (el keyword de modo
    automático) es el reemplazo seguro si el modo quedó en 0x0 -- bug
    real 2026-11-15: se guardó `mode = "0x0@60", disabled = false` y el
    monitor nunca volvió a prender, ni siquiera desde la UI (el estado
    vivo seguía reportando disabled = true y el modo 0x0 se arrastraba a
    cada guardado)."""
    if spec.get("disabled") or spec.get("mirror_of"):
        return spec
    try:
        w, h = mode_wh(spec.get("mode") or "")
    except (ValueError, AttributeError):
        w, h = 0, 0
    if w and h:
        return spec
    return dict(spec, mode="preferred")


def save_and_apply(monitors, touch_assignments=None):
    monitors = [_normalized(m) for m in monitors]
    common.atomic_write(CONFIG_LUA, serialize(monitors))
    save_state(monitors)
    if touch_assignments is not None:
        save_touch(touch_assignments)
    subprocess.Popen(["hyprctl", "reload"], start_new_session=True,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

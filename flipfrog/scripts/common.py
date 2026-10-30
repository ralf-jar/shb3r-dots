"""Helpers livianos sin GTK, compartidos por daemons y módulos del
dashboard por igual -- a diferencia de waybar_lib.py (que importa el
stack GTK/GtkLayerShell completo a nivel de módulo), este archivo no
carga nada pesado, así que un daemon que corre cada pocos segundos
(clock.py, sysmon.py) no paga ese costo solo por reusar un helper de
10 líneas."""

import ctypes
import os
import queue
import re
import threading
import traceback

COLORS_CSS = os.path.expanduser("~/.config/flipfrog/themer/colors.css")

# rgba() y rgb() sin alpha (temas viejos); grupos acotados en [^,)]
# para que el último no sea goloso y cruce al siguiente @define-color.
_COLOR_RE = r"@define-color {} rgba?\(([^,)]+),([^,)]+),([^,)]+)(?:,([^,)]+))?\)"


def theme_color_rgba(name, css=None):
    """(r, g, b, a) floats del @define-color `name` en colors.css --
    a=1.0 si la declaración no trae canal alpha. `css` opcional evita
    releer el archivo si el llamador ya lo tiene en memoria (clock.py,
    dos lookups por tick)."""
    if css is None:
        with open(COLORS_CSS) as f:
            css = f.read()
    m = re.search(_COLOR_RE.format(re.escape(name)), css)
    r, g, b = (float(x) for x in m.group(1, 2, 3))
    a = float(m.group(4)) if m.group(4) is not None else 1.0
    return r, g, b, a


def theme_color_hex(name):
    """Color con nombre de colors.css en hex -- blanco si el tema está
    corrupto o ausente."""
    try:
        r, g, b, _ = theme_color_rgba(name)
        return f"#{int(r):02x}{int(g):02x}{int(b):02x}"
    except (FileNotFoundError, AttributeError, ValueError):
        return "#ffffff"


def set_process_name(name):
    """prctl(PR_SET_NAME) -- ver CLAUDE.md "Nombre de proceso real".
    Límite duro del kernel: 16 bytes incluyendo el \\0 final, 15
    caracteres útiles."""
    try:
        buf = ctypes.create_string_buffer(name.encode()[:15])
        ctypes.CDLL(None).prctl(15, buf, 0, 0, 0)
    except OSError:
        pass


_WORK_QUEUE = queue.Queue()
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def _worker_loop():
    while True:
        fn = _WORK_QUEUE.get()
        try:
            fn()
        except Exception:
            traceback.print_exc()
        finally:
            _WORK_QUEUE.task_done()


def run_async(fn):
    """Encola `fn` en un solo hilo worker compartido (FIFO) -- los
    toggles del dashboard (toggles/*.py) lo usan para que activar un
    switch no bloquee el hilo principal de GTK (state-set corre en ese
    hilo) con I/O + subprocess (hyprctl reload, etc.), y para que dos
    toggles disparados en rápida sucesión que tocan el MISMO archivo
    nunca corran su read-modify-write en paralelo -- eso perdería la
    escritura de uno de los dos. Un solo
    worker global (no uno por archivo) porque el volumen es bajísimo
    (clicks de usuario) y mantiene el orden real de los clicks incluso
    entre archivos distintos. No pensado para trabajo que reporta
    resultado a la UI -- `fn` corre fuera del hilo de GTK, un callback a
    un widget necesita su propio GLib.idle_add (ver vpn_toggle.py)."""
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if not _WORKER_STARTED:
            threading.Thread(target=_worker_loop, daemon=True).start()
            _WORKER_STARTED = True
    _WORK_QUEUE.put(fn)


def workspace_selector(ws):
    """Selector de workspace para `hl.dsp.focus`/`hl.dsp.window.move`.

    Hyprland 0.57 dejó de poner `id` en los workspaces con nombre
    ("gaming", "🎬", "🎨", "🪟"): el JSON solo trae `name`. Los
    numerados sí traen `id` (y `name` = str(id)). Leer `ws["id"]` directo
    truena con KeyError en cuanto hay un workspace con nombre activo."""
    ws = ws or {}
    ws_id = ws.get("id")
    name = ws.get("name") or ""
    if isinstance(ws_id, int) and ws_id > 0:
        return str(ws_id)
    if name:
        return f'"name:{name}"'
    return "1"


def workspace_sort_key(ws):
    """Orden de workspaces: numerados primero por id, después los de
    nombre alfabético (mismo criterio que el visor de la barra)."""
    ws = ws or {}
    ws_id = ws.get("id")
    if isinstance(ws_id, int) and ws_id > 0:
        return (0, ws_id, "")
    return (1, 0, ws.get("name") or "")


def atomic_write(path, content, binary=False):
    """Escribe `path` de forma atómica (archivo temporal + os.replace())
    -- nunca open(path, "w") directo, que trunca el archivo antes de
    escribir: un lector concurrente (otra instancia del script,
    reload.sh) puede leerlo vacío a mitad de camino (pasó una vez con
    colors.css, quedó en 1 byte). Nombre de temporal fijo (`path +
    ".tmp"`) -- para el caso de dos escritores concurrentes sobre el
    MISMO archivo, usar tempfile.mkstemp(dir=...) en su lugar (ver
    theme-editor.py/sync_radius.py/eq_actions.py, entre otros)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb" if binary else "w") as f:
        f.write(content)
    os.replace(tmp, path)

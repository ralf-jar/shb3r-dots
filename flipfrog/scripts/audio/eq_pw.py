"""Helpers de bajo nivel para el ecualizador de PipeWire -- wrappers
finos sobre `pw-dump`/`pw-cli`. Ver CLAUDE.md "Pestaña Sonido"."""

import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from i18n import t

TIMEOUT = 5

EQ_INPUT_NAME = "effect_input.eq6"
EQ_OUTPUT_NAME = "effect_output.eq6"
BAND_COUNT = 6
MIN_GAIN, MAX_GAIN = -12.0, 12.0

# Banda 1/6 son shelf (grave/agudo), 2-5 son peaking -- ver sink-eq6.conf.
BAND_LABELS = {
    1: t("sonido", "banda_graves"),
    2: t("sonido", "banda_100hz"),
    3: t("sonido", "banda_500hz"),
    4: t("sonido", "banda_2khz"),
    5: t("sonido", "banda_5khz"),
    6: t("sonido", "banda_agudos"),
}


def dump():
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=TIMEOUT)
        return json.loads(out.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return []


def find_node_id(node_name):
    for obj in dump():
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        if obj.get("info", {}).get("props", {}).get("node.name") == node_name:
            return obj["id"]
    return None


def eq_sink_loaded():
    return find_node_id(EQ_INPUT_NAME) is not None


def current_output_target():
    """node.name del sink de hardware al que está conectado
    "effect_output.eq6" ahora mismo, o None si no hay link todavía."""
    output_id = find_node_id(EQ_OUTPUT_NAME)
    if output_id is None:
        return None

    dumped = dump()
    node_names = {
        obj["id"]: obj.get("info", {}).get("props", {}).get("node.name")
        for obj in dumped
        if obj.get("type") == "PipeWire:Interface:Node"
    }
    for obj in dumped:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        props = obj.get("info", {}).get("props", {})
        if props.get("link.output.node") == output_id:
            return node_names.get(props.get("link.input.node"))
    return None


def resolve_default_sink_name():
    """Nombre de sink real para mostrar/controlar volumen -- ver
    CLAUDE.md "eq_pw.resolve_default_sink_name()"."""
    try:
        out = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=TIMEOUT)
        default = out.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if not default:
        return None
    if default == EQ_INPUT_NAME:
        return current_output_target() or default
    return default


def get_band_gains():
    """{1: 0.0, 2: 0.0, ...} -- 0.0 para cualquier banda si el sink no
    está cargado (filter-chain.service no corriendo)."""
    gains = {n: 0.0 for n in range(1, BAND_COUNT + 1)}
    node_id = find_node_id(EQ_INPUT_NAME)
    if node_id is None:
        return gains

    for obj in dump():
        if obj.get("id") != node_id:
            continue
        for props in obj.get("info", {}).get("params", {}).get("Props", []):
            flat = props.get("params")
            if not flat:
                continue
            for i in range(0, len(flat) - 1, 2):
                key = flat[i]
                if isinstance(key, str) and key.startswith("eq_band_") and key.endswith(":Gain"):
                    band = int(key.split("_")[2].split(":")[0])
                    gains[band] = float(flat[i + 1])
        break
    return gains


def set_band_gain(band, gain_db):
    """Escribe el gain EN VIVO -- no persiste nada a disco (ver
    eq_actions.py para eso). Clampeado acá también (no solo en la UI),
    para cualquier otro llamador (eq_router.py al reaplicar lo
    guardado)."""
    node_id = find_node_id(EQ_INPUT_NAME)
    if node_id is None:
        return False, "El sink del ecualizador no está cargado (filter-chain.service)"

    gain_db = max(MIN_GAIN, min(MAX_GAIN, float(gain_db)))
    prop = f"eq_band_{band}:Gain"
    try:
        result = subprocess.run(
            ["pw-cli", "s", str(node_id), "Props", f'{{ params = [ "{prop}" {gain_db} ] }}'],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return False, str(e)

    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()
        return False, msg or f"pw-cli devolvió código {result.returncode}"
    return True, ""

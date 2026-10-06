#!/usr/bin/env python3
"""Hilo de core/ff_core.py -- espera el sink del EQ, reaplica los gains
guardados, y mantiene el ecualizador en el camino de la señal sin
importar la salida activa. Ver CLAUDE.md "Pestaña Sonido"."""

import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import eq_pw
import eq_actions

POLL_SECONDS = 2
STARTUP_TIMEOUT = 30


def _wait_for_sink(stop):
    waited = 0
    while not eq_pw.eq_sink_loaded():
        if waited >= STARTUP_TIMEOUT or stop.wait(1):
            return False
        waited += 1
    return True


def _default_sink_name():
    try:
        out = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _eq_output_stream_index():
    """Índice de sink-input del propio "effect_output.eq6" -- pactl lo
    lista como un stream más."""
    try:
        out = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    index = None
    for line in out.stdout.splitlines():
        if line.startswith("Sink Input #"):
            index = line.split("#", 1)[1].strip()
        elif f'node.name = "{eq_pw.EQ_OUTPUT_NAME}"' in line:
            return index
    return None


def _move_streams_to_eq(eq_output_index):
    """Sin saltar el stream del propio EQ, moverlo a su entrada borra
    su target y WirePlumber lo reconecta solo a otra salida, que
    sonaba junto con la elegida."""
    try:
        out = subprocess.run(["pactl", "list", "short", "sink-inputs"], capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return
    for line in out.stdout.splitlines():
        if line.strip() and line.split()[0] != eq_output_index:
            subprocess.run(
                ["pactl", "move-sink-input", line.split()[0], eq_pw.EQ_INPUT_NAME],
                capture_output=True, timeout=5,
            )


def _disconnect_eq_output_links():
    """Sin esto, cambiar de salida A->B dejaría el audio sonando en LAS
    DOS a la vez -- PipeWire no reemplaza un link viejo solo porque se
    crea uno nuevo desde el mismo puerto, hay que sacar el anterior a
    mano."""
    node_id = eq_pw.find_node_id(eq_pw.EQ_OUTPUT_NAME)
    if node_id is None:
        return
    for obj in eq_pw.dump():
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        if obj.get("info", {}).get("props", {}).get("link.output.node") == node_id:
            subprocess.run(["pw-cli", "destroy", str(obj["id"])], capture_output=True, timeout=5)


def route_to(hw_sink_name):
    # Fija el target del stream del EQ en WirePlumber; un pw-link a mano
    # no lo cambia y WirePlumber vuelve a enlazar el stream a su propia
    # elección cada vez que reescanea.
    eq_output_index = _eq_output_stream_index()
    if eq_output_index is not None:
        subprocess.run(
            ["pactl", "move-sink-input", eq_output_index, hw_sink_name],
            capture_output=True, timeout=5,
        )
    else:
        _disconnect_eq_output_links()
        for ch in ("FL", "FR"):
            subprocess.run(
                ["pw-link", f"{eq_pw.EQ_OUTPUT_NAME}:output_{ch}", f"{hw_sink_name}:playback_{ch}"],
                capture_output=True, timeout=5,
            )
    subprocess.run(["pactl", "set-default-sink", eq_pw.EQ_INPUT_NAME], capture_output=True, timeout=5)
    _move_streams_to_eq(eq_output_index)

    # Preajuste por dispositivo (pestaña "Sonido", "Guardar para este
    # dispositivo") -- si ESTE sink de hardware puntual tiene una curva
    # guardada, la reaplica (y de paso queda persistida en eq-bands.json
    # como el gain "actual", vía eq_actions.set_band_gain -- así la
    # pestaña "Sonido" ya la refleja si se abre justo después). Sin
    # curva guardada para este dispositivo, el gain que ya estaba
    # sonando queda tal cual -- no hay nada "de fábrica" a qué volver.
    device_gains = eq_actions.get_gains_for_device(hw_sink_name)
    if device_gains:
        for band, gain_db in device_gains.items():
            eq_actions.set_band_gain(band, gain_db)


def run(stop):
    if not _wait_for_sink(stop):
        return  # filter-chain.service no cargó a tiempo -- nada que rutear

    eq_actions.apply_saved_gains()

    last_hw_sink = None
    while not stop.is_set():
        current = _default_sink_name()
        if current and current != eq_pw.EQ_INPUT_NAME and current != last_hw_sink:
            route_to(current)
            last_hw_sink = current
        stop.wait(POLL_SECONDS)

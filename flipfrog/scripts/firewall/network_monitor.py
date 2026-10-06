#!/usr/bin/env python3
"""Daemon on-demand de ancho de banda por proceso (nethogs). Ver
CLAUDE.md "Actividad de red"."""

import json
import os
import signal
import subprocess
import sys

import psutil

PROCESS_NAME = "ff-net-monitor"

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common

WAYBAR_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "waybar"))
CACHE_FILE = os.path.join(WAYBAR_DIR, "network-usage.json")
LOCK = "/tmp/network-monitor.pid"

REFRESH_SECONDS = 2
MIN_KBPS = 0.05  # ruido de fondo (ACKs, keepalives) -- no vale la pena mostrarlo

_running = True
_proc = None


def _stop(_signum, _frame):
    global _running
    _running = False
    if _proc is not None:
        _proc.terminate()


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def _process_label(pid, path):
    """Nombre real del proceso vía psutil (mismo dato que muestra
    process_module.py) -- más legible que el path crudo de nethogs (ej.
    "/opt/zen-browser-bin/zen-bin" -> "zen-bin"). Cae al último tramo
    del path si el proceso ya no existe (terminó entre que nethogs lo
    vio y que se resolvió aquí)."""
    try:
        return psutil.Process(int(pid)).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return path.rsplit("/", 1)[-1] or path


def _flush(entries):
    common.atomic_write(CACHE_FILE, json.dumps(entries))


def main():
    global _proc
    common.set_process_name(PROCESS_NAME)

    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))

    try:
        _proc = subprocess.Popen(
            ["nethogs", "-t", "-d", str(REFRESH_SECONDS)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        batch = []
        while _running:
            line = _proc.stdout.readline()
            if not line:
                if _proc.poll() is not None:
                    break
                continue
            line = line.rstrip("\n")

            if line == "Refreshing:":
                batch = []
                continue
            if line == "":
                _flush(batch)
                continue

            parts = line.split("\t")
            if len(parts) != 3:
                continue
            ident, sent, recv = parts
            try:
                sent, recv = float(sent), float(recv)
            except ValueError:
                continue
            if sent < MIN_KBPS and recv < MIN_KBPS:
                continue

            path, pid, _uid = ident.rsplit("/", 2)
            label = "Desconocido" if path == "unknown TCP" else _process_label(pid, path)
            batch.append({
                "pid": pid, "name": label,
                "sent_kbps": round(sent, 1), "recv_kbps": round(recv, 1),
            })
    finally:
        if os.path.exists(LOCK):
            try:
                os.remove(LOCK)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()

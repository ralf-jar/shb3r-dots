#!/usr/bin/env python3
"""Servicios de fondo que tienen que seguir vivos toda la sesión
(alarmas, ruteo del ecualizador, registro del firewall), cada uno en
su hilo dentro de un solo proceso: un intérprete y una copia de la
librería estándar en vez de tres (~9 MB cada uno). Autostart.

  ff_core.py                 todos
  ff_core.py alarms eq ...   solo esos (para depurar uno aislado)

Un servicio que truena se reinicia a los RESTART_DELAY s sin tumbar a
los demás. Arrancarlo de nuevo reemplaza a la instancia anterior."""

import os
import signal
import sys
import threading
import time
import traceback

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
SCRIPTS_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, SCRIPTS_DIR)
for sub in ("alarms", "audio", "firewall"):
    sys.path.insert(0, os.path.join(SCRIPTS_DIR, sub))

import common
import alarm_daemon
import eq_router
import firewall_logger

PROCESS_NAME = "ff-core"
LOCK = "/tmp/ff-core.pid"
RESTART_DELAY = 10
SERVICES = {
    "alarms": alarm_daemon.run,
    "eq": eq_router.run,
    "firewall": firewall_logger.run,
}


def _supervise(name, run, stop):
    while not stop.is_set():
        try:
            run(stop)
            return
        except Exception:
            print(f"ff-core: {name} falló, reinicio en {RESTART_DELAY} s", file=sys.stderr)
            traceback.print_exc()
            stop.wait(RESTART_DELAY)


def _replace_previous():
    try:
        with open(LOCK) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        return
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def main():
    names = sys.argv[1:] or list(SERVICES)
    unknown = [n for n in names if n not in SERVICES]
    if unknown:
        sys.exit(f"uso: ff_core.py [{' '.join(SERVICES)}]  (desconocido: {' '.join(unknown)})")

    common.set_process_name(PROCESS_NAME)
    _replace_previous()
    common.atomic_write(LOCK, str(os.getpid()))

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    threads = [threading.Thread(target=_supervise, args=(n, SERVICES[n], stop), name=n, daemon=True)
               for n in names]
    for thread in threads:
        thread.start()
    try:
        stop.wait()
        for thread in threads:
            thread.join(timeout=3)
    finally:
        try:
            with open(LOCK) as f:
                if f.read().strip() == str(os.getpid()):
                    os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    main()

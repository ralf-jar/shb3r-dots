#!/usr/bin/env python3
"""
bandcamp_ctl.py
CLI mínima para los botones de la isla de la barra (anterior/pausa/
siguiente, ver bar/bar_modules.py) -- si el daemon no
corre, silencio total: no tiene sentido lanzarlo solo para pausar o
saltar algo que no existe (eso lo hace bandcamp_popup.py al abrir, ver
bandcamp_ipc.ensure_daemon_running).
"""
import sys

import bandcamp_ipc


def main():
    if len(sys.argv) < 2:
        return
    if sys.argv[1] == "volume" and len(sys.argv) > 2:
        bandcamp_ipc.send_command("volume", delta=float(sys.argv[2]))
    else:
        bandcamp_ipc.send_command(sys.argv[1])


if __name__ == "__main__":
    main()

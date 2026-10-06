#!/usr/bin/env python3
"""Invocado desde autostart.lua al iniciar sesión. Ver CLAUDE.md
"Autoconexión al iniciar sesión"."""

import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import bluetooth_state
import bluetooth_actions


def main():
    delay, devices = bluetooth_state.get_autoconnect()
    if not devices:
        return
    time.sleep(delay)
    for mac in devices:
        bluetooth_actions.connect(mac)


if __name__ == "__main__":
    main()

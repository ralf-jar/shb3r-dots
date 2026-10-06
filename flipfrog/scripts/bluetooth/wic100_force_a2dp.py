#!/usr/bin/env python3
"""Daemon que fuerza A2DP en el WI-C100 al reconectar. Ver CLAUDE.md
"Pestaña Bluetooth" -- BlueZ a veces negocia solo el perfil headset
(HSP/HFP) si algo pide el micrófono antes de terminar la conexión."""

import os
import subprocess
import sys
import time

PROCESS_NAME = "ff-wic100-a2dp"

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common

CARD = "bluez_card.74_B7_E6_41_CC_34"
DEVICE_PATH = "/org/bluez/hci0/dev_74_B7_E6_41_CC_34"

SETTLE_DELAY = 1.5  # segundos para que termine la negociación de perfiles


def _force_a2dp():
    time.sleep(SETTLE_DELAY)
    subprocess.run(
        ["pactl", "set-card-profile", CARD, "a2dp-sink"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main():
    common.set_process_name(PROCESS_NAME)

    proc = subprocess.Popen(
        ["dbus-monitor", "--system",
         f"type='signal',interface='org.freedesktop.DBus.Properties',path='{DEVICE_PATH}'"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )

    awaiting_value = False
    try:
        for line in proc.stdout:
            if "string \"Connected\"" in line:
                awaiting_value = True
            elif awaiting_value:
                awaiting_value = False
                if "boolean true" in line:
                    _force_a2dp()
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()

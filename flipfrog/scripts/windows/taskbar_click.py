#!/usr/bin/env python3
"""on-click-window de hyprland/workspaces (workspace-taskbar): izquierdo enfoca, medio cierra."""
import subprocess
import sys


def main():
    if len(sys.argv) < 3:
        return
    address, button = sys.argv[1], sys.argv[2]
    if not address.startswith("0x"):
        address = "0x" + address

    if button == "1":
        lua = f'hl.dsp.focus({{ window = "address:{address}" }})'
    elif button == "2":
        lua = f'hl.dsp.window.close({{ window = "address:{address}" }})'
    else:
        return

    try:
        subprocess.run(["hyprctl", "dispatch", lua], timeout=2,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


if __name__ == "__main__":
    main()

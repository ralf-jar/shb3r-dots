#!/usr/bin/env python3
"""Sube/baja el volumen del sink por defecto, clamp 0-100%.
Uso: scroll-volume.py up|down [step]
Sink real resuelto vía eq_pw.py (import opcional) -- ver CLAUDE.md
"eq_pw.resolve_default_sink_name()"."""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
try:
    import eq_pw
except ImportError:
    eq_pw = None


def _target_sink():
    if eq_pw is not None:
        resolved = eq_pw.resolve_default_sink_name()
        if resolved:
            return resolved
    return "@DEFAULT_SINK@"


def get_volume(sink):
    r = subprocess.run(["pactl", "get-sink-volume", sink],
                        capture_output=True, text=True)
    m = re.search(r"(\d+)%", r.stdout)
    return int(m.group(1)) if m else 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("up", "down"):
        sys.exit(1)
    step = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    sink = _target_sink()
    vol = get_volume(sink)
    if sys.argv[1] == "up":
        vol = min(100, vol + step)
    else:
        vol = max(0, vol - step)

    subprocess.run(["pactl", "set-sink-volume", sink, f"{vol}%"],
                    capture_output=True)


if __name__ == "__main__":
    main()

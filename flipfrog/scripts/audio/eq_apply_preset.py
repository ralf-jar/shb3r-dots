#!/usr/bin/env python3
"""CLI para aplicar un preajuste del EQ desde otro proceso (usado por
bandcamp/bandcamp_daemon.py vía subprocess, ver CLAUDE.md "Autoecualizador").

Uso:
    eq_apply_preset.py --preset "Rock"
    eq_apply_preset.py --tags "rock,prog rock,post-rock"
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import eq_actions
import eq_presets


def apply_preset(name):
    gains = eq_presets.PRESETS.get(name)
    if gains is None:
        return False
    for band, gain_db in enumerate(gains, start=1):
        eq_actions.set_band_gain(band, gain_db)
    return True


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("--preset", "--tags"):
        print("uso: eq_apply_preset.py --preset <nombre> | --tags <tag1,tag2,...>", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--preset":
        preset = sys.argv[2]
    else:
        tags = [t for t in sys.argv[2].split(",") if t]
        preset = eq_presets.preset_for_tags(tags)

    if preset:
        apply_preset(preset)


if __name__ == "__main__":
    main()

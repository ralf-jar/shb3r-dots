#!/usr/bin/env python3
"""Escala el border-radius de todo el dashboard/Waybar
y reinicia Waybar. Ver CLAUDE.md "Redondeo de bordes"
para el mecanismo y por qué está diseñado así."""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_ROOT = os.path.dirname(os.path.dirname(THEMER_DIR))
STATE_FILE = os.path.join(THEMER_DIR, "corner-radius.json")

MIN_RADIUS, MAX_RADIUS = 0, 30
DEFAULT_RADIUS = 25

# Alcance real del tema -- ver CLAUDE.md "Redondeo de bordes" (qué queda
# afuera y por qué).
CSS_GLOBS = [
    "flipfrog/themer/global.css",
    "flipfrog/scripts/**/*.css",
]

NUM_BEFORE_PX_RE = re.compile(r"\d+(?=px)")


def discover_css_files():
    found = []
    for pattern in CSS_GLOBS:
        matches = glob.glob(os.path.join(CONFIG_ROOT, pattern), recursive=True)
        found.extend(matches)
    return sorted(set(found))


def load_radius():
    try:
        with open(STATE_FILE) as f:
            return int(json.load(f).get("radius", DEFAULT_RADIUS))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return DEFAULT_RADIUS


def _atomic_write(path, content):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.replace(tmp, path)


def save_radius(value):
    _atomic_write(STATE_FILE, json.dumps({"radius": value}))


def _rewrite_css(path, value):
    with open(path) as f:
        lines = f.readlines()
    changed = False
    for i, line in enumerate(lines):
        if "border-radius:" not in line:
            continue
        new_line = NUM_BEFORE_PX_RE.sub(str(value), line)
        if new_line != line:
            lines[i] = new_line
            changed = True
    if changed:
        _atomic_write(path, "".join(lines))


def _apply_hyprland_rounding(value):
    """decorations.lua relee corner-radius.json en cada `hyprctl reload`;
    el eval solo adelanta el cambio a la sesión actual (`hyprctl keyword`
    no funciona con config Lua)."""
    subprocess.run(
        ["hyprctl", "eval", f"hl.config({{decoration={{rounding={value}}}}})"],
        capture_output=True, timeout=2,
    )


def apply_radius(value):
    value = max(MIN_RADIUS, min(MAX_RADIUS, int(value)))
    for path in discover_css_files():
        _rewrite_css(path, value)
    save_radius(value)
    _apply_hyprland_rounding(value)
    return value


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: sync_radius.py <radio 0-30>", file=sys.stderr)
        sys.exit(1)
    try:
        radius = int(sys.argv[1])
    except ValueError:
        print("radio inválido", file=sys.stderr)
        sys.exit(1)
    print(f"Radio aplicado: {apply_radius(radius)}px")

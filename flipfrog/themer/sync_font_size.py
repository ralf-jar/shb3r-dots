#!/usr/bin/env python3
"""Escala el font-size de todo el dashboard/Waybar
y reinicia Waybar -- mismo patrón que sync_radius.py,
pero por DELTA en vez de valor absoluto: cada font-size:Npx del repo
tiene un tamaño de diseño distinto (títulos > texto normal), así que
se le suma la diferencia contra el tamaño base (13px, el `* {}` de
global.css) en vez de pisarlos todos al mismo valor."""
import glob
import json
import os
import re
import sys
import tempfile

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_ROOT = os.path.dirname(os.path.dirname(THEMER_DIR))
STATE_FILE = os.path.join(THEMER_DIR, "font-size.json")

MIN_FONT_SIZE, MAX_FONT_SIZE = 10, 20
BASE_FONT_SIZE = 13

# Mismo alcance que sync_radius.py -- ver CLAUDE.md "Redondeo de bordes".
CSS_GLOBS = [
    "flipfrog/themer/global.css",
    "flipfrog/scripts/**/*.css",
]

# Ancla al prefijo "font-size:" (no un \d+(?=px) genérico) -- una línea
# puede traer otra declaración con su propio valor en px al lado.
FONT_SIZE_RE = re.compile(r"(font-size:\s*)(\d+)(px)")

# Marcador de línea (comentario CSS, no afecta el render) para íconos
# que deben quedar SIEMPRE al mismo tamaño sin importar el slider --
# pedido explícito del usuario, 2026-09-06: los glyphs de Bluetooth/
# Volumen/Red (#pulseaudio, #bluetooth, #red en
# flipfrog/scripts/bar/bar.css) heredaban el `* {}` universal y escalaban con todo
# lo demás. La sustitución corre línea por línea (no sobre el archivo
# entero) para poder saltarse solo las líneas marcadas.
STATIC_MARKER = "sync_font_size: static"


def discover_css_files():
    found = []
    for pattern in CSS_GLOBS:
        matches = glob.glob(os.path.join(CONFIG_ROOT, pattern), recursive=True)
        found.extend(matches)
    return sorted(set(found))


def load_font_size():
    try:
        with open(STATE_FILE) as f:
            return int(json.load(f).get("user_size", BASE_FONT_SIZE))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return BASE_FONT_SIZE


def _atomic_write(path, content):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.replace(tmp, path)


def save_font_size(value):
    _atomic_write(STATE_FILE, json.dumps({"user_size": value}))


def _rewrite_css(path, delta):
    with open(path) as f:
        content = f.read()

    def repl(m):
        new_value = max(1, int(m.group(2)) + delta)
        return f"{m.group(1)}{new_value}{m.group(3)}"

    lines = content.splitlines(keepends=True)
    changed = False
    for i, line in enumerate(lines):
        if STATIC_MARKER in line:
            continue
        new_line, count = FONT_SIZE_RE.subn(repl, line)
        if count:
            lines[i] = new_line
            changed = True

    if changed:
        _atomic_write(path, "".join(lines))


def apply_font_size(value):
    """delta contra el tamaño ya aplicado (font-size.json), nunca contra
    BASE_FONT_SIZE directo -- así repetir el cambio no acumula: cada
    valor en disco ya tiene el delta anterior sumado."""
    value = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, int(value)))
    delta = value - load_font_size()
    if delta != 0:
        for path in discover_css_files():
            _rewrite_css(path, delta)
        save_font_size(value)
    return value


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("uso: sync_font_size.py <tamaño 10-20>", file=sys.stderr)
        sys.exit(1)
    try:
        size = int(sys.argv[1])
    except ValueError:
        print("tamaño inválido", file=sys.stderr)
        sys.exit(1)
    print(f"Tamaño aplicado: {apply_font_size(size)}px")

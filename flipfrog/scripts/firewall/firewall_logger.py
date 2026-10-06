#!/usr/bin/env python3
"""journalctl -> waybar/firewall-log.jsonl, como hilo de
core/ff_core.py. Ver CLAUDE.md "Logger de firewall"."""

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common

WAYBAR_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "waybar"))
LOG_FILE = os.path.join(WAYBAR_DIR, "firewall-log.jsonl")

MAX_ENTRIES = 500
FLUSH_INTERVAL = 3  # segundos

PREFIXES = {
    "[UFW BLOCK]": "BLOCK",
    "[UFW ALLOW]": "ALLOW",
    "[UFW LIMIT BLOCK]": "LIMIT",
}

FIELD_RE = re.compile(r"\b(SRC|DST|PROTO|SPT|DPT|IN|OUT)=(\S*)")

def _load_existing():
    entries = deque(maxlen=MAX_ENTRIES)
    try:
        with open(LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return entries


def _flush(entries):
    content = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
    common.atomic_write(LOG_FILE, content)


def parse_line(line):
    kind = None
    for prefix, label in PREFIXES.items():
        if prefix in line:
            kind = label
            break
    if kind is None:
        return None

    fields = dict(FIELD_RE.findall(line))
    return {
        "time": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "src": fields.get("SRC", "?"),
        "dst": fields.get("DST", "?"),
        "proto": fields.get("PROTO", "?"),
        "spt": fields.get("SPT", ""),
        "dpt": fields.get("DPT", ""),
        "iface": fields.get("IN") or fields.get("OUT") or "?",
    }


def run(stop):
    entries = _load_existing()
    last_flush = 0.0
    dirty = False

    proc = subprocess.Popen(
        ["journalctl", "-k", "-f", "-o", "cat", "--no-pager"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    # readline() bloquea esperando la próxima línea del kernel: al
    # pedir el paro se mata journalctl para que devuelva EOF.
    threading.Thread(target=lambda: stop.wait() and proc.terminate(), daemon=True).start()
    try:
        while not stop.is_set():
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue

            entry = parse_line(line)
            if entry is not None:
                entries.append(entry)
                dirty = True

            now = time.time()
            if dirty and now - last_flush >= FLUSH_INTERVAL:
                _flush(entries)
                last_flush = now
                dirty = False
    finally:
        if dirty:
            _flush(entries)
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    if not stop.is_set():
        raise RuntimeError("journalctl terminó solo")

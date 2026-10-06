#!/usr/bin/env python3
"""
focused_volume.py <delta>
Sube/baja (delta en %, ej. 5 / -5) el volumen de los streams de audio
de la ventana con foco (SUPER+CTRL+↑/↓). Un stream es de la ventana si
su application.process.id es el pid de la ventana o un descendiente
(Brave/Chromium sacan el audio de un proceso hijo; Proton/pressure-vessel
también cuelgan del pid de la ventana).
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from common import set_process_name
from i18n import t

TIMEOUT = 3
MAX_VOLUME = 100
ENV = {**os.environ, "LC_ALL": "C"}


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, env=ENV).stdout


def active_window():
    try:
        return json.loads(_run(["hyprctl", "activewindow", "-j"]) or "{}")
    except (json.JSONDecodeError, subprocess.TimeoutExpired):
        return {}


def process_tree(root):
    children = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as f:
                stat = f.read()
        except OSError:
            continue
        # comm va entre paréntesis y puede traer espacios: ppid es el 2º campo tras ")".
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        children.setdefault(ppid, []).append(int(entry))
    tree, stack = set(), [root]
    while stack:
        pid = stack.pop()
        if pid in tree:
            continue
        tree.add(pid)
        stack.extend(children.get(pid, []))
    return tree


def sink_inputs():
    inputs, current = [], None
    for line in _run(["pactl", "list", "sink-inputs"]).splitlines():
        line = line.strip()
        m = re.match(r"Sink Input #(\d+)", line)
        if m:
            current = {"index": int(m.group(1)), "pid": None, "binary": "", "volume": None}
            inputs.append(current)
            continue
        if current is None:
            continue
        m = re.match(r"Volume:.*?(\d+)%", line)
        if m and current["volume"] is None:
            current["volume"] = int(m.group(1))
        m = re.match(r'application\.process\.id = "(\d+)"', line)
        if m:
            current["pid"] = int(m.group(1))
        m = re.match(r'application\.process\.binary = "([^"]+)"', line)
        if m:
            current["binary"] = m.group(1)
    return inputs


def notify(text, volume=None):
    # Con No molestar dunst las encola y saldrían todas juntas al quitarlo.
    if _run(["dunstctl", "is-paused"]).strip() == "true":
        return
    cmd = ["notify-send", "-a", "flipfrog-volume", "-t", "1200",
           "-h", "string:x-dunst-stack-tag:focused-volume"]
    if volume is not None:
        cmd += ["-h", f"int:value:{volume}"]
    subprocess.Popen(cmd + [text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    set_process_name("ff-focus-vol")
    delta = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    window = active_window()
    pid = window.get("pid")
    if not pid or pid <= 0:
        return
    name = window.get("class") or window.get("title") or "?"

    tree = process_tree(pid)
    all_inputs = sink_inputs()
    inputs = [i for i in all_inputs if i["pid"] in tree]
    if not inputs:
        cls = name.casefold()
        inputs = [i for i in all_inputs if i["binary"] and i["binary"].casefold() in cls]
    if not inputs:
        notify(t("sonido", "volumen_ventana_sin_audio", app=name))
        return

    new_volume = None
    for inp in inputs:
        vol = max(0, min(MAX_VOLUME, (inp["volume"] or 0) + delta))
        subprocess.run(["pactl", "set-sink-input-volume", str(inp["index"]), f"{vol}%"],
                       capture_output=True, timeout=TIMEOUT)
        new_volume = vol if new_volume is None else max(new_volume, vol)
    notify(t("sonido", "volumen_ventana", app=name, volume=new_volume), new_volume)


if __name__ == "__main__":
    main()

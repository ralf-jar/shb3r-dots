"""Hyprland para la barra: peticiones por .socket.sock (sin lanzar
hyprctl), eventos por .socket2.sock y un estado compartido (workspaces,
ventanas, foco) que se relee en un hilo y se aplica en el de GTK."""

import json
import os
import socket
import subprocess
import threading
import time

from gi.repository import GLib

REFRESH_DEBOUNCE_MS = 30
REQUEST_TIMEOUT = 2

# Eventos que cambian lo que pinta la barra (workspaces, ventanas, foco,
# títulos para el tooltip).
REFRESH_EVENTS = {
    "workspace", "workspacev2", "focusedmon", "focusedmonv2",
    "activewindow", "activewindowv2", "openwindow", "closewindow",
    "movewindow", "movewindowv2", "createworkspace", "createworkspacev2",
    "destroyworkspace", "destroyworkspacev2", "renameworkspace",
    "moveworkspace", "moveworkspacev2", "windowtitle", "windowtitlev2",
    "monitoradded", "monitoraddedv2", "monitorremoved", "monitorremovedv2",
    "changefloatingmode", "fullscreen",
}


def _socket_dir():
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return os.path.join(runtime, "hypr", os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""))


def request(cmd):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(REQUEST_TIMEOUT)
    try:
        sock.connect(os.path.join(_socket_dir(), ".socket.sock"))
        sock.sendall(cmd.encode())
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    return b"".join(chunks).decode(errors="replace")


def request_json(what):
    try:
        return json.loads(request("j/" + what))
    except (OSError, ValueError):
        return None


def dispatch(lua):
    def run():
        try:
            subprocess.run(["hyprctl", "dispatch", lua], timeout=REQUEST_TIMEOUT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass

    threading.Thread(target=run, daemon=True).start()


def workspace_selector(ws):
    """Numéricos por id, con nombre por "name:" (mismo formato que los keybinds)."""
    return str(ws["id"]) if ws["name"].isdigit() else f'"name:{ws["name"]}"'


def focus_workspace(ws):
    dispatch(f"hl.dsp.focus({{ workspace = {workspace_selector(ws)} }})")


def focus_window(address):
    dispatch(f'hl.dsp.focus({{ window = "address:{address}" }})')


def close_window(address):
    dispatch(f'hl.dsp.window.close({{ window = "address:{address}" }})')


def _snapshot():
    workspaces = request_json("workspaces")
    clients = request_json("clients")
    active_ws = request_json("activeworkspace")
    active_win = request_json("activewindow")
    monitors = request_json("monitors")
    if workspaces is None or clients is None:
        return None
    return {
        "workspaces": workspaces,
        "clients": clients,
        "shown_workspaces": {m["activeWorkspace"]["name"] for m in monitors or []},
        "active_workspace": (active_ws or {}).get("name"),
        "active_window": (active_win or {}).get("address"),
    }


class HyprState:
    """Un solo lector para todas las barras (una por monitor)."""

    def __init__(self):
        self.snapshot = None
        self._subs = []
        self._busy = False
        self._again = False
        self._debounce = None

        threading.Thread(target=self._listen, daemon=True).start()
        self.refresh()

    def subscribe(self, callback, widget):
        entry = (callback, widget)
        self._subs.append(entry)
        widget.connect("destroy", lambda _w: self._subs.remove(entry))
        if self.snapshot is not None:
            callback(self.snapshot)

    def refresh(self):
        if self._debounce is None:
            self._debounce = GLib.timeout_add(REFRESH_DEBOUNCE_MS, self._start_fetch)

    def _start_fetch(self):
        self._debounce = None
        if self._busy:
            self._again = True
            return False
        self._busy = True
        threading.Thread(target=self._fetch, daemon=True).start()
        return False

    def _fetch(self):
        snap = _snapshot()
        GLib.idle_add(self._apply, snap)

    def _apply(self, snap):
        self._busy = False
        if snap is not None:
            self.snapshot = snap
            for callback, _widget in list(self._subs):
                callback(snap)
        if self._again:
            self._again = False
            self.refresh()
        return False

    def _listen(self):
        while True:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(os.path.join(_socket_dir(), ".socket2.sock"))
                buf = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    if any(line.split(b">>", 1)[0].decode(errors="replace") in REFRESH_EVENTS
                           for line in lines):
                        GLib.idle_add(self._refresh_once)
            except OSError:
                pass
            time.sleep(1)

    def _refresh_once(self):
        self.refresh()
        return False

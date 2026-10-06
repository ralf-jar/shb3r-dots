"""Control mínimo de mpv por su IPC JSON (socket unix) para el
reproductor de audiolibros -- mismo enfoque que bandcamp/
bandcamp_mpv.py (sin python-mpv, mpv como subproceso más)."""

import itertools
import json
import os
import shutil
import socket
import subprocess
import threading
import time

SOCKET_PATH = "/tmp/ff-audiobook-mpv.sock"
# Symlink con el nombre del daemon -- el comm del kernel sale del
# basename del ejecutable, así mpv agrupa con ff-audiobook en la pestaña
# "Procesos" (mismo truco que MPV_BIN_LINK de bandcamp_mpv.py).
MPV_BIN_LINK = "/tmp/ff-audiobook"
TIMEOUT = 2


def _mpv_binary():
    real = shutil.which("mpv")
    if not real:
        return "mpv"
    try:
        if os.path.lexists(MPV_BIN_LINK):
            os.remove(MPV_BIN_LINK)
        os.symlink(real, MPV_BIN_LINK)
        return MPV_BIN_LINK
    except OSError:
        return real


class Mpv:
    def __init__(self, volume=100):
        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)
        self._proc = subprocess.Popen(
            [_mpv_binary(), "--no-video", "--idle=yes", "--keep-open=yes",
             "--no-terminal", "--no-config", "--audio-display=no",
             f"--volume={volume}", "--audio-client-name=ff-audiobook",
             "--title=Audiolibro", f"--input-ipc-server={SOCKET_PATH}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            if os.path.exists(SOCKET_PATH):
                break
            time.sleep(0.1)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(SOCKET_PATH)
        self._ids = itertools.count(1)
        self._pending = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buf = b""
        while True:
            try:
                chunk = self._sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                slot = self._pending.get(msg.get("request_id"))
                if slot:
                    slot[1] = msg
                    slot[0].set()

    def command(self, *args):
        req_id = next(self._ids)
        slot = [threading.Event(), None]
        self._pending[req_id] = slot
        try:
            with self._lock:
                self._sock.sendall((json.dumps({"command": list(args), "request_id": req_id}) + "\n").encode())
            slot[0].wait(TIMEOUT)
        except OSError:
            return None
        finally:
            self._pending.pop(req_id, None)
        msg = slot[1]
        return msg.get("data") if msg and msg.get("error") == "success" else None

    def get(self, prop):
        return self.command("get_property", prop)

    def set(self, prop, value):
        return self.command("set_property", prop, value)

    def alive(self):
        return self._proc.poll() is None

    def quit(self):
        try:
            self.command("quit")
            self._proc.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            self._proc.kill()
        try:
            self._sock.close()
        except OSError:
            pass

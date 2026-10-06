"""Cliente liviano de audiobook_daemon.py -- mismo protocolo que
downloads/downloads_ipc.py (una conexión por comando, una línea JSON de
pedido y una de respuesta)."""

import json
import os
import socket
import subprocess
import time

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
DAEMON_SCRIPT = os.path.join(SCRIPT_DIR, "audiobook_daemon.py")
SOCKET_PATH = "/tmp/ff-audiobook-ctl.sock"
LOCK = "/tmp/ff-audiobook-daemon.pid"


def daemon_pid():
    try:
        with open(LOCK) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid
    except (ProcessLookupError, ValueError, FileNotFoundError):
        return None


def ensure_daemon_running():
    if daemon_pid() is not None:
        return
    subprocess.Popen(["python3", DAEMON_SCRIPT], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        if daemon_pid() is not None and os.path.exists(SOCKET_PATH):
            return
        time.sleep(0.1)


def send_command(cmd, timeout=2, **params):
    """None si el daemon no responde."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(SOCKET_PATH)
        sock.sendall((json.dumps({"cmd": cmd, **params}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        sock.close()
        return json.loads(buf.split(b"\n", 1)[0]) if buf else None
    except (OSError, json.JSONDecodeError):
        return None

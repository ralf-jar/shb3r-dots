"""
bandcamp_ipc.py
Cliente liviano de bandcamp_daemon.py -- usado por bandcamp_popup.py y por los scripts de
la isla de Waybar (flipfrog/scripts/bandcamp/, bandcamp_ctl.py). Protocolo: una
conexión por comando (una línea JSON de pedido, una línea JSON de
respuesta, cierra) -- sin sockets persistentes, mismo criterio que
MixerPopup en flipfrog/scripts/audio/mixer.py (sondeo sincrónico cada
cierto intervalo alcanza para lo poco que cambia acá).

ensure_daemon_running()/daemon_pid() calcan el patrón de
theme/theme_module.py con theme-rotator.py: PID en un lock propio, Popen
con start_new_session=True si no está corriendo, sin chequeo doble del
lado del daemon -- la responsabilidad de no lanzar dos instancias es de
quien llama (igual que theme_module.py nunca lanza un segundo rotator si
rotator_pid() ya devuelve algo).
"""
import json
import os
import socket
import subprocess
import time

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
DAEMON_SCRIPT = os.path.join(SCRIPT_DIR, "bandcamp_daemon.py")
SOCKET_PATH = "/tmp/bandcamp-radio-ctl.sock"
LOCK = "/tmp/bandcamp-radio-daemon.pid"


def daemon_pid():
    if not os.path.exists(LOCK):
        return None
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
    subprocess.Popen(
        ["python3", DAEMON_SCRIPT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(50):  # ~5s, mismo margen que el propio socket de mpv en bandcamp_mpv.py
        if os.path.exists(SOCKET_PATH):
            return
        time.sleep(0.1)


def send_command(cmd, timeout=2, **params):
    """None si el daemon no responde (no corre, o está trabado) -- quien
    llama decide si eso es un no-op silencioso (botones de la isla, ver
    bandcamp_ctl.py) o algo a mostrar en la UI (bandcamp_popup.py)."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(SOCKET_PATH)
        payload = {"cmd": cmd}
        payload.update(params)
        sock.sendall((json.dumps(payload) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        sock.close()
        if not buf:
            return None
        return json.loads(buf.split(b"\n", 1)[0])
    except (OSError, json.JSONDecodeError):
        return None


def get_state():
    return send_command("get_state")

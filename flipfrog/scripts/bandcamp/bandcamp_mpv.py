"""
bandcamp_mpv.py
Control de mpv por su protocolo IPC nativo (JSON sobre socket unix),
sin depender de python-mpv -- en este entorno `import mpv` resuelve
por accidente a ~/.config/mpv/ como namespace package (cwd en
sys.path), no a la librería real (confirmado a mano). mpv se maneja
como cualquier otro subproceso del repo (pactl, bluetoothctl).

Python (queue_feed.py) es dueño de la cola de reproducción -- mpv solo
reproduce UNA pista a la vez (`loadfile ... replace`); el avance a la
siguiente lo dispara el evento `end-file` de aquí (tanto fin normal como
error de red/stream vencido -- ambos casos hay que saltar a la
siguiente pista, no quedarse trabado), no la playlist propia de mpv.
"""
import json
import os
import shutil
import socket
import subprocess
import threading
import time

SOCKET_PATH = "/tmp/bandcamp-radio-mpv.sock"
# Symlink hacia el mpv real, con el MISMO nombre que bandcamp_daemon.py/
# PROCESS_NAME (pedido explícito del usuario) -- psutil.Process.name()
# (usado por process_module.py para agrupar) lee el comm del kernel, que
# se deriva del basename del ejecutable pasado a execve(), NO de argv[0]
# ni de --audio-client-name (eso último es un namespace aparte, de
# PipeWire/Pulse, no del proceso del SO). Confirmado a mano: ejecutar
# mpv a través de un symlink con otro nombre sí cambia el comm real a
# ese nombre, aunque el binario en sí sea externo y no lo controlemos.
# Mismo nombre que el daemon a propósito: process_module.py agrupa por
# nombre y suma RAM/CPU del grupo -- así el orquestador liviano y el
# motor de audio (el que de verdad pesa) aparecen como un solo total,
# en vez de dos entradas separadas que hay que sumar a mano.
MPV_BIN_LINK = "/tmp/ff-radio"


def _mpv_binary():
    """Recrea el symlink en cada arranque del daemon (no hace falta que
    persista entre sesiones) y devuelve su ruta -- si algo falla (mpv no
    instalado, /tmp no escribible), cae al "mpv" genérico de siempre en
    vez de romper la reproducción por esto."""
    try:
        real_mpv = shutil.which("mpv")
        if not real_mpv:
            return "mpv"
        if os.path.lexists(MPV_BIN_LINK):
            os.remove(MPV_BIN_LINK)
        os.symlink(real_mpv, MPV_BIN_LINK)
        return MPV_BIN_LINK
    except OSError:
        return "mpv"


class MpvPlayer:
    def __init__(self, on_end_file=None, on_error=None, on_property_change=None):
        """`on_end_file`/`on_error`/`on_property_change` se llaman desde
        el hilo lector, NO desde el hilo de GTK -- quien los pase debe
        envolver cualquier toque a widgets en GLib.idle_add."""
        self._on_end_file = on_end_file
        self._on_error = on_error
        self._on_property_change = on_property_change
        self.paused = False
        self._lock = threading.Lock()

        if os.path.exists(SOCKET_PATH):
            os.remove(SOCKET_PATH)

        self._proc = subprocess.Popen(
            [
                _mpv_binary(), "--no-video", "--idle=yes", "--volume=100", "--volume-max=100",
                # Nombre de cliente propio para PipeWire/Pulse -- sin
                # esto el sink-input se reporta como "mpv" genérico:
                # (a) flipfrog/scripts/audio/mixer.py tiene
                # IGNORED_APPS = ["mpvpaper", "mpv"] a propósito (para
                # no mostrar el mpv silencioso del wallpaper), así que
                # este stream quedaba invisible/inaccesible ahí; y
                # (b) module-stream-restore de PipeWire persiste
                # volumen por nombre de app -- heredaba el volumen en
                # 0% guardado para "mpv" (confirmado a mano, el
                # wallpaper de video se muteó en algún momento), sin
                # importar --volume=100 aquí. Confirmado a mano que con
                # un nombre propio arranca en volumen normal y aparece
                # en el mixer como app aparte.
                "--audio-client-name=bandcamp-radio",
                f"--input-ipc-server={SOCKET_PATH}", "--really-quiet",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        for _ in range(50):  # ~5s
            if os.path.exists(SOCKET_PATH):
                break
            time.sleep(0.1)

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(SOCKET_PATH)

        self._reader_thread = threading.Thread(target=self._read_events, daemon=True)
        self._reader_thread.start()

        # Para la barra de progreso: mpv manda un evento
        # "property-change" cada vez que cambian, sin tener que
        # sondear con get_property.
        self._send(["observe_property", 1, "time-pos"])
        self._send(["observe_property", 2, "duration"])

    def _send(self, command):
        with self._lock:
            self._sock.sendall((json.dumps({"command": command}) + "\n").encode())

    def _read_events(self):
        buf = b""
        while True:
            try:
                chunk = self._sock.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._handle_message(msg)

    def _handle_message(self, msg):
        event = msg.get("event")
        if event == "end-file":
            if msg.get("reason") in ("eof", "error") and self._on_end_file:
                self._on_end_file()
        elif event == "property-change":
            if self._on_property_change:
                self._on_property_change(msg.get("name"), msg.get("data"))
        elif event is None and msg.get("error") not in (None, "success"):
            if self._on_error:
                self._on_error(msg)

    def load(self, url):
        self.paused = False
        self._send(["loadfile", url, "replace"])

    def set_pause(self, paused):
        self.paused = paused
        self._send(["set_property", "pause", paused])

    def toggle_pause(self):
        self.set_pause(not self.paused)

    def seek(self, position_seconds):
        self._send(["set_property", "time-pos", position_seconds])

    def add_volume(self, delta):
        self._send(["add", "volume", delta])

    def stop(self):
        self._send(["stop"])

    def shutdown(self):
        try:
            self._send(["quit"])
        except OSError:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=2)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        try:
            self._sock.close()
        except OSError:
            pass
        if os.path.exists(SOCKET_PATH):
            try:
                os.remove(SOCKET_PATH)
            except OSError:
                pass

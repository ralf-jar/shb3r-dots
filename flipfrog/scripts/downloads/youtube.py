"""Descargas de YouTube, sin GTK -- usado solo por download_daemon.py.
Descarga con las funciones de fish `ytd` (mp3) o `ytdv [-r ALTO]` (mp4)
(`fish/functions/`, vía `fish -c`) corriendo CON cwd = carpeta destino
-- su `-o` es relativo y no admiten `-P`. Así el popup baja exactamente
lo mismo que los alias en la terminal y cambiar sus opciones no toca
este archivo. Progreso leído de
la salida de yt-dlp (líneas `[download]` separadas por \\r)."""

import json
import os
import re
import signal
import subprocess
import threading
import time

URL_RE = re.compile(
    r'(?:https?://)?(?:www\.|m\.|music\.)?(?:youtube\.com/(?:watch\?|shorts/|playlist\?|live/)|youtu\.be/)[^\s"\'<>]+',
    re.I)
PROGRESS_RE = re.compile(r'\[download\]\s+([\d.]+)% of\s+~?\s*([\d.]+)\s*([KMGT]?i?B)')
SPEED_RE = re.compile(r' at\s+([\d.]+)\s*([KMGT]?i?B)/s')
DEST_RE = re.compile(r'\[(?:download|ExtractAudio|Merger)\] (?:Destination: |Merging formats into ")(.+?)"?$')
THUMB_RE = re.compile(r'\[info\] Writing video thumbnail .* to: (.+)')
FINAL_RE = re.compile(r'\[(?:ExtractAudio|Merger|VideoConvertor)\].*?(?:Destination: |into ")(.+?)"?$')
ALREADY_RE = re.compile(r'\[download\] (.+) has already been downloaded')
UNITS = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4,
         "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4}
INFO_TIMEOUT = 30
PLAYLIST_FORMATS = ("audio", "best", "1080", "720", "480", "360")


class YoutubeError(Exception):
    """`code` es un código de i18n (módulo "descargas")."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_links(text):
    """[("youtube", url)] en orden de aparición, sin repetidos."""
    links = []
    for match in URL_RE.finditer(text):
        url = match.group(0)
        if not url.lower().startswith("http"):
            url = "https://" + url
        if ("youtube", url) not in links:
            links.append(("youtube", url))
    return links


def _size(fmt):
    return fmt.get("filesize") or fmt.get("filesize_approx") or 0


def _video_formats(formats):
    """[{"id": alto, "size": bytes aprox.}] de mayor a menor, mismo criterio
    que `ytdv -r`: mejor video con height <= alto + mejor audio."""
    audio = max((_size(f) for f in formats
                 if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")), default=0)
    video = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")]
    options = []
    for height in sorted({f["height"] for f in video}, reverse=True):
        size = max((_size(f) for f in video if f["height"] == height), default=0)
        options.append({"id": str(height), "size": size + audio if size else 0})
    options.insert(0, {"id": "audio", "size": audio})
    return options


def info(url):
    """(título, formatos). Si el enlace trae una lista (`ytd`/`ytdv` no
    pasan --no-playlist y la bajan entera) el título es el de la lista y
    los formatos son genéricos, sin tamaño (leer cada video tardaría)."""
    try:
        r = subprocess.run(["yt-dlp", "-J", "--flat-playlist", url],
                           capture_output=True, text=True, timeout=INFO_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise YoutubeError("error_red")
    except OSError:
        raise YoutubeError("error_youtube")
    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise YoutubeError("error_no_encontrado")
    name = data.get("title")
    if r.returncode != 0 or not name:
        raise YoutubeError("error_no_encontrado")
    if data.get("_type") == "playlist":
        return name, [{"id": fid, "size": 0} for fid in PLAYLIST_FORMATS]
    return name, _video_formats(data.get("formats") or [])


def _kill_group(proc):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
            proc.wait(timeout=3)
            return
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            pass


def run_watched(proc, should_stop):
    """Revisa should_stop() cada 0.5 s en un hilo aparte (el proceso
    puede pasar minutos sin imprimir nada) y mata el grupo entero al
    pausar/cancelar (SIGKILL si SIGTERM no basta en 3 s). Devuelve una
    lista que recibe el motivo."""
    stopped = []

    def watch():
        while proc.poll() is None:
            reason = should_stop()
            if reason:
                stopped.append(reason)
                _kill_group(proc)
                return
            time.sleep(0.5)

    threading.Thread(target=watch, daemon=True).start()
    return stopped


def _bytes(value, unit):
    return int(float(value) * UNITS.get(unit, 1))


def _command(url, fmt):
    if fmt == "audio":
        return ["fish", "-c", "ytd $argv", "--", url]
    if fmt == "best":
        return ["fish", "-c", "ytdv $argv", "--", url]
    return ["fish", "-c", "ytdv $argv", "--", "-r", fmt, url]


def download(url, fmt, dest, on_progress, should_stop):
    """Corre `ytd`/`ytdv` (según fmt, ver _command) con cwd=dest. on_progress(done, size, speed);
    should_stop() -> None/"paused"/"canceled" (ver run_watched).
    Devuelve (outcome, error, path). Pausar deja el .part (yt-dlp lo
    continúa solo al relanzar); cancelar borra todo lo que creó esta
corrida (.part, video sin convertir, miniatura, mp3/mp4 a medias)."""
    try:
        proc = subprocess.Popen(
            _command(url, fmt), cwd=os.path.abspath(dest),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        return "error", "error_youtube", None
    stopped = run_watched(proc, should_stop)

    created, final, last_error = set(), None, ""
    buf = b""
    while True:
        chunk = proc.stdout.read1(4096)
        if not chunk:
            break
        buf += chunk
        *lines, buf = re.split(rb"[\r\n]", buf)
        for raw in lines:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            if line.startswith("ERROR"):
                last_error = line
            m = DEST_RE.match(line) or THUMB_RE.match(line)
            if m:
                path = os.path.join(dest, m.group(1))
                created.update((path, path + ".part"))
            m = FINAL_RE.search(line) or ALREADY_RE.search(line)
            if m:
                final = os.path.join(dest, m.group(1))
            m = PROGRESS_RE.search(line)
            if m:
                size = _bytes(m.group(2), m.group(3))
                speed = SPEED_RE.search(line)
                on_progress(int(size * float(m.group(1)) / 100), size,
                            _bytes(*speed.groups()) if speed else 0)

    proc.wait()
    if stopped:
        if stopped[0] == "canceled":
            for path in created:
                if os.path.exists(path):
                    os.remove(path)
        return stopped[0], None, None
    if proc.returncode != 0:
        net = any(k in last_error.lower() for k in ("timed out", "connection", "network", "resolve"))
        return "error", "error_red" if net else "error_youtube", None
    return "done", None, final

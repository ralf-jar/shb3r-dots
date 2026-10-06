"""
local_library.py
Biblioteca de archivos locales para el modo "Local" (queue_feed.LocalFeed)
-- sin GTK ni red. Cada carpeta con audio adentro (directo, no en
subcarpetas) es un ítem del catálogo, igual que un álbum de Bandcamp.

Metadatos por pista vía `ffprobe` al reproducirla (no al escanear: ~30 ms
por archivo, una biblioteca de miles tardaría segundos en listar). El
título sale del nombre de archivo, no de la etiqueta -- las descargas de
YouTube (`ytd`) traen el título completo del video y el canal como
artista. Carátula: imagen embebida (extraída con ffmpeg a
~/.cache/bandcamp-radio/local-art/) o cover/folder.jpg de la carpeta.
"""
import hashlib
import json
import os
import subprocess

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wav", ".wma", ".webm"}
COVER_NAMES = ("cover", "folder", "front", "album")
ART_CACHE_DIR = os.path.expanduser("~/.cache/bandcamp-radio/local-art")
URL_PREFIX = "file://"
ALL_SUFFIX = "#all"
TIMEOUT = 5


def default_music_dir():
    try:
        out = subprocess.run(["xdg-user-dir", "MUSIC"], capture_output=True, text=True, timeout=TIMEOUT)
        path = out.stdout.strip()
        if path and os.path.isdir(path) and path != os.path.expanduser("~"):
            return path
    except (OSError, subprocess.TimeoutExpired):
        pass
    for name in ("Musica", "Música", "Music"):
        path = os.path.expanduser(f"~/{name}")
        if os.path.isdir(path):
            return path
    return os.path.expanduser("~")


def is_audio(name):
    return os.path.splitext(name)[1].lower() in AUDIO_EXTS and not name.startswith(".")


def list_folders(root):
    """[(ruta, [archivos ordenados])] de cada carpeta con audio directo."""
    folders = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        files = sorted((f for f in filenames if is_audio(f)), key=str.lower)
        if files:
            folders.append((dirpath, [os.path.join(dirpath, f) for f in files]))
    return folders


def title_from_path(path):
    name = os.path.basename(path)
    while os.path.splitext(name)[1].lower() in AUDIO_EXTS:
        name = os.path.splitext(name)[0]
    return name


def probe(path):
    """(artista, duración) -- (None, 0) si ffprobe falla o no está."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_entries", "format=duration:format_tags=artist,album_artist", path],
            capture_output=True, text=True, timeout=TIMEOUT)
        fmt = json.loads(out.stdout or "{}").get("format") or {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None, 0
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0
    return tags.get("artist") or tags.get("album_artist"), duration


def _folder_cover(folder):
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    for name in sorted(names):
        stem, ext = os.path.splitext(name)
        if stem.lower() in COVER_NAMES and ext.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            return os.path.join(folder, name)
    return None


def cover_for(path):
    """Ruta a una imagen para `path` (archivo de audio), o None. La
    extracción se cachea por ruta+mtime; un intento fallido también (archivo
    vacío), para no volver a correr ffmpeg en cada reproducción."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    key = hashlib.sha256(f"{path}\0{mtime}".encode()).hexdigest()
    cached = os.path.join(ART_CACHE_DIR, key + ".jpg")
    if os.path.exists(cached):
        return cached if os.path.getsize(cached) else _folder_cover(os.path.dirname(path))

    os.makedirs(ART_CACHE_DIR, exist_ok=True)
    tmp = cached + ".tmp.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-v", "quiet", "-y", "-i", path, "-an", "-frames:v", "1",
             "-vf", "scale=400:400:force_original_aspect_ratio=increase,crop=400:400", tmp],
            timeout=TIMEOUT * 2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if os.path.exists(tmp) and os.path.getsize(tmp):
        os.replace(tmp, cached)
        return cached
    try:
        os.remove(tmp)
    except OSError:
        pass
    open(cached, "wb").close()
    return _folder_cover(os.path.dirname(path))


def folder_cover(files):
    """Carátula de la carpeta: cover.jpg si hay, si no la embebida de la
    primera pista que tenga una (hasta 3 intentos)."""
    if files:
        found = _folder_cover(os.path.dirname(files[0]))
        if found:
            return found
    for path in files[:3]:
        found = cover_for(path)
        if found:
            return found
    return None

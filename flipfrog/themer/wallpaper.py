#!/usr/bin/env python3
"""Fondo de pantalla sin waypaper: awww para imágenes (y gif), mpvpaper
para video. El fondo activo vive en themer/wallpaper.json, ruta con `~`
(mismo formato que el comentario `/* wallpaper: */` de los .theme).

    wallpaper.py set <ruta>    aplica y guarda
    wallpaper.py restore       reaplica el guardado (autostart)
    wallpaper.py current       imprime la ruta guardada, con `~`
    wallpaper.py freeze|thaw   fondo fijo mientras hay juego o modo cine
                               (hypr/config/gaming-mode.lua)

Con un juego abierto o en modo cine el fondo queda fijo: un video se
muestra como un frame, un gif pausado. `set` respeta eso (cambiar de tema
a media partida pone el fondo nuevo ya congelado) y `thaw` reanuda el
fondo guardado en ese momento, no el que había al congelar.
"""

import json
import os
import subprocess
import sys
import time

THEMER_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(THEMER_DIR, "..", "scripts"))
import common

STATE_FILE = os.path.join(THEMER_DIR, "wallpaper.json")
VIDEO_EXTS = {"mp4", "webm", "mkv", "mov", "m4v", "avi"}
DEMO_WALLPAPER = os.path.join(THEMER_DIR, "wallpaperdemo", "aishot-2062.jpg")

# Mismas opciones que tenía waypaper/config.ini (fill = fill, transición random).
AWWW_OPTIONS = [
    "--resize", "crop",
    "--fill-color", "ffffff",
    "--filter", "Lanczos3",
    "--transition-type", "random",
    "--transition-step", "63",
    "--transition-angle", "0",
    "--transition-duration", "2",
    "--transition-fps", "60",
]
MPV_OPTIONS = "no-audio loop panscan=1.0"

# Mismos nombres que FREEZE_WORKSPACES en hypr/config/gaming-mode.lua.
FREEZE_WORKSPACES = {"gaming", "🎬"}
FROZEN_MARKER = "/tmp/flipfrog-wallpaper-frozen"
FROZEN_FRAME = "/tmp/flipfrog-wallpaper-frame.png"


def _collapse_home(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + "/") else path


def current_path(expand=True):
    """Ruta del fondo activo, o None si nunca se guardó uno."""
    try:
        with open(STATE_FILE) as f:
            path = json.load(f).get("path")
    except (FileNotFoundError, ValueError):
        return None
    if not path:
        return None
    return os.path.expanduser(path) if expand else path


def is_video(path):
    return os.path.splitext(path)[1].lstrip(".").lower() in VIDEO_EXTS


def is_gif(path):
    return path.lower().endswith(".gif")


def _alive(name):
    # -r sin Z: un zombie que su padre no recogió no cuenta como vivo.
    return subprocess.run(["pgrep", "-r", "D,R,S,T", "-x", name],
                          stdout=subprocess.DEVNULL).returncode == 0


def kill_mpvpaper():
    """pkill no espera: sin esta espera el video viejo queda encima del
    fondo nuevo."""
    if not _alive("mpvpaper"):
        return
    subprocess.run(["pkill", "-x", "mpvpaper"])
    for _ in range(20):
        if not _alive("mpvpaper"):
            return
        time.sleep(0.1)
    subprocess.run(["pkill", "-9", "-x", "mpvpaper"])


def ensure_awww_daemon():
    if subprocess.run(["awww", "query"], capture_output=True).returncode == 0:
        return
    if not _alive("awww-daemon"):
        subprocess.Popen(["awww-daemon"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        if subprocess.run(["awww", "query"], capture_output=True).returncode == 0:
            return
        time.sleep(0.1)


def show_video(path):
    subprocess.Popen(["mpvpaper", "--fork", "-o", MPV_OPTIONS, "*", path],
                     start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show_image(path):
    subprocess.run(["awww", "img", path, *AWWW_OPTIONS], check=False)


def apply(path):
    """Muestra `path` sin guardarlo."""
    kill_mpvpaper()
    ensure_awww_daemon()
    (show_video if is_video(path) else show_image)(path)


def freeze_active():
    try:
        out = subprocess.run(["hyprctl", "workspaces", "-j"], capture_output=True,
                             text=True, timeout=2).stdout
        return any(w.get("name") in FREEZE_WORKSPACES for w in json.loads(out))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def show_frozen(path):
    """Video → un frame como imagen; gif → sin transición y pausado
    (pausar a media transición lo dejaría congelado a medias)."""
    kill_mpvpaper()
    ensure_awww_daemon()
    # Pausado, awww acepta el `img` pero no avanza la transición: el
    # fondo nuevo nunca se veía (el gif de antes queda pausado por freeze).
    subprocess.run(["awww", "unpause"], check=False)
    if is_video(path):
        subprocess.run(["ffmpegthumbnailer", "-i", path, "-o", FROZEN_FRAME, "-s", "0"], check=False)
        subprocess.run(["awww", "img", FROZEN_FRAME], check=False)
    elif is_gif(path):
        subprocess.run(["awww", "img", path, "--resize", "crop", "--transition-type", "none"], check=False)
        subprocess.run(["awww", "pause"], check=False)
    else:
        show_image(path)


def freeze():
    path = current_path()
    if path and is_video(path) and os.path.isfile(path):
        show_frozen(path)
    elif path and is_gif(path):
        subprocess.run(["awww", "pause"], check=False)
    open(FROZEN_MARKER, "w").close()


def thaw():
    if os.path.exists(FROZEN_MARKER):
        os.remove(FROZEN_MARKER)
    path = current_path()
    if path and is_video(path) and os.path.isfile(path):
        apply(path)
    else:
        subprocess.run(["awww", "unpause"], check=False)


def set_wallpaper(path):
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        # Tema copiado de otra máquina sin su fondo.
        path = DEMO_WALLPAPER
    (show_frozen if freeze_active() else apply)(path)
    common.atomic_write(STATE_FILE, json.dumps({"path": _collapse_home(path)}) + "\n")


def restore():
    path = current_path()
    if path and os.path.isfile(path):
        apply(path)
    elif path:
        apply(DEMO_WALLPAPER)
    else:
        ensure_awww_daemon()


def main():
    args = sys.argv[1:]
    if args[:1] == ["set"] and len(args) == 2:
        set_wallpaper(args[1])
    elif args == ["restore"]:
        restore()
    elif args == ["current"]:
        print(current_path(expand=False) or "")
    elif args == ["freeze"]:
        freeze()
    elif args == ["thaw"]:
        thaw()
    else:
        sys.exit("uso: wallpaper.py set <ruta> | restore | current | freeze | thaw")


if __name__ == "__main__":
    main()

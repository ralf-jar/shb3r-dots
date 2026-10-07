"""Temas de cursor de los repositorios oficiales para "Obtener más" del
selector de cursores (cursor_picker.py). Sin GTK.

Sin base de archivos de pacman (`pacman -Fy` pide root), así que la lista
de paquetes es fija. Vista previa sin instalar: `pacman -Sp` da la URL del
paquete (sin root), se baja a la caché y se extrae solo
usr/share/icons/ -- pesan entre 17 KB y 1.5 MB."""

import os
import shutil
import subprocess
import urllib.request

import look_settings

PACKAGES = (
    "capitaine-cursors",
    "breeze-cursors",
    "oxygen-cursors",
    "vimix-cursors",
    "dracula-cursors-git",
    "xcursor-vanilla-dmz",
    "xcursor-vanilla-dmz-aa",
    "xcursor-comix",
    "xcursor-themes",
    "xcursor-neutral",
)
CACHE_DIR = os.path.expanduser("~/.cache/flipfrog-cursor-previews")
# Algunos espejos de CachyOS responden 403 a cualquier cliente que no
# se presente como pacman.
USER_AGENT = "pacman/7.0.0 (Linux x86_64) libalpm/15.0.0"
TIMEOUT = 10
DOWNLOAD_TIMEOUT = 60
INSTALL_TIMEOUT = 300


def _run(cmd, timeout=TIMEOUT):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None


def not_installed():
    """Paquetes de PACKAGES que no están instalados (`pacman -Q` falla con
    los que faltan; los que no existen en los repos se descartan después,
    al pedir su URL)."""
    result = _run(["pacman", "-Qq", *PACKAGES])
    installed = set(result.stdout.split()) if result else set()
    return [pkg for pkg in PACKAGES if pkg not in installed]


def preview_dir(pkg):
    """Carpeta con los usr/share/icons/<tema>/ del paquete extraídos, o None
    si no está en los repos o no se pudo bajar. Caché por nombre de
    archivo (incluye la versión)."""
    result = _run(["pacman", "-Sp", "--print-format", "%l", pkg])
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    url = result.stdout.strip().splitlines()[0]
    filename = os.path.basename(url)
    target = os.path.join(CACHE_DIR, filename.split(".pkg.tar")[0])
    icons = os.path.join(target, "usr", "share", "icons")
    if os.path.isdir(icons):
        return icons

    os.makedirs(CACHE_DIR, exist_ok=True)
    archive = os.path.join(CACHE_DIR, filename)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response, \
                open(archive + ".part", "wb") as f:
            shutil.copyfileobj(response, f)
        os.replace(archive + ".part", archive)
        os.makedirs(target, exist_ok=True)
        extracted = _run(["bsdtar", "-xf", archive, "-C", target, "usr/share/icons/*"])
    except OSError:
        extracted = None
    finally:
        for leftover in (archive, archive + ".part"):
            if os.path.exists(leftover):
                os.remove(leftover)
    if extracted is None or extracted.returncode != 0 or not os.path.isdir(icons):
        shutil.rmtree(target, ignore_errors=True)
        return None
    return icons


def package_themes(icons):
    """Temas con cursors/ dentro de la carpeta extraída de un paquete."""
    return sorted((name for name in os.listdir(icons)
                   if os.path.isdir(os.path.join(icons, name, "cursors"))), key=str.lower)


def preview_bases(icons):
    """La carpeta del paquete primero y luego las del sistema: un tema puede
    heredar (Inherits=) de uno ya instalado."""
    return [icons, *look_settings.CURSOR_DIRS]


def install(pkg):
    """`pkexec pacman -S`: contraseña por el agente de polkit
    (hyprpolkitagent). True si quedó instalado."""
    result = _run(["pkexec", "pacman", "-S", "--needed", "--noconfirm", pkg], INSTALL_TIMEOUT)
    return result is not None and result.returncode == 0

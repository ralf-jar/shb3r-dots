"""Temas de cursor e íconos de los repositorios oficiales para "Obtener
cursores" / "Obtener íconos". Sin GTK.

Sin base de archivos de pacman (`pacman -Fy` pide root), así que la lista
de paquetes es fija (theme_kinds.py). Vista previa sin instalar: `pacman
-Sp` da la URL del paquete (sin root), se baja a la caché y se extrae solo
usr/share/icons/."""

import os
import shutil
import subprocess
import urllib.request

# Algunos espejos de CachyOS responden 403 a cualquier cliente que no
# se presente como pacman.
USER_AGENT = "pacman/7.0.0 (Linux x86_64) libalpm/15.0.0"
TIMEOUT = 10
DOWNLOAD_TIMEOUT = 300
INSTALL_TIMEOUT = 600


def _run(cmd, timeout=TIMEOUT):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None


def not_installed(kind):
    """Paquetes del `kind` que no están instalados (`pacman -Q` falla con
    los que faltan; los que no existen en los repos se descartan después)."""
    result = _run(["pacman", "-Qq", *kind.repo_packages])
    installed = set(result.stdout.split()) if result else set()
    return [pkg for pkg in kind.repo_packages if pkg not in installed]


def download_sizes(pkgs):
    """{paquete: "21.76 MiB"} de `pacman -Si`; los que no están en los
    repos no aparecen."""
    result = _run(["pacman", "-Si", *pkgs])
    sizes, name = {}, None
    for line in (result.stdout.splitlines() if result else []):
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "Name":
            name = value.strip()
        elif key == "Download Size" and name and name not in sizes:
            sizes[name] = value.strip()
    return sizes


def preview_dir(kind, pkg, download=True):
    """Carpeta con los usr/share/icons/<tema>/ del paquete extraídos, o None
    si no está en los repos, no se pudo bajar o (download=False) todavía
    no se bajó. Caché por nombre de archivo (incluye la versión)."""
    result = _run(["pacman", "-Sp", "--print-format", "%l", pkg])
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    url = result.stdout.strip().splitlines()[0]
    filename = os.path.basename(url)
    target = os.path.join(kind.repo_cache, filename.split(".pkg.tar")[0])
    icons = os.path.join(target, "usr", "share", "icons")
    if os.path.isdir(icons) or not download:
        return icons if os.path.isdir(icons) else None

    os.makedirs(kind.repo_cache, exist_ok=True)
    archive = os.path.join(kind.repo_cache, filename)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response, \
                open(archive + ".part", "wb") as f:
            shutil.copyfileobj(response, f)
        os.replace(archive + ".part", archive)
        os.makedirs(target, exist_ok=True)
        extracted = _run(["bsdtar", "-xf", archive, "-C", target, "usr/share/icons/*"], DOWNLOAD_TIMEOUT)
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


def package_themes(kind, icons):
    """{tema: carpeta} dentro de la carpeta extraída de un paquete."""
    themes = kind.find_themes(icons)
    return {name: themes[name] for name in sorted(themes, key=str.lower)}


def install(pkg):
    """`pkexec pacman -S`: contraseña por el agente de polkit
    (hyprpolkitagent). True si quedó instalado."""
    result = _run(["pkexec", "pacman", "-S", "--needed", "--noconfirm", pkg], INSTALL_TIMEOUT)
    return result is not None and result.returncode == 0

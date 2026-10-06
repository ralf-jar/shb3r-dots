#!/usr/bin/env python3
"""Genera un .desktop por cada juego instalado de Steam para que
workspace-taskbar le encuentre ícono. Corre una vez; cada 3 h lo
relanza un timer transitorio de systemd --user armado en autostart.lua. Solo agrega juegos nuevos,
nunca pisa uno existente. Ver CLAUDE.md "Íconos de apps en
escritorios" para el orden de prioridad de íconos."""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

PROCESS_NAME = "ff-steam-sync"

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common

APPLICATIONS_DIR = os.path.expanduser("~/.local/share/applications")
HICOLOR_APPS_DIR = os.path.expanduser("~/.local/share/icons/hicolor/128x128/apps")
STEAM_DIR = os.path.expanduser("~/.local/share/Steam")

# Carpetas de redistribuibles/instaladores a excluir al buscar el .exe
# principal (VC++, DirectX, .NET, el propio instalador).
REDIST_RE = re.compile(r"(?i)(_commonredist|redist|directx|vcredist|dotnet|__installer|_CommonRedist)")

# Ejecutables conocidos que NO son el juego (anti-cheat, crash
# reporters, helpers de Unreal/Epic) -- excluidos por nombre, no solo
# por carpeta.
KNOWN_NON_GAME_EXE_RE = re.compile(
    r"(?i)(easyanticheat|battleye|start_protected_game|crashreport|"
    r"epicwebhelper|prereqsetup|ueprereq|vc_redist|unins)"
)


def library_paths():
    """Todas las bibliotecas de Steam -- no asume que están todas en
    la carpeta default, puede haber más de un disco agregado."""
    vdf = os.path.join(STEAM_DIR, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf) as f:
            content = f.read()
    except FileNotFoundError:
        return [STEAM_DIR]
    paths = re.findall(r'"path"\s+"([^"]+)"', content)
    return paths or [STEAM_DIR]


def installed_games():
    """{appid: {"name", "installdir", "library"}}. Incluye también
    runtimes/redistribuibles de Steam (sin flag limpio para
    distinguirlos) -- inofensivo, nunca matchean una ventana real."""
    games = {}
    for lib in library_paths():
        steamapps = os.path.join(lib, "steamapps")
        for manifest in glob.glob(os.path.join(steamapps, "appmanifest_*.acf")):
            try:
                with open(manifest, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except OSError:
                continue
            appid_m = re.search(r'"appid"\s+"(\d+)"', content)
            name_m = re.search(r'"name"\s+"([^"]+)"', content)
            installdir_m = re.search(r'"installdir"\s+"([^"]+)"', content)
            if not (appid_m and name_m and installdir_m):
                continue
            games[appid_m.group(1)] = {
                "name": name_m.group(1),
                "installdir": installdir_m.group(1),
                "library": lib,
            }
    return games


def papirus_icon(appid):
    matches = glob.glob(f"/usr/share/icons/Papirus*/*/apps/steam_icon_{appid}.*")
    return f"steam_icon_{appid}" if matches else None


def steam_cached_icon(appid):
    matches = glob.glob(os.path.join(
        os.path.expanduser("~/.local/share/icons/hicolor"), "*", "apps", f"steam_icon_{appid}.png"
    ))
    return f"steam_icon_{appid}" if matches else None


def find_main_exe(library, installdir):
    """Heurística: el .exe más pesado del árbol, salvo REDIST_RE/
    KNOWN_NON_GAME_EXE_RE -- el binario real del motor pesa cientos de
    MB, muy por encima de launchers/anti-cheat. (Un intento anterior
    priorizaba menor profundidad de carpetas, pero eso hacía ganar al
    stub de EasyAntiCheat en la raíz sobre el binario real de Unreal
    Engine varias carpetas adentro -- caso real, SMITE 2.)"""
    base = os.path.join(library, "steamapps", "common", installdir)
    if not os.path.isdir(base):
        return None
    candidates = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not REDIST_RE.search(d)]
        for fn in files:
            if (
                fn.lower().endswith(".exe")
                and not REDIST_RE.search(fn)
                and not KNOWN_NON_GAME_EXE_RE.search(fn)
            ):
                full = os.path.join(root, fn)
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                candidates.append((size, full))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _square_and_save(im, appid):
    from PIL import Image
    im = im.convert("RGBA")
    size = max(im.size)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2), im)
    canvas = canvas.resize((128, 128), Image.LANCZOS)
    os.makedirs(HICOLOR_APPS_DIR, exist_ok=True)
    dest = os.path.join(HICOLOR_APPS_DIR, f"steam_icon_{appid}.png")
    canvas.save(dest)
    return f"steam_icon_{appid}"


def extract_exe_icon(appid, exe_path):
    """wrestool + icotool (icoutils) -- ícono real embebido en el exe.
    Se salta solo si icoutils no está instalado o el exe no trae uno."""
    if not (shutil.which("wrestool") and shutil.which("icotool")):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        ico_path = os.path.join(tmp, "icon.ico")
        r = subprocess.run(
            ["wrestool", "-x", "-t", "14", "-o", ico_path, exe_path],
            capture_output=True,
        )
        if r.returncode != 0 or not os.path.exists(ico_path):
            return None
        r = subprocess.run(["icotool", "-x", "-o", tmp, ico_path], capture_output=True)
        if r.returncode != 0:
            return None
        pngs = glob.glob(os.path.join(tmp, "*.png"))
        if not pngs:
            return None

        def png_size(p):
            m = re.search(r"_(\d+)x(\d+)x", os.path.basename(p))
            return int(m.group(1)) if m else 0

        best = max(pngs, key=png_size)
        from PIL import Image
        return _square_and_save(Image.open(best), appid)


def cropped_fallback_icon(appid, library):
    """Último recurso: recorta a cuadrado el logo/portada que Steam
    cachea en librarycache -- peor en logos panorámicos, mejor que el
    genérico."""
    cache_dir = os.path.join(library, "appcache", "librarycache", appid)
    for fname in ("logo.png", "library_600x900.jpg"):
        src = os.path.join(cache_dir, fname)
        if not os.path.exists(src):
            continue
        from PIL import Image
        return _square_and_save(Image.open(src), appid)
    return None


def resolve_icon(appid, game):
    return (
        papirus_icon(appid)
        or steam_cached_icon(appid)
        or _try_exe_icon(appid, game)
        or cropped_fallback_icon(appid, game["library"])
        or "steam"
    )


def _try_exe_icon(appid, game):
    exe = find_main_exe(game["library"], game["installdir"])
    return extract_exe_icon(appid, exe) if exe else None


def write_desktop_entry(appid, game, icon):
    dest = os.path.join(APPLICATIONS_DIR, f"steam_app_{appid}.desktop")
    content = (
        "[Desktop Entry]\n"
        f"Name={game['name']}\n"
        "Comment=Play this game on Steam\n"
        f"Exec=steam steam://rungameid/{appid}\n"
        f"Icon={icon}\n"
        f"StartupWMClass=steam_app_{appid}\n"
        "Terminal=false\n"
        "Type=Application\n"
        "Categories=Game;\n"
    )
    common.atomic_write(dest, content)


def sync_once():
    os.makedirs(APPLICATIONS_DIR, exist_ok=True)
    added = []
    for appid, game in installed_games().items():
        dest = os.path.join(APPLICATIONS_DIR, f"steam_app_{appid}.desktop")
        if os.path.exists(dest):
            continue
        icon = resolve_icon(appid, game)
        write_desktop_entry(appid, game, icon)
        added.append(game["name"])
    if added:
        print("Nuevos juegos agregados:", ", ".join(added), file=sys.stderr)
    return added


def main():
    common.set_process_name(PROCESS_NAME)
    sync_once()


if __name__ == "__main__":
    main()

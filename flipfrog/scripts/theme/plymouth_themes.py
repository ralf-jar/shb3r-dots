"""Temas de la pantalla de arranque (Plymouth) para boot_picker.py. Sin GTK.

El tema va dentro del initramfs: aplicarlo es `plymouth-set-default-theme
-R`, que también lo reconstruye (limine-mkinitcpio / mkinitcpio -P, según
plymouth-update-initrd), con pkexec. Los de la KDE Store se copian a
THEMES_DIR con un marcador para poder quitarlos sin tocar los del sistema."""

import configparser
import os
import re
import shutil
import subprocess

THEMES_DIR = "/usr/share/plymouth/themes"
MODULES_DIR = "/usr/lib/plymouth"
SET_THEME = "plymouth-set-default-theme"
MARKER = ".flipfrog-store"
SAFE_NAME_RE = re.compile(r"^[\w.+-]+$")
FRAME_RE = re.compile(r"^(.*?)[-_]?(\d+)\.png$", re.I)
SCRIPT_BG_RE = re.compile(r"SetBackgroundTopColor\s*\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)")
STILL_NAMES = ("watermark", "logo", "header-image", "header")
CONTROL_NAMES = ("box", "bullet", "entry", "lock", "capslock", "keyboard", "keymap-render", "progress_bar",
                 "progress_box", "star", "background", "bg", "wallpaper")
MIN_STILL_BYTES = 2048
MAX_FRAMES = 48
TIMEOUT = 10
AUTH_TIMEOUT = 120
# Reconstruir el initramfs de todos los kernels tarda.
APPLY_TIMEOUT = 900


def available():
    return shutil.which(SET_THEME) is not None and os.path.isdir(THEMES_DIR)


def _parser(path):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read(path)
    except configparser.Error:
        pass
    return parser


def theme_dir(theme):
    return os.path.join(THEMES_DIR, theme)


def config(path, theme):
    return _parser(os.path.join(path, theme + ".plymouth"))


def current():
    try:
        result = subprocess.run([SET_THEME], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip()


def installed():
    try:
        names = os.listdir(THEMES_DIR)
    except OSError:
        return []
    return sorted((n for n in names if os.path.isfile(os.path.join(THEMES_DIR, n, n + ".plymouth"))),
                  key=str.lower)


def missing_module(path, theme):
    """El plugin (ModuleName) si no está instalado: sin él plymouth cae al
    modo texto."""
    module = config(path, theme).get("Plymouth Theme", "ModuleName", fallback="").strip()
    return module if module and not os.path.isfile(os.path.join(MODULES_DIR, module + ".so")) else None


def _image_dir(path, theme, parser):
    """ImageDir apunta a la ruta instalada; para un tema sin instalar (en la
    caché) las imágenes están en su propia carpeta."""
    module = parser.get("Plymouth Theme", "ModuleName", fallback="")
    image_dir = parser.get(module, "ImageDir", fallback="").strip() if parser.has_section(module) else ""
    return image_dir if image_dir and os.path.isdir(image_dir) and path.startswith(THEMES_DIR) else path


def _background(path, theme, parser, module):
    """(r, g, b) en 0-1: BackgroundStartColor de two-step o el
    SetBackgroundTopColor del script; negro si no dice."""
    if parser.has_option(module, "BackgroundStartColor"):
        try:
            value = int(parser.get(module, "BackgroundStartColor").strip(), 16)
            return ((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255
        except ValueError:
            pass
    script = parser.get("script", "ScriptFile", fallback="") if parser.has_section("script") else ""
    for candidate in (os.path.join(path, os.path.basename(script)) if script else "",
                      os.path.join(path, theme + ".script")):
        try:
            with open(candidate, encoding="utf-8", errors="replace") as f:
                match = SCRIPT_BG_RE.search(f.read())
        except OSError:
            continue
        if match:
            return tuple(min(float(v), 1.0) for v in match.groups())
    return 0.0, 0.0, 0.0


def preview(path, theme):
    """{"background": (r, g, b), "frames": [png...], "backdrop": png|None,
    "still": png|None, "watermark": png|None, "align"/"watermark_align":
    (h, v) en 0-1 (two-step), "firmware": usa el logo del fabricante}. Frames = la secuencia numerada más larga
    (animation-NN, throbber-NN, progress-NN), hasta MAX_FRAMES; sin
    secuencia, una imagen fija (logo/watermark)."""
    parser = config(path, theme)
    module = parser.get("Plymouth Theme", "ModuleName", fallback="")
    images = _image_dir(path, theme, parser)
    try:
        pngs = sorted(f for f in os.listdir(images) if f.lower().endswith(".png"))
    except OSError:
        pngs = []

    groups = {}
    for name in pngs:
        match = FRAME_RE.match(name)
        if match:
            groups.setdefault(match.group(1).lower(), []).append((int(match.group(2)), name))
    frames = []
    if groups:
        best = max(groups.values(), key=len)
        if len(best) > 1:
            best.sort()
            step = max(1, len(best) // MAX_FRAMES)
            frames = [os.path.join(images, name) for _n, name in best[::step][:MAX_FRAMES]]

    lower = {name.lower(): name for name in pngs}
    backdrop = next((os.path.join(images, lower[n]) for n in ("background.png", "bg.png", "wallpaper.png")
                     if n in lower), None)
    still = None
    if not frames:
        still = next((os.path.join(images, name) for name in pngs
                      if os.path.splitext(name)[0].lower() in STILL_NAMES), None)
    if not frames and still is None:
        # Sin nombre conocido: la imagen más pesada que no sea un control
        # (caja de contraseña, viñetas, candado).
        sizes = [(os.path.getsize(os.path.join(images, name)), name) for name in pngs
                 if os.path.splitext(name)[0].lower() not in CONTROL_NAMES]
        sizes = [entry for entry in sizes if entry[0] >= MIN_STILL_BYTES]
        if sizes:
            still = os.path.join(images, max(sizes)[1])
    watermark = next((os.path.join(images, lower[n]) for n in ("watermark.png",) if n in lower), None)
    if still == watermark:
        still = None

    def align(prefix, default_v=0.5):
        h = parser.get(module, prefix + "HorizontalAlignment", fallback="0.5") if module else "0.5"
        v = parser.get(module, prefix + "VerticalAlignment", fallback=str(default_v)) if module else default_v
        try:
            return float(h), float(v)
        except ValueError:
            return 0.5, default_v

    firmware = any(parser.get(section, "UseFirmwareBackground", fallback="").strip().lower() == "true"
                   for section in ("boot-up", module) if section and parser.has_section(section))
    return {"background": _background(path, theme, parser, module), "frames": frames,
            "backdrop": backdrop, "still": still, "watermark": watermark,
            "align": align(""), "watermark_align": align("Watermark", 0.96), "firmware": firmware}


def installed_from_store(theme):
    return os.path.isfile(os.path.join(THEMES_DIR, theme, MARKER))


def store_ids():
    ids = set()
    for theme in installed():
        try:
            with open(os.path.join(THEMES_DIR, theme, MARKER)) as f:
                ids.add(f.read().strip())
        except OSError:
            continue
    return ids


def _pkexec(script, *args, timeout=AUTH_TIMEOUT):
    try:
        result = subprocess.run(["pkexec", "sh", "-c", script, "sh", *args],
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def apply(theme):
    """Elige el tema y reconstruye el initramfs. True si quedó activo."""
    if not SAFE_NAME_RE.match(theme):
        return False
    return _pkexec(f'{SET_THEME} -R "$1"', theme, timeout=APPLY_TIMEOUT) and current() == theme


def _drop_outside_links(root):
    """Contenido de terceros: un symlink que sale de su carpeta dejaría
    leer cualquier archivo desde la carpeta del sistema (y meterlo al
    initramfs)."""
    real_root = os.path.realpath(root)
    for dirpath, dirnames, files in os.walk(root):
        for name in dirnames + files:
            path = os.path.join(dirpath, name)
            if os.path.islink(path) and os.path.commonpath(
                    [real_root, os.path.realpath(path)]) != real_root:
                os.unlink(path)


def install(themes, item_id):
    """Copia cada tema a THEMES_DIR/<nombre del .plymouth> con el
    marcador, en una sola llamada a pkexec. Lista de temas instalados, o
    None si falló."""
    pairs = []
    for name, path in themes.items():
        if SAFE_NAME_RE.match(name) and not os.path.exists(theme_dir(name)):
            _drop_outside_links(path)
            pairs += [path, name]
    if not pairs:
        return []
    script = ('set -e; dir=$1; id=$2; shift 2; while [ $# -gt 1 ]; do '
              'cp -r -- "$1" "$dir/$2"; printf %s "$id" > "$dir/$2/' + MARKER + '"; '
              'chmod -R u=rwX,go=rX "$dir/$2"; shift 2; done')
    if not _pkexec(script, THEMES_DIR, item_id, *pairs):
        return None
    return pairs[1::2]


def uninstall(theme):
    """Solo temas instalados desde aquí (con marcador)."""
    if not SAFE_NAME_RE.match(theme) or not installed_from_store(theme):
        return False
    return _pkexec('[ -f "$1/' + MARKER + '" ] && rm -rf -- "$1"', theme_dir(theme))

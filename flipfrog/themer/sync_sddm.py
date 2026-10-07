#!/usr/bin/env python3
"""Tema de SDDM (flipfrog/sddm/flipfrog): copia el fondo y escribe colores,
redondeo y textos del tema activo en /var/lib/flipfrog-sddm/. SDDM corre con
su propio usuario y no puede leer ~/ -- install.sh crea esa carpeta con este
usuario como dueño y enlaza ahí el theme.conf.user del tema. Sin la carpeta
(SDDM no instalado o tema sin instalar) no hace nada."""

import json
import os
import subprocess
import sys
import tempfile

THEMER_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(THEMER_DIR, "..", "scripts"))
import common
from i18n import current_lang, t
import wallpaper

STATE_DIR = "/var/lib/flipfrog-sddm"
BACKGROUND = os.path.join(STATE_DIR, "background.jpg")
CONF = os.path.join(STATE_DIR, "theme.conf.user")
RADIUS_FILE = os.path.join(THEMER_DIR, "corner-radius.json")
MAX_WIDTH = 2560
LOCALES = {"es-MX": "es_MX", "en": "en_US"}


def argb(name, alpha=None):
    r, g, b, a = common.theme_color_rgba(name)
    a = a if alpha is None else alpha
    return "#{:02x}{:02x}{:02x}{:02x}".format(round(a * 255), round(r), round(g), round(b))


def radius():
    try:
        with open(RADIUS_FILE) as f:
            return int(json.load(f).get("radius", 4))
    except (OSError, ValueError):
        return 4


def copy_background():
    """Un JPEG del fondo actual: frame de un video, primer cuadro de un gif,
    reducido a MAX_WIDTH. Devuelve la ruta o "" si no hay fondo."""
    path = wallpaper.current_path()
    if not path or not os.path.isfile(path):
        path = wallpaper.DEMO_WALLPAPER
    from PIL import Image
    with tempfile.TemporaryDirectory() as tmp:
        if wallpaper.is_video(path):
            frame = os.path.join(tmp, "frame.png")
            subprocess.run(["ffmpegthumbnailer", "-i", path, "-o", frame, "-s", "0"],
                           capture_output=True, timeout=30)
            path = frame
        try:
            img = Image.open(path)
            img.seek(0)
            img = img.convert("RGB")
        except (OSError, EOFError):
            return ""
        if img.width > MAX_WIDTH:
            img = img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
        fd, out = tempfile.mkstemp(dir=STATE_DIR, suffix=".jpg")
        os.close(fd)
        img.save(out, "JPEG", quality=90)
        os.chmod(out, 0o644)
        os.replace(out, BACKGROUND)
    return BACKGROUND


def main():
    if not os.access(STATE_DIR, os.W_OK):
        return
    lines = [
        "[General]",
        f"background={copy_background()}",
        f"backgroundColor={argb('mybackgroundhover', 1.0)}",
        f"panelColor={argb('mybackground')}",
        f"hoverColor={argb('mybackgroundhover')}",
        f"foreground={argb('myforeground')}",
        f"accent={argb('myforegroundhover')}",
        f"accent2={argb('myforegroundhover2')}",
        f"border={argb('myborders')}",
        f"radius={radius()}",
        f"locale={LOCALES.get(current_lang(), 'es_MX')}",
        f"dateFormat={t('sddm', 'formato_fecha')}",
        f"textPassword={t('sddm', 'contrasena')}",
        f"textLoginFailed={t('sddm', 'contrasena_incorrecta')}",
        f"textCapsLock={t('sddm', 'mayusculas')}",
        f"textSuspend={t('sddm', 'suspender')}",
        f"textReboot={t('sddm', 'reiniciar')}",
        f"textPowerOff={t('sddm', 'apagar')}",
    ]
    common.atomic_write(CONF, "\n".join(lines) + "\n")
    os.chmod(CONF, 0o644)


if __name__ == "__main__":
    main()

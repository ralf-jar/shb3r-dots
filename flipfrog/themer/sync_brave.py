#!/usr/bin/env python3
"""Genera brave/theme/manifest.json (extensión de tema de Chromium,
cargada desempaquetada en brave://extensions) desde colors.css.

Brave solo lee el manifest al arrancar: probado (2026-09-25) que
chrome.management.setEnabled(false/true) desde otra extensión reaplica
el tema en memoria sin releer el archivo, y no hay API de extensión
para colores de la interfaz. El cambio se ve al reiniciar Brave, siempre
que no exista `Cached Theme.pak` (ver main())."""
import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.config/flipfrog/scripts"))
import common

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(THEMER_DIR, "brave", "theme", "manifest.json")
CACHED_PACK = os.path.join(THEMER_DIR, "brave", "theme", "Cached Theme.pak")
KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA1F7Q3xjQeERV7mXAcjDraAMHXwfj7gjt1mr9fcP/PGiEETaCjOoO6yOv50DZpJ/qcptBK1J8XVydZfMAKV6mp+WcBbDj6K2s94MqBggHa5oEYPcvvVWdH9Os3BostwAMlfPp91bIfNQiTmOuwa2UcZfrlw7atSzbs3uQm2YCXmLvUurJEGdFrgw+H2zJz0F6VZ6Tw1WG9kQly02Buj+wyljLvQZ0C4SXssr1gFF6aqS6rW3PE8xUz9ZhR2YdcvX4vbU8V3QxKdCNRhcuHZgQCVK7xb8KwfpE4tTOJU1VSh15tE15GjUjR0bPNccSNy1xJFmr/jK+rbSsH5yojXmM3QIDAQAB"

# Chromium ignora el alpha: se compone sobre negro.
TARGETS = {
    "frame": "mybackground",
    "frame_inactive": "mybackground",
    "frame_incognito": "mybackground",
    "frame_incognito_inactive": "mybackground",
    "toolbar": "mybackgroundhover",
    "tab_text": "myforeground",
    "tab_background_text": "myforegroundhover2",
    "tab_background_text_inactive": "myborderinactive",
    "bookmark_text": "myforeground",
    "toolbar_text": "myforeground",
    "toolbar_button_icon": "myforegroundhover",
    "omnibox_background": "mybackground",
    "omnibox_text": "myforeground",
    "ntp_background": "mybackground",
    "ntp_text": "myforeground",
    "ntp_link": "myforegroundhover",
}


def rgb(name, css):
    r, g, b, a = common.theme_color_rgba(name, css)
    return [round(r * a), round(g * a), round(b * a)]


def build(css):
    return {
        "manifest_version": 3,
        "name": "flipfrog",
        "version": "1.0",
        "key": KEY,
        "theme": {"colors": {k: rgb(v, css) for k, v in TARGETS.items()}},
    }


def main():
    with open(common.COLORS_CSS) as f:
        css = f.read()
    content = json.dumps(build(css), indent=2) + "\n"
    try:
        with open(MANIFEST) as f:
            if f.read() == content:
                return
    except FileNotFoundError:
        pass
    common.atomic_write(MANIFEST, content)

    # Brave arranca desde este caché si existe, sin releer el manifest.
    try:
        os.remove(CACHED_PACK)
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    main()

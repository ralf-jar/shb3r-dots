#!/usr/bin/env python3
"""Sincroniza el color de fondo de Zen Browser con `mybackground` de
colors.css -- mismo objetivo que sync_kitty.py/sync_vesktop.py/
sync_millennium.py, vía el mod "Transparent Zen" (ZenMods, autor
@sameerasw) que ya venía instalado en este perfil
(`chrome/zen-themes/`, generado en `chrome/zen-themes.css`).

Ese mod expone su color de fondo como DOS preferencias comunes de
Firefox/Zen (`about:config`, persistidas en `prefs.js` del perfil):
    mod.sameerasw.zen_bg_color_enabled  (bool)
    mod.sameerasw.zen_transparency_color  ("#rrggbbaa", CON alpha)
El propio `zen-themes.css` (generado, "NO EDITAR" -- el mod lo
regenera) lee esa preferencia vía la sintaxis `@media -moz-pref(...)`
de Firefox y la usa como valor de `--zen-main-browser-background`. A
diferencia de Millennium/Vesktop, el formato SÍ soporta alpha (8 dígitos
hex), así que este script -a diferencia de sync_kitty.py/sync_vesktop.py/
sync_millennium.py- síi manda el alpha de `mybackground`, no solo el
RGB.

Investigación sobre "refresh en caliente" (misma pregunta que ya se hizo
para Vesktop y Steam): probado en vivo, editando `prefs.js` con Zen
corriendo y SIN reiniciarlo -- cero efecto visual (captura de pantalla
antes/después idéntica). Firefox/Zen solo lee `prefs.js` al arrancar el
perfil; mientras corre, su propio `nsIPrefBranch` en memoria es la
fuente de verdad y es ese estado en memoria el que se vuelca de nuevo a
`prefs.js` en cada checkpoint periódico y al cerrar -- si Zen sigue
abierto un buen rato después de correr este script (no solo al cerrarlo
manualmente), ese autoguardado periódico puede pisar nuestro valor nuevo
con el viejo que todavía tiene en memoria. A diferencia de Vesktop/
Millennium (que solo escriben su config ante una acción explícita del
usuario en la UI), aquí el riesgo de que el cambio se pierda si tarda en
reiniciarse es más alto -- conviene cerrar y volver a abrir Zen pronto
después de cambiar de tema, no solo "la próxima vez que se te ocurra".

Igual que con Vesktop: existe (en teoría) una vía para aplicar esto en
caliente -- Firefox soporta su propio protocolo remoto (Remote Agent,
`--remote-debugging-port`) desde el que se podría escribir la preferencia
directo en el proceso vivo y aprovechar que `-moz-pref()` sí es reactivo
de fábrica (a diferencia de Steam/Millennium, que no tienen ningún
mecanismo reactivo ni con acceso directo). No se implementó: exponer ese
puerto en un navegador (con acceso a cookies, contraseñas guardadas,
sesiones) es un riesgo de seguridad mayor que hacerlo en Discord o Steam,
mismo motivo ya descartado en sync_vesktop.py.

Edición por reemplazo de línea (regex), no reescritura completa: prefs.js
no es JSON, es una secuencia de `user_pref(...)` -- se preservan todas
las demás (cientos) tal cual, mismo criterio que sync_kitty.py con
kitty.conf. Escritura atómica (temporal + os.replace()).
"""
import configparser
import os
import re
import sys

sys.path.insert(0, os.path.expanduser("~/.config/flipfrog/scripts"))
import common

THEMER_DIR = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(THEMER_DIR, "colors.css")
ZEN_CONFIG_DIR = os.path.expanduser("~/.config/zen")
PROFILES_INI_PATH = os.path.join(ZEN_CONFIG_DIR, "profiles.ini")

DEFINE_COLOR_RE = re.compile(r"@define-color\s+([\w-]+)\s+(rgba?)\(([^)]+)\)")

ENABLED_PREF_RE = re.compile(
    r'user_pref\("mod\.sameerasw\.zen_bg_color_enabled",\s*\w+\);'
)
COLOR_PREF_RE = re.compile(
    r'user_pref\("mod\.sameerasw\.zen_transparency_color",\s*"[^"]*"\);'
)


def parse_colors(css_content):
    colors = {}
    for match in DEFINE_COLOR_RE.finditer(css_content):
        name, kind, raw_values = match.groups()
        parts = [float(p.strip()) for p in raw_values.split(",")]
        r, g, b = parts[0], parts[1], parts[2]
        a = parts[3] if kind == "rgba" and len(parts) > 3 else 1.0
        colors[name] = (round(r), round(g), round(b), a)
    return colors


def default_profile_path():
    if not os.path.exists(PROFILES_INI_PATH):
        return None
    config = configparser.ConfigParser()
    config.read(PROFILES_INI_PATH)

    for section in config.sections():
        if section.startswith("Install") and config.has_option(section, "Default"):
            return os.path.join(ZEN_CONFIG_DIR, config.get(section, "Default"))

    for section in config.sections():
        if section.startswith("Profile") and config.get(section, "Default", fallback="0") == "1":
            return os.path.join(ZEN_CONFIG_DIR, config.get(section, "Path"))

    return None


def sync_zen(r, g, b, a):
    profile_path = default_profile_path()
    if not profile_path:
        return
    prefs_path = os.path.join(profile_path, "prefs.js")
    try:
        with open(prefs_path) as f:
            content = f.read()
    except FileNotFoundError:
        return

    alpha_hex = format(round(a * 255), "02x")
    hex_color = f"#{r:02x}{g:02x}{b:02x}{alpha_hex}"

    new_enabled_line = 'user_pref("mod.sameerasw.zen_bg_color_enabled", true);'
    new_color_line = f'user_pref("mod.sameerasw.zen_transparency_color", "{hex_color}");'

    if not ENABLED_PREF_RE.search(content) or not COLOR_PREF_RE.search(content):
        return

    content = ENABLED_PREF_RE.sub(new_enabled_line, content, count=1)
    content = COLOR_PREF_RE.sub(new_color_line, content, count=1)

    common.atomic_write(prefs_path, content)


def main():
    with open(CSS_PATH) as f:
        css_content = f.read()
    colors = parse_colors(css_content)
    if "mybackground" not in colors:
        return
    r, g, b, a = colors["mybackground"]
    sync_zen(r, g, b, a)


if __name__ == "__main__":
    main()

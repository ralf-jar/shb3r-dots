"""Traducciones del dashboard -- ver flipfrog/i18n/es-MX.json (idioma
base, obligatorio) y en.json (parcial, cualquier modulo+codigo faltante
cae a es-MX.json). A diferencia de WAYBAR_DIR/THEMER_DIR (que cada
script recalcula según su profundidad), acá la resolución de rutas
vive una sola vez porque este módulo es el único dueño de esos
archivos -- los llamadores solo hacen `from i18n import t`.

Cambiar de idioma (set_lang) no re-renderiza texto ya construido en un
proceso YA abierto -- mismo criterio que radio de bordes/tema, afecta
recién a la próxima apertura de popup o dashboard."""

import json
import os

import common

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
I18N_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "i18n"))
LANG_STATE_FILE = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "waybar", "language.json"))
BASE_LANG = "es-MX"

_cache = {}


def _load(lang):
    if lang not in _cache:
        path = os.path.join(I18N_DIR, f"{lang}.json")
        try:
            with open(path) as f:
                entries = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            entries = []
        _cache[lang] = {(e["modulo"], e["codigo"]): e["texto"] for e in entries}
    return _cache[lang]


def current_lang():
    try:
        with open(LANG_STATE_FILE) as f:
            return json.load(f).get("lang", BASE_LANG)
    except (FileNotFoundError, json.JSONDecodeError):
        return BASE_LANG


def t(modulo, codigo, **kwargs):
    lang = current_lang()
    text = _load(lang).get((modulo, codigo))
    if text is None and lang != BASE_LANG:
        text = _load(BASE_LANG).get((modulo, codigo))
    if text is None:
        text = codigo
    return text.format(**kwargs) if kwargs else text


def all_translations(modulo, codigo):
    """Texto de `modulo`+`codigo` en todos los idiomas instalados -- para
    reconocer un valor guardado en disco con el idioma de cuando se creó."""
    texts = {_load(code).get((modulo, codigo)) for code, _name in list_languages()}
    texts.discard(None)
    return texts


def set_lang(lang):
    common.atomic_write(LANG_STATE_FILE, json.dumps({"lang": lang}))


def list_languages():
    """[(codigo, nombre), ...] -- un idioma por cada *.json en I18N_DIR,
    orden alfabético por código. `nombre` sale de la entrada especial
    modulo="_meta"/codigo="nombre_idioma" DENTRO de ese mismo archivo
    (cae al código si falta) -- agregar un idioma nuevo es solo sumar
    el archivo, el droplist de "Temas" lo recoge solo sin tocar código."""
    if not os.path.isdir(I18N_DIR):
        return []
    codes = sorted(
        fname[:-len(".json")] for fname in os.listdir(I18N_DIR) if fname.endswith(".json")
    )
    # BASE_LANG primero (es el idioma default del droplist), el resto
    # alfabético por código.
    codes.sort(key=lambda c: (c != BASE_LANG, c))
    return [(code, _load(code).get(("_meta", "nombre_idioma"), code)) for code in codes]

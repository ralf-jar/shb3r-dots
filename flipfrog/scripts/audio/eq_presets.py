"""Curvas de EQ "conocidas de internet" adaptadas a las 6 bandas de
sink-eq6.conf, en dB (ver eq_pw.MIN_GAIN/MAX_GAIN). sound_module.py
también permite guardar preajustes propios -- persistidos aparte en
eq-custom-presets.json (load/save/delete_custom_preset más abajo), no
mezclados con las constantes de fábrica de este archivo."""

import json
import os
import tempfile

CUSTOM_PRESETS_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "eq-custom-presets.json")

PRESETS = {
    "Plano": (0, 0, 0, 0, 0, 0),
    "Realce de graves": (6, 4, 1, 0, 0, 0),
    "Realce de agudos": (0, 0, 0, 1, 4, 6),
    "Rock": (4, 2, -2, 1, 3, 4),
    "Pop": (-1, 2, 4, 3, 1, -1),
    "Jazz": (3, 2, -1, 1, 2, 3),
    "Clásica": (3, 2, 0, 0, -2, 3),
    "Realce de voz": (-2, -1, 3, 4, 2, -1),
    "Loudness": (5, 3, -1, -1, 2, 5),
}

PRESET_ORDER = list(PRESETS.keys())


# ---- Preajustes propios (guardados desde la pestaña "Sonido") --------

def load_custom_presets():
    """{nombre: (g1..g6)} -- mismo formato que PRESETS, así
    sound_module.py puede tratar built-in y propios con el mismo código
    de aplicar/matchear."""
    try:
        with open(CUSTOM_PRESETS_FILE) as f:
            data = json.load(f)
        return {name: tuple(vals) for name, vals in data.items()}
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _save_custom_presets(presets):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CUSTOM_PRESETS_FILE))
    with os.fdopen(fd, "w") as f:
        json.dump({name: list(vals) for name, vals in presets.items()}, f)
    os.replace(tmp, CUSTOM_PRESETS_FILE)


def save_custom_preset(name, gains):
    """gains: iterable de 6 valores en dB (banda 1 a 6). Sobrescribe si
    ya existía un propio con ese nombre -- no valida contra PRESETS
    (fábrica), así que un nombre propio puede coincidir con uno de
    fábrica; sound_module.py resuelve ese choque prefiriendo el propio
    al armar el mapa combinado."""
    presets = load_custom_presets()
    presets[name] = tuple(gains)
    _save_custom_presets(presets)


def delete_custom_preset(name):
    presets = load_custom_presets()
    presets.pop(name, None)
    _save_custom_presets(presets)


# ---- Autoecualizador (Descubre Bandcamp) ------------------------------
# Mapeo aproximado tag/género -> preajuste. Lista ORDENADA (no dict): la
# primera keyword que matchea gana, así un género específico puede
# priorizarse antes que uno genérico. Match por substring simple, sin
# NLP, a propósito -- "aproximado" es el requisito real.
GENRE_KEYWORDS = [
    (("metal", "hardcore", "punk", "grind"), "Rock"),
    (("rock",), "Rock"),
    (("hip hop", "hip-hop", "rap", "trap"), "Realce de graves"),
    (("dubstep", "drum and bass", "drum & bass", "dnb", "bass music"), "Realce de graves"),
    (("techno", "house", "edm", "electronic", "dance", "idm"), "Loudness"),
    (("jazz",), "Jazz"),
    (("classical", "orchestral", "chamber", "opera"), "Clásica"),
    (("folk", "singer-songwriter", "acoustic", "country"), "Realce de voz"),
    (("pop",), "Pop"),
    (("ambient", "drone", "soundtrack"), "Plano"),
]


def preset_for_tags(tags):
    """Primer preajuste cuya keyword aparece en alguno de los tags
    (substring, case-insensitive) -- None si ningún tag matcheó nada
    (deja el ecualizador como está, no lo fuerza a "Plano" sin
    motivo -- a diferencia de un género reconocido pero "neutro" como
    ambient, que sí mapea a Plano a propósito)."""
    lowered = [t.lower() for t in (tags or [])]
    for keywords, preset in GENRE_KEYWORDS:
        for tag in lowered:
            if any(kw in tag for kw in keywords):
                return preset
    return None

"""Aplica un gain en vivo (eq_pw.set_band_gain) y lo persiste a disco
(eq-bands.json) -- ver CLAUDE.md "Pestaña Sonido"."""

import json
import os
import tempfile

import eq_pw

BANDS_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "eq-bands.json")
DEVICE_GAINS_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "eq-device-gains.json")


def load_saved_gains():
    try:
        with open(BANDS_FILE) as f:
            data = json.load(f)
        return {int(k): float(v) for k, v in data.items()}
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _save_gains(gains):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(BANDS_FILE))
    with os.fdopen(fd, "w") as f:
        json.dump({str(k): v for k, v in gains.items()}, f)
    os.replace(tmp, BANDS_FILE)


def set_band_gain(band, gain_db):
    ok, msg = eq_pw.set_band_gain(band, gain_db)
    if ok:
        gains = load_saved_gains()
        gains[band] = max(eq_pw.MIN_GAIN, min(eq_pw.MAX_GAIN, float(gain_db)))
        _save_gains(gains)
    return ok, msg


def reset_all():
    ok_all = True
    for band in range(1, eq_pw.BAND_COUNT + 1):
        ok, _msg = set_band_gain(band, 0.0)
        ok_all = ok_all and ok
    return ok_all


def apply_saved_gains():
    """Reaplica en vivo sin volver a persistir -- llamado por
    eq_router.py al arrancar sesión."""
    for band, gain_db in load_saved_gains().items():
        eq_pw.set_band_gain(band, gain_db)


# ---- Preajuste por dispositivo de salida ------------------------------
# "Guardar para este dispositivo" -- mapeo aparte de eq-bands.json,
# node.name del sink de hardware -> gains, para que eq_router.py aplique
# una curva distinta según el dispositivo (audífonos vs. parlantes).

def load_device_gains():
    """{node.name del sink: {banda: db}}."""
    try:
        with open(DEVICE_GAINS_FILE) as f:
            data = json.load(f)
        return {dev: {int(k): float(v) for k, v in gains.items()} for dev, gains in data.items()}
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _save_device_gains(all_gains):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(DEVICE_GAINS_FILE))
    with os.fdopen(fd, "w") as f:
        json.dump({dev: {str(k): v for k, v in gains.items()} for dev, gains in all_gains.items()}, f)
    os.replace(tmp, DEVICE_GAINS_FILE)


def save_gains_for_device(sink_name, gains):
    """gains: {banda: db} -- típicamente el resultado de
    eq_pw.get_band_gains(), lo que esté sonando ahora mismo."""
    all_gains = load_device_gains()
    all_gains[sink_name] = {int(k): float(v) for k, v in gains.items()}
    _save_device_gains(all_gains)


def get_gains_for_device(sink_name):
    """None si nunca se guardó nada para ESTE sink puntual -- eq_router.py
    no debe tocar el gain actual en ese caso (a diferencia de un
    preajuste elegido a mano, quedarse con lo que ya sonaba es lo
    esperado para un dispositivo sin curva propia guardada)."""
    return load_device_gains().get(sink_name)

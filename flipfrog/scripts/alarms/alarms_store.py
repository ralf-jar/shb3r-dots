"""Estado de alarmas -- sin GTK, compartido por alarm_popup.py (CRUD
del usuario) y alarm_daemon.py (marca `last_fired`/auto-apaga las de
una sola vez al sonar). DOS escritores reales sobre el mismo archivo
-- mkstemp en vez de common.atomic_write (ver el docstring de esa
función: ese patrón simple es solo para UN escritor)."""

import json
import os
import tempfile
import uuid

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
ALARMS_FILE = os.path.join(SCRIPT_DIR, "alarms.json")


def load_alarms():
    try:
        with open(ALARMS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_alarms(alarms):
    fd, tmp_path = tempfile.mkstemp(dir=SCRIPT_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(alarms, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, ALARMS_FILE)
    except BaseException:
        os.remove(tmp_path)
        raise


def new_alarm_id():
    return uuid.uuid4().hex[:8]

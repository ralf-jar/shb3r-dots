"""Preferencias del asistente de voz, inyectadas en el prompt de
brain.py. Gitignored (estado personal, no código)."""

import json
import os
import tempfile

CONFIG_PATH = os.path.join(os.path.dirname(os.path.realpath(__file__)), "assistant-config.json")

FIELDS = ("ciudad", "sistema_operativo", "notas")


def load():
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return {field: data.get(field, "") for field in FIELDS}


def save(values):
    data = {field: values.get(field, "").strip() for field in FIELDS}
    # temp + os.replace (atómico) -- mismo patrón que el resto del repo
    # para cualquier archivo reescrito en caliente, ver CLAUDE.md.
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(CONFIG_PATH))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CONFIG_PATH)
    except BaseException:
        os.remove(tmp_path)
        raise


def as_prompt_context():
    """Bloque de texto para sumar al prompt de sistema de brain.py, o
    cadena vacía si no hay nada configurado (ningún campo tiene valor)."""
    values = load()
    lines = []
    if values["ciudad"]:
        lines.append(f"- Ciudad donde vive el usuario: {values['ciudad']}")
    if values["sistema_operativo"]:
        lines.append(f"- Sistema operativo del usuario: {values['sistema_operativo']}")
    if values["notas"]:
        lines.append(f"- Notas adicionales: {values['notas']}")
    if not lines:
        return ""
    return "Contexto del usuario (úsalo cuando sea relevante, ej. preguntas de clima o de comandos de sistema):\n" + "\n".join(lines)

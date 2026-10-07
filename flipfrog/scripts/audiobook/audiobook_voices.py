"""Voces de Piper para audiolibros, sin GTK. Guarda las nuevas en la
biblioteca (también lee ~/.cache/waybar-voice-assistant/, donde se bajaban
antes). Una voz = modelo .onnx + locutor (los modelos
multi-locutor, como sharvard, cuentan como una voz por locutor)."""

import glob
import json
import os

import requests

LIB_DIR = os.path.expanduser("~/.local/share/flipfrog/audiobooks")
VOICES_DIR = os.path.join(LIB_DIR, "voices")
SEARCH_DIRS = [os.path.expanduser("~/.cache/waybar-voice-assistant"), VOICES_DIR]

HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
# Voz a bajar si el libro está en un idioma sin ninguna voz instalada.
DOWNLOADABLE = {
    "es": "es/es_MX/claude/high/es_MX-claude-high",
    "en": "en/en_US/lessac/medium/en_US-lessac-medium",
    "pt": "pt/pt_BR/faber/medium/pt_BR-faber-medium",
    "fr": "fr/fr_FR/siwis/medium/fr_FR-siwis-medium",
    "it": "it/it_IT/paola/medium/it_IT-paola-medium",
    "de": "de/de_DE/thorsten/medium/de_DE-thorsten-medium",
}
# Default en español: sharvard, locutora (la baja install.sh con los extras).
PREFERRED = {"es": ("es_ES-sharvard-medium", 1)}


def _speakers(config_path):
    try:
        with open(config_path) as f:
            config = json.load(f)
    except (OSError, ValueError):
        return {}
    return config.get("speaker_id_map") or {}


def installed():
    """[{"id", "model", "speaker", "language", "label"}], `id` estable
    (`<modelo>#<locutor>`) para guardarlo como preferencia."""
    voices = []
    seen = set()
    for folder in SEARCH_DIRS:
        for model in sorted(glob.glob(os.path.join(folder, "*.onnx"))):
            name = os.path.basename(model)[:-5]
            if name in seen or "_" not in name or not os.path.exists(model + ".json"):
                continue
            seen.add(name)
            lang = name.split("_")[0]
            region = name.split("-")[0]
            speakers = _speakers(model + ".json")
            for sid, label_speaker in (sorted((v, k) for k, v in speakers.items()) or [(0, None)]):
                label = name.split("-")[1] if "-" in name else name
                label = f"{label.capitalize()} ({region})"
                if label_speaker:
                    label += f" · {label_speaker}"
                voices.append({"id": f"{name}#{sid}", "model": model, "speaker": sid,
                               "language": lang, "label": label})
    return voices


def pick(language, preferred_id=None):
    """Voz instalada para `language` -- la preferencia del usuario si
    es del mismo idioma, si no la default del idioma, si no la primera.
    None si no hay ninguna de ese idioma (hay que bajarla)."""
    voices = [v for v in installed() if v["language"] == language]
    if not voices:
        return None
    by_id = {v["id"]: v for v in voices}
    if preferred_id in by_id:
        return by_id[preferred_id]
    if language in PREFERRED:
        name, sid = PREFERRED[language]
        if f"{name}#{sid}" in by_id:
            return by_id[f"{name}#{sid}"]
    return voices[0]


def get(voice_id):
    return next((v for v in installed() if v["id"] == voice_id), None)


def download(language):
    """Baja la voz default de `language` a VOICES_DIR. None si no hay
    ninguna conocida para ese idioma o falla la descarga."""
    path = DOWNLOADABLE.get(language)
    if not path:
        return None
    os.makedirs(VOICES_DIR, exist_ok=True)
    name = os.path.basename(path)
    target = os.path.join(VOICES_DIR, name + ".onnx")
    try:
        for suffix in (".onnx.json", ".onnx"):
            tmp = os.path.join(VOICES_DIR, name + suffix + ".part")
            with requests.get(f"{HF}/{path}{suffix}", stream=True, timeout=(15, 60)) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            os.replace(tmp, os.path.join(VOICES_DIR, name + suffix))
    except (requests.RequestException, OSError):
        return None
    return pick(language) if os.path.exists(target) else None

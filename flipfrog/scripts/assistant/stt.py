"""Wrapper de whisper-cpp para voice_assistant.py -- WAV -> texto. Lee
`-otxt`, no stdout (más limpio). No hace falta resamplear el WAV de
entrada, whisper-cli lo hace solo."""

import os
import subprocess
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
MODEL_PATH = os.path.expanduser("~/.cache/waybar-voice-assistant/whisper-small.bin")

TIMEOUT_S = 60


def transcribe(wav_path, lang="es"):
    """None si falla (modelo ausente, timeout, audio vacío) -- nunca
    excepciona, voice_assistant.py decide qué mostrar en ese caso."""
    if not os.path.exists(MODEL_PATH):
        return None

    out_prefix = tempfile.mktemp(prefix="voice-assistant-stt-")
    try:
        subprocess.run(
            [
                "whisper-cli", "-m", MODEL_PATH, "-f", wav_path,
                "-l", lang, "-nt", "-np", "-otxt", "-of", out_prefix,
            ],
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
        txt_path = out_prefix + ".txt"
        if not os.path.exists(txt_path):
            return None
        with open(txt_path) as f:
            text = f.read().strip()
        return text or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    finally:
        txt_path = out_prefix + ".txt"
        if os.path.exists(txt_path):
            os.remove(txt_path)

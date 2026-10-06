"""Wrapper de Piper para voice_assistant.py -- texto -> audio hablado.
Ver CLAUDE.md, "Asistente de voz" (motor, voz elegida, pw-play)."""

import os
import subprocess
import tempfile

MODEL_PATH = os.path.expanduser("~/.cache/waybar-voice-assistant/es_ES-sharvard-medium.onnx")
CONFIG_PATH = MODEL_PATH + ".json"
SPEAKER_ID = "1"  # mujer, ver docstring de arriba

GEN_TIMEOUT_S = 30


def _synthesize(text):
    """Devuelve la ruta del WAV generado, o None si falla."""
    if not os.path.exists(MODEL_PATH):
        return None

    out_path = tempfile.mktemp(prefix="voice-assistant-tts-", suffix=".wav")
    try:
        subprocess.run(
            [
                "piper-tts", "-m", MODEL_PATH, "-c", CONFIG_PATH,
                "-s", SPEAKER_ID, "-f", out_path,
            ],
            input=text, capture_output=True, text=True, timeout=GEN_TIMEOUT_S,
        )
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            return None
        return out_path
    except subprocess.TimeoutExpired:
        return None


def stop_playback(proc):
    """Corta la reproducción en curso -- para cuando el popup se cierra
    con el audio todavía sonando (ver voice_assistant.py, mismo motivo
    que `recorder._stop_cleanly` para pw-record: un hilo daemon no
    alcanza a hacer su propio cleanup si el proceso principal termina
    antes)."""
    proc.terminate()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def speak(text, on_start=None):
    """Bloqueante -- sintetiza y reproduce, pensado para correr en un
    hilo `daemon` aparte (ver voice_assistant.py). No excepciona: si algo
    falla, simplemente no suena nada (voice_assistant.py ya muestra el
    texto en el diálogo de todas formas).

    `on_start(proc)`, si se pasa, se llama apenas arranca `pw-play` --
    le da al que llama una referencia para poder cortar la reproducción
    de forma sincrónica desde el hilo principal de GTK (`stop_playback`)
    si el popup se cierra mientras todavía está sonando."""
    wav_path = _synthesize(text)
    if wav_path is None:
        return
    try:
        proc = subprocess.Popen(
            ["pw-play", wav_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if on_start is not None:
            on_start(proc)
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        os.remove(wav_path)

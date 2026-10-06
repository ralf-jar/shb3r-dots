"""Graba el micrófono para voice_assistant.py -- corta sola cuando
detecta silencio tras empezar a hablar (python-webrtcvad). `pw-record`,
NUNCA `parecord` -- ver CLAUDE.md "Asistente de voz".

VAD pide frames mono; la captura es estéreo, `_downmix` baja cada
frame de 20ms promediando L+R antes de `vad.is_speech()`."""

import array
import os
import signal
import subprocess
import tempfile
import time

import webrtcvad

SAMPLE_RATE = 48000
CHANNELS = 2
WAV_HEADER_BYTES = 44

FRAME_MS = 20
FRAME_STEREO_BYTES = int(SAMPLE_RATE * (FRAME_MS / 1000) * CHANNELS * 2)

POLL_INTERVAL_S = 0.2
SILENCE_MS_TO_STOP = 900
PRE_SPEECH_TIMEOUT_S = 8
MAX_DURATION_S = 30
VAD_AGGRESSIVENESS = 2  # 0-3, 2 = moderado


def _downmix(stereo_bytes):
    samples = array.array("h")
    samples.frombytes(stereo_bytes)
    mono = array.array("h", bytes(len(samples)))  # mitad de muestras, se pisan abajo
    for i in range(0, len(samples), 2):
        mono[i // 2] = (samples[i] + samples[i + 1]) // 2
    return mono.tobytes()


def _stop_cleanly(proc):
    """SIGINT -- la señal con la que pw-record cierra el WAV en limpio.
    Si no responde en 2s, se insiste con más fuerza."""
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def record_until_silence(on_start=None):
    """Bloqueante -- pensado para un hilo daemon aparte. Devuelve la
    ruta del WAV grabado, o None si nunca se detectó habla.

    `on_start(proc)`: ver CLAUDE.md "Interrumpir grabación/audio al
    cerrar" -- le da a voice_assistant.py la referencia para cortar
    `pw-record` sincrónico si el popup se cierra a mitad de grabación."""
    tmp_path = tempfile.mktemp(prefix="voice-assistant-", suffix=".wav")

    proc = subprocess.Popen(
        ["pw-record", "--latency", "20ms", tmp_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if on_start is not None:
        on_start(proc)

    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    read_pos = WAV_HEADER_BYTES
    leftover = b""
    speech_started = False
    start_time = time.monotonic()
    last_speech_time = start_time

    try:
        while True:
            time.sleep(POLL_INTERVAL_S)
            now = time.monotonic()

            try:
                with open(tmp_path, "rb") as f:
                    f.seek(read_pos)
                    chunk = f.read()
            except FileNotFoundError:
                chunk = b""
            read_pos += len(chunk)

            buf = leftover + chunk
            n_frames = len(buf) // FRAME_STEREO_BYTES
            for i in range(n_frames):
                frame = buf[i * FRAME_STEREO_BYTES:(i + 1) * FRAME_STEREO_BYTES]
                if vad.is_speech(_downmix(frame), SAMPLE_RATE):
                    speech_started = True
                    last_speech_time = now
            leftover = buf[n_frames * FRAME_STEREO_BYTES:]

            if not speech_started and (now - start_time) > PRE_SPEECH_TIMEOUT_S:
                _stop_cleanly(proc)
                os.remove(tmp_path)
                return None

            if speech_started and (now - last_speech_time) * 1000 > SILENCE_MS_TO_STOP:
                _stop_cleanly(proc)
                return tmp_path

            if (now - start_time) > MAX_DURATION_S:
                _stop_cleanly(proc)
                if speech_started:
                    return tmp_path
                os.remove(tmp_path)
                return None
    except BaseException:
        _stop_cleanly(proc)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

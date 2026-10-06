"""EPUB -> audiolibro, sin GTK. Corre dentro de audiobook_daemon.py.

Cada frase se sintetiza por separado con Piper (en el mismo proceso, sin
subprocess por frase) y el PCM se escribe a mano en un WAV, con silencios
propios entre frases/párrafos/encabezados -- así el tiempo exacto de
inicio/fin de cada frase se conoce por construcción, que es lo que usa el
lector para resaltar el texto. Al final ffmpeg codifica un solo MP3 con
capítulos ID3 (CHAP) y la portada del EPUB."""

import os
import re
import subprocess
import time
import wave

import epub_reader
import audiobook_library as library

SENTENCE_GAP = 0.35
PARAGRAPH_GAP = 0.7
HEADING_GAP = 1.1
MP3_BITRATE = "64k"
UNSPEAKABLE_RE = re.compile(r"[*_#~|<>\[\]{}=]+")


class Canceled(Exception):
    pass


def _meta_escape(text):
    return re.sub(r"([=;#\\\n])", r"\\\1", text)


def _spoken(block_type, text):
    if block_type in ("h", "title"):
        text = epub_reader.spoken_heading(text)
    return UNSPEAKABLE_RE.sub(" ", text).strip()


def _encode_mp3(wav_path, mp3_path, meta_path, cover, duration, progress, cancel):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-progress", "pipe:1", "-nostats",
           "-i", wav_path, "-i", meta_path]
    if cover:
        cmd += ["-i", cover]
    cmd += ["-map", "0:a"]
    if cover:
        cmd += ["-map", "2:v", "-c:v", "copy", "-disposition:v", "attached_pic",
                "-metadata:s:v", "comment=Cover (front)"]
    cmd += ["-map_metadata", "1", "-map_chapters", "1", "-c:a", "libmp3lame",
            "-b:a", MP3_BITRATE, "-ac", "1", "-id3v2_version", "3", mp3_path]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in proc.stdout:
        if cancel.is_set():
            proc.kill()
            proc.wait()
            raise Canceled()
        if line.startswith("out_time_us=") and duration:
            try:
                progress("encoding", min(int(line.split("=")[1]) / 1e6 / duration, 1.0), duration)
            except ValueError:
                pass
    if proc.wait() != 0:
        raise RuntimeError(proc.stderr.read()[-500:])


def convert(epub_path, voice_spec, progress, cancel):
    """Devuelve el id del libro nuevo. `progress(phase, fraction,
    audio_seconds)`; `cancel` es un threading.Event. Lanza
    epub_reader.EpubError, Canceled o RuntimeError."""
    from piper import PiperVoice
    from piper.config import SynthesisConfig

    progress("reading", 0.0, 0.0)
    parsed = epub_reader.load(epub_path)
    book_id = library.make_id(parsed["title"], epub_path)
    folder = library.book_dir(book_id)
    if library.load_book(book_id):
        raise epub_reader.EpubError("error_ya_existe")
    os.makedirs(folder, exist_ok=True)

    head = [{"type": "title", "level": 1, "text": parsed["title"],
             "sentences": [parsed["title"]]}]
    if parsed["author"]:
        head.append({"type": "author", "level": 0, "text": parsed["author"],
                     "sentences": [parsed["author"]]})
    blocks = head + parsed["blocks"]
    chapters = [dict(c, block=c["block"] + len(head)) for c in parsed["chapters"]]
    if chapters and chapters[0]["title"] == parsed["title"]:
        chapters[0]["block"] = 0

    total_chars = sum(len(s) for b in blocks for s in b["sentences"]) or 1
    wav_path = os.path.join(folder, ".audio.wav")
    cover_name = None
    done_chars = 0
    samples = 0
    rate = 22050
    out_blocks = []
    block_starts = []

    def silence(seconds):
        nonlocal samples
        n = int(seconds * rate)
        wav.writeframes(b"\0\0" * n)
        samples += n

    last_progress = 0
    try:
        if parsed["cover"]:
            data, ext = parsed["cover"]
            cover_name = f"cover.{ext}"
            with open(os.path.join(folder, cover_name), "wb") as f:
                f.write(data)

        voice = PiperVoice.load(voice_spec["model"])
        syn = SynthesisConfig(speaker_id=voice_spec["speaker"])
        rate = voice.config.sample_rate

        with wave.open(wav_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)

            for i, block in enumerate(blocks):
                if i and block["type"] in ("h", "title"):
                    silence(HEADING_GAP - PARAGRAPH_GAP)
                block_starts.append(samples / rate)
                timed = []
                for sentence in block["sentences"]:
                    if cancel.is_set():
                        raise Canceled()
                    start = samples / rate
                    spoken = _spoken(block["type"], sentence)
                    if re.search(r"\w", spoken):
                        for chunk in voice.synthesize(spoken, syn):
                            wav.writeframes(chunk.audio_int16_bytes)
                            samples += len(chunk.audio_int16_bytes) // 2
                    end = samples / rate
                    timed.append([sentence, round(start, 3), round(end, 3)])
                    silence(SENTENCE_GAP)
                    done_chars += len(sentence)
                    now = time.time()
                    if now - last_progress > 0.5:
                        progress("speaking", done_chars / total_chars, samples / rate)
                        last_progress = now
                silence((HEADING_GAP if block["type"] in ("h", "title") else PARAGRAPH_GAP) - SENTENCE_GAP)
                out_blocks.append({"t": block["type"], "l": block["level"], "s": timed})

        duration = samples / rate
        chapter_list = []
        for c in chapters:
            chapter_list.append({"title": c["title"], "depth": c["depth"],
                                 "start": round(block_starts[c["block"]], 3)})

        meta_path = os.path.join(folder, ".chapters.txt")
        with open(meta_path, "w") as f:
            f.write(";FFMETADATA1\n")
            f.write(f"title={_meta_escape(parsed['title'])}\n")
            f.write(f"album={_meta_escape(parsed['title'])}\n")
            if parsed["author"]:
                f.write(f"artist={_meta_escape(parsed['author'])}\n")
            f.write("genre=Audiobook\n")
            for n, c in enumerate(chapter_list):
                end = chapter_list[n + 1]["start"] if n + 1 < len(chapter_list) else duration
                f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(c['start'] * 1000)}\n"
                        f"END={int(end * 1000)}\ntitle={_meta_escape(c['title'])}\n")

        audio_name = re.sub(r'[/\\:*?"<>|]+', "", parsed["title"]).strip()[:80] or "audiolibro"
        audio_name += ".mp3"
        cover_path = os.path.join(folder, cover_name) if cover_name else None
        _encode_mp3(wav_path, os.path.join(folder, audio_name), meta_path,
                    cover_path, duration, progress, cancel)
        os.remove(meta_path)
    except BaseException:
        library.delete_book(book_id)
        raise
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

    library.save_book(book_id, {
        "title": parsed["title"], "author": parsed["author"],
        "language": parsed["language"], "voice": voice_spec["id"],
        "created": time.time(), "duration": round(duration, 3),
        "audio": audio_name, "cover": cover_name, "source": os.path.basename(epub_path),
        "chapters": chapter_list, "blocks": out_blocks,
    })
    return book_id

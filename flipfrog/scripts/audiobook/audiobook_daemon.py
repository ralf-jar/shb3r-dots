#!/usr/bin/env python3
"""Daemon de audiolibros (SUPER+A) -- dueño de la cola de conversión
EPUB->MP3 y del reproductor (mpv), para que cerrar el popup no corte ni
una conversión ni la escucha (mismo criterio que bandcamp/
bandcamp_daemon.py y downloads/download_daemon.py). Lanzado a demanda por
audiobook_popup.py; se cierra solo tras IDLE_EXIT sin convertir ni
reproducir."""

import bisect
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
from common import set_process_name
from i18n import t
import epub_reader
import audiobook_convert
import audiobook_library as library
import audiobook_voices as voices
from audiobook_mpv import Mpv
from audiobook_ipc import SOCKET_PATH, LOCK

IDLE_EXIT = 900
SAVE_EVERY = 5
SPEAK_WEIGHT = 0.95


def _notify(title, body):
    subprocess.Popen(["notify-send", "-a", t("audiolibros", "titulo"), title, body],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Daemon:
    def __init__(self):
        self.lock = threading.RLock()
        self.wake = threading.Condition(self.lock)
        self.conversions = []
        self.cancel_events = {}
        self.library_version = 0
        self.player = library.load_player()
        self.mpv = None
        self.book_id = None
        self.book = None
        self.book_state = None
        self.sentence_starts = []
        self.sentence_texts = []
        self.last_activity = time.time()

    # --- conversión -----------------------------------------------------

    def _add_epub(self, path):
        if not path or not os.path.isfile(path):
            return {"ok": False, "error": "error_epub_invalido"}
        item = {"id": uuid.uuid4().hex[:10], "epub": path,
                "name": os.path.splitext(os.path.basename(path))[0],
                "status": "queued", "progress": 0.0, "eta": None, "error": None}
        self.conversions.append(item)
        self.wake.notify_all()
        return {"ok": True, "id": item["id"]}

    def _convert_loop(self):
        while True:
            with self.lock:
                item = next((c for c in self.conversions if c["status"] == "queued"), None)
                if item is None:
                    self.wake.wait()
                    continue
                item["status"] = "reading"
                cancel = threading.Event()
                self.cancel_events[item["id"]] = cancel

            try:
                self._convert(item, cancel)
            except audiobook_convert.Canceled:
                with self.lock:
                    if item in self.conversions:
                        self.conversions.remove(item)
            except epub_reader.EpubError as e:
                self._fail(item, e.code)
            except Exception:  # ffmpeg/piper -- se muestra, no tumba el daemon
                self._fail(item, "error_conversion")
            finally:
                self.cancel_events.pop(item["id"], None)

    def _fail(self, item, code):
        with self.lock:
            item.update(status="error", error=code, eta=None)

    def _convert(self, item, cancel):
        parsed = epub_reader.load(item["epub"])
        with self.lock:
            item["name"] = parsed["title"]
            preferred = self.player.get("voice")

        voice = voices.pick(parsed["language"], preferred)
        if voice is None:
            with self.lock:
                item["status"] = "voice"
            voice = voices.download(parsed["language"])
            if voice is None:
                raise epub_reader.EpubError("error_sin_voz")

        started = time.time()

        def progress(phase, fraction, _seconds):
            overall = fraction * SPEAK_WEIGHT if phase in ("reading", "speaking") \
                else SPEAK_WEIGHT + fraction * (1 - SPEAK_WEIGHT)
            elapsed = time.time() - started
            with self.lock:
                item["status"] = phase
                item["progress"] = overall
                item["eta"] = elapsed / overall * (1 - overall) if overall > 0.01 else None

        book_id = audiobook_convert.convert(item["epub"], voice, progress, cancel)
        with self.lock:
            if item in self.conversions:
                self.conversions.remove(item)
            self.library_version += 1
        _notify(t("audiolibros", "notif_listo_titulo"), t("audiolibros", "notif_listo", title=parsed["title"]))
        return book_id

    # --- reproductor ------------------------------------------------------

    def _ensure_mpv(self):
        if self.mpv is None or not self.mpv.alive():
            self.mpv = Mpv(volume=self.player.get("volume", 100))

    def _position(self):
        pos = self.mpv.get("time-pos") if self.mpv and self.book_id else None
        return float(pos) if pos is not None else (self.book_state or {}).get("position", 0.0)

    def _save_position(self):
        if self.book_id and self.book_state is not None and library.load_book(self.book_id):
            self.book_state["position"] = self._position()
            self.book_state["last_played"] = time.time()
            library.save_state(self.book_id, self.book_state)

    def _open(self, book_id):
        if book_id == self.book_id:
            return {"ok": True}
        book = library.load_book(book_id)
        if not book:
            return {"ok": False}
        self._save_position()
        self._ensure_mpv()

        self.book_id, self.book = book_id, book
        self.book_state = library.load_state(book_id)
        self.sentence_starts, self.sentence_texts = [], []
        for block in book["blocks"]:
            for text, start, _end in block["s"]:
                self.sentence_starts.append(start)
                self.sentence_texts.append(text)

        position = self.book_state["position"]
        if position >= book["duration"] - 1:
            position = 0.0
        self.mpv.set("pause", True)
        self.mpv.set("start", f"{position:.3f}")
        self.mpv.command("loadfile", library.audio_path(book_id, book), "replace")
        self.mpv.set("speed", self.book_state["speed"])
        self.player["last_book"] = book_id
        library.save_player(self.player)
        return {"ok": True}

    def _close_book(self):
        self._save_position()
        if self.mpv:
            self.mpv.command("stop")
        self.book_id = self.book = self.book_state = None
        self.player["last_book"] = None
        library.save_player(self.player)

    def _player_state(self):
        if not self.book_id or not self.mpv:
            return None
        paused = self.mpv.get("pause")
        eof = self.mpv.get("eof-reached")
        return {
            "book_id": self.book_id,
            "title": self.book.get("title", ""),
            "position": self._position(),
            "duration": self.book["duration"],
            "paused": True if paused is None else bool(paused or eof),
            "speed": self.book_state["speed"],
            "volume": self.player.get("volume", 100),
            "bookmarks": self.book_state["bookmarks"],
        }

    def _sentence_at(self, position):
        idx = bisect.bisect_right(self.sentence_starts, position) - 1
        return self.sentence_texts[idx] if 0 <= idx < len(self.sentence_texts) else ""

    # --- comandos ---------------------------------------------------------

    def handle(self, req):
        cmd = req.get("cmd")
        with self.lock:
            self.last_activity = time.time()

            if cmd == "get_state":
                return {"library_version": self.library_version,
                        "conversions": [dict(c) for c in self.conversions],
                        "player": self._player_state(),
                        "voice": self.player.get("voice")}
            if cmd == "add_epub":
                return self._add_epub(req.get("path"))
            if cmd == "cancel_conversion":
                item = next((c for c in self.conversions if c["id"] == req.get("id")), None)
                if item:
                    if item["id"] in self.cancel_events:
                        self.cancel_events[item["id"]].set()
                    else:
                        self.conversions.remove(item)
                return {"ok": True}
            if cmd == "set_voice":
                self.player["voice"] = req.get("voice")
                library.save_player(self.player)
                return {"ok": True}
            if cmd == "delete_book":
                if req.get("id") == self.book_id:
                    self._close_book()
                library.delete_book(req.get("id"))
                self.library_version += 1
                return {"ok": True}
            if cmd == "open":
                return self._open(req.get("id"))
            if cmd == "close_book":
                self._close_book()
                return {"ok": True}

            if not self.book_id or not self.mpv:
                return {"ok": False}

            if cmd == "toggle_pause":
                if self.mpv.get("eof-reached"):
                    self.mpv.command("seek", 0, "absolute")
                    self.mpv.set("pause", False)
                else:
                    self.mpv.set("pause", not self.mpv.get("pause"))
                self._save_position()
            elif cmd == "seek":
                self.mpv.command("seek", max(0.0, float(req.get("position", 0))), "absolute+exact")
                if req.get("play"):
                    self.mpv.set("pause", False)
                self._save_position()
            elif cmd == "seek_rel":
                self.mpv.command("seek", float(req.get("delta", 0)), "relative+exact")
                self._save_position()
            elif cmd == "set_speed":
                speed = float(req.get("speed", 1.0))
                self.book_state["speed"] = speed
                self.mpv.set("speed", speed)
                self._save_position()
            elif cmd == "set_volume":
                volume = max(0, min(130, int(req.get("volume", 100))))
                self.player["volume"] = volume
                self.mpv.set("volume", volume)
                library.save_player(self.player)
            elif cmd == "add_bookmark":
                pos = self._position()
                self.book_state["bookmarks"].append({"position": round(pos, 3), "text": self._sentence_at(pos)})
                self.book_state["bookmarks"].sort(key=lambda b: b["position"])
                self._save_position()
            elif cmd == "remove_bookmark":
                marks = self.book_state["bookmarks"]
                idx = int(req.get("index", -1))
                if 0 <= idx < len(marks):
                    marks.pop(idx)
                self._save_position()
            else:
                return {"ok": False}
            return {"ok": True}

    # --- ciclo de vida ------------------------------------------------------

    def _busy(self):
        converting = any(c["status"] not in ("error",) for c in self.conversions)
        playing = bool(self.book_id and self.mpv and self.mpv.get("pause") is False
                       and not self.mpv.get("eof-reached"))
        return converting or playing

    def serve(self):
        try:
            os.remove(SOCKET_PATH)
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET_PATH)
        server.listen(8)
        server.settimeout(SAVE_EVERY)

        threading.Thread(target=self._convert_loop, daemon=True).start()
        last_book = self.player.get("last_book")
        if last_book and library.load_book(last_book):
            with self.lock:
                self._open(last_book)

        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                with self.lock:
                    if self._busy():
                        self._save_position()
                        self.last_activity = time.time()
                    elif time.time() - self.last_activity > IDLE_EXIT:
                        self.shutdown()
                        return
                continue
            with conn:
                try:
                    conn.settimeout(3)
                    buf = b""
                    while b"\n" not in buf:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                    reply = self.handle(json.loads(buf.split(b"\n", 1)[0]))
                    conn.sendall((json.dumps(reply) + "\n").encode())
                except (OSError, ValueError):
                    pass

    def shutdown(self):
        with self.lock:
            for event in self.cancel_events.values():
                event.set()
            self._save_position()
            if self.mpv:
                self.mpv.quit()


def _cleanup():
    for path in (SOCKET_PATH, LOCK):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def main():
    set_process_name("ff-audiobook")
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    daemon = Daemon()

    def _on_term(*_):
        daemon.shutdown()
        _cleanup()
        os._exit(0)

    signal.signal(signal.SIGTERM, _on_term)
    try:
        daemon.serve()
    finally:
        _cleanup()


if __name__ == "__main__":
    main()

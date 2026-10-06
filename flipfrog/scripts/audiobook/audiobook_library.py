"""Biblioteca de audiolibros en disco, sin GTK -- compartido por el
daemon (único escritor) y el popup (solo lectura).

~/.local/share/flipfrog/audiobooks/<id>/
    book.json   estructura + tiempos por frase (audiobook_convert.py)
    <título>.mp3  un solo MP3 con capítulos ID3 y portada
    cover.<ext>
    state.json  posición, velocidad y marcadores (daemon)
player.json (raíz) -- volumen, voz preferida, último libro abierto."""

import hashlib
import json
import os
import re
import shutil
import time

from audiobook_voices import LIB_DIR

PLAYER_FILE = os.path.join(LIB_DIR, "player.json")
DEFAULT_STATE = {"position": 0.0, "speed": 1.0, "bookmarks": [], "last_played": 0}


def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def book_dir(book_id):
    return os.path.join(LIB_DIR, book_id)


def make_id(title, epub_path):
    with open(epub_path, "rb") as f:
        digest = hashlib.sha1(f.read()).hexdigest()[:8]
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "libro"
    return f"{slug}-{digest}"


def load_book(book_id):
    return _read(os.path.join(book_dir(book_id), "book.json"), None)


def save_book(book_id, book):
    _write(os.path.join(book_dir(book_id), "book.json"), book)


def load_state(book_id):
    return {**DEFAULT_STATE, **_read(os.path.join(book_dir(book_id), "state.json"), {})}


def save_state(book_id, state):
    _write(os.path.join(book_dir(book_id), "state.json"), state)


def load_player():
    return {"volume": 100, "voice": None, "last_book": None, **_read(PLAYER_FILE, {})}


def save_player(player):
    os.makedirs(LIB_DIR, exist_ok=True)
    _write(PLAYER_FILE, player)


def list_books():
    """Resumen de cada libro terminado, el último escuchado primero."""
    books = []
    if not os.path.isdir(LIB_DIR):
        return books
    for book_id in os.listdir(LIB_DIR):
        book = load_book(book_id)
        if not book:
            continue
        state = load_state(book_id)
        books.append({
            "id": book_id, "title": book["title"], "author": book.get("author", ""),
            "duration": book["duration"], "cover": book.get("cover"),
            "audio": book["audio"], "position": state["position"],
            "last_played": state["last_played"], "created": book.get("created", 0),
        })
    books.sort(key=lambda b: (b["last_played"], b["created"]), reverse=True)
    return books


def audio_path(book_id, book=None):
    book = book or load_book(book_id)
    return os.path.join(book_dir(book_id), book["audio"]) if book else None


def cover_path(book_id, book=None):
    book = book or load_book(book_id)
    return os.path.join(book_dir(book_id), book["cover"]) if book and book.get("cover") else None


def delete_book(book_id):
    shutil.rmtree(book_dir(book_id), ignore_errors=True)


def touch_played(book_id):
    state = load_state(book_id)
    state["last_played"] = time.time()
    save_state(book_id, state)

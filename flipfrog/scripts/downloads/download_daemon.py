#!/usr/bin/env python3
"""Daemon de descargas de MediaFire, YouTube y torrents (SUPER+D) -- único dueño de la cola,
para que cerrar el popup nunca corte una descarga (mismo criterio que
bandcamp/bandcamp_daemon.py). Lanzado a demanda por download_popup.py,
se cierra solo tras IDLE_EXIT segundos sin nada pendiente ni clientes.

Dos hilos: `_resolver_loop` completa nombre/tamaño (y expande carpetas)
de los enlaces recién pegados, en orden; `_download_loop` baja un
archivo a la vez, en el orden de la cola. `.part` + `Range` para
reanudar tras pausar, reintentar o reiniciar el daemon. YouTube va por
youtube.py (alias `ytd`/`ytdv` de fish), con la carpeta destino del item:
al resolverse queda en "choosing" hasta que el popup elige el formato.
Torrents: resueltos igual (nombre/tamaño/infohash), luego `_torrent_loop`
los baja todos en paralelo, fuera de la fila de uno a la vez (torrent.py)."""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

import requests

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
from common import atomic_write, set_process_name
from i18n import t
import mediafire
import torrent
import youtube
from downloads_ipc import SOCKET_PATH, LOCK

QUEUE_FILE = os.path.join(SCRIPT_DIR, "queue.json")
CHUNK = 256 * 1024
IDLE_EXIT = 600
SAVE_EVERY = 3
MAX_ATTEMPTS = 3
ACTIVE = ("pending", "choosing", "queued", "downloading")
BUSY = ("pending", "queued", "downloading")


class Daemon:
    def __init__(self):
        self.lock = threading.Condition()
        self.items = []
        self.paused = False
        self.cancel_ids = set()
        self.batch_done = 0
        self.last_activity = time.time()
        self.engine = None
        self._load()

    # --- persistencia ---

    def _load(self):
        try:
            with open(QUEUE_FILE) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        self.paused = data.get("paused", False)
        self.items = data.get("items", [])
        for item in self.items:
            item["speed"] = 0
            if item["status"] == "downloading":
                item["status"] = "queued"

    def _save(self):
        atomic_write(QUEUE_FILE, json.dumps({"paused": self.paused, "items": self.items}))

    def _find(self, item_id):
        return next((i for i in self.items if i["id"] == item_id), None)

    # --- comandos ---

    def handle(self, req):
        cmd = req.get("cmd")
        with self.lock:
            self.last_activity = time.time()
            if cmd == "get_state":
                return {"paused": self.paused, "items": [dict(i) for i in self.items]}
            if cmd == "add":
                return self._add(req.get("text", ""), req.get("dest"))
            if cmd == "set_paused":
                self.paused = bool(req.get("paused"))
            elif cmd == "set_format":
                self._set_format(req.get("id"), req.get("format"))
            elif cmd == "cancel":
                self._cancel(req.get("id"))
            elif cmd == "retry":
                item = self._find(req.get("id"))
                if item and item["status"] in ("error", "canceled"):
                    item["status"] = "queued" if item.get("resolved") else "pending"
                    item["error"] = None
                    item["attempts"] = 0
            elif cmd == "remove":
                item = self._find(req.get("id"))
                if item:
                    self._cancel(item["id"])
                    self.items.remove(item)
            elif cmd == "clear_finished":
                self.items = [i for i in self.items if i["status"] not in ("done", "canceled")]
            else:
                return {"ok": False}
            self._save()
            self.lock.notify_all()
            return {"ok": True}

    def _add(self, text, dest):
        if not dest:
            return {"ok": False, "added": 0}
        known = {(i["kind"], i["key"]) for i in self.items if i["status"] in ACTIVE}
        added = 0
        links = mediafire.parse_links(text) + youtube.parse_links(text) + torrent.parse_links(text)
        for kind, key in links:
            if (kind, key) in known:
                continue
            self.items.append({
                "id": uuid.uuid4().hex[:12], "kind": kind, "key": key,
                "name": key, "size": 0, "done": 0, "speed": 0,
                "status": "pending", "error": None, "resolved": False,
                "dest": dest, "subdir": "", "path": None, "attempts": 0,
            })
            added += 1
        self._save()
        self.lock.notify_all()
        return {"ok": True, "added": added}

    def _set_format(self, item_id, fmt):
        item = self._find(item_id)
        options = {o["id"]: o for o in (item or {}).get("formats") or []}
        if not item or fmt not in options or item["status"] not in ("choosing", "queued"):
            return
        item.update(format=fmt, size=options[fmt]["size"], status="queued")

    def _cancel(self, item_id):
        item = self._find(item_id)
        if not item or item["status"] not in ACTIVE:
            return
        if item["status"] == "downloading" and item["kind"] != "torrent":
            self.cancel_ids.add(item_id)
        else:
            item["status"] = "canceled"
            self._remove_part(item)

    @staticmethod
    def _remove_part(item):
        if item.get("path"):
            try:
                os.remove(item["path"] + ".part")
            except FileNotFoundError:
                pass

    # --- resolución ---

    def _resolver_loop(self):
        session = mediafire._session()
        while True:
            with self.lock:
                item = next((i for i in self.items if i["status"] == "pending"), None)
                if item is None:
                    self.lock.wait()
                    continue
                kind, key = item["kind"], item["key"]

            try:
                if kind == "torrent":
                    result = [torrent.resolve(key)]
                elif kind == "youtube":
                    name, formats = youtube.info(key)
                    result = [{"name": name, "size": 0, "formats": formats}]
                elif kind == "folder":
                    result = mediafire.folder_files(key, session)
                else:
                    result = [mediafire.file_info(key, session)]
                error = None
            except (mediafire.MediafireError, youtube.YoutubeError, torrent.TorrentError) as e:
                result, error = None, e.code

            with self.lock:
                if item not in self.items or item["status"] != "pending":
                    continue
                if kind == "torrent" and not error and any(
                        i.get("infohash") == result[0]["infohash"] and i["status"] in ACTIVE
                        for i in self.items if i is not item):
                    error = "error_torrent_repetido"
                if error:
                    item["status"], item["error"] = "error", error
                elif kind == "torrent":
                    item.update(result[0], resolved=True, status="queued")
                elif kind == "folder":
                    known = {i["key"] for i in self.items if i["kind"] == "file" and i["status"] in ACTIVE}
                    idx = self.items.index(item)
                    files = [self._file_item(f, item["dest"]) for f in result if f["key"] not in known]
                    self.items[idx:idx + 1] = files
                elif kind == "youtube":
                    item.update(name=result[0]["name"], formats=result[0]["formats"],
                                resolved=True, status="choosing")
                else:
                    info = result[0]
                    item.update(name=info["name"], size=info["size"], resolved=True, status="queued")
                self._save()
                self.lock.notify_all()

    @staticmethod
    def _file_item(info, dest):
        return {
            "id": uuid.uuid4().hex[:12], "kind": "file", "key": info["key"],
            "name": info["name"], "size": info["size"], "done": 0, "speed": 0,
            "status": "queued", "error": None, "resolved": True,
            "dest": dest, "subdir": info.get("subdir", ""), "path": None, "attempts": 0,
        }

    # --- descarga ---

    def _unique_path(self, item):
        folder = os.path.join(item["dest"], item["subdir"])
        base, ext = os.path.splitext(mediafire.safe_name(item["name"]))
        taken = {i["path"] for i in self.items if i.get("path") and i is not item}
        n = 0
        while True:
            name = f"{base}{ext}" if n == 0 else f"{base} ({n}){ext}"
            path = os.path.join(folder, name)
            if path not in taken and not os.path.exists(path) and not os.path.exists(path + ".part"):
                return path
            n += 1

    def _download_loop(self):
        session = mediafire._session()
        while True:
            with self.lock:
                item = None if self.paused else next(
                    (i for i in self.items if i["status"] == "queued" and i["kind"] != "torrent"), None)
                if item is None:
                    self._maybe_notify()
                    self.lock.wait()
                    continue
                item["status"] = "downloading"
                item["attempts"] = item.get("attempts", 0) + 1
                if not item.get("path") and item["kind"] != "youtube":
                    item["path"] = self._unique_path(item)
                self._save()

            if item["kind"] == "youtube":
                outcome, error = self._download_youtube(item)
            else:
                outcome, error = self._download(session, item)
            retry_later = False

            with self.lock:
                self.cancel_ids.discard(item["id"])
                item["speed"] = 0
                if item not in self.items:
                    self._remove_part(item)
                    continue
                if outcome == "done":
                    item["status"] = "done"
                    self.batch_done += 1
                elif outcome == "canceled":
                    item["status"] = "canceled"
                    item["done"] = 0
                    self._remove_part(item)
                elif outcome == "paused":
                    item["status"] = "queued"
                    item["attempts"] -= 1
                elif item["attempts"] < MAX_ATTEMPTS and error == "error_red":
                    item["status"] = "queued"
                    retry_later = True
                else:
                    item["status"], item["error"] = "error", error
                self._save()
            if retry_later:
                time.sleep(3)

    def _download(self, session, item):
        path = item["path"]
        part = path + ".part"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            url = mediafire.direct_link(item["key"], session)
        except mediafire.MediafireError as e:
            return "error", e.code
        except OSError:
            return "error", "error_disco"

        offset = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}

        try:
            with session.get(url, stream=True, headers=headers, timeout=(15, 60)) as r:
                if r.status_code == 416:
                    offset = 0
                    os.remove(part)
                    return "error", "error_red"
                r.raise_for_status()
                if "text/html" in r.headers.get("content-type", ""):
                    return "error", "error_sin_enlace"
                if r.status_code != 206:
                    offset = 0
                length = int(r.headers.get("content-length") or 0)
                with self.lock:
                    item["done"] = offset
                    if length:
                        item["size"] = offset + length

                window_start, window_bytes = time.time(), 0
                last_save = time.time()
                with open(part, "ab" if offset else "wb") as f:
                    for chunk in r.iter_content(CHUNK):
                        f.write(chunk)
                        window_bytes += len(chunk)
                        now = time.time()
                        with self.lock:
                            item["done"] += len(chunk)
                            if now - window_start >= 0.5:
                                item["speed"] = int(window_bytes / (now - window_start))
                                window_start, window_bytes = now, 0
                            if now - last_save >= SAVE_EVERY:
                                self._save()
                                last_save = now
                            if item["id"] in self.cancel_ids or item not in self.items:
                                return "canceled", None
                            if self.paused:
                                return "paused", None
        except requests.RequestException:
            return "error", "error_red"
        except OSError:
            return "error", "error_disco"

        if item["size"] and os.path.getsize(part) < item["size"]:
            return "error", "error_red"
        os.replace(part, path)
        return "done", None

    def _stop_check(self, item):
        def should_stop():
            with self.lock:
                if item["id"] in self.cancel_ids or item not in self.items:
                    return "canceled"
                return "paused" if self.paused else None
        return should_stop

    def _download_youtube(self, item):
        try:
            os.makedirs(item["dest"], exist_ok=True)
        except OSError:
            return "error", "error_disco"

        def on_progress(done, size, speed):
            with self.lock:
                item["done"], item["size"] = done, size
                if speed:
                    item["speed"] = speed

        outcome, error, path = youtube.download(item["key"], item.get("format") or "audio", item["dest"],
                                                on_progress, self._stop_check(item))
        if outcome == "done":
            with self.lock:
                item["path"] = path
                if path and os.path.exists(path):
                    item["size"] = item["done"] = os.path.getsize(path)
        return outcome, error

    def _torrent_loop(self):
        """Cada segundo concilia la sesión de libtorrent con la cola. La
        sesión se crea con el primer torrent y vive hasta que el daemon sale."""
        while True:
            time.sleep(1)
            with self.lock:
                items = [i for i in self.items if i["kind"] == "torrent"]
                if self.engine is None:
                    if torrent.lt is None or not any(i["status"] in ("queued", "downloading") for i in items):
                        continue
                    self.engine = torrent.Engine()
                before = {i["id"]: i["status"] for i in items}
                finished = self.engine.tick(items, self.paused)
                self.batch_done += finished
                if finished or any(before[i["id"]] != i["status"] for i in items):
                    self._save()
                    self.lock.notify_all()
                self._maybe_notify()

    def _close_engine(self):
        if self.engine is not None:
            self.engine.close()

    def _maybe_notify(self):
        if self.batch_done and not any(i["status"] in BUSY for i in self.items):
            subprocess.Popen(["notify-send", "-a", "Descargas", t("descargas", "titulo"),
                              t("descargas", "notif_completas", count=self.batch_done)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.batch_done = 0

    # --- servidor ---

    def serve(self):
        try:
            os.remove(SOCKET_PATH)
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(SOCKET_PATH)
        server.listen(8)
        server.settimeout(5)

        threading.Thread(target=self._resolver_loop, daemon=True).start()
        threading.Thread(target=self._download_loop, daemon=True).start()
        threading.Thread(target=self._torrent_loop, daemon=True).start()

        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                with self.lock:
                    busy = not self.paused and any(i["status"] in BUSY for i in self.items)
                    if not busy and time.time() - self.last_activity > IDLE_EXIT:
                        self._close_engine()
                        self._save()
                        return
                continue
            with conn:
                try:
                    conn.settimeout(2)
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


def _cleanup(*_):
    for path in (SOCKET_PATH, LOCK):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    os._exit(0)


def main():
    set_process_name("ff-downloads")
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    daemon = Daemon()

    def _on_term(*_):
        with daemon.lock:
            daemon._close_engine()
            daemon._save()
        _cleanup()

    signal.signal(signal.SIGTERM, _on_term)
    try:
        daemon.serve()
    finally:
        _cleanup()


if __name__ == "__main__":
    main()

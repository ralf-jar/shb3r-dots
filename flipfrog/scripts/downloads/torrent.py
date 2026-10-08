"""Torrents (magnet, .torrent por URL o archivo local), sin GTK -- usado
solo por download_daemon.py. libtorrent en el mismo proceso (python
bindings de libtorrent-rasterbar): a diferencia de MediaFire/YouTube no
van uno a la vez, todos los torrents de la cola bajan en paralelo dentro
de la sesión. No se siembra: al completarse se quita de la sesión.

Metadatos (`<infohash>.torrent`) y avance (`<infohash>.resume`, resume
data de libtorrent) en CACHE_DIR, para reanudar sin volver a verificar
tras pausar o reiniciar el daemon."""

import os
import re
import time
from urllib.parse import unquote

import requests

try:
    import libtorrent as lt
except ImportError:
    lt = None

CACHE_DIR = os.path.expanduser("~/.cache/flipfrog-torrents")
MAGNET_RE = re.compile(r'magnet:\?[^\s"\'<>]+', re.I)
URL_RE = re.compile(r'https?://[^\s"\'<>]+?\.torrent(?:\?[^\s"\'<>]*)?(?=[\s"\'<>]|$)', re.I)
FETCH_TIMEOUT = 20
MAX_TORRENT_FILE = 20 * 1024 * 1024
RESUME_EVERY = 30
CLOSE_TIMEOUT = 5


class TorrentError(Exception):
    """`code` es un código de i18n (módulo "descargas")."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_links(text):
    """[("torrent", magnet|url|ruta)] en orden de aparición, sin repetidos.
    Rutas locales una por línea (pueden traer espacios), con o sin file://."""
    found = []
    for match in sorted(list(MAGNET_RE.finditer(text)) + list(URL_RE.finditer(text)),
                        key=lambda m: m.start()):
        found.append(match.group(0))
    for line in text.splitlines():
        line = line.strip().strip("'\"")
        if line.startswith("file://"):
            line = unquote(line[7:])
        path = os.path.expanduser(line)
        if path.lower().endswith(".torrent") and os.path.isabs(path) and os.path.isfile(path):
            found.append(path)
    links = []
    for key in found:
        if ("torrent", key) not in links:
            links.append(("torrent", key))
    return links


def _hash_str(info_hashes):
    return str(info_hashes.get_best())


def _cache_path(infohash, ext):
    return os.path.join(CACHE_DIR, f"{infohash}.{ext}")


def resolve(key):
    """{"name", "size", "infohash"}. Un magnet no trae tamaño (size 0):
    los metadatos llegan de los pares ya dentro de la sesión."""
    if lt is None:
        raise TorrentError("error_sin_libtorrent")
    if key.lower().startswith("magnet:"):
        try:
            params = lt.parse_magnet_uri(key)
        except RuntimeError:
            raise TorrentError("error_torrent_invalido")
        infohash = _hash_str(params.info_hashes)
        return {"name": params.name or infohash, "size": 0, "infohash": infohash}

    if key.startswith("http"):
        try:
            r = requests.get(key, timeout=FETCH_TIMEOUT)
        except requests.RequestException:
            raise TorrentError("error_red")
        if r.status_code == 404:
            raise TorrentError("error_no_encontrado")
        if not r.ok:
            raise TorrentError("error_red")
        data = r.content
    else:
        try:
            if os.path.getsize(key) > MAX_TORRENT_FILE:
                raise TorrentError("error_torrent_invalido")
            with open(key, "rb") as f:
                data = f.read()
        except OSError:
            raise TorrentError("error_no_encontrado")

    try:
        info = lt.torrent_info(lt.bdecode(data))
    except (RuntimeError, TypeError):
        raise TorrentError("error_torrent_invalido")
    infohash = _hash_str(info.info_hashes())
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(infohash, "torrent"), "wb") as f:
        f.write(data)
    return {"name": info.name(), "size": info.total_size(), "infohash": infohash}


def forget(infohash):
    """Borra metadatos y avance guardados (torrent completado o cancelado)."""
    for ext in ("torrent", "resume"):
        try:
            os.remove(_cache_path(infohash, ext))
        except FileNotFoundError:
            pass


class Engine:
    """Sesión de libtorrent. `tick()` se llama cada segundo con el lock
    del daemon tomado: concilia la sesión con los items de la cola y
    escribe el avance en ellos."""

    def __init__(self):
        self.session = lt.session({
            "listen_interfaces": "0.0.0.0:6881,[::]:6881",
            "alert_mask": lt.alert_category.error | lt.alert_category.status | lt.alert_category.storage,
            "enable_dht": True,
        })
        self.checking = (lt.torrent_status.checking_files, lt.torrent_status.checking_resume_data)
        self.handles = {}
        self.hashes = {}
        self.errors = {}
        self.last_resume = time.time()
        os.makedirs(CACHE_DIR, exist_ok=True)

    def _add(self, item):
        infohash = item["infohash"]
        resume, meta = _cache_path(infohash, "resume"), _cache_path(infohash, "torrent")
        try:
            if os.path.exists(resume):
                with open(resume, "rb") as f:
                    params = lt.read_resume_data(f.read())
            elif os.path.exists(meta):
                params = lt.add_torrent_params()
                params.ti = lt.torrent_info(meta)
            else:
                params = lt.parse_magnet_uri(item["key"])
        except RuntimeError:
            self.errors[item["id"]] = "error_torrent_invalido"
            return
        params.save_path = item["dest"]
        params.flags &= ~lt.torrent_flags.auto_managed
        params.flags &= ~lt.torrent_flags.paused
        self.handles[item["id"]] = self.session.add_torrent(params)
        self.hashes[item["id"]] = infohash

    def _remove(self, item_id, delete_files):
        handle = self.handles.pop(item_id)
        infohash = self.hashes.pop(item_id)
        self.session.remove_torrent(handle, lt.session.delete_files if delete_files else 0)
        if delete_files:
            forget(infohash)

    def tick(self, items, paused):
        """Devuelve cuántos torrents se completaron en esta vuelta."""
        self._read_alerts()
        if paused != self.session.is_paused():
            self.session.pause() if paused else self.session.resume()

        by_id = {i["id"]: i for i in items}
        for item_id in list(self.handles):
            item = by_id.get(item_id)
            if item is None or item["status"] not in ("queued", "downloading"):
                self._remove(item_id, item is None or item["status"] == "canceled")

        finished = 0
        for item in items:
            if item["status"] not in ("queued", "downloading"):
                continue
            if item["id"] not in self.handles and item["id"] not in self.errors:
                self._add(item)
            error = self.errors.pop(item["id"], None)
            handle = self.handles.get(item["id"])
            status = handle.status() if handle else None
            if status and status.errc.value():
                error = error or "error_torrent"
            if error:
                if handle:
                    self._remove(item["id"], False)
                item["status"], item["error"], item["speed"] = "error", error, 0
                continue

            item["status"] = "queued" if paused else "downloading"
            item["peers"] = status.num_peers
            item["speed"] = status.download_payload_rate
            if not status.has_metadata:
                item["meta"] = False
                continue
            item.update(meta=True, name=status.name, size=status.total_wanted,
                        done=status.total_wanted_done)
            if status.is_finished and status.state not in self.checking:
                self._remove(item["id"], False)
                forget(item["infohash"])
                item.update(status="done", speed=0, path=os.path.join(item["dest"], status.name))
                finished += 1

        if time.time() - self.last_resume >= RESUME_EVERY:
            self.save_resume()
        return finished

    def save_resume(self):
        self.last_resume = time.time()
        for handle in self.handles.values():
            if handle.need_save_resume_data():
                handle.save_resume_data(lt.torrent_handle.save_info_dict)

    def _read_alerts(self):
        pending = 0
        ids = {_hash_str(h.info_hashes()): i for i, h in self.handles.items()}
        for alert in self.session.pop_alerts():
            if isinstance(alert, lt.save_resume_data_alert):
                infohash = _hash_str(alert.handle.info_hashes())
                try:
                    data = lt.write_resume_data_buf(alert.params)
                    tmp = _cache_path(infohash, "resume.tmp")
                    with open(tmp, "wb") as f:
                        f.write(data)
                    os.replace(tmp, _cache_path(infohash, "resume"))
                except OSError:
                    pass
                pending += 1
            elif isinstance(alert, lt.save_resume_data_failed_alert):
                pending += 1
            elif isinstance(alert, lt.file_error_alert):
                item_id = ids.get(_hash_str(alert.handle.info_hashes()))
                if item_id:
                    self.errors[item_id] = "error_disco"
        return pending

    def close(self):
        """Guarda el avance de todos antes de salir (espera hasta CLOSE_TIMEOUT)."""
        self.session.pause()
        expected = 0
        for handle in self.handles.values():
            handle.save_resume_data(lt.torrent_handle.save_info_dict)
            expected += 1
        deadline = time.time() + CLOSE_TIMEOUT
        while expected > 0 and time.time() < deadline:
            self.session.wait_for_alert(500)
            expected -= self._read_alerts()

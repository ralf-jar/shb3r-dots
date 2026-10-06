#!/usr/bin/env python3
"""
bandcamp_daemon.py
Proceso persistente dueño de mpv y de la cola de reproducción -- separado
de bandcamp_popup.py (la ventana flotante, SUPER+M) para que cerrar esa ventana
NUNCA corte la música: bandcamp_popup.py y los scripts de la isla de Waybar
(flipfrog/scripts/bandcamp/) son clientes livianos que se conectan/
desconectan, la reproducción sigue sin importar si hay algún cliente
escuchando.

Lanzado a demanda, nunca desde autostart.lua -- mismo criterio que
theme-rotator.py: la primera vez que se abre el popup (bandcamp_ipc.
ensure_daemon_running) se spawnea con PID propio en
/tmp/bandcamp-radio-daemon.pid y sigue corriendo con el popup cerrado
hasta el logout. No tiene sentido un mpv en modo idle desde el arranque
de sesión si el usuario nunca abrió "Descubre Bandcamp".

Protocolo de control: socket unix en /tmp/bandcamp-radio-ctl.sock, una
conexión = un comando (una línea JSON de pedido, una línea JSON de
respuesta, cierra). Sin conexión persistente ni pub/sub -- bandcamp_popup.py y
los scripts de la isla simplemente preguntan get_state cada 1s (ver
bandcamp_ipc.py), mucho más simple que mantener sockets abiertos de varios
clientes concurrentes para lo poco que cambia (título, posición, pausa).

"Autoecualizador" (cmd_set_auto_eq, bandcamp_popup.py autoeq_btn): cada vez que
se resuelven los tags REALES de una canción nueva (_fetch_tags), si
está prendido dispara EQ_APPLY_SCRIPT (flipfrog/scripts/audio/
eq_apply_preset.py) con esos tags, en un subprocess aparte -- ese script
mapea género -> preajuste y aplica los 6 gains a la pestaña "Sonido" del
dashboard. Acoplamiento débil (subprocess, chequeo de os.path.exists()
antes de invocar): un fallo del ecualizador nunca corta la reproducción.
Estado persistido en auto-eq.json (gitignored, como last-tag.json/
favorites.json) -- sound_module.py (pestaña "Sonido") también lo lee/
escribe directo (mismo archivo, mismo protocolo de socket) para poder
apagarlo desde el otro lado si el usuario ajusta el ecualizador a mano.
"""
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
from i18n import t
import bandcamp_api
from queue_feed import DiscoverFeed, AlbumFeed, WishlistFeed, ArtistFeed, LocalFeed
from bandcamp_mpv import MpvPlayer

SOCKET_PATH = "/tmp/bandcamp-radio-ctl.sock"
LOCK = "/tmp/bandcamp-radio-daemon.pid"
PROCESS_NAME = "ff-radio"

# "Autoecualizador" (bandcamp_popup.py, autoeq_btn) -- ver docstring del
# módulo. Si EQ_APPLY_SCRIPT falla, _apply_auto_eq() no hace nada.
EQ_APPLY_SCRIPT = os.path.join(SCRIPT_DIR, "..", "audio", "eq_apply_preset.py")
AUTO_EQ_FILE = os.path.join(SCRIPT_DIR, "auto-eq.json")


def _load_auto_eq():
    try:
        with open(AUTO_EQ_FILE) as f:
            return bool(json.load(f).get("enabled", False))
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _save_auto_eq(enabled):
    tmp = AUTO_EQ_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"enabled": enabled}, f)
    os.replace(tmp, AUTO_EQ_FILE)


def _apply_auto_eq(tags):
    if not os.path.exists(EQ_APPLY_SCRIPT):
        return
    try:
        subprocess.Popen(
            ["python3", EQ_APPLY_SCRIPT, "--tags", ",".join(tags)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


# "Me gusta -> ver álbum" (cmd_like) puede volver a cualquiera de estos
# tres modos de búsqueda una vez que el álbum termina -- DiscoverFeed
# (tags), WishlistFeed (wishlist de un fan) y ArtistFeed (discografía)
# comparten la misma interfaz de "cola resumible", a diferencia de
# AlbumFeed (nunca se "resume" un álbum, se entra a él).
RESUMABLE_FEED_TYPES = (DiscoverFeed, WishlistFeed, ArtistFeed, LocalFeed)


def _subdomain_from_url(url):
    """Extrae el subdominio real de un item_url YA conocido (la canción
    que está sonando, por ejemplo) -- pedido explícito del usuario:
    normalize_artist_slug (adivinar el subdominio a partir del nombre
    para mostrar) falla con cualquier banda cuya URL no sea EXACTAMENTE
    su nombre en minúsculas sin espacios (ej. "Depravity" con nombre
    para mostrar "DEPRAVITY" pero slug real "depravitydeath" -- caso
    real confirmado a mano, hay colisiones de nombre en Bandcamp). Si ya
    se conoce el item_url real de lo que está sonando, el subdominio de
    ESE link es siempre correcto, no hace falta adivinar nada."""
    if not url:
        return None
    m = re.match(r"https?://([^./]+)\.bandcamp\.com", url)
    return m.group(1) if m else None


def _no_results_message(feed):
    """El mensaje de "sin resultados" antes era el mismo texto fijo
    ("No hay más resultados para este tag.") para CUALQUIER feed
    agotado -- confuso de verdad cuando lo que falló fue una búsqueda de
    Artista/Wishlist (ej. slug adivinado mal, cuenta inactiva), no un
    tag. Cada tipo de feed ya tiene el nombre que buscó guardado
    (display_name/username/subdomain), se usa acá para un mensaje real."""
    if isinstance(feed, WishlistFeed):
        return t("bandcamp", "no_results_wishlist", name=feed.display_name)
    if isinstance(feed, ArtistFeed):
        return t("bandcamp", "no_results_artist", name=feed.display_name)
    if isinstance(feed, LocalFeed):
        return t("bandcamp", "no_results_local", name=feed.query or feed.root)
    return t("bandcamp", "no_results_tag")


class Daemon:
    def __init__(self):
        self._lock = threading.Lock()
        self.saved_discover_feed = None
        self.current_feed = None
        self.current_track = None
        self.current_album = None
        self.previous = None
        self.tags = []
        self.recommendations = []
        self.artist_albums = []
        self.duration = 0
        self.position = 0
        self.paused = False
        self.loading = False
        self.status = "Elige un tag para empezar"
        self.auto_eq = _load_auto_eq()
        self._tag_gen = 0
        self._rec_gen = 0
        self._artist_gen = 0

        # Catálogo elegible (bandcamp_popup.py, pestaña "Buscar") -- una búsqueda
        # nueva arma el feed y lo deja ACÁ, sin tocar current_feed/mpv,
        # hasta que el usuario elige un ítem (cmd_play_pending). Así
        # buscar nunca corta lo que está sonando. pending_catalog solo se
        # usa para discover/wishlist -- en modo "artist" state() expone
        # directo self.artist_albums (ya progresivo, ver
        # _fetch_artist_albums), sin duplicar el fetch.
        self.pending_feed = None
        self.pending_mode = None
        self.pending_catalog = []
        self.pending_loading = False
        self.pending_error = None
        self.pending_label = None
        self._pending_gen = 0

        self.mpv = MpvPlayer(
            on_end_file=self._on_track_ended,
            on_error=self._on_mpv_error,
            on_property_change=self._on_mpv_property,
        )

    # ---- estado expuesto a los clientes ----

    def state(self):
        with self._lock:
            t = self.current_track or {}
            a = self.current_album or {}
            in_album = isinstance(self.current_feed, AlbumFeed)
            return {
                "loading": self.loading,
                "paused": self.paused,
                "status": self.status,
                "title": t.get("title"),
                "artist": t.get("artist") or a.get("band_name"),
                "album_title": a.get("album_title"),
                "art_url": a.get("art_url"),
                "item_url": a.get("item_url"),
                "tags": list(self.tags),
                "auto_eq": self.auto_eq,
                "recommendations": list(self.recommendations),
                "artist_albums": list(self.artist_albums),
                "duration": self.duration,
                "position": self.position,
                "can_prev": self.previous is not None,
                "mode": "album" if in_album else "discover",
                "local": bool(a.get("local")),
                "local_path": t.get("stream_url") if a.get("local") else None,
                "local_playlist_gen": (
                    self.current_feed.playlist_gen if isinstance(self.current_feed, LocalFeed) else None
                ),
                "can_resume_discover": self.saved_discover_feed is not None,
                "search_label": (
                    self.saved_discover_feed.label if in_album and self.saved_discover_feed
                    else (self.current_feed.label if isinstance(self.current_feed, RESUMABLE_FEED_TYPES) else None)
                ),
                "pending_mode": self.pending_mode,
                "pending_label": self.pending_label,
                "pending_loading": self.pending_loading,
                "pending_error": self.pending_error,
                "pending_catalog": (
                    list(self.artist_albums) if self.pending_mode == "artist"
                    else list(self.pending_catalog)
                ),
            }

    # ---- comandos ----

    def _start_pending(self, feed, mode, label):
        """Común a los tres cmd_search_* -- arma el catálogo elegible
        SIN tocar current_feed/mpv (pedido explícito del usuario: buscar
        no debe cortar lo que está sonando). Devuelve la generación para
        que el llamador la pase al hilo de fetch, así un resultado viejo
        se descarta si el usuario ya lanzó otra búsqueda mientras
        corría."""
        self._pending_gen += 1
        gen = self._pending_gen
        self.pending_feed = feed
        self.pending_mode = mode
        self.pending_catalog = []
        self.pending_loading = True
        self.pending_error = None
        self.pending_label = label
        return gen

    def _fetch_pending_catalog(self, feed, gen):
        """Hilo de catálogo para Discover/Wishlist -- Artista reusa
        _fetch_artist_albums tal cual (ver cmd_search_artist), no pasa
        por acá."""
        try:
            catalog = feed.catalog_preview()
        except Exception as e:
            with self._lock:
                if gen == self._pending_gen:
                    self.pending_loading = False
                    self.pending_error = t("bandcamp", "error_generic", msg=e)
            return
        with self._lock:
            if gen != self._pending_gen:
                return  # el usuario ya buscó otra cosa mientras esto cargaba
            self.pending_loading = False
            self.pending_catalog = catalog
            if not catalog:
                self.pending_error = _no_results_message(feed)

    def cmd_search(self, tags, slice_):
        feed = DiscoverFeed(tags, slice=slice_)
        with self._lock:
            gen = self._start_pending(feed, "discover", feed.label)
        threading.Thread(target=self._fetch_pending_catalog, args=(feed, gen), daemon=True).start()

    def cmd_search_wishlist(self, username):
        real_username = bandcamp_api.resolve_username(username)
        feed = WishlistFeed(real_username, display_name=username)
        with self._lock:
            gen = self._start_pending(feed, "wishlist", feed.label)
        threading.Thread(target=self._fetch_pending_catalog, args=(feed, gen), daemon=True).start()

    def cmd_search_local(self, query):
        feed = LocalFeed(query)
        with self._lock:
            gen = self._start_pending(feed, "local", feed.label)
        threading.Thread(target=self._fetch_pending_catalog, args=(feed, gen), daemon=True).start()

    def cmd_load_more_catalog(self):
        """Flecha derecha de catalog_row al fondo de la tira (bandcamp_popup.py,
        _on_strip_arrow_clicked) -- fuerza otra página del feed actual
        (Discover/Wishlist) aunque el buffer no esté vacío todavía.
        No-op si ya hay un fetch en curso (evita dos hilos mutando el
        mismo feed._buffer a la vez) o si el modo activo es Artista (esa
        discografía crece sola vía _fetch_artist_albums, no pasa por
        catalog_preview())."""
        with self._lock:
            if self.pending_loading:
                return
            feed = self.pending_feed
            gen = self._pending_gen
            if feed is None or not hasattr(feed, "catalog_preview"):
                return
            self.pending_loading = True
        threading.Thread(target=self._fetch_more_catalog, args=(feed, gen), daemon=True).start()

    def _fetch_more_catalog(self, feed, gen):
        try:
            feed._fetch_more()
            catalog = feed.catalog_preview()
        except Exception as e:
            with self._lock:
                if gen == self._pending_gen:
                    self.pending_loading = False
                    self.pending_error = t("bandcamp", "error_generic", msg=e)
            return
        with self._lock:
            if gen != self._pending_gen:
                return  # el usuario ya buscó otra cosa mientras esto cargaba
            self.pending_loading = False
            self.pending_catalog = catalog

    def cmd_search_artist(self, name, item_url_hint=None):
        # Si ya se conoce el item_url real (clic en el nombre del artista
        # de la canción que está sonando, ver bandcamp_popup.py/_on_artist_clicked)
        # se usa ESE subdominio directo -- confirmado a mano que adivinar
        # a partir del nombre falla con colisiones de nombre reales en
        # Bandcamp. Solo cae a adivinar si no hay pista (búsqueda manual
        # en modo "Artista", sin canción de referencia).
        subdomain = _subdomain_from_url(item_url_hint) or bandcamp_api.resolve_artist_slug(name)
        feed = ArtistFeed(subdomain, display_name=name)
        with self._lock:
            self._start_pending(feed, "artist", feed.label)
            self.artist_albums = []
        # Discografía completa vía _fetch_artist_albums -- ya progresiva
        # (álbum por álbum) y con su propio generation guard
        # (_artist_gen), reusada tal cual: llena self.artist_albums, que
        # state() expone como pending_catalog mientras pending_mode ==
        # "artist" (ver state()) y sigue siendo la tira "Álbumes de
        # <banda>" de siempre una vez que se elige un álbum inicial.
        self._fetch_artist_albums(subdomain)

    def cmd_play_pending(self, item_url):
        """Commit del catálogo elegible (bandcamp_popup.py, clic en un chip de la
        pestaña "Buscar") -- recién acá se toca current_feed/mpv. Mismo
        patrón que _advance()/_advance_worker: hilo aparte (pop_item/
        start_from_album pueden pegarle a la red, ver queue_feed.py) +
        guard de self.loading, descarta el resultado si el usuario ya
        lanzó otra búsqueda mientras tanto."""
        with self._lock:
            if self.loading or self.pending_feed is None:
                return
            self.loading = True
            feed = self.pending_feed
            mode = self.pending_mode
        threading.Thread(target=self._play_pending_worker, args=(feed, mode, item_url), daemon=True).start()

    def _play_pending_worker(self, feed, mode, item_url):
        try:
            result = feed.start_from_album(item_url) if mode == "artist" else feed.pop_item(item_url)
        except Exception as e:
            with self._lock:
                self.loading = False
                self.status = f"Error: {e}"
            return
        with self._lock:
            self.loading = False
            if feed is not self.pending_feed or result is None:
                return  # se buscó otra cosa mientras cargaba, o el ítem ya no está
            track, album = result
            self.current_feed = feed
            self.saved_discover_feed = None
            if mode != "artist":
                # Modo Artista ya dejó self.artist_albums lleno durante
                # el catálogo elegible (_fetch_artist_albums) -- se
                # mantiene para la tira "Álbumes de <banda>" mientras
                # suena. Cualquier otro modo no tiene discografía que
                # mostrar, limpia lo que haya quedado de una búsqueda de
                # Artista anterior.
                self.artist_albums = []
            # pending_feed/mode/catalog/label NO se limpian acá (pedido
            # explícito del usuario, corrige un bug real: la pestaña
            # "Buscar" se quedaba vacía apenas se elegía un chip, aunque
            # el usuario quisiera seguir mirando el resto del catálogo).
            # feed sigue siendo el MISMO objeto que current_feed -- elegir
            # otro chip de la misma tanda vuelve a pop_item/
            # start_from_album sobre el feed real que ya está sonando,
            # sin re-buscar nada. Un cmd_search_* nuevo pisa estos cuatro
            # de cero vía _start_pending, así que no queda nunca "pegado"
            # a una búsqueda vieja.
            self.previous = None
            self._load_track(track, album)
            self.status = f"«{feed.label}»" if not feed.skipped else \
                f"«{feed.label}» — {feed.skipped} canciones no disponibles saltadas"

    def cmd_play_album(self, item_url, meta=None):
        """Salta directo a un álbum sin esperar el auto-play secuencial --
        clic en un chip de recomendaciones/discografía en el popup, o en
        un álbum favorito (bandcamp_popup.py, favorites.json). `meta` viene
        completo de entrada solo en el caso de un favorito -- ese álbum
        ya no está necesariamente en artist_albums/recommendations de
        ESTA sesión (pudo guardarse en una búsqueda anterior), así que no
        tiene sentido buscarlo ahí; el cliente ya conoce sus metadatos
        (item_url/band_name/album_title/art_url) desde favorites.json y
        los manda directo. Guarda el feed actual para poder volver (mismo
        mecanismo que cmd_like), a menos que ya estuviera DENTRO de un
        álbum sin nada que resumir."""
        with self._lock:
            if meta is None:
                meta = next((a for a in self.artist_albums if a.get("item_url") == item_url), None)
                if meta is None:
                    for section in self.recommendations:
                        meta = next((it for it in section["items"] if it.get("item_url") == item_url), None)
                        if meta:
                            break
            if meta is None:
                return
            if isinstance(self.current_feed, RESUMABLE_FEED_TYPES):
                self.saved_discover_feed = self.current_feed
            self.current_feed = AlbumFeed(item_url, meta)
            self.status = f"Reproduciendo álbum: {meta.get('album_title')}"
            self.previous = None
        self._advance()

    def cmd_skip(self):
        self._advance()

    def cmd_prev(self):
        with self._lock:
            if not self.previous:
                return
            track, album = self.previous
            swap = (self.current_track, self.current_album) if self.current_track else None
            self._load_track(track, album, remember_previous=False)
            self.previous = swap
            self.status = "Repitiendo la canción anterior"

    def cmd_toggle_pause(self):
        with self._lock:
            paused = not self.paused
        self.mpv.set_pause(paused)
        with self._lock:
            self.paused = paused

    def local_playlist(self):
        with self._lock:
            feed = self.current_feed
            if not isinstance(feed, LocalFeed):
                return {"gen": None, "tracks": []}
            return {"gen": feed.playlist_gen, "tracks": feed.playlist()}

    def cmd_play_local_track(self, index):
        with self._lock:
            feed = self.current_feed
            if not isinstance(feed, LocalFeed) or not feed.jump(index):
                return
        self._advance()

    def cmd_volume(self, delta):
        self.mpv.add_volume(delta)

    def cmd_like(self):
        with self._lock:
            if isinstance(self.current_feed, RESUMABLE_FEED_TYPES) and self.current_album \
                    and self.current_album.get("item_url"):
                self.saved_discover_feed = self.current_feed
                self.current_feed = AlbumFeed(self.current_album["item_url"], self.current_album)
                self.status = f"Reproduciendo álbum: {self.current_album.get('album_title')}"
            elif self.saved_discover_feed:
                self.current_feed = self.saved_discover_feed
                self.saved_discover_feed = None
            else:
                return
        self._advance()

    def cmd_seek(self, position):
        self.mpv.seek(position)

    def cmd_set_auto_eq(self, enabled):
        with self._lock:
            self.auto_eq = bool(enabled)
            _save_auto_eq(self.auto_eq)
            tags = list(self.tags)
        # Si se prende con una canción ya sonando, aplica de una vez --
        # no hace falta esperar a que cambie de canción para ver el
        # efecto (fuera del lock: _apply_auto_eq solo lanza un
        # subprocess, no toca estado del daemon).
        if self.auto_eq and tags:
            _apply_auto_eq(tags)

    # ---- avance de cola ----

    def _advance(self):
        with self._lock:
            if self.loading or self.current_feed is None:
                return
            self.loading = True
            feed = self.current_feed
        threading.Thread(target=self._advance_worker, args=(feed,), daemon=True).start()

    def _advance_worker(self, feed):
        try:
            result = feed.next_track()
        except Exception as e:
            with self._lock:
                self.loading = False
                self.status = f"Error: {e}"
            return

        resume = False
        with self._lock:
            self.loading = False
            if feed is not self.current_feed:
                return  # el usuario ya buscó otro tag / cambió de modo mientras esto cargaba
            if result is None:
                if isinstance(feed, AlbumFeed) and self.saved_discover_feed:
                    self.status = "Álbum terminado, volviendo al descubrimiento…"
                    self.current_feed = self.saved_discover_feed
                    self.saved_discover_feed = None
                    resume = True
                else:
                    self.status = _no_results_message(feed)
            else:
                track, album = result
                self._load_track(track, album)
                label = "álbum completo" if isinstance(feed, AlbumFeed) else f"«{feed.label}»"
                if feed.skipped:
                    self.status = f"{label} — {feed.skipped} canciones no disponibles saltadas"
                else:
                    self.status = label
        if resume:
            self._advance()

    def _load_track(self, track, album, remember_previous=True):
        """Llamado siempre con self._lock ya tomado."""
        if remember_previous and self.current_track is not None:
            self.previous = (self.current_track, self.current_album)
        self.current_track = track
        self.current_album = album
        self.duration = track.get("duration") or 0
        self.position = 0
        self.paused = False
        self.tags = []
        self.recommendations = []
        self.mpv.load(track["stream_url"])
        self._fetch_tags(album.get("item_url"))
        self._fetch_recommendations(album.get("item_url"))

    def _fetch_tags(self, item_url):
        """Llamado siempre con self._lock ya tomado -- solo agenda el
        hilo, no bloquea ni vuelve a tomar el lock acá."""
        self._tag_gen += 1
        gen = self._tag_gen
        if not item_url:
            return

        def worker():
            try:
                tags = bandcamp_api.fetch_tags(item_url)
            except Exception:
                return
            with self._lock:
                if gen != self._tag_gen:
                    return
                self.tags = tags
                auto_eq = self.auto_eq
            # "Autoecualizador" (ver EQ_APPLY_SCRIPT arriba) -- recién acá
            # se conocen los tags REALES de la canción nueva (antes de
            # esto, self.tags queda vacío desde _load_track). Fuera del
            # lock: _apply_auto_eq solo lanza un subprocess, no toca
            # estado del daemon.
            if auto_eq and tags:
                _apply_auto_eq(tags)

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_recommendations(self, item_url):
        """Mismo patrón que _fetch_tags -- hilo aparte, se descarta si
        para cuando responde ya se pasó a otra canción (gen)."""
        self._rec_gen += 1
        gen = self._rec_gen
        if not item_url:
            return

        def worker():
            try:
                recs = bandcamp_api.fetch_recommendations(item_url)
            except Exception:
                return
            with self._lock:
                if gen == self._rec_gen:
                    self.recommendations = recs

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_artist_albums(self, subdomain):
        """Discografía completa para la tira "Álbumes de <banda>" del
        popup -- se llena de forma PROGRESIVA (álbum por álbum, no todo
        de golpe al final) para que la tira empiece a mostrar carátulas
        apenas hay alguna resuelta, en vez de esperar fetch_album() de
        los N álbumes completos. gen descarta resultados de una búsqueda
        de artista vieja si el usuario ya buscó otra cosa mientras esto
        corría."""
        self._artist_gen += 1
        gen = self._artist_gen

        def worker():
            try:
                urls = bandcamp_api.fetch_artist_albums(subdomain)
            except Exception:
                return
            albums = []
            for url in urls:
                with self._lock:
                    if gen != self._artist_gen:
                        return
                try:
                    _tracks, meta = bandcamp_api.fetch_album(url)
                except Exception:
                    continue
                if not meta:
                    continue
                albums.append(meta)
                with self._lock:
                    if gen != self._artist_gen:
                        return
                    self.artist_albums = list(albums)

        threading.Thread(target=worker, daemon=True).start()

    # ---- callbacks de mpv (hilo lector de bandcamp_mpv.py) ----

    def _on_track_ended(self):
        self._advance()

    def _on_mpv_error(self, msg):
        with self._lock:
            self.status = f"Error de mpv: {msg.get('error', msg)}"

    def _on_mpv_property(self, name, value):
        with self._lock:
            if name == "duration" and value:
                self.duration = value
            elif name == "time-pos" and value is not None:
                self.position = value

    def shutdown(self):
        self.mpv.shutdown()


def _handle_conn(daemon, conn):
    try:
        conn.settimeout(5)
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk
        req = json.loads(buf.split(b"\n", 1)[0])
        cmd = req.get("cmd")

        if cmd == "get_state":
            reply = daemon.state()
        elif cmd == "search":
            daemon.cmd_search(req.get("tags") or [], req.get("slice") or "new")
            reply = {"ok": True}
        elif cmd == "search_wishlist":
            username = (req.get("username") or "").strip()
            if not username:
                reply = {"error": "falta username"}
            else:
                daemon.cmd_search_wishlist(username)
                reply = {"ok": True}
        elif cmd == "search_artist":
            name = (req.get("name") or "").strip()
            if not name:
                reply = {"error": "falta nombre"}
            else:
                daemon.cmd_search_artist(name, item_url_hint=req.get("item_url"))
                reply = {"ok": True}
        elif cmd == "search_local":
            daemon.cmd_search_local(req.get("query") or "")
            reply = {"ok": True}
        elif cmd == "play_album":
            item_url = (req.get("item_url") or "").strip()
            if not item_url:
                reply = {"error": "falta item_url"}
            else:
                daemon.cmd_play_album(item_url, meta=req.get("meta"))
                reply = {"ok": True}
        elif cmd == "play_pending":
            item_url = (req.get("item_url") or "").strip()
            if not item_url:
                reply = {"error": "falta item_url"}
            else:
                daemon.cmd_play_pending(item_url)
                reply = {"ok": True}
        elif cmd == "skip":
            daemon.cmd_skip()
            reply = {"ok": True}
        elif cmd == "prev":
            daemon.cmd_prev()
            reply = {"ok": True}
        elif cmd == "toggle_pause":
            daemon.cmd_toggle_pause()
            reply = {"ok": True}
        elif cmd == "get_local_playlist":
            reply = daemon.local_playlist()
        elif cmd == "play_local_track":
            daemon.cmd_play_local_track(int(req.get("index") or 0))
            reply = {"ok": True}
        elif cmd == "volume":
            daemon.cmd_volume(req.get("delta") or 0)
            reply = {"ok": True}
        elif cmd == "like":
            daemon.cmd_like()
            reply = {"ok": True}
        elif cmd == "seek":
            daemon.cmd_seek(req.get("position") or 0)
            reply = {"ok": True}
        elif cmd == "set_auto_eq":
            daemon.cmd_set_auto_eq(bool(req.get("enabled")))
            reply = {"ok": True}
        elif cmd == "load_more_catalog":
            daemon.cmd_load_more_catalog()
            reply = {"ok": True}
        else:
            reply = {"error": "comando desconocido"}

        conn.sendall((json.dumps(reply) + "\n").encode())
    except (OSError, json.JSONDecodeError):
        pass
    finally:
        conn.close()


def main():
    common.set_process_name(PROCESS_NAME)

    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))

    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    daemon = Daemon()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(8)

    def cleanup(*_):
        try:
            daemon.shutdown()
        except Exception:
            pass
        for path in (SOCKET_PATH, LOCK):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    try:
        while True:
            conn, _ = server.accept()
            threading.Thread(target=_handle_conn, args=(daemon, conn), daemon=True).start()
    finally:
        cleanup()


if __name__ == "__main__":
    main()

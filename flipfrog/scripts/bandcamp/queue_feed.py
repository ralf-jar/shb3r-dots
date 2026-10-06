"""
queue_feed.py
Cuatro fuentes de pistas para bandcamp_daemon.py, misma interfaz (`next_track()`
-> (track, album_meta) o None si no hay más):

- DiscoverFeed: el modo "descubrimiento" -- pagina el feed de un tag
  (bandcamp_api.discover), un track por álbum (su featured_track),
  saltando en silencio los álbumes sin preview streamable. `skipped`
  cuenta cuántos se saltaron, para mostrarlo en la UI.
- AlbumFeed: el modo "me gusta" -- todas las pistas reproducibles de UN
  álbum (bandcamp_api.fetch_album_tracks), mismo criterio de salto.
- WishlistFeed: la wishlist pública de un fan de Bandcamp ("u:<usuario>"
  en el buscador de bandcamp_popup.py) -- primer lote directo de la página
  (bandcamp_api.fetch_wishlist_page, ya trae stream_url), lotes
  siguientes vía la API de fancollection (bandcamp_api.fetch_wishlist_more,
  un fetch extra por pista -- mismo costo que AlbumFeed).
- ArtistFeed: la discografía completa de una banda/sello
  ("b:<nombre>"/"a:<nombre>" en el buscador) -- descubre la lista de
  álbumes (bandcamp_api.fetch_artist_albums) y los reproduce uno por uno
  con bandcamp_api.fetch_album(), como una cola de AlbumFeed encadenados.

`album_meta` siempre trae {item_url, band_name, album_title, art_url}
-- lo que necesita la tarjeta "reproduciendo ahora" (portada, link de
compra) sin pedir nada más a la API en ese momento.
"""
import os
import random

import bandcamp_api
import local_library
from i18n import t


class DiscoverFeed:
    def __init__(self, tags, slice="new", size=20):
        # `tags`: un tag (str) o una lista -- Bandcamp ya soporta
        # combinar varios en una sola búsqueda (ver bandcamp_api.discover).
        self.tags = [tags] if isinstance(tags, str) else list(tags)
        self.slice = slice
        self.size = size
        self.skipped = 0
        self._cursor = None
        self._buffer = []
        self._exhausted = False

    @property
    def label(self):
        return " + ".join(self.tags)

    def _fetch_more(self):
        if self._exhausted:
            return
        data = bandcamp_api.discover(self.tags, slice=self.slice, cursor=self._cursor, size=self.size)
        results = data.get("results") or []
        self._cursor = data.get("cursor")
        if not results:
            self._exhausted = True
            return
        for r in results:
            ft = r.get("featured_track")
            if ft and ft.get("stream_url"):
                self._buffer.append(r)
            else:
                self.skipped += 1

    def next_track(self):
        while not self._buffer and not self._exhausted:
            self._fetch_more()
        if not self._buffer:
            return None
        return self._to_track_album(self._buffer.pop(0))

    @staticmethod
    def _to_track_album(r):
        ft = r["featured_track"]
        track = {
            "title": ft.get("title"),
            "artist": r.get("band_name"),
            "stream_url": ft.get("stream_url"),
            "duration": ft.get("duration"),
        }
        image = r.get("primary_image") or {}
        album = {
            "item_url": r.get("item_url"),
            "band_name": r.get("band_name"),
            "album_title": r.get("title"),
            "art_url": bandcamp_api.art_url(image.get("image_id")),
        }
        return track, album

    def catalog_preview(self):
        """Cátalogo elegible para bandcamp_popup.py (pestaña "Buscar") -- asegura
        al menos un fetch pero NO consume `_buffer` (a diferencia de
        next_track()), para que el usuario pueda ver los resultados y
        elegir sin arrancar nada todavía. Ver pop_item() para el commit
        real cuando elige uno."""
        if not self._buffer and not self._exhausted:
            self._fetch_more()
        out = []
        for r in self._buffer:
            ft = r.get("featured_track") or {}
            image = r.get("primary_image") or {}
            out.append({
                "item_url": r.get("item_url"),
                "band_name": r.get("band_name"),
                "album_title": r.get("title"),
                "track_title": ft.get("title"),
                "art_url": bandcamp_api.art_url(image.get("image_id")),
            })
        return out

    def pop_item(self, item_url):
        """Saca de `_buffer` el ítem elegido en el catálogo (bandcamp_daemon.py,
        cmd_play_pending) -- el resto queda para que next_track() lo siga
        consumiendo en modo radio normal, sin repetir el elegido."""
        for i, r in enumerate(self._buffer):
            if r.get("item_url") == item_url:
                return self._to_track_album(self._buffer.pop(i))
        return None


class AlbumFeed:
    def __init__(self, item_url, album_meta):
        self.item_url = item_url
        self.album_meta = album_meta
        self.skipped = 0
        self._tracks = None
        self._idx = 0

    def _ensure_loaded(self):
        if self._tracks is not None:
            return
        self._tracks = []
        for t in bandcamp_api.fetch_album_tracks(self.item_url):
            if t.get("stream_url"):
                self._tracks.append(t)
            else:
                self.skipped += 1

    def next_track(self):
        self._ensure_loaded()
        if self._idx >= len(self._tracks):
            return None
        track = self._tracks[self._idx]
        self._idx += 1
        return track, self.album_meta


class WishlistFeed:
    """`username` ya resuelto (bandcamp_api.resolve_username) antes de
    llegar acá -- este feed no conoce alias, solo el slug real de la
    URL. Primer lote sin costo extra (bandcamp_api.fetch_wishlist_page,
    ya trae stream_url); agotado ese lote, pagina con fetch_wishlist_more
    mientras la API diga que hay más (`_exhausted` corta ahí, no antes:
    la última página igual hay que terminar de reproducirla)."""
    def __init__(self, username, display_name=None, size=50):
        self.username = username
        self.display_name = display_name or username
        self.size = size
        self.skipped = 0
        self._buffer = []
        self._fan_id = None
        self._token = None
        self._first_fetch_done = False
        self._exhausted = False

    @property
    def label(self):
        return t("bandcamp", "label_wishlist_of", name=self.display_name)

    def _fetch_more(self):
        if self._exhausted:
            return
        if not self._first_fetch_done:
            self._first_fetch_done = True
            fan_id, tracks, token = bandcamp_api.fetch_wishlist_page(self.username, size=self.size)
            self._fan_id = fan_id
            self._token = token
            if fan_id is None or not tracks:
                self._exhausted = True
                return
            self._buffer.extend(tracks)
            return

        if self._fan_id is None or self._token is None:
            self._exhausted = True
            return
        results, next_token, more_available = bandcamp_api.fetch_wishlist_more(
            self._fan_id, self._token, size=self.size)
        self._token = next_token
        if not more_available:
            self._exhausted = True  # esta es la última página -- igual se deja reproducir lo que trajo
        if not results:
            self._exhausted = True
            return
        self._buffer.extend(results)

    def next_track(self):
        while not self._buffer and not self._exhausted:
            self._fetch_more()
        if not self._buffer:
            return None
        return self._buffer.pop(0)

    def catalog_preview(self):
        """Mismo contrato que DiscoverFeed.catalog_preview() -- `_buffer`
        acá ya son tuplas (track, album), solo hace falta mapearlas a la
        forma común sin consumirlas."""
        if not self._buffer and not self._exhausted:
            self._fetch_more()
        return [{
            "item_url": album.get("item_url"),
            "band_name": album.get("band_name"),
            "album_title": album.get("album_title"),
            "track_title": track.get("title"),
            "art_url": album.get("art_url"),
        } for track, album in self._buffer]

    def pop_item(self, item_url):
        for i, (track, album) in enumerate(self._buffer):
            if album.get("item_url") == item_url:
                return self._buffer.pop(i)
        return None


class ArtistFeed:
    """`subdomain` ya resuelto (bandcamp_api.resolve_artist_slug) antes
    de llegar acá, mismo criterio que WishlistFeed con el username --
    este feed no conoce alias, solo el subdominio real. Descubre la
    lista de álbumes una sola vez (fetch_artist_albums) y los va
    consumiendo con fetch_album() uno por uno -- a diferencia de
    AlbumFeed (un álbum fijo conocido de antemano), acá se sabe la lista
    completa recién en el primer next_track()."""
    def __init__(self, subdomain, display_name=None):
        self.subdomain = subdomain
        self.display_name = display_name or subdomain
        self.skipped = 0
        self._album_urls = None
        self._album_idx = 0
        self._track_buffer = []
        self._current_album_meta = None

    @property
    def label(self):
        return t("bandcamp", "label_discography_of", name=self.display_name)

    def _load_next_album(self):
        if self._album_urls is None:
            self._album_urls = bandcamp_api.fetch_artist_albums(self.subdomain)

        while self._album_idx < len(self._album_urls):
            item_url = self._album_urls[self._album_idx]
            self._album_idx += 1
            try:
                tracks, album_meta = bandcamp_api.fetch_album(item_url)
            except Exception:
                continue
            playable = [t for t in tracks if t.get("stream_url")]
            self.skipped += len(tracks) - len(playable)
            if playable:
                self._current_album_meta = album_meta
                self._track_buffer = playable
                return True
        return False

    def next_track(self):
        while not self._track_buffer:
            if not self._load_next_album():
                return None
        track = self._track_buffer.pop(0)
        return track, self._current_album_meta

    def start_from_album(self, item_url):
        """Commit del catálogo elegible (bandcamp_daemon.py, cmd_play_pending) --
        a diferencia de _load_next_album (avanza secuencial desde
        _album_idx), acá el usuario elige CUALQUIER álbum de la
        discografía como punto de partida. Deja _album_idx apuntando
        DESPUÉS del elegido para que el modo radio siga con el álbum
        siguiente, nunca repitiendo el que se acaba de elegir ni
        reiniciando desde el principio de la discografía."""
        if self._album_urls is None:
            self._album_urls = bandcamp_api.fetch_artist_albums(self.subdomain)
        try:
            idx = self._album_urls.index(item_url)
        except ValueError:
            return None
        try:
            tracks, album_meta = bandcamp_api.fetch_album(item_url)
        except Exception:
            return None
        playable = [t for t in tracks if t.get("stream_url")]
        self.skipped += len(tracks) - len(playable)
        if not playable:
            return None
        self._current_album_meta = album_meta
        self._track_buffer = playable
        self._album_idx = idx + 1
        return self.next_track()


class LocalFeed:
    """Modo "Local" -- `query` vacío lista la biblioteca entera
    (local_library.default_music_dir()); si es una carpeta existente se
    usa como raíz; si no, filtra carpetas por nombre y suma un ítem con
    las pistas cuyo nombre coincide. Cada ítem se reproduce en aleatorio;
    al acabar una carpeta sigue con la siguiente del catálogo."""
    def __init__(self, query=""):
        query = (query or "").strip()
        expanded = os.path.expanduser(query)
        if query and os.path.isdir(expanded):
            self.root, self.query = os.path.abspath(expanded), ""
        else:
            self.root, self.query = local_library.default_music_dir(), query
        self.skipped = 0
        self._items = None
        self._order = []
        self._special = set()
        self._playlist = []
        self._pos = -1
        self._current = None
        self.playlist_gen = 0

    @property
    def label(self):
        return t("bandcamp", "label_local", name=self.query or os.path.basename(self.root) or self.root)

    def _load(self):
        if self._items is not None:
            return
        folders = local_library.list_folders(self.root)
        q = self.query.casefold()
        items = {}
        order = []
        special = set()
        if not q:
            everything = [f for _, files in folders for f in files]
            if len(folders) > 1:
                key = local_library.URL_PREFIX + self.root + local_library.ALL_SUFFIX
                items[key] = (t("bandcamp", "local_all"), everything)
                order.append(key)
                special.add(key)
        else:
            matches = [f for _, files in folders for f in files
                       if q in local_library.title_from_path(f).casefold()]
            if matches:
                key = local_library.URL_PREFIX + self.root + "#q=" + self.query
                items[key] = (t("bandcamp", "local_matches", query=self.query), matches)
                order.append(key)
                special.add(key)
        for path, files in folders:
            rel = os.path.relpath(path, self.root)
            if q and q not in rel.casefold():
                continue
            key = local_library.URL_PREFIX + path
            items[key] = (os.path.basename(path) if rel != "." else os.path.basename(self.root), files)
            order.append(key)
        self._items = items
        self._order = order
        self._special = special

    def _fetch_more(self):
        self._load()

    def catalog_preview(self):
        self._load()
        out = []
        for key in self._order:
            name, files = self._items[key]
            out.append({
                "item_url": key,
                "band_name": t("bandcamp", "local_track_count", n=len(files)),
                "album_title": name,
                "track_title": name,
                "art_url": local_library.folder_cover(files),
            })
        return out

    def _start(self, key):
        name, files = self._items[key]
        self._current = key
        self._playlist = random.sample(files, len(files))
        self._pos = -1
        self.playlist_gen += 1

    def playlist(self):
        """Orden aleatorio de la carpeta en curso (lo ya sonado incluido)."""
        return [{"path": p, "title": local_library.title_from_path(p)} for p in self._playlist]

    def jump(self, index):
        """La próxima next_track() devuelve la pista `index` de playlist()."""
        if 0 <= index < len(self._playlist):
            self._pos = index - 1
            return True
        return False

    def pop_item(self, item_url):
        self._load()
        if item_url not in self._items:
            return None
        self._start(item_url)
        return self.next_track()

    def next_track(self):
        self._load()
        if self._current is None:
            if not self._order:
                return None
            self._start(self._order[0])
        while self._pos + 1 >= len(self._playlist):
            if self._current in self._special:
                return None
            idx = self._order.index(self._current) + 1
            if idx >= len(self._order):
                return None
            self._start(self._order[idx])

        self._pos += 1
        path = self._playlist[self._pos]
        artist, duration = local_library.probe(path)
        folder_name = self._items[self._current][0]
        track = {
            "title": local_library.title_from_path(path),
            "artist": artist,
            "stream_url": path,
            "duration": duration,
        }
        album = {
            "item_url": None,
            "band_name": artist,
            "album_title": folder_name,
            "art_url": local_library.cover_for(path),
            "local": True,
        }
        return track, album


if __name__ == "__main__":
    feed = DiscoverFeed("ambient", slice="new", size=5)
    for _ in range(6):
        result = feed.next_track()
        if result is None:
            print("sin más pistas")
            break
        track, album = result
        print(f"{album['band_name']} - {track['title']} ({album['item_url']})")
    print("saltadas:", feed.skipped)

    album_feed = AlbumFeed(album["item_url"], album)
    print("\nAlbumFeed completo:")
    while True:
        result = album_feed.next_track()
        if result is None:
            break
        track, _ = result
        print(" ", track["title"])
    print("saltadas en álbum:", album_feed.skipped)

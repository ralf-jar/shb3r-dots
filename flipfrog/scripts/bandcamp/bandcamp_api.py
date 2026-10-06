"""
bandcamp_api.py
Cliente mínimo, sin autenticación, de varios endpoints reales de
Bandcamp (confirmados a mano con curl, no documentación de terceros):

- POST /api/discover/1/discover_web: feed de álbumes/singles que
  matchean un tag, cada uno con una `featured_track` (pista destacada
  con `stream_url` mp3-128 ya listo para reproducir) o `None` si
  Bandcamp no ofrece preview de ese ítem -- ese `None` ES el criterio
  de "saltar", no hace falta heurística propia.
- GET a la página de un álbum: trae un atributo `data-tralbum` con el
  tracklist completo (`trackinfo[]`), cada pista con su propio
  `file["mp3-128"]` o `None` si esa pista puntual no es streamable.
- GET a bandcamp.com/<username>/wishlist: página pública de la wishlist
  de un fan (si el perfil no es privado) -- solo se usa para resolver el
  fan_id numérico del dueño (FAN_ID_RE). Hasta 2026-08-11 cada tarjeta
  traía además su propio `data-playerdata` con la pista + stream_url ya
  resuelto para el primer lote; Bandcamp lo sacó del HTML (confirmado a
  mano, 0 matches donde antes había ~20) -- ahora cada tarjeta es un
  `<div>` con atributos sueltos (data-tralbumid/data-itemtype/etc.) sin
  ningún JSON embebido, así que el primer lote se resuelve igual que
  cualquier otro (ver abajo).
- POST /api/fancollection/1/wishlist_items: toda la paginación de la
  wishlist, primer lote incluido (mismo endpoint que usa el botón "ver
  más" de la página real) -- NO trae stream_url directo, hay que
  resolverlo con fetch_album_tracks() por ítem (mismo costo que
  AlbumFeed). El primer lote se pide con un token sintético "ahora
  mismo" (ver fetch_wishlist_page) ya que esta API espera un cursor real,
  no acepta "dame los más recientes" con older_than_token=None.

Los `stream_url` son firmados con expiración corta -- nunca cachear,
pedirlos frescos cada vez.
"""
import concurrent.futures
import html
import json
import os
import re
import time
import urllib.parse

import requests

DISCOVER_URL = "https://bandcamp.com/api/discover/1/discover_web"
WISHLIST_URL = "https://bandcamp.com/{username}/wishlist"
WISHLIST_ITEMS_URL = "https://bandcamp.com/api/fancollection/1/wishlist_items"
USER_AGENT = "Mozilla/5.0"

TRALBUM_RE = re.compile(r'data-tralbum="([^"]*)"')
# <a class="tag" href="https://bandcamp.com/discover/<slug>?from=...">Etiqueta</a>
# (confirmado a mano bajando una página de álbum real -- el slug de la
# URL ya viene URL-encoded, ej. "70%27s" para "70's").
TAG_LINK_RE = re.compile(r'<a class="tag" href="[^"]*?/discover/([^"?]+)\?[^"]*"\s*>([^<]*)</a>')

# Recomendaciones al pie de la página de un álbum/track -- confirmado a
# mano bajando una página real: hay 0, 1 o 2 bloques
# <div class="recs-section bc-recs">, uno para las recomendaciones
# propias de la banda/sello ("<Nombre> recommends:", opcional, solo si el
# artista curó algo) y otro para las de la plataforma ("If you like
# <Nombre>, you may also like:", casi siempre presente). Cada ítem es un
# <li class="recommended-album ..."> con TODO lo necesario como atributo
# plano -- título, banda, carátula chica ya recortada por Bandcamp
# (120px) y el link real al álbum -- sin fetch extra por ítem, a
# diferencia de fetch_artist_albums (que solo da la URL).
RECS_SECTION_RE = re.compile(
    r'<p class="section-title">\s*(.*?)\s*</p>.*?<ul class="horizontal">(.*?)</ul>',
    re.DOTALL,
)
RECS_ITEM_RE = re.compile(
    r'data-albumtitle="([^"]*)".*?data-artist="([^"]*)".*?'
    r'<img class="album-art" src="([^"]*)".*?'
    r'<a class="album-link" href="([^"?]*)',
    re.DOTALL,
)

# El fan_id de la API de discover_web no sirve para resolver el dueño de
# la wishlist -- la página trae VARIOS fan_id sueltos (gente que sigue,
# regalos, etc.), confirmado a mano. Este patrón puntual
# ("username":"<slug>","name":"...","fan_id":N) es el único que
# corresponde de verdad al dueño del perfil que se está mirando.
FAN_ID_RE = r'"username":"{}","name":"[^"]*","fan_id":(\d+)'

USERNAME_ALIASES_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "username_aliases.json")


def normalize_tag(text):
    return text.strip().lower().replace(" ", "-")


def discover(tags, slice="new", cursor=None, size=20):
    """Un batch del feed de descubrimiento para `tags` (un tag o una
    lista de tags -- Bandcamp ya soporta combinar varios en una sola
    búsqueda). `slice`: "top" (más vendidos), "new" (recién publicados)
    o "rand" (sorpréndeme). Devuelve el JSON crudo de la API:
    {results, cursor, result_count, ...}."""
    if isinstance(tags, str):
        tags = [tags]
    body = {
        "category_id": 0,
        "tag_norm_names": [normalize_tag(t) for t in tags],
        "geoname_id": 0,
        "slice": slice,
        "time_facet_id": None,
        "cursor": cursor,
        "size": size,
        "include_result_types": ["a", "s"],
        "followed_bands": False,
    }
    resp = requests.post(
        DISCOVER_URL,
        json=body,
        headers={"User-Agent": USER_AGENT},
        timeout=25,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("__api_special__"):
        raise RuntimeError(f"Bandcamp discover_web error: {data}")
    return data


def art_url(image_id, size="10"):
    if not image_id:
        return None
    return f"https://f4.bcbits.com/img/a{image_id}_{size}.jpg"


def _fetch_tralbum(item_url):
    """GET crudo a la página de un álbum/track, ya parseado el blob
    data-tralbum -- compartido por fetch_album_tracks() (solo pistas,
    usado por AlbumFeed/fetch_wishlist_more, que ya conocen el álbum por
    otro lado) y fetch_album() (pistas + metadata, usado por ArtistFeed,
    que SÍ necesita título/portada porque descubre álbumes sobre la
    marcha). None si la página no tiene ese blob."""
    resp = requests.get(item_url, headers={"User-Agent": USER_AGENT}, timeout=25)
    resp.raise_for_status()
    match = TRALBUM_RE.search(resp.text)
    if not match:
        return None
    return json.loads(html.unescape(match.group(1)))


def _tracks_from_tralbum(tralbum):
    artist = tralbum.get("artist")
    tracks = []
    for t in tralbum.get("trackinfo") or []:
        file_info = t.get("file") or {}
        tracks.append({
            "title": t.get("title"),
            "artist": artist,
            "stream_url": file_info.get("mp3-128"),
            "duration": t.get("duration"),
            "track_num": t.get("track_num"),
        })
    return tracks


def fetch_album_tracks(item_url):
    """GET a la página de un álbum/track, devuelve la lista de pistas
    en orden de álbum: {title, artist, stream_url|None, duration,
    track_num}."""
    tralbum = _fetch_tralbum(item_url)
    return _tracks_from_tralbum(tralbum) if tralbum else []


def _parse_year(date_str):
    """"album_release_date"/"current.release_date" del blob tralbum vienen
    como "21 Nov 2025 00:00:00 GMT" -- alcanza con el primer grupo de 4
    dígitos, sin parsear la fecha completa (no hace falta más que el
    año para los chips de carátula)."""
    if not date_str:
        return None
    m = re.search(r"\b(\d{4})\b", date_str)
    return m.group(1) if m else None


def fetch_album(item_url):
    """Como fetch_album_tracks(), pero además devuelve la metadata del
    álbum (título, portada, año) en el mismo formato `album_meta` que usa
    el resto de queue_feed.py -- ArtistFeed descubre álbumes sobre la
    marcha (fetch_artist_albums) y no los conoce de antemano como
    AlbumFeed, necesita esto para la tarjeta "reproduciendo ahora" sin
    pedir la página dos veces. Devuelve ([], {}) si la página no tiene
    blob data-tralbum."""
    tralbum = _fetch_tralbum(item_url)
    if not tralbum:
        return [], {}
    current = tralbum.get("current") or {}
    album_meta = {
        "item_url": item_url,
        "band_name": tralbum.get("artist"),
        "album_title": current.get("title"),
        "art_url": art_url(tralbum.get("art_id")),
        "year": _parse_year(tralbum.get("album_release_date") or current.get("release_date")),
    }
    return _tracks_from_tralbum(tralbum), album_meta


def fetch_tags(item_url):
    """GET a la página de un álbum/track, devuelve sus tags/géneros
    reales de Bandcamp: [{slug, label}], en el mismo orden en que
    aparecen en la página. `slug` ya viene en el formato que espera
    `tag_norm_names` de discover()."""
    resp = requests.get(item_url, headers={"User-Agent": USER_AGENT}, timeout=25)
    resp.raise_for_status()
    tags = []
    seen = set()
    for slug_enc, label in TAG_LINK_RE.findall(resp.text):
        slug = urllib.parse.unquote(slug_enc)
        if slug in seen:
            continue
        seen.add(slug)
        tags.append({"slug": slug, "label": html.unescape(label).strip()})
    return tags


def fetch_recommendations(item_url):
    """GET aparte a la misma página de fetch_tags (Bandcamp no expone
    esto por una API separada) -- devuelve [{title, items:[{item_url,
    album_title, band_name, art_url}, ...]}, ...], una entrada por
    sección encontrada, en el mismo orden en que aparecen en la página.
    Lista vacía si el álbum no tiene ninguna (poco común pero pasa)."""
    resp = requests.get(item_url, headers={"User-Agent": USER_AGENT}, timeout=25)
    resp.raise_for_status()
    sections = []
    for title, ul_html in RECS_SECTION_RE.findall(resp.text):
        items = []
        for albumtitle, artist, art_src, href in RECS_ITEM_RE.findall(ul_html):
            items.append({
                "item_url": html.unescape(href),
                "album_title": html.unescape(albumtitle),
                "band_name": html.unescape(artist),
                "art_url": art_src,
            })
        if items:
            sections.append({"title": html.unescape(title).strip(), "items": items})
    return sections


def resolve_username(name):
    """Bandcamp no deja resolver el nombre para mostrar de un fan a su
    slug real de URL sin loguearse (confirmado a mano) -- si cambiaste
    de nombre para mostrar en algún momento, la URL sigue usando el slug
    viejo para siempre. username_aliases.json (gitignored, mismo criterio
    que blacklist.json/favorites.json) guarda mapeos nombre-para-mostrar
    -> slug real a mano; si `name` no tiene alias, se usa tal cual (así
    sigue andando directo con el slug real, ej. "fulanito")."""
    try:
        with open(USERNAME_ALIASES_FILE) as f:
            aliases = json.load(f)
        return aliases.get(name.lower(), name)
    except (FileNotFoundError, json.JSONDecodeError):
        return name


def fetch_wishlist_page(username, size=50):
    """Primer lote de la wishlist pública de `username` (ya resuelto por
    resolve_username -- este slug es el de la URL, no el nombre para
    mostrar). Hasta 2026-08-11 esto scrapeaba un blob JSON por tarjeta
    directo del HTML (con stream_url ya resuelto, sin fetch extra) --
    Bandcamp lo sacó de la página (confirmado a mano). Ya no hay forma
    de traer el primer lote "gratis": se resuelve el fan_id igual que
    siempre desde el HTML (FAN_ID_RE, eso sigue funcionando) y de ahí en
    más se delega TODO a fetch_wishlist_more() -- mismo costo por ítem
    que ya se pagaba solo para paginar más allá del primer lote, ahora
    aplica desde el ítem 1 también. El token real de paginación es
    opaco/depende de un ítem previo real, así que se arranca con uno
    sintético "ahora mismo" (confirmado a mano que la API lo acepta como
    "los más recientes primero" -- older_than_token=None en cambio no
    devuelve nada). Devuelve (fan_id|None, [(track, album), ...],
    next_token|None) -- mismo contrato que antes, WishlistFeed no
    necesita cambios. Perfil privado/inexistente: fan_id None, lista
    vacía (mismo criterio de "no hay resultados" que discover())."""
    resp = requests.get(WISHLIST_URL.format(username=username),
                         headers={"User-Agent": USER_AGENT}, timeout=25)
    if resp.status_code == 404:
        return None, [], None
    resp.raise_for_status()

    # FAN_ID_RE busca comillas literales -- el HTML crudo las trae como
    # &quot; adentro de los atributos data-*, por eso se busca sobre una
    # copia des-escapada aparte.
    m = re.search(FAN_ID_RE.format(re.escape(username)), html.unescape(resp.text))
    fan_id = int(m.group(1)) if m else None
    if fan_id is None:
        return None, [], None

    now_token = f"{int(time.time())}::a::"
    try:
        tracks, next_token, _more = fetch_wishlist_more(fan_id, now_token, size=size)
    except requests.RequestException:
        return fan_id, [], None
    return fan_id, tracks, next_token


WISHLIST_FETCH_WORKERS = 10


def _resolve_wishlist_item(item):
    """fetch_album_tracks() de un solo ítem del lote -- factoreado para
    correr en pool (fetch_wishlist_more), None si no hay stream_url
    resoluble (mismo criterio de "saltar" que antes, ahora por ítem
    fallido en vez de por excepción del loop entero)."""
    item_url = item.get("item_url")
    if not item_url:
        return None
    try:
        item_tracks = fetch_album_tracks(item_url)
    except Exception:
        return None
    if not item_tracks:
        return None
    featured_num = item.get("featured_track_number")
    track = next((t for t in item_tracks if t.get("track_num") == featured_num), item_tracks[0])
    if not track.get("stream_url"):
        return None
    return track, {
        "item_url": item_url,
        "band_name": item.get("band_name"),
        "album_title": item.get("album_title") or item.get("item_title"),
        "art_url": item.get("item_art_url"),
    }


def fetch_wishlist_more(fan_id, older_than_token, size=50):
    """Página siguiente de una wishlist ya en curso (fan_id/token
    salidos de fetch_wishlist_page o de una llamada anterior a esta
    función) -- a diferencia del primer lote, la API de fancollection NO
    incluye stream_url, hay que resolverlo con fetch_album_tracks() por
    ítem (mismo costo por pista que AlbumFeed), WISHLIST_FETCH_WORKERS a
    la vez en vez de uno por uno (pool.map conserva el orden del lote
    pese a resolverse en paralelo). Devuelve ([(track, album), ...],
    next_token|None, more_available)."""
    resp = requests.post(
        WISHLIST_ITEMS_URL,
        json={"fan_id": fan_id, "older_than_token": older_than_token, "count": size},
        headers={"User-Agent": USER_AGENT}, timeout=25,
    )
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items") or []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WISHLIST_FETCH_WORKERS) as pool:
        resolved = pool.map(_resolve_wishlist_item, items)
    results = [r for r in resolved if r is not None]

    return results, data.get("last_token"), bool(data.get("more_available"))


ARTIST_ALIASES_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "artist_aliases.json")
ALBUM_HREF_RE = re.compile(r'href="(/album/[^"]+)"')


def normalize_artist_slug(name):
    """Primer intento de adivinar el subdominio de una banda a partir de
    su nombre -- la búsqueda real de Bandcamp (bandcamp.com/search) está
    detrás de un challenge anti-bot (confirmado a mano con curl, devuelve
    una página "Client Challenge" en vez de resultados), así que no hay
    forma confiable de resolver esto por búsqueda real. Mismo criterio
    que "accidente" -> "accidente.bandcamp.com": minúsculas, sin espacios
    ni símbolos. Cuando el subdominio real no coincide con esta
    adivinanza (nombres con espacios, acentos, o un slug elegido sin
    relación al nombre -- mismo problema que un nombre para mostrar distinto del slug con
    wishlists), resolve_artist_slug() prueba primero un alias manual en
    artist_aliases.json antes de caer acá."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def resolve_artist_slug(name):
    """Mismo patrón que resolve_username() -- alias manual primero
    (artist_aliases.json, gitignored), si no hay adivina el subdominio
    con normalize_artist_slug()."""
    try:
        with open(ARTIST_ALIASES_FILE) as f:
            aliases = json.load(f)
        alias = aliases.get(name.lower())
        if alias:
            return alias
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return normalize_artist_slug(name)


def fetch_artist_albums(subdomain):
    """GET a <subdomain>.bandcamp.com/music, devuelve la lista de
    item_url (absolutas) de cada álbum listado, en el mismo orden en que
    Bandcamp los presenta (más reciente primero, normalmente) -- ArtistFeed
    los consume en ese orden con fetch_album() uno por uno. Lista vacía si
    el subdominio no existe (404) o la banda no tiene álbumes propios
    (solo tracks sueltos, o página vacía)."""
    resp = requests.get(f"https://{subdomain}.bandcamp.com/music",
                         headers={"User-Agent": USER_AGENT}, timeout=25)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()

    seen = set()
    urls = []
    for href in ALBUM_HREF_RE.findall(resp.text):
        if href in seen:
            continue
        seen.add(href)
        urls.append(f"https://{subdomain}.bandcamp.com{href}")
    return urls


if __name__ == "__main__":
    import sys

    tag = sys.argv[1] if len(sys.argv) > 1 else "ambient"
    t0 = time.time()
    data = discover(tag, slice="new", size=10)
    print(f"discover({tag!r}) -> {len(data['results'])} resultados en {time.time()-t0:.2f}s")
    playable = 0
    for r in data["results"]:
        ft = r.get("featured_track")
        status = "OK " if ft and ft.get("stream_url") else "--- "
        if ft and ft.get("stream_url"):
            playable += 1
        print(f"  [{status}] {r.get('band_name')} - {r.get('title')}")
    print(f"{playable}/{len(data['results'])} reproducibles")

    for r in data["results"]:
        if r.get("featured_track"):
            print("\nfetch_album_tracks ->", r["item_url"])
            for tr in fetch_album_tracks(r["item_url"]):
                mark = "OK " if tr["stream_url"] else "--- "
                print(f"  [{mark}] {tr['track_num']}. {tr['title']}")
            break

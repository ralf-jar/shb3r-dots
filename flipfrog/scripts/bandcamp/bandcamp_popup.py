#!/usr/bin/env python3
"""
bandcamp_popup.py
Ventana flotante de "Descubre Bandcamp" (SUPER+M, ver CLAUDE.md
"Descubre Bandcamp"). Popup layer-shell (waybar_lib.build_layer_window)
que NO reproduce nada por sí mismo, es un cliente liviano de bandcamp_daemon.py
(mismo directorio), el proceso persistente que de verdad tiene mpv
abierto. Cerrar esta ventana (Escape, click afuera,
SUPER+M de nuevo) NO corta la música: solo destruye el cliente,
bandcamp_daemon.py sigue sonando de fondo hasta que el usuario cierra sesión.

Sondea bandcamp_daemon.py cada 1s (GLib.timeout_add + bandcamp_ipc.get_state(),
mismo criterio que MixerPopup._refresh en flipfrog/scripts/audio/mixer.py:
un socket unix local alcanza para preguntar sincrónico sin hilo aparte)
en vez de mantener una conexión persistente -- no hace falta un
mecanismo de pub/sub para lo poco que cambia (título, posición, pausa).
"""
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Gio, GLib, Pango, PangoCairo
import requests

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top
from common import theme_color_rgba
from i18n import t
import bandcamp_ipc

CSS_FILE = os.path.join(SCRIPT_DIR, "bandcamp_popup.css")
LOCK = "/tmp/bandcamp-radio-popup.pid"
LAST_TAG_FILE = os.path.join(SCRIPT_DIR, "last-tag.json")
WISHLIST_HISTORY_FILE = os.path.join(SCRIPT_DIR, "wishlist-history.json")
WISHLIST_HISTORY_MAX = 12
FAVORITES_FILE = os.path.join(SCRIPT_DIR, "favorites.json")

SLICES = [
    ("new", t("bandcamp", "slice_new")),
    ("top", t("bandcamp", "slice_top")),
    ("rand", t("bandcamp", "slice_rand")),
]
# Antes u:<usuario>/a:<banda>/b:<banda> eran prefijos que había que
# escribir a mano en tag_entry -- ahora son un modo más del mismo combo
# que ya elegía el slice (pedido explícito del usuario, "ese que dice
# Nuevo"). slice_combo.get_active_id() decide la rama en
# _on_search_clicked; SLICES (arriba) sigue siendo la lista de slices
# válidos para el modo "tags" -- estos dos son modos aparte, no slices.
SEARCH_MODES = [
    ("artist", t("bandcamp", "mode_artist")),
    ("wishlist", t("bandcamp", "mode_wishlist")),
    ("local", t("bandcamp", "mode_local")),
]
MODE_PLACEHOLDERS = {
    "artist": t("bandcamp", "placeholder_artist_name"),
    "wishlist": t("bandcamp", "placeholder_wishlist_user"),
    "local": t("bandcamp", "placeholder_local"),
}
DEFAULT_PLACEHOLDER = t("bandcamp", "placeholder_tags")
# Géneros predefinidos estilo Bandcamp (misma lista/orden que muestra
# bandcamp.com/discover) + subgéneros más populares de cada uno, para
# navegar sin tener que escribir el tag a mano -- slug ya en formato
# dash-lowercase (bandcamp_api.normalize_tag es idempotente sobre esto),
# label es el texto mostrado en el botón.
GENRES = [
    {"slug": "rock", "label": "Rock"},
    {"slug": "electronic", "label": "Electronic"},
    {"slug": "metal", "label": "Metal"},
    {"slug": "alternative", "label": "Alternative"},
    {"slug": "hip-hop-rap", "label": "Hip-Hop/Rap"},
    {"slug": "experimental", "label": "Experimental"},
    {"slug": "punk", "label": "Punk"},
    {"slug": "folk", "label": "Folk"},
    {"slug": "pop", "label": "Pop"},
    {"slug": "ambient", "label": "Ambient"},
    {"slug": "soundtrack", "label": "Soundtrack"},
    {"slug": "world-music", "label": "World"},
    {"slug": "jazz", "label": "Jazz"},
    {"slug": "acoustic", "label": "Acoustic"},
    {"slug": "funk", "label": "Funk"},
    {"slug": "r-b-soul", "label": "R&B/Soul"},
    {"slug": "devotional", "label": "Devotional"},
    {"slug": "classical", "label": "Classical"},
    {"slug": "reggae", "label": "Reggae"},
    {"slug": "country", "label": "Country"},
    {"slug": "blues", "label": "Blues"},
    {"slug": "audiobooks", "label": "Audiobooks"},
    {"slug": "latin", "label": "Latin"},
]
SUBGENRES = {
    "rock": [
        {"slug": "indie-rock", "label": "Indie Rock"},
        {"slug": "classic-rock", "label": "Classic Rock"},
        {"slug": "prog-rock", "label": "Prog Rock"},
        {"slug": "hard-rock", "label": "Hard Rock"},
        {"slug": "psychedelic-rock", "label": "Psychedelic Rock"},
        {"slug": "garage-rock", "label": "Garage Rock"},
        {"slug": "post-rock", "label": "Post-Rock"},
        {"slug": "math-rock", "label": "Math Rock"},
    ],
    "electronic": [
        {"slug": "house", "label": "House"},
        {"slug": "techno", "label": "Techno"},
        {"slug": "dubstep", "label": "Dubstep"},
        {"slug": "drum-and-bass", "label": "Drum & Bass"},
        {"slug": "idm", "label": "IDM"},
        {"slug": "synth-pop", "label": "Synth-Pop"},
        {"slug": "electro", "label": "Electro"},
        {"slug": "downtempo", "label": "Downtempo"},
    ],
    "metal": [
        {"slug": "black-metal", "label": "Black Metal"},
        {"slug": "death-metal", "label": "Death Metal"},
        {"slug": "doom-metal", "label": "Doom Metal"},
        {"slug": "thrash-metal", "label": "Thrash Metal"},
        {"slug": "heavy-metal", "label": "Heavy Metal"},
        {"slug": "metalcore", "label": "Metalcore"},
        {"slug": "power-metal", "label": "Power Metal"},
        {"slug": "sludge-metal", "label": "Sludge Metal"},
    ],
    "alternative": [
        {"slug": "indie", "label": "Indie"},
        {"slug": "indie-pop", "label": "Indie Pop"},
        {"slug": "shoegaze", "label": "Shoegaze"},
        {"slug": "dream-pop", "label": "Dream Pop"},
        {"slug": "grunge", "label": "Grunge"},
        {"slug": "post-punk", "label": "Post-Punk"},
        {"slug": "noise-pop", "label": "Noise Pop"},
    ],
    "hip-hop-rap": [
        {"slug": "boom-bap", "label": "Boom Bap"},
        {"slug": "trap", "label": "Trap"},
        {"slug": "conscious-hip-hop", "label": "Conscious Hip-Hop"},
        {"slug": "lo-fi-hip-hop", "label": "Lo-Fi Hip-Hop"},
        {"slug": "gangsta-rap", "label": "Gangsta Rap"},
        {"slug": "cloud-rap", "label": "Cloud Rap"},
        {"slug": "old-school-hip-hop", "label": "Old School Hip-Hop"},
    ],
    "experimental": [
        {"slug": "noise", "label": "Noise"},
        {"slug": "drone", "label": "Drone"},
        {"slug": "musique-concrete", "label": "Musique Concrète"},
        {"slug": "avant-garde", "label": "Avant-Garde"},
        {"slug": "glitch", "label": "Glitch"},
        {"slug": "field-recordings", "label": "Field Recordings"},
    ],
    "punk": [
        {"slug": "pop-punk", "label": "Pop Punk"},
        {"slug": "hardcore-punk", "label": "Hardcore Punk"},
        {"slug": "ska-punk", "label": "Ska Punk"},
        {"slug": "post-punk", "label": "Post-Punk"},
        {"slug": "garage-punk", "label": "Garage Punk"},
        {"slug": "anarcho-punk", "label": "Anarcho-Punk"},
        {"slug": "crust-punk", "label": "Crust Punk"},
    ],
    "folk": [
        {"slug": "indie-folk", "label": "Indie Folk"},
        {"slug": "folk-rock", "label": "Folk Rock"},
        {"slug": "singer-songwriter", "label": "Singer-Songwriter"},
        {"slug": "traditional-folk", "label": "Traditional Folk"},
        {"slug": "freak-folk", "label": "Freak Folk"},
        {"slug": "americana", "label": "Americana"},
    ],
    "pop": [
        {"slug": "synth-pop", "label": "Synth-Pop"},
        {"slug": "dream-pop", "label": "Dream Pop"},
        {"slug": "indie-pop", "label": "Indie Pop"},
        {"slug": "k-pop", "label": "K-Pop"},
        {"slug": "electropop", "label": "Electropop"},
        {"slug": "bedroom-pop", "label": "Bedroom Pop"},
    ],
    "ambient": [
        {"slug": "dark-ambient", "label": "Dark Ambient"},
        {"slug": "drone", "label": "Drone"},
        {"slug": "space-ambient", "label": "Space Ambient"},
        {"slug": "new-age", "label": "New Age"},
        {"slug": "ambient-techno", "label": "Ambient Techno"},
    ],
    "soundtrack": [
        {"slug": "video-game-music", "label": "Video Game Music"},
        {"slug": "film-score", "label": "Film Score"},
        {"slug": "anime", "label": "Anime"},
        {"slug": "tv-score", "label": "TV Score"},
    ],
    "world-music": [
        {"slug": "afrobeat", "label": "Afrobeat"},
        {"slug": "celtic", "label": "Celtic"},
        {"slug": "flamenco", "label": "Flamenco"},
        {"slug": "klezmer", "label": "Klezmer"},
        {"slug": "balkan", "label": "Balkan"},
        {"slug": "indian-classical", "label": "Indian Classical"},
    ],
    "jazz": [
        {"slug": "bebop", "label": "Bebop"},
        {"slug": "smooth-jazz", "label": "Smooth Jazz"},
        {"slug": "free-jazz", "label": "Free Jazz"},
        {"slug": "jazz-fusion", "label": "Jazz Fusion"},
        {"slug": "swing", "label": "Swing"},
        {"slug": "cool-jazz", "label": "Cool Jazz"},
        {"slug": "big-band", "label": "Big Band"},
    ],
    "acoustic": [
        {"slug": "fingerstyle", "label": "Fingerstyle"},
        {"slug": "unplugged", "label": "Unplugged"},
        {"slug": "singer-songwriter", "label": "Singer-Songwriter"},
        {"slug": "acoustic-pop", "label": "Acoustic Pop"},
    ],
    "funk": [
        {"slug": "funk-jam", "label": "Funk Jam"},
        {"slug": "deep-funk", "label": "Deep Funk"},
        {"slug": "funk-rock", "label": "Funk Rock"},
        {"slug": "jazz-funk", "label": "Jazz Funk"},
        {"slug": "boogie", "label": "Boogie"},
        {"slug": "g-funk", "label": "G-Funk"},
        {"slug": "rare-groove", "label": "Rare Groove"},
        {"slug": "electro", "label": "Electro"},
        {"slug": "go-go", "label": "Go-Go"},
    ],
    "r-b-soul": [
        {"slug": "neo-soul", "label": "Neo Soul"},
        {"slug": "quiet-storm", "label": "Quiet Storm"},
        {"slug": "motown", "label": "Motown"},
        {"slug": "contemporary-r-b", "label": "Contemporary R&B"},
        {"slug": "doo-wop", "label": "Doo-Wop"},
    ],
    "devotional": [
        {"slug": "gospel", "label": "Gospel"},
        {"slug": "christian", "label": "Christian"},
        {"slug": "worship", "label": "Worship"},
        {"slug": "gregorian-chant", "label": "Gregorian Chant"},
        {"slug": "kirtan", "label": "Kirtan"},
        {"slug": "spiritual", "label": "Spiritual"},
    ],
    "classical": [
        {"slug": "baroque", "label": "Baroque"},
        {"slug": "romantic", "label": "Romantic"},
        {"slug": "contemporary-classical", "label": "Contemporary Classical"},
        {"slug": "opera", "label": "Opera"},
        {"slug": "chamber-music", "label": "Chamber Music"},
        {"slug": "orchestral", "label": "Orchestral"},
        {"slug": "piano", "label": "Piano"},
        {"slug": "choral", "label": "Choral"},
    ],
    "reggae": [
        {"slug": "dub", "label": "Dub"},
        {"slug": "ska", "label": "Ska"},
        {"slug": "roots", "label": "Roots"},
        {"slug": "dancehall", "label": "Dancehall"},
        {"slug": "rocksteady", "label": "Rocksteady"},
        {"slug": "ragga", "label": "Ragga"},
        {"slug": "lovers-rock", "label": "Lovers Rock"},
    ],
    "country": [
        {"slug": "bluegrass", "label": "Bluegrass"},
        {"slug": "alt-country", "label": "Alt-Country"},
        {"slug": "honky-tonk", "label": "Honky Tonk"},
        {"slug": "outlaw-country", "label": "Outlaw Country"},
        {"slug": "americana", "label": "Americana"},
        {"slug": "country-rock", "label": "Country Rock"},
    ],
    "blues": [
        {"slug": "delta-blues", "label": "Delta Blues"},
        {"slug": "chicago-blues", "label": "Chicago Blues"},
        {"slug": "electric-blues", "label": "Electric Blues"},
        {"slug": "blues-rock", "label": "Blues Rock"},
        {"slug": "acoustic-blues", "label": "Acoustic Blues"},
    ],
    "audiobooks": [
        {"slug": "fiction", "label": "Fiction"},
        {"slug": "non-fiction", "label": "Non-Fiction"},
        {"slug": "poetry", "label": "Poetry"},
        {"slug": "self-help", "label": "Self-Help"},
        {"slug": "childrens", "label": "Children's"},
    ],
    "latin": [
        {"slug": "reggaeton", "label": "Reggaeton"},
        {"slug": "salsa", "label": "Salsa"},
        {"slug": "bachata", "label": "Bachata"},
        {"slug": "cumbia", "label": "Cumbia"},
        {"slug": "latin-pop", "label": "Latin Pop"},
        {"slug": "bossa-nova", "label": "Bossa Nova"},
        {"slug": "merengue", "label": "Merengue"},
    ],
}
ART_SIZE = 180
LOCAL_LIST_MAX_HEIGHT = 240
# corner-radius.json de flipfrog/themer -- sync_radius.py reescribe el
# border-radius de bandcamp_popup.css, pero no el radio horneado en el
# pixbuf de las carátulas (_round_corners).
CORNER_RADIUS_FILE = os.path.join(SCRIPT_DIR, "..", "..", "themer", "corner-radius.json")
DEFAULT_RADIUS = 25


def _current_radius():
    try:
        with open(CORNER_RADIUS_FILE) as f:
            return int(json.load(f).get("radius", DEFAULT_RADIUS))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return DEFAULT_RADIUS


# Marcador del progreso (#progress-scale slider, ver bandcamp_popup.css) --
# EL MISMO glifo que ya aparece al inicio de cada línea en Kitty (no
# una copia/captura de pantalla de él): kitty.conf mapea el codepoint
# U+F0001 a la familia "PromptIcons" (`symbol_map U+F0001-U+F0001
# PromptIcons`), una fuente de un solo glifo instalada en
# ~/.local/share/fonts/PromptIcons/PromptIcons.ttf. Acá se renderiza
# ESE mismo glifo con Pango+cairo directo desde la fuente (mira a la
# izquierda de origen, se espeja con un flip de cairo) -- por eso
# entra en juego PangoCairo. frog-marker.png (el que referencia
# bandcamp_popup.css) se regenera en cada apertura del popup para seguir el
# color del tema en vivo, igual que el resto de la UI; a diferencia
# del enfoque anterior (PNG + -gtk-recolor()/máscara de alfa), acá
# "cambiar de color" es solo pasar otro RGBA a Pango, y "cambiar de
# tamaño" es solo cambiar FROG_MARKER_SIZE -- no hay ningún asset de
# imagen que editar a mano.
FROG_GLYPH = chr(0xF0001)
FROG_GLYPH_FONT = "PromptIcons"
FROG_MARKER_FILE = os.path.join(SCRIPT_DIR, "frog-marker.png")
FROG_MARKER_SIZE = 26
# myforegroundhover2, no myforegroundhover -- ESE es el mismo color que
# #progress-scale highlight (la barra ya reproducida), así que la mitad
# de la rana que cae sobre esa barra se camuflaba (mismo RGB exacto,
# confirmado a mano comparando contra el glifo real de Kitty: es la
# misma rana, no un glifo distinto -- solo la mitad visible por el
# camuflaje de color).
FROG_MARKER_COLOR_NAME = "myforegroundhover"


def _current_theme_color(name):
    """(r,g,b,a) 0-1 de `@define-color <name>` del tema activo, o None."""
    try:
        r, g, b, a = theme_color_rgba(name)
    except (OSError, AttributeError, ValueError):
        return None
    return (r / 255, g / 255, b / 255, a)


def _render_frog_marker():
    """Sobreescribe frog-marker.png con el glifo PromptIcons renderizado
    al color del tema -- set_absolute_size (no set_size) para que
    FROG_MARKER_SIZE sea un tamaño en píxeles exacto del canvas cairo,
    no puntos dependientes de la resolución de Pango. Falla silencioso
    -- si la fuente no está instalada en esta máquina, frog-marker.png
    queda con el render de la corrida anterior en vez de romper el
    popup (probablemente vacío/tofu la primera vez, no crítico)."""
    color = _current_theme_color(FROG_MARKER_COLOR_NAME)
    if color is None:
        return
    try:
        pad = max(2, FROG_MARKER_SIZE // 8)
        canvas = FROG_MARKER_SIZE + pad * 2
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, canvas, canvas)
        ctx = cairo.Context(surface)
        # SIN espejar -- el glifo ya mira a la derecha de origen (mismo
        # que se ve en Kitty, confirmado a mano comparando contra una
        # captura real). Un intento anterior asumía que miraba a la
        # izquierda y lo espejaba acá, dejándolo mirando al lado
        # contrario del real.

        layout = PangoCairo.create_layout(ctx)
        desc = Pango.FontDescription()
        desc.set_family(FROG_GLYPH_FONT)
        desc.set_absolute_size(FROG_MARKER_SIZE * Pango.SCALE)
        layout.set_font_description(desc)
        layout.set_text(FROG_GLYPH, -1)

        ink, _logical = layout.get_pixel_extents()
        ctx.translate(pad - ink.x + (canvas - pad * 2 - ink.width) / 2,
                       pad - ink.y + (canvas - pad * 2 - ink.height) / 2)
        ctx.set_source_rgba(*color)
        PangoCairo.show_layout(ctx, layout)

        pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, canvas, canvas)
        fd, tmp_path = tempfile.mkstemp(dir=SCRIPT_DIR, suffix=".png")
        os.close(fd)
        pixbuf.savev(tmp_path, "png", [], [])
        os.replace(tmp_path, FROG_MARKER_FILE)
    except (GLib.Error, OSError):
        pass


# Estado "sin canción" (album_label/art_image cuando no hay nada
# reproduciendo o el daemon recién arranca) -- ver _set_placeholder_art.
# En album_label, no title_label, a pedido explícito del usuario --
# title_label se queda en "—" como el resto de los campos vacíos.
NO_TRACK_LABEL = t("bandcamp", "no_track_playing")
PLACEHOLDER_ART_ICON = "audio-x-generic-symbolic"
PLACEHOLDER_ART_ICON_SIZE = 72
# Carátula chica de los chips de recomendaciones/discografía de artista
# (_build_album_chip) -- deliberadamente chica, son tiras de MUCHOS
# ítems, no la tarjeta principal de "reproduciendo ahora".
ALBUM_CHIP_SIZE = 56
# Catálogo elegible de la pestaña "Buscar" (_rebuild_catalog) -- carátulas
# 2.3x más grandes que el resto de las tiras (recomendaciones/discografía
# de artista siguen en ALBUM_CHIP_SIZE), pedido explícito del usuario.
# El radio NO escala con el tamaño (.album-chip y .album-chip.catalog-chip
# comparten el mismo border-radius plano en bandcamp_popup.css, sin proporción --
# ver CLAUDE.md "Redondeo de bordes", "sin excepciones"), así que ambos
# chips usan _current_radius() directo, igual que ART_RADIUS.
CATALOG_CHIP_SIZE = round(ALBUM_CHIP_SIZE * 2.3)
# Alto fijo del scroller de la pestaña "Favoritos" -- mismo criterio que
# LogViewer en firewall_log_viewer.py (min Y max, no solo un piso): así
# el popup no cambia de tamaño al ir sumando/sacando favoritos ni al
# alternar entre pestañas, siempre scrollea a partir de este alto.
FAVORITES_LIST_HEIGHT = 360
# Logo oficial de Bandcamp provisto por el usuario -- viewBox original
# (0 0 450.689 450.69) es un cuadrado con mucho margen vacío arriba/abajo
# del wordmark real (que ocupa aprox. y=99..331); BANDCAMP_LOGO_VIEWBOX
# lo recorta a ese rango para que el ícono no quede con un padding enorme
# al escalarlo a un tamaño de botón. fill="#000000" fijo en el SVG (igual
# que connect.svg/disconnect.svg en bluetooth_tab.py) -- se recolorea a
# mano en _load_bandcamp_pixbuf.
BANDCAMP_LOGO_SVG = os.path.join(SCRIPT_DIR, "bandcamp-logotype.svg")
BANDCAMP_LOGO_VIEWBOX = "0 85 450.689 255"
BANDCAMP_COLOR = "#1da0c3"
# Ancho generoso (nunca el que termina limitando el escalado) -- alto es
# la dimensión real que importa acá, ver _load_bandcamp_pixbuf.
BANDCAMP_LOGO_WIDTH = 90
BANDCAMP_LOGO_HEIGHT = 40
# Tamaño en píxeles del ícono play/pausa (Gtk.Image.set_pixel_size) --
# GTK3 no expone esto por CSS (confirmado: "-gtk-icon-size" no es una
# propiedad válida en este motor, y no hay otra que controle el pixel-size
# real de un ícono cargado por nombre), así que queda como constante acá
# en vez de repetido a mano en los dos lugares que lo tocan.
PLAY_PAUSE_ICON_SIZE = 54
# "u:<usuario>" / "b:<banda>" -- ya no los escribe el usuario a mano
# (Wishlist/Artista son modos del combo, ver SEARCH_MODES/
# _on_search_mode_changed), quedan solo como formato interno de
# last-tag.json (_save_last_tag/_load_last_tag) para saber con qué modo
# reabrir el popup. El nombre puede ser un alias
# (username_aliases.json o artist_aliases.json, nombre
# para mostrar -> slug real de la URL/subdominio) o el slug real directo
# -- resuelto del lado del daemon (bandcamp_api.resolve_username/
# resolve_artist_slug), no acá.
WISHLIST_RE = re.compile(r"^u:(\S+)$", re.IGNORECASE)
ARTIST_RE = re.compile(r"^[ba]:(\S+)$", re.IGNORECASE)
LOCAL_RE = re.compile(r"^l:(.*)$", re.IGNORECASE)


def _fmt_time(seconds):
    seconds = int(seconds or 0)
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


def _split_tags(text):
    """Acepta ";" y "," como separador -- un tag viejo guardado con coma
    (last-tag.json, costumbre de antes) no debe convertirse en un solo
    tag inválido tipo "punk, folk" sin normalizar, que Bandcamp no
    matchea con nada."""
    return [t.strip() for t in re.split(r"[;,]", text) if t.strip()]


def _load_wishlist_history():
    try:
        with open(WISHLIST_HISTORY_FILE) as f:
            data = json.load(f)
        return [u for u in data.get("usernames", []) if isinstance(u, str)]
    except (OSError, json.JSONDecodeError):
        return []


def _save_wishlist_history(usernames):
    # Escritura atómica (temp + os.replace) -- mismo patrón que
    # _save_last_tag y todo archivo reescrito en caliente en este repo
    # (ver CLAUDE.md), un lector concurrente no debería poder verlo
    # truncado a mitad de camino.
    tmp = WISHLIST_HISTORY_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"usernames": usernames}, f)
        os.replace(tmp, WISHLIST_HISTORY_FILE)
    except OSError:
        pass


def _load_favorites():
    """Álbumes: {item_url, band_name, album_title, art_url} -- exactamente
    el album_meta que ya maneja el resto del repo (queue_feed.py,
    _build_album_chip), así que un favorito se puede reproducir sin
    volver a pedirle nada a Bandcamp (ver cmd_play_album en bandcamp_daemon.py,
    acepta meta directo por este motivo). Artistas: {name, item_url_hint}
    -- item_url_hint es el item_url de la última canción conocida de ESE
    artista al momento de favoritearlo, mismo mecanismo que
    _on_artist_clicked (bandcamp_daemon.py saca el subdominio real de esa URL en
    vez de adivinarlo del nombre, evita el problema de colisiones de
    nombre real en Bandcamp)."""
    try:
        with open(FAVORITES_FILE) as f:
            data = json.load(f)
        albums = [a for a in data.get("albums", []) if isinstance(a, dict) and a.get("item_url")]
        artists = [a for a in data.get("artists", []) if isinstance(a, dict) and a.get("name")]
        return albums, artists
    except (OSError, json.JSONDecodeError):
        return [], []


def _save_favorites(albums, artists):
    tmp = FAVORITES_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"albums": albums, "artists": artists}, f)
        os.replace(tmp, FAVORITES_FILE)
    except OSError:
        pass


def _round_corners(pixbuf, radius):
    """Recorta las esquinas del pixbuf con un path de cairo -- GTK3 en
    este sistema no recorta un Gtk.Image hijo aunque su contenedor tenga
    border-radius por CSS (confirmado a mano, mismo motivo que
    _round_corners en theme/theme_module.py: el redondeado tiene que
    estar horneado en la imagen misma, no en el widget)."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    ctx = cairo.Context(surface)
    deg = math.pi / 180.0
    ctx.new_sub_path()
    ctx.arc(width - radius, radius, radius, -90 * deg, 0)
    ctx.arc(width - radius, height - radius, radius, 0, 90 * deg)
    ctx.arc(radius, height - radius, radius, 90 * deg, 180 * deg)
    ctx.arc(radius, radius, radius, 180 * deg, 270 * deg)
    ctx.close_path()
    ctx.clip()
    Gdk.cairo_set_source_pixbuf(ctx, pixbuf, 0, 0)
    ctx.paint()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)


ART_CACHE_DIR = os.path.expanduser("~/.cache/bandcamp-radio/art")


def _cached_art_bytes(url):
    """Bytes de la carátula en `url` -- de disco si ya se bajó antes, si
    no la trae por red y la deja cacheada para la próxima. Las URLs de
    Bandcamp son inmutables por álbum, así que cachear para siempre por
    URL (sin expiración) es seguro. Una ruta absoluta (modo "Local") se
    lee directo."""
    if url.startswith("/"):
        with open(url, "rb") as f:
            return f.read()
    os.makedirs(ART_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(ART_CACHE_DIR, hashlib.sha256(url.encode()).hexdigest())
    try:
        with open(cache_path, "rb") as f:
            return f.read()
    except OSError:
        pass
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    content = resp.content
    fd, tmp_path = tempfile.mkstemp(dir=ART_CACHE_DIR)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    os.replace(tmp_path, cache_path)
    return content


def _load_bandcamp_pixbuf(max_width, max_height):
    """Logo de Bandcamp recoloreado a BANDCAMP_COLOR, todo en memoria --
    mismo mecanismo que _action_icon_pixbuf en bluetooth_tab.py. None si
    el SVG no está (usuario lo borró) en vez de excepcionar; el llamador
    cae a un botón de texto en ese caso. preserve_aspect_ratio=True
    (encaja dentro de max_width/max_height, no fuerza los dos) --
    BANDCAMP_LOGO_VIEWBOX es una estimación a ojo del recorte real del
    wordmark, no medida exacta, así que forzar ambas dimensiones (False)
    estiraba el logo más ancho de lo que su proporción real permite."""
    try:
        with open(BANDCAMP_LOGO_SVG, "r", encoding="utf-8") as f:
            svg = f.read()
        svg = svg.replace("#000000", BANDCAMP_COLOR)
        svg = re.sub(r'viewBox="[^"]*"', f'viewBox="{BANDCAMP_LOGO_VIEWBOX}"', svg, count=1)
        stream = Gio.MemoryInputStream.new_from_data(svg.encode("utf-8"))
        return GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, max_width, max_height, True)
    except (OSError, GLib.Error):
        return None


MARQUEE_MAX_CHARS = 26  # medido a mano: lo que entra en título/artista/álbum sin desbordar la tarjeta
MARQUEE_TICK_MS = 350
MARQUEE_PAUSE_TICKS = 6  # pausa en cada punta antes de seguir


class _Marquee:
    """Título/artista/álbum pueden ser mucho más largos que la tarjeta
    -- en reposo se corta a MARQUEE_MAX_CHARS, y si el texto real no
    entra se desliza de a un carácter (ida y vuelta, con pausa en cada
    punta) en vez de partir línea o desbordar. `set_text` es el setter
    real del label (Gtk.Label.set_text, o el alias que arma
    _build_info_button)."""
    def __init__(self, set_text, max_chars=MARQUEE_MAX_CHARS):
        self._set_text = set_text
        self._max_chars = max_chars
        self._text = ""
        self._offset = 0
        self._direction = 1
        self._pause = 0
        self._timeout_id = None

    def update(self, text):
        text = text or ""
        if text == self._text:
            return
        self._text = text
        self._offset = 0
        self._direction = 1
        self._pause = MARQUEE_PAUSE_TICKS
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None
        self._render()
        if len(text) > self._max_chars:
            self._timeout_id = GLib.timeout_add(MARQUEE_TICK_MS, self._tick)

    def _render(self):
        self._set_text(self._text[self._offset:self._offset + self._max_chars])

    def _tick(self):
        if self._pause > 0:
            self._pause -= 1
            return True
        max_offset = len(self._text) - self._max_chars
        self._offset += self._direction
        if self._offset >= max_offset:
            self._offset = max_offset
            self._direction = -1
            self._pause = MARQUEE_PAUSE_TICKS
        elif self._offset <= 0:
            self._offset = 0
            self._direction = 1
            self._pause = MARQUEE_PAUSE_TICKS
        self._render()
        return True


class Popup:
    def __init__(self):
        self.window, container = build_layer_window("bandcamp-radio", CSS_FILE)
        container.set_name("container")
        position_fixed_top(container, margin=60)

        self._last_art_url = None
        self._last_tags_key = "[]"
        self._last_recs_key = "[]"
        self._last_artist_albums_key = "[]"
        self._item_url = None
        self._current_artist = None
        self._current_album_title = None
        self._duration = 0
        self._position = 0
        self._seeking = False
        self._wishlist_history = _load_wishlist_history()
        self._fav_albums, self._fav_artists = _load_favorites()
        # Usuario de la última búsqueda de wishlist enviada, hasta que el
        # próximo _apply_state confirme si encontró canción (éxito, se
        # guarda en el histórico) o no (error/sin resultados, se
        # descarta) -- ver _apply_state.
        self._pending_wishlist_username = None
        self._is_local = False
        self._local_gen = None
        self._local_tracks = None
        self._local_rows = {}
        self._local_current_path = None

        self._build_ui(container)
        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()
        # Recién acá la ventana tiene un scale-factor real (get_scale_factor
        # devuelve 1 hasta que el widget está mapeado a un monitor) --
        # confirmado a mano con `hyprctl monitors -j`: HDMI-A-2 en esta
        # máquina corre a escala 1.5, así que un pixbuf pedido a tamaño
        # lógico (sin este ajuste) queda 1.5x más chico que los píxeles
        # físicos reales y el compositor lo estira -- eso es lo que se veía
        # pixelado, no un problema del SVG en sí.
        self._apply_bandcamp_icon()

        self._load_last_tag()
        self._restore_genre_selection(self.tag_entry.get_text())
        self._poll_state()
        GLib.timeout_add(1000, self._poll_state)

    # ---- UI ----

    def _build_ui(self, container):
        # "Reproductor"/"Favoritos"/"Buscar" -- no un Gtk.Notebook (este
        # popup no usa ese widget en ningún otro lado, y el chrome
        # default de GTK3 no sigue el tema por CSS sin trabajo extra)
        # sino tres botones que alternan qué página está empacada en
        # `container` -- mismo criterio liviano que el resto del popup
        # (secciones plegables, tiras con flechas), sin arrastrar un
        # widget nuevo solo para esto. Antes había 2 pestañas
        # ("Buscar" con TODO junto -- buscador, géneros, tarjeta
        # "reproduciendo ahora" -- y "Favoritos") -- pedido explícito del
        # usuario, se separa el reproductor del buscador: player_page
        # agrupa la tarjeta "reproduciendo ahora" (antes vivía dentro de
        # discover_page), search_page el buscador/géneros/catálogo
        # elegible.
        self.tabs_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.tabs_row.set_name("tabs-row")
        container.pack_start(self.tabs_row, False, False, 0)

        self.player_tab_btn = Gtk.Button(label=t("bandcamp", "tab_player"))
        self.player_tab_btn.get_style_context().add_class("tab-btn")
        self.player_tab_btn.connect("clicked", lambda *_: self._switch_tab("player"))
        self.tabs_row.pack_start(self.player_tab_btn, True, True, 0)

        self.favorites_tab_btn = Gtk.Button(label=t("bandcamp", "tab_favorites"))
        self.favorites_tab_btn.get_style_context().add_class("tab-btn")
        self.favorites_tab_btn.connect("clicked", lambda *_: self._switch_tab("favorites"))
        self.tabs_row.pack_start(self.favorites_tab_btn, True, True, 0)

        self.search_tab_btn = Gtk.Button(label=t("bandcamp", "tab_search"))
        self.search_tab_btn.get_style_context().add_class("tab-btn")
        self.search_tab_btn.connect("clicked", lambda *_: self._switch_tab("search"))
        self.tabs_row.pack_start(self.search_tab_btn, True, True, 0)

        self.player_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        container.pack_start(self.player_page, False, False, 0)

        self.favorites_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        container.pack_start(self.favorites_page, False, False, 0)
        self.favorites_page.set_no_show_all(True)
        self.favorites_page.hide()

        self.search_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        container.pack_start(self.search_page, False, False, 0)
        self.search_page.set_no_show_all(True)
        self.search_page.hide()

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_row.set_name("search-row")
        self.search_page.pack_start(search_row, False, False, 0)

        self.tag_entry = Gtk.Entry()
        self.tag_entry.set_placeholder_text(DEFAULT_PLACEHOLDER)
        self.tag_entry.connect("activate", self._on_search_clicked)
        search_row.pack_start(self.tag_entry, True, True, 0)

        # Antes había que escribir "u:usuario"/"b:banda" a mano en
        # tag_entry -- ahora "Artista"/"Wishlist" son dos ítems más de
        # este mismo combo (pedido explícito del usuario), junto a los
        # slices de siempre. _on_search_mode_changed cambia el
        # placeholder de tag_entry y muestra/oculta wishlist_row según
        # el modo elegido.
        self.slice_combo = Gtk.ComboBoxText()
        for slug, label in SLICES + SEARCH_MODES:
            self.slice_combo.append(slug, label)
        self.slice_combo.set_active_id("new")
        self.slice_combo.connect("changed", self._on_search_mode_changed)
        search_row.pack_start(self.slice_combo, False, False, 0)

        # Debajo del buscador y arriba de todo lo demás (géneros, tags
        # de la canción adentro de la tarjeta) -- pedido explícito del
        # usuario, antes vivía pegado a now_playing en vez de al buscador.
        self.status_label = Gtk.Label(label=t("bandcamp", "status_choose_tag"))
        self.status_label.set_name("status-label")
        self.status_label.set_xalign(0)
        self.search_page.pack_start(self.status_label, False, False, 0)

        # Histórico de usuarios de wishlist buscados con éxito (ver
        # _apply_state/_remember_wishlist_username) -- solo visible en
        # modo "Wishlist", para volver a elegir uno rápido sin escribirlo
        # de nuevo. Mismo patrón de tira que genres_flow/tags_flow.
        self.wishlist_row, self.wishlist_scroller, self.wishlist_flow = self._build_scroll_strip("wishlist-history")
        self.search_page.pack_start(self.wishlist_row, False, False, 0)
        self.wishlist_row.set_no_show_all(True)
        self.wishlist_row.hide()
        self._rebuild_wishlist_history()

        # Una sola fila que scrollea a lo ANCHO (igual que la franja de
        # géneros de Bandcamp), no un grid que scrollea a lo alto -- por
        # eso Gtk.Box horizontal en vez de Gtk.FlowBox (FlowBox reflowea
        # a varias filas según el ancho disponible, no sirve para tirar
        # todos los chips en una tira scrolleable). Sin scrollbar visible
        # (policy NEVER/NEVER) -- se navega con las flechas de
        # _build_scroll_strip o con la rueda del mouse (_on_strip_scroll).
        self.genres_row, self.genres_scroller, self.genres_flow = self._build_scroll_strip("genres")
        self.search_page.pack_start(self.genres_row, False, False, 0)

        self._active_genre = None
        self._genre_buttons = {}
        for genre in GENRES:
            btn = self._build_genre_chip(genre["slug"], genre["label"])
            self._genre_buttons[genre["slug"]] = btn
            self.genres_flow.pack_start(btn, False, False, 0)
        self.genres_flow.show_all()
        # Confirmado a mano: sin esto, el hadjustment de genres_scroller
        # arranca clavado en el extremo derecho (se ve "Devotional..
        # Latin" en vez de "Rock" primero) en vez del 0 esperado -- hace
        # falta forzarlo tras el primer layout real (idle_add, no en el
        # mismo tick que show_all).
        GLib.idle_add(lambda: self.genres_scroller.get_hadjustment().set_value(0))

        self.subgenres_row, self.subgenres_scroller, self.subgenres_flow = self._build_scroll_strip("subgenres")
        self.search_page.pack_start(self.subgenres_row, False, False, 0)
        # Con background propio (@myforegroundhover2) la franja ya no
        # colapsa sola a 0 alto cuando subgenres_flow está vacía -- antes
        # de elegir un género se veía una píldora rosa vacía sin sentido.
        # set_no_show_all para que el show_all() de más abajo (una sola
        # vez, en __init__) no la vuelva a mostrar.
        self.subgenres_row.set_no_show_all(True)
        self.subgenres_row.hide()

        self._active_subgenre = None
        self._subgenre_buttons = {}

        # Catálogo elegible -- pedido explícito del usuario, 2026-08-20:
        # buscar ya no reproduce al instante (tag/género/subgénero,
        # Wishlist o Artista), el daemon arma un catálogo y espera a que
        # se elija un ítem acá (play_pending) para recién ahí arrancar,
        # dejando intacto lo que sonaba antes hasta ese momento. Mismo
        # chip que recomendaciones/álbumes de artista (_build_album_chip,
        # con on_click propio -- ver _on_catalog_chip_clicked), con un
        # label chico de la canción puntual debajo cuando el catálogo la
        # trae (Discover/Wishlist -- Artista no, ahí el chip es el álbum
        # entero). Sin colapsar por default (a diferencia de
        # Recomendaciones/Álbumes en player_page, que son info
        # secundaria) -- es la razón de ser de esta pestaña.
        self.catalog_row, self.catalog_scroller, self.catalog_flow = self._build_scroll_strip("catalog")
        self.search_page.pack_start(self.catalog_row, False, False, 0)
        self.catalog_row.set_no_show_all(True)
        self.catalog_row.hide()
        self._last_pending_catalog_key = None
        self._catalog_rendered_urls = []

        # search_page arranca oculto (no_show_all=True + hide(), ver
        # _build_ui arriba) porque la pestaña por default es "Reproductor"
        # -- eso significa que el show_all() del final de __init__ NUNCA
        # recorre sus hijos (gtk_widget_show_all corta de una si el
        # widget mismo tiene no_show_all -- ni siquiera llega a mirar sus
        # hijos), a diferencia de cuando discover_page (la pestaña única
        # de antes) arrancaba siempre visible. Un show_all() sobre
        # search_page mismo pega contra ESE mismo corte (no_show_all
        # está en el widget que lo recibe, no en sus ancestros) -- por
        # eso acá se llama show_all() pieza por pieza sobre lo estático
        # (wishlist_row/subgenres_row/catalog_row quedan afuera a
        # propósito, manejan su visibilidad solos) -- mismo fix de fondo
        # que favorites_page (_build_favorites_page, "scroller.show_all()
        # de más abajo"), adaptado porque acá no hay un único wrapper.
        search_row.show_all()
        self.status_label.show_all()
        self.genres_row.show_all()

        # now_playing pasó de ser una sola fila horizontal a una tarjeta
        # vertical -- top_row (carátula + info) arriba, tags_row (tags de
        # la canción) como fila nueva debajo, ambas DENTRO de la misma
        # tarjeta (#now-playing sigue siendo el fondo/radio de todo el
        # bloque, ya no solo de la fila superior).
        now_playing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        now_playing.set_name("now-playing")
        self.player_page.pack_start(now_playing, False, False, 0)

        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        now_playing.pack_start(top_row, False, False, 0)

        self.art_image = Gtk.Image()
        self.art_image.set_name("art-image")
        self.art_image.set_size_request(ART_SIZE, ART_SIZE)
        # valign FILL es el default -- sin esto, GTK estira el widget a
        # la altura completa de top_row (fijada por info_box, más alta
        # que ART_SIZE por título+artista+álbum+controles+progreso
        # apilados), dejando el margen/fondo #art-image rectangular en
        # vez de cuadrado. set_size_request es un piso, no un tamaño
        # fijo (mismo gotcha que min-width/min-height en CSS).
        self.art_image.set_valign(Gtk.Align.START)
        self._set_placeholder_art()
        top_row.pack_start(self.art_image, False, False, 0)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        top_row.pack_start(info_box, True, True, 0)

        self.title_label = Gtk.Label(label="—")
        self.title_label.set_name("title-label")
        self.title_label.set_xalign(0)
        info_box.pack_start(self.title_label, False, False, 0)
        self._title_marquee = _Marquee(self.title_label.set_text)

        # Artista y álbum son Gtk.Button (no Gtk.Label) -- clic en el
        # álbum reproduce el álbum completo (antes el botón separado
        # "Reproducir álbum", eliminado a pedido del usuario); clic en el
        # artista busca su discografía completa, igual que escribir
        # "b:<banda>" a mano (_search_artist, reusado por las dos vías).
        # set_relief(NONE) + CSS propio los deja verse como el label de
        # antes, no como un botón. Cada uno vive en su propia fila junto a
        # una estrella (_build_fav_toggle_button) para favoritear ESE
        # artista/álbum en particular por separado -- expand=True en el
        # botón de info, no en la estrella, para que la estrella quede
        # pegada al texto en vez de correrse al borde derecho de la
        # tarjeta.
        artist_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        info_box.pack_start(artist_row, False, False, 0)
        self.artist_label = self._build_info_button("artist-label")
        self.artist_label.connect("clicked", self._on_artist_clicked)
        artist_row.pack_start(self.artist_label, True, True, 0)
        self.artist_fav_btn = self._build_fav_toggle_button()
        self.artist_fav_btn.connect("clicked", self._on_toggle_artist_favorite)
        artist_row.pack_start(self.artist_fav_btn, False, False, 0)
        self._artist_marquee = _Marquee(self.artist_label.set_text)

        album_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        info_box.pack_start(album_row, False, False, 0)
        self.album_label = self._build_info_button("album-label")
        self.album_label.connect("clicked", self._on_like_clicked)
        album_row.pack_start(self.album_label, True, True, 0)
        self.album_fav_btn = self._build_fav_toggle_button()
        self.album_fav_btn.connect("clicked", self._on_toggle_album_favorite)
        album_row.pack_start(self.album_fav_btn, False, False, 0)
        self._album_marquee = _Marquee(self.album_label.set_text)

        # Controles y progreso -- antes vivían sueltos debajo de la
        # tarjeta, ahora adentro de info_box (pedido explícito del
        # usuario, "dentro del mismo cuadro de la canción"). Orden pedido
        # explícito: controles arriba, barra de progreso debajo (antes al
        # revés).
        # bottom_row: autoeq_btn pegado al borde izquierdo, controles
        # centrados en el medio, logo de Bandcamp a la derecha -- mismo
        # renglón. self.controls_row con expand=True + halign CENTER (en
        # vez de expand=False + spacer aparte) para que se centre en el
        # espacio que le sobra entre autoeq_btn y buy_row -- ver
        # _apply_bandcamp_icon para cómo se empareja ese espacio cuando
        # los dos anchos no coinciden.
        bottom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        info_box.pack_start(bottom_row, False, False, 0)

        # "Autoecualizador" -- pegado al borde izquierdo con un gap fijo
        # de 15px (pedido explícito del usuario, ver más abajo), AFUERA
        # de self.controls_row -- antes vivía adentro del grupo
        # centrado, pero ahí no había forma de separarlo visualmente del
        # transporte sin desarmar el centrado real (dos vueltas atrás:
        # primero probado como primer elemento de controls_row +
        # espaciador simétrico, funcionaba pero no lo dejaba pegado al
        # borde). Prende/apaga cmd_set_auto_eq en bandcamp_daemon.py, que en cada
        # canción nueva (tags reales resueltos) dispara flipfrog/scripts/
        # audio/eq_apply_preset.py con esos tags -- ver docstring de
        # bandcamp_daemon.py y de sound_module.py (pestaña "Sonido" del
        # dashboard) para el mecanismo completo. Ícono real de Papirus
        # ("multimedia-equalizer-symbolic", sigue el color de CSS como
        # el resto de los botones de transporte), no un glifo.
        self.autoeq_btn = Gtk.ToggleButton()
        self.autoeq_btn.set_image(Gtk.Image.new_from_icon_name("multimedia-equalizer-symbolic", Gtk.IconSize.BUTTON))
        self.autoeq_btn.get_image().set_pixel_size(16)
        self.autoeq_btn.set_name("autoeq-btn")
        self.autoeq_btn.set_margin_start(15)
        self.autoeq_btn.set_tooltip_text(t("bandcamp", "tooltip_autoeq"))
        self._autoeq_handler_id = self.autoeq_btn.connect("toggled", self._on_autoeq_toggled)
        bottom_row.pack_start(self.autoeq_btn, False, False, 0)

        self.controls_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.controls_row.set_name("controls-row")
        self.controls_row.set_halign(Gtk.Align.CENTER)
        bottom_row.pack_start(self.controls_row, True, True, 0)

        # Íconos reales del tema (Gtk.Image + set_pixel_size), no glifos de
        # texto -- confirmado a mano con Pango que "⏮"/"⏸"/"⏭" en este
        # sistema no los resuelve Rowdies/Symbols Nerd Font sino que caen
        # hasta "Noto Sans Symbols 2", cuya métrica de línea (logical
        # height) es ~2.3x la altura visual real del glifo (24px de fuente
        # -> ~22px de tinta pero ~56px de alto de línea). GtkButton siempre
        # crece para caber el tamaño NATURAL del label sin importar
        # min-height/min-width en CSS, así que ningún botón con ese glifo
        # podía quedar circular. set_pixel_size fuerza un tamaño exacto en
        # píxeles sin pasar por Pango -- mismo motivo que
        # IconTheme.load_icon(..., FORCE_SIZE) en menu_module.py/
        # process_module.py, pero con Gtk.Image.new_from_icon_name (no
        # IconTheme.load_icon) porque los símbolos "-symbolic" sí siguen el
        # "color" de CSS (ver bluetooth_tab.py) -- IconTheme.load_icon
        # hornea el color al cargar el pixbuf, sin CSS de por medio.
        self.prev_btn = Gtk.Button()
        self.prev_btn.set_image(Gtk.Image.new_from_icon_name("media-seek-backward-symbolic", Gtk.IconSize.BUTTON))
        self.prev_btn.get_image().set_pixel_size(18)
        self.prev_btn.set_name("prev-btn")
        self.prev_btn.set_sensitive(False)
        self.prev_btn.connect("clicked", self._on_prev_clicked)
        self.controls_row.pack_start(self.prev_btn, False, False, 0)

        self.play_pause_icon = Gtk.Image.new_from_icon_name("media-playback-pause-symbolic", Gtk.IconSize.BUTTON)
        self.play_pause_icon.set_pixel_size(PLAY_PAUSE_ICON_SIZE)
        self.play_pause_btn = Gtk.Button()
        self.play_pause_btn.set_image(self.play_pause_icon)
        self.play_pause_btn.set_name("play-pause-btn")
        self.play_pause_btn.connect("clicked", self._on_play_pause_clicked)
        self.controls_row.pack_start(self.play_pause_btn, False, False, 0)

        self.skip_btn = Gtk.Button()
        self.skip_btn.set_image(Gtk.Image.new_from_icon_name("media-seek-forward-symbolic", Gtk.IconSize.BUTTON))
        self.skip_btn.get_image().set_pixel_size(18)
        self.skip_btn.set_name("skip-btn")
        self.skip_btn.connect("clicked", self._on_skip_clicked)
        self.controls_row.pack_start(self.skip_btn, False, False, 0)

        # Ícono de Bandcamp tintado a su color de marca (BANDCAMP_COLOR)
        # en vez de un botón de texto -- mismo mecanismo que connect.svg/
        # disconnect.svg en bluetooth_tab.py (fill="#000000" fijo en el
        # SVG, se recolorea a mano vía Gio.MemoryInputStream, todo en
        # memoria). Nombre visible solo como tooltip, no como label.
        self.buy_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bottom_row.pack_start(self.buy_row, False, False, 0)

        self.buy_btn = Gtk.Button()
        self.buy_btn.set_name("buy-btn")
        self.buy_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.buy_btn.set_tooltip_text(t("bandcamp", "buy_on_bandcamp"))
        self.buy_btn.set_sensitive(False)
        self.buy_btn.connect("clicked", self._on_buy_clicked)
        # Clic derecho copia el link en vez de abrirlo -- "clicked" solo
        # dispara con click izquierdo, hace falta el evento crudo para
        # distinguir el botón 3 del mouse.
        self.buy_btn.connect("button-press-event", self._on_buy_right_click)
        self.buy_row.pack_start(self.buy_btn, False, False, 0)

        # Progreso debajo de los controles -- tiempo transcurrido a la
        # izquierda del slider, total a la derecha (antes un solo label
        # combinado "0:00 / 0:00" a la derecha del slider).
        progress_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        progress_row.set_name("progress-row")
        info_box.pack_start(progress_row, False, False, 0)

        # width_chars + xalign (mismo criterio que las columnas de
        # network_tab.py/process_module.py, ver CLAUDE.md) -- sin esto,
        # cada dígito pesa distinto en píxeles (p. ej. "1" vs "4") y el
        # ancho NATURAL del label cambia tick a tick, corriendo cuánto
        # espacio le queda a progress_scale (expand=True) y haciendo que
        # el slider parezca cambiar de tamaño solo.
        self.elapsed_label = Gtk.Label(label="0:00")
        self.elapsed_label.set_name("elapsed-label")
        self.elapsed_label.set_width_chars(5)
        self.elapsed_label.set_xalign(0.0)
        progress_row.pack_start(self.elapsed_label, False, False, 0)

        self.progress_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.progress_scale.set_name("progress-scale")
        self.progress_scale.set_draw_value(False)
        self.progress_scale.set_hexpand(True)
        self.progress_scale.connect("button-press-event", self._on_seek_start)
        self.progress_scale.connect("button-release-event", self._on_seek_end)
        progress_row.pack_start(self.progress_scale, True, True, 0)

        self.total_label = Gtk.Label(label="0:00")
        self.total_label.set_name("total-label")
        self.total_label.set_width_chars(5)
        self.total_label.set_xalign(1.0)
        progress_row.pack_start(self.total_label, False, False, 0)

        self.local_section, self.local_content, self.local_header, self.local_header_label = \
            self._build_collapsible_section("local-tracks", t("bandcamp", "section_local_tracks"))
        now_playing.pack_start(self.local_section, False, False, 0)
        self.local_section.set_no_show_all(True)
        self.local_section.hide()
        self.local_header.connect("clicked", lambda *_: self._render_local_tracks())

        local_scroller = Gtk.ScrolledWindow()
        local_scroller.set_name("local-tracks-scroller")
        local_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        local_scroller.set_max_content_height(LOCAL_LIST_MAX_HEIGHT)
        local_scroller.set_propagate_natural_height(True)
        self.local_content.pack_start(local_scroller, False, False, 0)
        self.local_scroller = local_scroller

        self.local_list = Gtk.ListBox()
        self.local_list.set_name("local-tracks-list")
        self.local_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.local_list.connect("row-activated", self._on_local_track_activated)
        local_scroller.add(self.local_list)

        # Tags reales de la canción sonando -- colapsados por default,
        # mismo patrón que Recomendaciones/Álbumes más abajo (pedido
        # explícito del usuario: antes se mostraban siempre sueltos,
        # ahora solo si se expande el control). tags_row en sí es la
        # misma tira de siempre (flechas, sin scrollbar visible, sin el
        # color de énfasis de genres/subgenres -- ver bandcamp_popup.css,
        # #tags-scroller sigue sin background), ahora empacada DENTRO de
        # tags_content en vez de directo en now_playing.
        self.tags_section, self.tags_content, self.tags_header, self.tags_header_label = \
            self._build_collapsible_section("tags", t("bandcamp", "section_tags_title"))
        now_playing.pack_start(self.tags_section, False, False, 0)
        self.tags_section.set_no_show_all(True)
        self.tags_section.hide()

        self.tags_row, self.tags_scroller, self.tags_flow = self._build_scroll_strip("tags")
        self.tags_content.pack_start(self.tags_row, False, False, 0)

        # Recomendaciones (Bandcamp las expone directo en el HTML del
        # álbum -- ver bandcamp_api.fetch_recommendations, sin fetch
        # extra) y discografía completa en modo Artista -- las dos
        # colapsadas por default (pedido explícito del usuario, para no
        # saturar el popup) y ocultas del todo si no hay nada que
        # mostrar (canción sin recomendaciones, o no estás en modo
        # Artista). Clic en cualquier carátula salta directo a ese álbum
        # (cmd_play_album), sin esperar el auto-play secuencial.
        self.recs_section, self.recs_content, self.recs_header, self.recs_header_label = \
            self._build_collapsible_section("recs", t("bandcamp", "recommendations_title"))
        now_playing.pack_start(self.recs_section, False, False, 0)
        self.recs_section.set_no_show_all(True)
        self.recs_section.hide()

        self.artist_albums_section, self.artist_albums_content, self.artist_albums_header, \
            self.artist_albums_header_label = self._build_collapsible_section(
                "artist-albums", t("bandcamp", "albums_title"))
        now_playing.pack_start(self.artist_albums_section, False, False, 0)
        self.artist_albums_section.set_no_show_all(True)
        self.artist_albums_section.hide()
        self.artist_albums_row, self.artist_albums_scroller, self.artist_albums_flow = \
            self._build_scroll_strip("artist-albums")
        self.artist_albums_content.pack_start(self.artist_albums_row, False, False, 0)

        self._build_favorites_page(self.favorites_page)
        self._active_tab = "player"
        self.player_tab_btn.get_style_context().add_class("active")

    def _build_favorites_page(self, page):
        """Pestaña "Favoritos" -- álbumes y artistas favoriteados con la
        estrella junto a artist_row/album_row (ver _on_toggle_artist_favorite/
        _on_toggle_album_favorite), listados para reproducir con un clic sin
        tener que volver a buscarlos. Alto fijo (FAVORITES_LIST_HEIGHT,
        min Y max) -- mismo motivo que LogViewer en firewall_log_viewer.py:
        el popup no debe cambiar de tamaño según cuántos favoritos haya ni
        al alternar de pestaña."""
        scroller = Gtk.ScrolledWindow()
        scroller.set_name("favorites-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(FAVORITES_LIST_HEIGHT)
        scroller.set_max_content_height(FAVORITES_LIST_HEIGHT)
        page.pack_start(scroller, False, False, 0)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroller.add(inner)

        albums_title = Gtk.Label(label=t("bandcamp", "albums_title"))
        albums_title.set_xalign(0)
        albums_title.get_style_context().add_class("favorites-section-title")
        inner.pack_start(albums_title, False, False, 0)

        self.fav_albums_empty_label = Gtk.Label(
            label=t("bandcamp", "favorites_empty_albums"))
        self.fav_albums_empty_label.set_xalign(0)
        self.fav_albums_empty_label.set_line_wrap(True)
        self.fav_albums_empty_label.get_style_context().add_class("favorites-empty-label")
        # no_show_all -- _rebuild_favorites_tab controla su visibilidad a
        # mano con set_visible() según haya o no favoritos; sin esto, el
        # scroller.show_all() de más abajo (necesario para revelar el
        # resto de la pestaña, ver ese comentario) la volvería a mostrar
        # de golpe aunque ya hubiera favoritos.
        self.fav_albums_empty_label.set_no_show_all(True)
        inner.pack_start(self.fav_albums_empty_label, False, False, 0)

        self.fav_albums_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.pack_start(self.fav_albums_box, False, False, 0)

        artists_title = Gtk.Label(label=t("bandcamp", "artists_title"))
        artists_title.set_xalign(0)
        artists_title.get_style_context().add_class("favorites-section-title")
        inner.pack_start(artists_title, False, False, 0)

        self.fav_artists_empty_label = Gtk.Label(
            label=t("bandcamp", "favorites_empty_artists"))
        self.fav_artists_empty_label.set_xalign(0)
        self.fav_artists_empty_label.set_line_wrap(True)
        self.fav_artists_empty_label.get_style_context().add_class("favorites-empty-label")
        self.fav_artists_empty_label.set_no_show_all(True)
        inner.pack_start(self.fav_artists_empty_label, False, False, 0)

        self.fav_artists_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.pack_start(self.fav_artists_box, False, False, 0)

        self._rebuild_favorites_tab()

        # favorites_page tiene no_show_all=True (ver _build_ui) para que
        # el show_all() inicial de toda la ventana no revele esta pestaña
        # de entrada -- pero gtk_widget_show_all() sobre un widget con
        # no_show_all=True vuelve INMEDIATO sin recorrer nada de lo que
        # tiene adentro (confirmado a mano: sin esto, scroller/inner/los
        # dos títulos quedaban con visible=False para siempre, ya que
        # _rebuild_favorites_tab de arriba solo hace show_all() sobre
        # fav_albums_box/fav_artists_box puntualmente, nunca sobre sus
        # ancestros). show_all() acá se llama DIRECTO sobre `scroller`
        # (hijo de favorites_page, sin no_show_all propio) -- eso sí
        # recorre todo el subárbol una sola vez, dejándolo visible para
        # siempre; de ahí en más, _switch_tab solo necesita show()/hide()
        # sobre favorites_page mismo.
        scroller.show_all()

    def _switch_tab(self, tab):
        """Alterna qué página está visible -- show()/hide() sobre el
        contenedor entero, nunca show_all() acá (pisaría el estado
        colapsado/oculto de filas internas como subgenres_row/tags_row/
        catalog_row, que usan set_no_show_all + hide() por su cuenta).
        Tres pestañas (Reproductor/Favoritos/Buscar, pedido explícito del
        usuario) en vez del booleano de antes -- sin cambio de pestaña
        automático en ningún punto del popup (mismo pedido), este método
        solo lo dispara un clic directo en tabs_row."""
        if tab == self._active_tab:
            return
        pages = {
            "player": (self.player_page, self.player_tab_btn),
            "favorites": (self.favorites_page, self.favorites_tab_btn),
            "search": (self.search_page, self.search_tab_btn),
        }
        for name, (page, btn) in pages.items():
            if name == tab:
                page.show()
                btn.get_style_context().add_class("active")
            else:
                page.hide()
                btn.get_style_context().remove_class("active")
        self._active_tab = tab

    def _build_fav_toggle_button(self):
        """Estrella junto a artist_row/album_row -- rellena
        (starred-symbolic) si ese artista/álbum ya está en favoritos,
        vacía (non-starred-symbolic) si no. Arranca sin sensibilidad: no
        hay canción cargada todavía al construir la UI, _update_favorite_
        buttons la habilita apenas hay algo sonando."""
        btn = Gtk.Button()
        btn.set_image(Gtk.Image.new_from_icon_name("non-starred-symbolic", Gtk.IconSize.BUTTON))
        btn.get_image().set_pixel_size(15)
        btn.get_style_context().add_class("fav-toggle-btn")
        btn.set_sensitive(False)
        return btn

    def _build_favorite_row(self, title, subtitle, art_url, on_play, on_remove):
        """Fila de la pestaña "Favoritos" -- reusa _load_chip_art (mismo
        mecanismo async de _build_album_chip) para la carátula; sin
        art_url (siempre el caso para un artista) cae a un ícono
        genérico de persona en vez de dejar el slot vacío."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("favorite-row")

        image = Gtk.Image()
        image.set_size_request(ALBUM_CHIP_SIZE, ALBUM_CHIP_SIZE)
        image.set_from_icon_name(
            "audio-x-generic" if art_url else "avatar-default-symbolic", Gtk.IconSize.DIALOG)
        row.pack_start(image, False, False, 0)
        if art_url:
            self._load_chip_art(image, art_url)

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_valign(Gtk.Align.CENTER)
        title_label = Gtk.Label(label=title)
        title_label.set_xalign(0)
        title_label.set_line_wrap(True)
        title_label.get_style_context().add_class("favorite-row-title")
        text_box.pack_start(title_label, False, False, 0)
        if subtitle:
            subtitle_label = Gtk.Label(label=subtitle)
            subtitle_label.set_xalign(0)
            subtitle_label.get_style_context().add_class("favorite-row-subtitle")
            text_box.pack_start(subtitle_label, False, False, 0)
        row.pack_start(text_box, True, True, 0)

        play_btn = Gtk.Button()
        play_btn.set_image(Gtk.Image.new_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.BUTTON))
        play_btn.get_image().set_pixel_size(16)
        play_btn.get_style_context().add_class("favorite-play-btn")
        play_btn.set_tooltip_text(t("bandcamp", "tooltip_play"))
        play_btn.connect("clicked", lambda *_: on_play())
        row.pack_start(play_btn, False, False, 0)

        remove_btn = Gtk.Button()
        remove_btn.set_image(Gtk.Image.new_from_icon_name("starred-symbolic", Gtk.IconSize.BUTTON))
        remove_btn.get_image().set_pixel_size(16)
        remove_btn.get_style_context().add_class("favorite-remove-btn")
        remove_btn.set_tooltip_text(t("bandcamp", "tooltip_remove_favorite"))
        remove_btn.connect("clicked", lambda *_: on_remove())
        row.pack_start(remove_btn, False, False, 0)

        return row

    def _rebuild_favorites_tab(self):
        for child in self.fav_albums_box.get_children():
            self.fav_albums_box.remove(child)
        for entry in self._fav_albums:
            row = self._build_favorite_row(
                entry.get("album_title") or "—", entry.get("band_name"), entry.get("art_url"),
                on_play=lambda e=entry: self._on_play_favorite_album(e),
                on_remove=lambda e=entry: self._on_remove_favorite_album(e))
            self.fav_albums_box.pack_start(row, False, False, 0)
        self.fav_albums_box.show_all()
        self.fav_albums_empty_label.set_visible(not self._fav_albums)

        for child in self.fav_artists_box.get_children():
            self.fav_artists_box.remove(child)
        for entry in self._fav_artists:
            row = self._build_favorite_row(
                entry.get("name") or "—", t("bandcamp", "discography_full"), None,
                on_play=lambda e=entry: self._on_play_favorite_artist(e),
                on_remove=lambda e=entry: self._on_remove_favorite_artist(e))
            self.fav_artists_box.pack_start(row, False, False, 0)
        self.fav_artists_box.show_all()
        self.fav_artists_empty_label.set_visible(not self._fav_artists)

    def _is_album_favorited(self, item_url):
        return any(a.get("item_url") == item_url for a in self._fav_albums)

    def _is_artist_favorited(self, name):
        return any(a.get("name", "").lower() == name.lower() for a in self._fav_artists)

    def _on_toggle_album_favorite(self, *_):
        item_url = self._item_url
        if not item_url:
            return
        if self._is_album_favorited(item_url):
            self._fav_albums = [a for a in self._fav_albums if a.get("item_url") != item_url]
        else:
            self._fav_albums.insert(0, {
                "item_url": item_url,
                "band_name": self._current_artist,
                "album_title": self._current_album_title,
                "art_url": self._last_art_url,
            })
        _save_favorites(self._fav_albums, self._fav_artists)
        self._update_favorite_buttons()
        self._rebuild_favorites_tab()

    def _on_toggle_artist_favorite(self, *_):
        name = self._current_artist
        if not name:
            return
        if self._is_artist_favorited(name):
            self._fav_artists = [a for a in self._fav_artists if a.get("name", "").lower() != name.lower()]
        else:
            self._fav_artists.insert(0, {"name": name, "item_url_hint": self._item_url})
        _save_favorites(self._fav_albums, self._fav_artists)
        self._update_favorite_buttons()
        self._rebuild_favorites_tab()

    def _on_play_favorite_album(self, entry):
        """Reproduce directo (cmd_play_album, sin catálogo de por medio --
        elegir un favorito ya es una elección concreta) -- cambia a
        "Reproductor" para verlo sonar, única excepción al "sin cambio de
        pestaña automático" porque acá SÍ arranca al toque, a diferencia
        de _on_play_favorite_artist."""
        item_url = entry.get("item_url")
        if not item_url:
            return
        bandcamp_ipc.ensure_daemon_running()
        bandcamp_ipc.send_command("play_album", item_url=item_url, meta=entry)
        self._switch_tab("player")
        self._poll_state()

    def _on_play_favorite_artist(self, entry):
        """A diferencia de _on_play_favorite_album, esto arma un catálogo
        elegible (mismo criterio que cualquier búsqueda de Artista, ver
        daemon.cmd_search_artist) -- sin cambio de pestaña automático
        (pedido explícito del usuario), el usuario entra a "Buscar" a
        mano para elegir el álbum inicial."""
        name = entry.get("name")
        if not name:
            return
        bandcamp_ipc.ensure_daemon_running()
        self.slice_combo.set_active_id("artist")
        self.tag_entry.set_text(name)
        self._save_last_tag(f"b:{name}")
        self._search_artist(name, item_url_hint=entry.get("item_url_hint"))

    def _on_remove_favorite_album(self, entry):
        item_url = entry.get("item_url")
        self._fav_albums = [a for a in self._fav_albums if a.get("item_url") != item_url]
        _save_favorites(self._fav_albums, self._fav_artists)
        self._update_favorite_buttons()
        self._rebuild_favorites_tab()

    def _on_remove_favorite_artist(self, entry):
        name = (entry.get("name") or "").lower()
        self._fav_artists = [a for a in self._fav_artists if a.get("name", "").lower() != name]
        _save_favorites(self._fav_albums, self._fav_artists)
        self._update_favorite_buttons()
        self._rebuild_favorites_tab()

    def _update_favorite_buttons(self):
        """Refresca el relleno de las estrellas junto a artist_row/
        album_row -- llamado desde _apply_state en cada tick (barato,
        solo dos búsquedas lineales sobre listas cortas) y tras cualquier
        mutación local de _fav_albums/_fav_artists."""
        has_artist = bool(self._current_artist) and not self._is_local
        self.artist_fav_btn.set_sensitive(has_artist)
        artist_fav = has_artist and self._is_artist_favorited(self._current_artist)
        self.artist_fav_btn.get_image().set_from_icon_name(
            "starred-symbolic" if artist_fav else "non-starred-symbolic", Gtk.IconSize.BUTTON)
        self.artist_fav_btn.get_image().set_pixel_size(15)
        self.artist_fav_btn.set_tooltip_text(
            t("bandcamp", "tooltip_remove_artist_favorite") if artist_fav
            else t("bandcamp", "tooltip_add_artist_favorite"))

        has_album = bool(self._item_url)
        self.album_fav_btn.set_sensitive(has_album)
        album_fav = has_album and self._is_album_favorited(self._item_url)
        self.album_fav_btn.get_image().set_from_icon_name(
            "starred-symbolic" if album_fav else "non-starred-symbolic", Gtk.IconSize.BUTTON)
        self.album_fav_btn.get_image().set_pixel_size(15)
        self.album_fav_btn.set_tooltip_text(
            t("bandcamp", "tooltip_remove_album_favorite") if album_fav
            else t("bandcamp", "tooltip_add_album_favorite"))

    # ---- Búsqueda / control (todo pasa por bandcamp_ipc, nunca mpv directo) ----

    def _on_search_mode_changed(self, combo):
        """slice_combo -- además de los slices de siempre (new/top/rand)
        ahora tiene dos modos más (Artista/Wishlist, ver SEARCH_MODES)
        que antes eran prefijos "a:"/"b:"/"u:" escritos a mano. Cambiar
        de modo actualiza el placeholder y muestra/oculta wishlist_row
        (solo tiene sentido en modo Wishlist, y solo si hay algo en el
        histórico)."""
        mode = combo.get_active_id()
        self.tag_entry.set_placeholder_text(MODE_PLACEHOLDERS.get(mode, DEFAULT_PLACEHOLDER))
        if mode == "wishlist" and self._wishlist_history:
            self.wishlist_row.set_no_show_all(False)
            self.wishlist_row.show_all()
        else:
            self.wishlist_row.hide()

    def _on_search_clicked(self, *_):
        raw = self.tag_entry.get_text().strip()
        mode = self.slice_combo.get_active_id() or "new"
        if not raw and mode != "local":
            return
        bandcamp_ipc.ensure_daemon_running()

        if mode == "local":
            self._save_last_tag(f"l:{raw}")
            self.status_label.set_text(t("bandcamp", "status_searching"))
            bandcamp_ipc.send_command("search_local", query=raw)
            self._poll_state()
        elif mode == "wishlist":
            self._save_last_tag(f"u:{raw}")
            self._pending_wishlist_username = raw
            self._search_wishlist(raw)
        elif mode == "artist":
            self._save_last_tag(f"b:{raw}")
            self._search_artist(raw)
        else:
            tags = _split_tags(raw)
            if not tags:
                return
            self._save_last_tag(raw)
            self.status_label.set_text(t("bandcamp", "status_searching"))
            bandcamp_ipc.send_command("search", tags=tags, slice=mode)
            self._poll_state()

    def _on_skip_clicked(self, *_):
        bandcamp_ipc.send_command("skip")
        self._poll_state()

    def _on_prev_clicked(self, *_):
        bandcamp_ipc.send_command("prev")
        self._poll_state()

    def _on_play_pause_clicked(self, *_):
        bandcamp_ipc.send_command("toggle_pause")
        self._poll_state()

    def _on_autoeq_toggled(self, btn):
        bandcamp_ipc.send_command("set_auto_eq", enabled=btn.get_active())

    def _on_like_clicked(self, *_):
        bandcamp_ipc.send_command("like")
        self._poll_state()

    def _search_artist(self, name, item_url_hint=None):
        self.status_label.set_text(t("bandcamp", "status_searching_discography", name=name))
        if item_url_hint:
            bandcamp_ipc.send_command("search_artist", name=name, item_url=item_url_hint)
        else:
            bandcamp_ipc.send_command("search_artist", name=name)
        self._poll_state()

    def _search_wishlist(self, username):
        self.status_label.set_text(t("bandcamp", "status_searching_wishlist", username=username))
        bandcamp_ipc.send_command("search_wishlist", username=username)
        self._poll_state()

    def _on_wishlist_history_clicked(self, username):
        self.slice_combo.set_active_id("wishlist")
        self.tag_entry.set_text(username)
        self._on_search_clicked()

    def _remember_wishlist_username(self, username):
        """Se llama solo tras una búsqueda de wishlist confirmada como
        exitosa (ver _apply_state) -- una que falló (usuario inválido,
        sin resultados) no ensucia el histórico."""
        history = [u for u in self._wishlist_history if u.lower() != username.lower()]
        history.insert(0, username)
        self._wishlist_history = history[:WISHLIST_HISTORY_MAX]
        _save_wishlist_history(self._wishlist_history)
        self._rebuild_wishlist_history()
        if self.slice_combo.get_active_id() == "wishlist":
            self.wishlist_row.set_no_show_all(False)
            self.wishlist_row.show_all()

    def _rebuild_wishlist_history(self):
        for child in self.wishlist_flow.get_children():
            self.wishlist_flow.remove(child)
        for username in self._wishlist_history:
            btn = Gtk.Button(label=username)
            btn.get_style_context().add_class("history-chip")
            btn.connect("clicked", lambda *_, u=username: self._on_wishlist_history_clicked(u))
            self.wishlist_flow.pack_start(btn, False, False, 0)
        self.wishlist_flow.show_all()
        GLib.idle_add(lambda: self.wishlist_scroller.get_hadjustment().set_value(0))

    def _on_artist_clicked(self, *_):
        """Clic en el nombre de la banda (artist_label, ver
        _build_info_button) -- misma acción que elegir el modo "Artista"
        del combo a mano, reusando _search_artist. A diferencia de una
        búsqueda manual, ACÁ ya se conoce el item_url real de la canción
        que está sonando -- se manda como pista para que el daemon saque
        el subdominio directo de esa URL en vez de adivinarlo del nombre
        (pedido explícito del usuario: adivinar falla con colisiones de
        nombre reales en Bandcamp, ej. "DEPRAVITY" mostrado pero
        "depravitydeath" en la URL real)."""
        if not self._current_artist:
            return
        self.slice_combo.set_active_id("artist")
        self.tag_entry.set_text(self._current_artist)
        self._save_last_tag(f"b:{self._current_artist}")
        bandcamp_ipc.ensure_daemon_running()
        self._search_artist(self._current_artist, item_url_hint=self._item_url)

    def _on_buy_clicked(self, *_):
        if self._item_url:
            subprocess.Popen(["xdg-open", self._item_url], start_new_session=True)

    def _on_buy_right_click(self, _widget, event):
        if event.button == 3 and self._item_url:
            # wl-copy por subprocess, nunca Gtk.Clipboard -- no sobrevive
            # el cierre del proceso bajo Wayland (mismo criterio que
            # updates_module.py).
            subprocess.run(["wl-copy"], input=self._item_url, text=True)
        return False

    def _apply_bandcamp_icon(self):
        """Genera el pixbuf del logo a BANDCAMP_LOGO_WIDTH/HEIGHT * scale-
        factor real del monitor y lo envuelve en un cairo surface con ESE
        mismo scale (Gdk.cairo_surface_create_from_pixbuf) -- Gtk.Image.
        new_from_pixbuf a secas ignora el scale-factor del monitor y
        muestra el pixbuf 1:1 en píxeles físicos, por eso se veía
        pixelado/estirado en un monitor de escala fraccionaria."""
        scale = self.window.get_scale_factor() or 1
        pixbuf = _load_bandcamp_pixbuf(BANDCAMP_LOGO_WIDTH * scale, BANDCAMP_LOGO_HEIGHT * scale)
        if pixbuf is None:
            self.buy_btn.set_label(t("bandcamp", "buy_on_bandcamp"))
        else:
            surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, None)
            self.buy_btn.set_image(Gtk.Image.new_from_surface(surface))

        # controls_row centra en el espacio que le sobra entre autoeq_btn
        # (izquierda, ancho fijo con su margen de 15px) y buy_row
        # (derecha) -- ver bottom_row en _build_ui. Si esos dos anchos no
        # son iguales, el centro real de controls_row queda corrido hacia
        # el lado más angosto. Se empareja agregando la diferencia como
        # margen del lado que falta (nunca los dos a la vez) -- confirmado
        # a mano con una ventana Gtk.OffscreenWindow reproduciendo la
        # misma estructura: sin esto, el grupo de transporte quedaba
        # corrido respecto al centro real de la barra de progreso.
        # Recién acá se conoce el ancho real de buy_btn (antes de
        # _apply_bandcamp_icon, buy_btn no tiene imagen/label todavía),
        # por eso el ajuste vive en este método y no en _build_ui.
        _, buy_width = self.buy_btn.get_preferred_width()
        _, autoeq_width = self.autoeq_btn.get_preferred_width()
        diff = autoeq_width - buy_width
        self.buy_row.set_margin_end(max(0, diff))
        self.controls_row.set_margin_start(max(0, -diff))

    def _build_info_button(self, name):
        """title_label/artist_label -- se ven como el Gtk.Label que eran
        antes (mismo #<name> de bandcamp_popup.css, sin fondo/borde de botón vía
        CSS), pero clickeables. set_text delegado a mano al Gtk.Label
        interno para no tener que cambiar cada `self.title_label.
        set_text(...)`/`self.artist_label.set_text(...)` ya existente."""
        label = Gtk.Label(label="")
        label.set_xalign(0)
        btn = Gtk.Button()
        btn.set_name(name)
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.add(label)
        btn.set_text = label.set_text
        return btn

    def _build_collapsible_section(self, name, base_title):
        """Sección plegable, colapsada por default -- botón de título
        (con ▸/▾) + una caja de contenido debajo que el llamador llena
        con lo que quiera (una o varias tiras). `header.expanded`/
        `header.base_title` viven como atributos sueltos del propio
        Gtk.Button (GObject permite atributos arbitrarios) en vez de un
        dict de closure aparte, para que _rebuild_* pueda leer/actualizar
        el título (ej. "Álbumes de <banda>") sin perder el estado
        expandido/colapsado actual. content_box con set_no_show_all para
        que un show_all() del wrapper (cuando llega data nueva) no lo
        muestre de golpe si el usuario nunca lo expandió -- mismo patrón
        que subgenres_row/tags_row."""
        wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wrapper.set_name(f"{name}-section")

        header = Gtk.Button()
        header.set_relief(Gtk.ReliefStyle.NONE)
        header.set_name(f"{name}-header")
        header.expanded = False
        header.base_title = base_title
        header_label = Gtk.Label(label=f"▸ {base_title}")
        header_label.set_xalign(0)
        header.add(header_label)
        wrapper.pack_start(header, False, False, 0)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        wrapper.pack_start(content_box, False, False, 0)
        content_box.set_no_show_all(True)
        content_box.hide()

        def _on_toggle(*_):
            header.expanded = not header.expanded
            header_label.set_text(f"{'▾' if header.expanded else '▸'} {header.base_title}")
            if header.expanded:
                content_box.set_no_show_all(False)
                content_box.show_all()
            else:
                content_box.hide()
        header.connect("clicked", _on_toggle)

        return wrapper, content_box, header, header_label

    def _sync_local_tracks(self, state):
        gen = state.get("local_playlist_gen") if state.get("local") else None
        if gen is None:
            self._local_gen = None
            self._local_tracks = None
            self.local_section.hide()
            return
        if gen != self._local_gen:
            reply = bandcamp_ipc.send_command("get_local_playlist")
            if reply is None or reply.get("gen") != gen:
                return
            self._local_gen = gen
            self._local_tracks = reply.get("tracks") or []
            self._local_rows = {}
            self.local_header.base_title = t("bandcamp", "section_local_tracks_count", n=len(self._local_tracks))
            self.local_header_label.set_text(
                f"{'▾' if self.local_header.expanded else '▸'} {self.local_header.base_title}")
            self.local_section.set_no_show_all(False)
            self.local_section.show()
            self.local_section.get_children()[0].show_all()
            self._render_local_tracks()
        path = state.get("local_path")
        if path != self._local_current_path:
            self._local_current_path = path
            self._mark_local_current(scroll=True)

    def _render_local_tracks(self):
        """Filas recién al expandir -- "Toda la biblioteca" son ~1350."""
        if not self.local_header.expanded or self._local_tracks is None or self._local_rows:
            return
        for child in self.local_list.get_children():
            self.local_list.remove(child)
        for i, track in enumerate(self._local_tracks):
            row = Gtk.ListBoxRow()
            row.track_index = i
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            num = Gtk.Label(label=str(i + 1))
            num.get_style_context().add_class("local-track-num")
            num.set_width_chars(len(str(len(self._local_tracks))))
            num.set_xalign(1.0)
            box.pack_start(num, False, False, 0)
            title = Gtk.Label(label=track["title"])
            title.get_style_context().add_class("local-track-title")
            title.set_xalign(0)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.set_max_width_chars(1)
            title.set_tooltip_text(track["title"])
            box.pack_start(title, True, True, 0)
            row.add(box)
            self.local_list.add(row)
            self._local_rows[track["path"]] = row
        self.local_list.show_all()
        # Piso explícito: el popup se asigna a su alto mínimo, y el mínimo
        # de un ScrolledWindow es casi nada aunque el natural sea mayor.
        natural = self.local_list.get_preferred_height()[1]
        self.local_scroller.set_min_content_height(min(natural, LOCAL_LIST_MAX_HEIGHT))
        self._mark_local_current(scroll=True)

    def _mark_local_current(self, scroll=False):
        for path, row in self._local_rows.items():
            ctx = row.get_style_context()
            if path == self._local_current_path:
                ctx.add_class("current")
            else:
                ctx.remove_class("current")
        row = self._local_rows.get(self._local_current_path)
        if scroll and row is not None and self.local_header.expanded:
            attempts = [0]

            def _try():
                attempts[0] += 1
                return not self._scroll_local_to(row) and attempts[0] < 20
            GLib.timeout_add(50, _try)

    def _scroll_local_to(self, row):
        """False si la fila todavía no tiene tamaño real."""
        alloc = row.get_allocation()
        if alloc.height <= 1:
            return False
        adj = self.local_scroller.get_vadjustment()
        page = adj.get_page_size()
        if alloc.y < adj.get_value() or alloc.y + alloc.height > adj.get_value() + page:
            adj.set_value(max(0, alloc.y - (page - alloc.height) / 2))
        return True

    def _on_local_track_activated(self, _list, row):
        bandcamp_ipc.send_command("play_local_track", index=row.track_index)

    def _build_album_chip(self, item_url, art_url, title, band, year=None, subtitle=None, on_click=None, size=None):
        """Chip de carátula chica -- reusado por la tira de
        recomendaciones, la de discografía de artista y (desde 2026-08-20)
        el catálogo elegible de la pestaña "Buscar". Clic salta directo a
        ESE ítem (por default cmd_play_album, sin pasar por el auto-play
        secuencial normal) -- `on_click(item_url)` opcional pisa eso para
        el catálogo, que en cambio manda play_pending (ver
        _on_catalog_chip_clicked). `year` es opcional -- bandcamp_api.
        fetch_album() lo saca del blob tralbum (discografía de artista),
        pero fetch_recommendations() no tiene esa fecha disponible en el
        HTML que scrapea, así que esos chips quedan sin año, no es un
        descuido. `subtitle` (la pista puntual que arrancaría, catálogo de
        Discover/Wishlist) y `year` nunca conviven -- son variantes del
        mismo label chico bajo la carátula, no dos filas. `size` opcional
        pisa ALBUM_CHIP_SIZE -- el catálogo de "Buscar" pasa
        CATALOG_CHIP_SIZE, las demás tiras se quedan en el tamaño chico
        default."""
        size = size or ALBUM_CHIP_SIZE
        image = Gtk.Image()
        image.set_size_request(size, size)
        image.set_from_icon_name("audio-x-generic", Gtk.IconSize.DIALOG)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.pack_start(image, False, False, 0)
        if year:
            year_label = Gtk.Label(label=str(year))
            year_label.get_style_context().add_class("album-chip-year")
            box.pack_start(year_label, False, False, 0)
        elif subtitle:
            subtitle_label = Gtk.Label(label=subtitle)
            subtitle_label.set_ellipsize(Pango.EllipsizeMode.END)
            subtitle_label.set_max_width_chars(12)
            subtitle_label.get_style_context().add_class("album-chip-subtitle")
            box.pack_start(subtitle_label, False, False, 0)

        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.get_style_context().add_class("album-chip")
        if size != ALBUM_CHIP_SIZE:
            btn.get_style_context().add_class("catalog-chip")
        tooltip = f"{subtitle} — {band}" if subtitle and band else (f"{title} — {band}" if title or band else None)
        btn.set_tooltip_text(tooltip)
        btn.add(box)
        handler = on_click or self._on_album_chip_clicked
        btn.connect("clicked", lambda *_: handler(item_url))

        if art_url:
            self._load_chip_art(image, art_url, size=size)
        return btn

    def _load_chip_art(self, image, url, size=None):
        """Mismo mecanismo que _load_art (thread + idle_add) pero para un
        chip chico de una tira -- sin token de generación: a diferencia
        de art_image (un solo widget reusado entre canciones), cada chip
        es un Gtk.Image nuevo que se tira entero cuando la tira se
        reconstruye, no hay resultado viejo que pueda "ganarle" a uno
        nuevo sobre el mismo widget. `size` opcional pisa ALBUM_CHIP_SIZE
        (y su radio proporcional) -- ver _build_album_chip."""
        size = size or ALBUM_CHIP_SIZE
        radius = _current_radius()

        def worker():
            try:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(_cached_art_bytes(url))
                loader.close()
                pixbuf = loader.get_pixbuf().scale_simple(
                    size, size, GdkPixbuf.InterpType.BILINEAR)
                pixbuf = _round_corners(pixbuf, radius)
            except Exception:
                return
            GLib.idle_add(image.set_from_pixbuf, pixbuf)

        threading.Thread(target=worker, daemon=True).start()

    def _on_album_chip_clicked(self, item_url):
        bandcamp_ipc.send_command("play_album", item_url=item_url)
        self._poll_state()

    def _on_catalog_chip_clicked(self, item_url):
        """Clic en un chip del catálogo elegible (pestaña "Buscar") --
        commit real, recién acá arranca la reproducción (ver
        daemon.cmd_play_pending). Sin cambio de pestaña automático
        (pedido explícito del usuario) -- el próximo poll ya refleja el
        catálogo vacío y la nueva canción sonando si se cambia a
        "Reproductor" a mano."""
        bandcamp_ipc.send_command("play_pending", item_url=item_url)
        self._poll_state()

    def _rebuild_recommendations(self, sections):
        for child in self.recs_content.get_children():
            self.recs_content.remove(child)
        for i, section in enumerate(sections):
            subtitle = Gtk.Label(label=section.get("title") or "")
            subtitle.set_xalign(0)
            subtitle.get_style_context().add_class("recs-subtitle")
            self.recs_content.pack_start(subtitle, False, False, 0)

            # A lo sumo 2 secciones reales en cualquier página de
            # Bandcamp (recomendación de la banda + de la plataforma,
            # ver bandcamp_api.RECS_SECTION_RE) -- nombre fijo por índice
            # en vez de dinámico para poder tener CSS #recs-strip-N-*
            # sin generar selectores al vuelo.
            row, scroller, flow = self._build_scroll_strip(f"recs-strip-{i}")
            self.recs_content.pack_start(row, False, False, 0)
            for item in section.get("items", []):
                chip = self._build_album_chip(
                    item.get("item_url"), item.get("art_url"),
                    item.get("album_title"), item.get("band_name"), year=item.get("year"))
                flow.pack_start(chip, False, False, 0)
            row.show_all()
            GLib.idle_add(lambda s=scroller: s.get_hadjustment().set_value(0))

        if sections:
            self.recs_section.set_no_show_all(False)
            self.recs_section.show_all()
            if not self.recs_header.expanded:
                self.recs_content.hide()
        else:
            self.recs_section.hide()

    def _rebuild_artist_albums(self, albums):
        for child in self.artist_albums_flow.get_children():
            self.artist_albums_flow.remove(child)
        for album in albums:
            chip = self._build_album_chip(
                album.get("item_url"), album.get("art_url"),
                album.get("album_title"), album.get("band_name"), year=album.get("year"))
            self.artist_albums_flow.pack_start(chip, False, False, 0)
        self.artist_albums_flow.show_all()
        GLib.idle_add(lambda: self.artist_albums_scroller.get_hadjustment().set_value(0))

        if albums:
            band_name = albums[0].get("band_name")
            title = t("bandcamp", "album_of_artist", band=band_name) if band_name \
                else t("bandcamp", "albums_title")
            self.artist_albums_header.base_title = title
            arrow = "▾" if self.artist_albums_header.expanded else "▸"
            self.artist_albums_header_label.set_text(f"{arrow} {title}")
            self.artist_albums_section.set_no_show_all(False)
            self.artist_albums_section.show_all()
            if not self.artist_albums_header.expanded:
                self.artist_albums_content.hide()
        else:
            self.artist_albums_section.hide()

    def _catalog_chip(self, entry):
        return self._build_album_chip(
            entry.get("item_url"), entry.get("art_url"),
            entry.get("album_title"), entry.get("band_name"),
            year=entry.get("year"), subtitle=entry.get("track_title"),
            on_click=self._on_catalog_chip_clicked, size=CATALOG_CHIP_SIZE)

    def _rebuild_catalog(self, catalog):
        """Grid de la pestaña "Buscar" (ver _apply_state, disparado por un
        diff de item_url contra self._last_pending_catalog_key) -- mismo
        chip que recomendaciones/álbumes de artista, pero con on_click
        propio (_on_catalog_chip_clicked -> play_pending) y subtitle en
        vez de year para discover/wishlist (track_title -- la pista
        puntual que arrancaría, ver queue_feed.py catalog_preview()). Las
        entradas de modo Artista no traen track_title (son álbumes
        completos, no una pista) pero sí year -- _build_album_chip ya
        elige uno u otro solo.

        Si `catalog` extiende al anterior (mismo prefijo de item_url,
        ver _on_strip_arrow_clicked -> "load_more_catalog") solo se
        agregan los chips nuevos al final, sin resetear el scroll --
        un rebuild completo mandaría la tira de vuelta al principio
        justo después de pedir más resultados."""
        urls = [c.get("item_url") for c in catalog]
        is_extension = bool(self._catalog_rendered_urls) and \
            urls[:len(self._catalog_rendered_urls)] == self._catalog_rendered_urls

        if is_extension:
            for entry in catalog[len(self._catalog_rendered_urls):]:
                self.catalog_flow.pack_start(self._catalog_chip(entry), False, False, 0)
            self.catalog_flow.show_all()
        else:
            for child in self.catalog_flow.get_children():
                self.catalog_flow.remove(child)
            for entry in catalog:
                self.catalog_flow.pack_start(self._catalog_chip(entry), False, False, 0)
            self.catalog_flow.show_all()
            GLib.idle_add(lambda: self.catalog_scroller.get_hadjustment().set_value(0))
        self._catalog_rendered_urls = urls

        if catalog:
            self.catalog_row.set_no_show_all(False)
            self.catalog_row.show_all()
        else:
            self.catalog_row.hide()

    def _build_scroll_strip(self, name):
        """Fila "<< [tira de chips sin scrollbar visible] >>" reusada por
        genres_flow/subgenres_flow -- devuelve (row, scroller, flow) para
        que el llamador solo tenga que empacar chips en `flow`."""
        flow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        flow.set_name(f"{name}-flow")

        scroller = Gtk.ScrolledWindow()
        scroller.set_name(f"{name}-scroller")
        # AUTOMATIC, no NEVER: con NEVER en los dos ejes GTK deja de
        # tratar esto como scrolleable de verdad -- confirmado a mano,
        # el ScrolledWindow pedía el ancho NATURAL completo (las 23
        # chips sin recortar) en vez de limitarse al espacio disponible,
        # así que la ventana entera crecía y nunca hacía falta scrollear
        # nada. Sigue siendo scrolleable (hadjustment real, flechas,
        # rueda del mouse) -- la barra en sí se esconde por CSS
        # (scrollbar { opacity: 0 } más abajo), no quitando la política.
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_overlay_scrolling(True)
        scroller.add(flow)
        scroller.connect("scroll-event", self._on_strip_scroll)

        left_btn = Gtk.Button()
        left_btn.set_image(Gtk.Image.new_from_icon_name("pan-start-symbolic", Gtk.IconSize.BUTTON))
        left_btn.get_image().set_pixel_size(14)
        left_btn.get_style_context().add_class("strip-arrow-btn")
        left_btn.connect("clicked", lambda *_: self._on_strip_arrow_clicked(scroller, -1))

        right_btn = Gtk.Button()
        right_btn.set_image(Gtk.Image.new_from_icon_name("pan-end-symbolic", Gtk.IconSize.BUTTON))
        right_btn.get_image().set_pixel_size(14)
        right_btn.get_style_context().add_class("strip-arrow-btn")
        right_btn.connect("clicked", lambda *_: self._on_strip_arrow_clicked(scroller, 1))

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.set_name(f"{name}-row")
        row.pack_start(left_btn, False, False, 0)
        row.pack_start(scroller, True, True, 0)
        row.pack_start(right_btn, False, False, 0)
        return row, scroller, flow

    def _on_strip_arrow_clicked(self, scroller, direction):
        adj = scroller.get_hadjustment()
        step = adj.get_page_size() * 0.6 or 80
        at_end = direction > 0 and adj.get_value() >= adj.get_upper() - adj.get_page_size() - 1
        new_value = adj.get_value() + direction * step
        adj.set_value(min(max(new_value, adj.get_lower()), adj.get_upper() - adj.get_page_size()))
        if scroller is self.catalog_scroller and at_end:
            bandcamp_ipc.send_command("load_more_catalog")

    def _on_strip_scroll(self, scroller, event):
        """genres_scroller/subgenres_scroller: rueda del mouse (vertical,
        arriba/abajo) mueve la tira horizontal -- son de una sola fila
        (policy vertical NEVER), así que el scroll vertical normal no
        tiene a dónde ir si no se redirige a mano. True detiene la
        propagación del evento (evita el manejo default de ScrolledWindow,
        que sin esto puede tratar de scrollear verticalmente y no hacer
        nada visible)."""
        adj = scroller.get_hadjustment()
        step = adj.get_step_increment() * 3
        handled, _dx, dy = event.get_scroll_deltas()
        if not handled:
            dy = -1 if event.direction == Gdk.ScrollDirection.UP else 1
        adj.set_value(min(max(adj.get_value() + dy * step, adj.get_lower()), adj.get_upper() - adj.get_page_size()))
        return True

    def _ensure_tag_mode(self):
        """Clic en un tag/género/subgénero siempre busca por TAG -- si el
        combo estaba en Artista/Wishlist, lo vuelve a Nuevo antes de
        buscar (pedido explícito del usuario: con Artista activo, un tag
        se buscaba como si fuera nombre de banda y no encontraba nada).
        Top/Sorpréndeme no se tocan, siguen siendo un slice válido para
        tags."""
        if self.slice_combo.get_active_id() in ("artist", "wishlist"):
            self.slice_combo.set_active_id("new")

    def _on_tag_search_clicked(self, slug):
        self._ensure_tag_mode()
        self.tag_entry.set_text(slug)
        self._on_search_clicked()

    def _on_tag_add_clicked(self, slug):
        self._ensure_tag_mode()
        current = _split_tags(self.tag_entry.get_text())
        if slug.lower() not in (t.lower() for t in current):
            current.append(slug)
        self.tag_entry.set_text("; ".join(current))
        self._on_search_clicked()

    def _build_genre_chip(self, slug, label):
        btn = Gtk.Button(label=label)
        btn.get_style_context().add_class("genre-btn")
        btn.connect("clicked", lambda *_: self._on_genre_clicked(slug))
        return btn

    def _on_genre_clicked(self, slug):
        self._ensure_tag_mode()
        if self._active_genre and self._active_genre in self._genre_buttons:
            self._genre_buttons[self._active_genre].get_style_context().remove_class("active")
        self._active_genre = slug
        self._genre_buttons[slug].get_style_context().add_class("active")

        self.tag_entry.set_text(slug)
        self._on_search_clicked()
        self._rebuild_subgenres(slug)

    def _rebuild_subgenres(self, genre_slug):
        for child in self.subgenres_flow.get_children():
            self.subgenres_flow.remove(child)
        self._active_subgenre = None
        self._subgenre_buttons = {}
        subs = SUBGENRES.get(genre_slug, [])
        for sub in subs:
            btn = Gtk.Button(label=sub["label"])
            btn.get_style_context().add_class("subgenre-btn")
            btn.connect("clicked", lambda *_, s=sub["slug"]: self._on_subgenre_clicked(genre_slug, s))
            self._subgenre_buttons[sub["slug"]] = btn
            self.subgenres_flow.pack_start(btn, False, False, 0)
        self.subgenres_flow.show_all()
        if subs:
            self.subgenres_row.set_no_show_all(False)
            self.subgenres_row.show_all()
            GLib.idle_add(lambda: self.subgenres_scroller.get_hadjustment().set_value(0))
        else:
            self.subgenres_row.hide()

    def _on_subgenre_clicked(self, genre_slug, sub_slug):
        self._ensure_tag_mode()
        if self._active_subgenre and self._active_subgenre in self._subgenre_buttons:
            self._subgenre_buttons[self._active_subgenre].get_style_context().remove_class("active")
        self._active_subgenre = sub_slug
        self._subgenre_buttons[sub_slug].get_style_context().add_class("active")

        self.tag_entry.set_text(f"{genre_slug}; {sub_slug}")
        self._on_search_clicked()

    def _build_tag_chip(self, tag):
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        chip.get_style_context().add_class("tag-chip")

        label_btn = Gtk.Button(label=tag["label"])
        label_btn.get_style_context().add_class("tag-label-btn")
        label_btn.connect("clicked", lambda *_: self._on_tag_search_clicked(tag["slug"]))
        chip.pack_start(label_btn, False, False, 0)

        add_btn = Gtk.Button(label="+")
        add_btn.get_style_context().add_class("tag-add-btn")
        add_btn.connect("clicked", lambda *_: self._on_tag_add_clicked(tag["slug"]))
        chip.pack_start(add_btn, False, False, 0)

        return chip

    def _rebuild_tags(self, tags):
        for child in self.tags_flow.get_children():
            self.tags_flow.remove(child)
        for tag in tags:
            self.tags_flow.pack_start(self._build_tag_chip(tag), False, False, 0)
        self.tags_flow.show_all()
        if tags:
            self.tags_section.set_no_show_all(False)
            self.tags_section.show_all()
            if not self.tags_header.expanded:
                self.tags_content.hide()
            GLib.idle_add(lambda: self.tags_scroller.get_hadjustment().set_value(0))
        else:
            self.tags_section.hide()

    # ---- Sondeo del daemon ----

    def _poll_state(self):
        self._apply_state(bandcamp_ipc.get_state())
        return True  # GLib.timeout_add: seguir repitiendo

    def _apply_state(self, state):
        if state is None:
            return

        # Catálogo elegible (pestaña "Buscar") -- status_label acá refleja
        # el estado del catálogo. pending_catalog cambia de forma
        # (discover/wishlist devuelven una lista nueva completa en cada
        # fetch; artist crece de a uno, ver daemon.state()) -- se
        # reconstruye solo si la lista de item_url realmente cambió,
        # mismo criterio "diff antes de reconstruir" que tags/
        # recomendaciones/álbumes de artista.
        pending_label = state.get("pending_label")
        pending_catalog = state.get("pending_catalog") or []
        if state.get("pending_loading"):
            self.status_label.set_text(
                t("bandcamp", "status_searching_in", label=pending_label) if pending_label
                else t("bandcamp", "status_searching"))
        elif state.get("pending_error"):
            self.status_label.set_text(state["pending_error"])
        elif pending_catalog:
            self.status_label.set_text("")
        else:
            self.status_label.set_text(t("bandcamp", "status_choose_tag"))

        catalog_key = json.dumps([c.get("item_url") for c in pending_catalog])
        if catalog_key != self._last_pending_catalog_key:
            self._last_pending_catalog_key = catalog_key
            self._rebuild_catalog(pending_catalog)

        # search_wishlist responde {"ok": True} de inmediato (bandcamp_daemon.py
        # solo agenda el fetch real en un hilo, ver cmd_search_wishlist/
        # _fetch_pending_catalog) -- no hay forma de saber si encontró
        # algo desde esa respuesta sola. Se decide acá, mirando el
        # próximo estado real: catálogo no vacío = encontró algo = éxito,
        # se guarda en el histórico; pending_error = sin resultados, se
        # descarta sin guardar. Mientras pending_loading siga en True
        # todavía no se sabe, se espera al próximo tick.
        if self._pending_wishlist_username and not state.get("pending_loading"):
            if pending_catalog:
                self._remember_wishlist_username(self._pending_wishlist_username)
            self._pending_wishlist_username = None

        self.prev_btn.set_sensitive(bool(state.get("can_prev")))
        # handler_block: sync desde el daemon (puede haberse apagado
        # solo desde la pestaña "Sonido" del dashboard, ver
        # sound_module.py) sin volver a mandarle el comando de vuelta.
        self.autoeq_btn.handler_block(self._autoeq_handler_id)
        self.autoeq_btn.set_active(bool(state.get("auto_eq")))
        self.autoeq_btn.handler_unblock(self._autoeq_handler_id)
        self.play_pause_icon.set_from_icon_name(
            "media-playback-start-symbolic" if state.get("paused") else "media-playback-pause-symbolic",
            Gtk.IconSize.BUTTON,
        )
        self.play_pause_icon.set_pixel_size(PLAY_PAUSE_ICON_SIZE)

        # Atada a la BÚSQUEDA (modo Artista), no a la canción puntual --
        # a diferencia de tags/recomendaciones, tiene sentido mostrarla
        # aunque todavía no haya cargado ninguna pista (fetch_artist_albums
        # corre en paralelo a la primera pista, no después).
        albums_key = json.dumps(state.get("artist_albums") or [])
        if albums_key != self._last_artist_albums_key:
            self._last_artist_albums_key = albums_key
            self._rebuild_artist_albums(state.get("artist_albums") or [])

        title = state.get("title")
        if not title:
            self._title_marquee.update("—")
            self._artist_marquee.update("")
            self._album_marquee.update(NO_TRACK_LABEL)
            self.buy_btn.set_sensitive(False)
            self.artist_label.set_sensitive(False)
            self.album_label.set_sensitive(False)
            self._current_artist = None
            self._current_album_title = None
            self._item_url = None
            self._update_favorite_buttons()
            self._sync_local_tracks({})
            if self._last_art_url is not None:
                self._last_art_url = None
                self._set_placeholder_art()
            # Controles sin nada que controlar -- pedido explícito del
            # usuario: sin esto quedaban clickeables (play/pause, saltar,
            # autoecualizador, la barra de progreso) sin ningún efecto
            # real mientras no hay pista cargada.
            self.play_pause_btn.set_sensitive(False)
            self.skip_btn.set_sensitive(False)
            self.autoeq_btn.set_sensitive(False)
            self.progress_scale.set_sensitive(False)
            if not self._seeking:
                self._duration = 0
                self._position = 0
                self.progress_scale.set_range(0, 1)
                self.progress_scale.set_value(0)
                self._update_time_label()
            return

        self.play_pause_btn.set_sensitive(True)
        self.skip_btn.set_sensitive(True)
        self.autoeq_btn.set_sensitive(True)
        self.progress_scale.set_sensitive(True)

        self._title_marquee.update(title)
        self._artist_marquee.update(state.get("artist") or "")
        self._album_marquee.update(state.get("album_title") or "")
        self._is_local = bool(state.get("local"))
        self._sync_local_tracks(state)
        self.buy_btn.set_sensitive(not self._is_local)
        self.artist_label.set_sensitive(bool(state.get("artist")) and not self._is_local)
        self.album_label.set_sensitive(not self._is_local)
        self._current_artist = state.get("artist") or None
        self._current_album_title = state.get("album_title") or None
        self._item_url = state.get("item_url")
        self._update_favorite_buttons()

        # Antes era el label del botón "Reproducir álbum"/"Volver a
        # «tag»" que se eliminó -- ahora es el tooltip de album_label,
        # que sigue siendo lo que se clickea para esa misma acción.
        if state.get("mode") == "album" and state.get("can_resume_discover"):
            self.album_label.set_tooltip_text(
                t("bandcamp", "tooltip_resume_search", label=state.get("search_label")))
        else:
            self.album_label.set_tooltip_text(t("bandcamp", "tooltip_play_album"))
        self.artist_label.set_tooltip_text(t("bandcamp", "tooltip_view_discography"))

        art_url = state.get("art_url")
        if art_url != self._last_art_url:
            self._last_art_url = art_url
            self._load_art(art_url)

        tags_key = json.dumps(state.get("tags") or [])
        if tags_key != self._last_tags_key:
            self._last_tags_key = tags_key
            self._rebuild_tags(state.get("tags") or [])

        recs_key = json.dumps(state.get("recommendations") or [])
        if recs_key != self._last_recs_key:
            self._last_recs_key = recs_key
            self._rebuild_recommendations(state.get("recommendations") or [])

        self._duration = state.get("duration") or 0
        self._position = state.get("position") or 0
        if not self._seeking:
            self.progress_scale.set_range(0, self._duration or 1)
            self.progress_scale.set_value(self._position)
        self._update_time_label()

    def _set_placeholder_art(self):
        """Carátula por default (nada cargado/reproduciendo todavía) --
        ícono simbólico monocromo (ver PLACEHOLDER_ART_ICON, coloreado por
        CSS vía #art-image) en vez del "audio-x-generic" a color de
        Papirus que traía antes, pedido explícito del usuario: no iba
        acorde a la línea minimalista del resto del popup (flechas/
        ecualizador/estrellas, todos símbolos de un solo color). pixel_size
        explícito -- Gtk.IconSize.DIALOG por sí solo renderiza chico
        (~48px) dentro del cuadro de ART_SIZE (180px), dejando un ícono
        perdido en una caja grande vacía."""
        self.art_image.set_from_icon_name(PLACEHOLDER_ART_ICON, Gtk.IconSize.DIALOG)
        self.art_image.set_pixel_size(PLACEHOLDER_ART_ICON_SIZE)

    def _load_art(self, url):
        self._set_placeholder_art()
        if not url:
            return
        generation = self._last_art_url  # el URL mismo alcanza como token de generación

        def worker():
            try:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(_cached_art_bytes(url))
                loader.close()
                pixbuf = loader.get_pixbuf().scale_simple(
                    ART_SIZE, ART_SIZE, GdkPixbuf.InterpType.BILINEAR)
                pixbuf = _round_corners(pixbuf, _current_radius())
            except Exception:
                return
            GLib.idle_add(self._apply_art, generation, pixbuf)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_art(self, generation, pixbuf):
        if generation == self._last_art_url:
            self.art_image.set_from_pixbuf(pixbuf)
        return False

    # ---- Progreso / seek ----

    def _on_seek_start(self, *_):
        self._seeking = True

    def _on_seek_end(self, *_):
        self._seeking = False
        bandcamp_ipc.send_command("seek", position=self.progress_scale.get_value())

    def _update_time_label(self):
        self.elapsed_label.set_text(_fmt_time(self._position))
        self.total_label.set_text(_fmt_time(self._duration))

    # ---- Persistencia mínima (último tag buscado) ----

    def _save_last_tag(self, tag):
        tmp = LAST_TAG_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump({"tag": tag}, f)
            os.replace(tmp, LAST_TAG_FILE)
        except OSError:
            pass

    def _load_last_tag(self):
        try:
            with open(LAST_TAG_FILE) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        tag = data.get("tag")
        if not tag:
            return
        # "u:"/"b:" siguen siendo el formato interno de last-tag.json
        # (nunca lo escribe el usuario a mano, ver _on_search_clicked) --
        # decide con qué modo del combo y qué texto reabrir el popup.
        wishlist_match = WISHLIST_RE.match(tag)
        artist_match = ARTIST_RE.match(tag)
        local_match = LOCAL_RE.match(tag)
        if local_match:
            self.slice_combo.set_active_id("local")
            self.tag_entry.set_text(local_match.group(1))
        elif wishlist_match:
            self.slice_combo.set_active_id("wishlist")
            self.tag_entry.set_text(wishlist_match.group(1))
        elif artist_match:
            self.slice_combo.set_active_id("artist")
            self.tag_entry.set_text(artist_match.group(1))
        else:
            self.tag_entry.set_text(tag)

    def _restore_genre_selection(self, text):
        """Re-marca género/subgénero como activos al reabrir el popup, sin
        volver a buscar -- _on_genre_clicked/_on_subgenre_clicked SÍ
        buscan, y llamarlos acá reiniciaría lo que el daemon ya está
        reproduciendo solo por haber cerrado y vuelto a abrir la
        ventana. No-op silencioso si el texto guardado no arranca con un
        slug de género conocido (wishlist "u:", discografía "b:"/"a:",
        o un tag libre cualquiera)."""
        tokens = _split_tags(text)
        if not tokens or tokens[0] not in self._genre_buttons:
            return
        genre_slug = tokens[0]
        self._active_genre = genre_slug
        self._genre_buttons[genre_slug].get_style_context().add_class("active")
        self._rebuild_subgenres(genre_slug)
        if len(tokens) > 1 and tokens[1] in self._subgenre_buttons:
            sub_slug = tokens[1]
            self._active_subgenre = sub_slug
            self._subgenre_buttons[sub_slug].get_style_context().add_class("active")

    # ---- Cierre ----

    def _on_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        # A propósito: NO se toca bandcamp_daemon.py acá -- cerrar el popup nunca
        # corta la música (ver docstring del módulo).
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    _render_frog_marker()  # antes de build_layer_window: CSS ya debe encontrar el PNG a color
    Popup()
    Gtk.main()


if __name__ == "__main__":
    main()

"""Wrapper de `claude -p` para voice_assistant.py -- texto transcrito
por stt.py -> respuesta de Claude. Ver CLAUDE.md "Asistente de voz"
para el diseño completo (modelo, permisos, secciones Ubicaciones/
Música/Calendario, filtro de Sources, voseo)."""

import datetime
import re
import subprocess
import urllib.parse

import assistant_config

MODEL = "claude-haiku-4-5-20251001"
TIMEOUT_S = 60

# Red de seguridad determinística -- ver CLAUDE.md "Filtro de Sources".
_SOURCES_RE = re.compile(r"\n+(sources|fuentes):.*", re.IGNORECASE | re.DOTALL)


def _strip_sources(text):
    return _SOURCES_RE.sub("", text).strip()


def _section_re(section_name):
    return re.compile(rf"\n*{re.escape(section_name)}:\s*\n(.*)", re.IGNORECASE | re.DOTALL)


def _render_links(text, section_name, url_builder):
    match = _section_re(section_name).search(text)
    if not match:
        return text
    before = text[:match.start()].rstrip()
    lines = [line.strip() for line in match.group(1).splitlines() if line.strip()]
    if not lines:
        return before
    rendered = "\n".join(f"{line}: {url_builder(line)}" for line in lines)
    return f"{before}\n\n{section_name}:\n{rendered}"


_LABELED_URL_RE = re.compile(r"^(.*?):\s*(https://\S+)$")


def _extract_link_section(text, section_name):
    """(cuerpo_sin_esa_sección, [(etiqueta, url), ...])."""
    match = _section_re(section_name).search(text)
    if not match:
        return text, []
    body = text[:match.start()].rstrip()
    pairs = []
    for line in match.group(1).splitlines():
        line_match = _LABELED_URL_RE.match(line.strip())
        if line_match:
            pairs.append((line_match.group(1).strip(), line_match.group(2).strip()))
    return body, pairs


def _maps_url(query):
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote_plus(query)


def _render_locations(text):
    return _render_links(text, "Ubicaciones", _maps_url)


_UBICACIONES_STRIP_RE = re.compile(r"\n+ubicaciones:.*", re.IGNORECASE | re.DOTALL)


def strip_locations(text):
    return _UBICACIONES_STRIP_RE.sub("", text).strip()


def extract_locations(text):
    """(cuerpo_sin_ubicaciones, [(nombre, url), ...])."""
    return _extract_link_section(text, "Ubicaciones")


_YOUTUBE_URL_RE = re.compile(
    r"^https://(www\.)?(youtube\.com/(watch\?v=|playlist\?list=)|youtu\.be/)", re.IGNORECASE
)


def _youtube_search_url(query):
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)


def _validate_music_url(label, url):
    return url if _YOUTUBE_URL_RE.match(url) else _youtube_search_url(label)


_MUSICA_STRIP_RE = re.compile(r"\n+música:.*", re.IGNORECASE | re.DOTALL)


def strip_music(text):
    return _MUSICA_STRIP_RE.sub("", text).strip()


def extract_music(text):
    """(cuerpo_sin_música, [(etiqueta, url_validada), ...])."""
    body, pairs = _extract_link_section(text, "Música")
    validated = [(label, _validate_music_url(label, url)) for label, url in pairs]
    return body, validated


MARKER = "Te describo la solución a continuación:"

CALENDAR_MARKER = "CALENDARIO:"
_CALENDAR_DATE_RE = re.compile(r"fecha:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_CALENDAR_EVENT_RE = re.compile(r"evento:\s*(.+)", re.IGNORECASE)

CALENDAR_QUERY_MARKER = "CONSULTA_CALENDARIO:"

BASE_SYSTEM_PROMPT = f"""Eres un asistente de voz. Responde la pregunta del usuario en español mexicano, conjugando SIEMPRE en "tú" -- por ejemplo "encuentras", "puedes", "tienes", "quieres", NUNCA "encontrás", "podés", "tenés", "querés" (voseo rioplatense, prohibido).

Por default sé MUY breve: 1-2 oraciones cortas, directo a la respuesta, sin relleno ni explicaciones de más. Solo sé extenso y detallado cuando la pregunta de verdad lo necesita (instrucciones técnicas paso a paso, código, o el usuario pide explícitamente más detalle/explicación).

Si la pregunta es TÉCNICA (código, comandos, configuración de sistema, programación):
1. Empieza tu respuesta EXACTAMENTE con: "{MARKER}"
2. Después da la respuesta completa y detallada (código, pasos, lo necesario) -- aquí sí aplica el detalle completo, no la brevedad de arriba.

Si NO es técnica (charla, clima, noticias, cultura general, recomendaciones, etc.):
Responde en 1-2 oraciones cortas, apta para leerse en voz alta, sin markdown y sin la frase técnica. Nada de rodeos tipo "la forma más fácil es..." ni explicar cómo buscar la info -- da la respuesta y ya.

Si la pregunta necesita información actual (clima, noticias, fechas de lanzamiento, precios), busca en internet antes de responder.

Si recomiendas sitios, tiendas, restaurantes u otros establecimientos físicos, agrega al final una sección nueva que empiece EXACTAMENTE con la línea "Ubicaciones:", y debajo una línea por lugar con el formato "Nombre del lugar, ciudad o zona" -- SOLO ese texto, nunca armes tú mismo una URL ni la escribas en el cuerpo de la respuesta. Ejemplo:
Ubicaciones:
Tacos El Güero, Guadalajara
Taquería La Central, Zapopan

Si el usuario pide música (una canción, un álbum, o una playlist -- ej. "pon Bohemian Rhapsody", "busca el álbum de Dark Side of the Moon", "ponme una playlist de rock"): usa WebSearch para encontrar el video/álbum/playlist REAL en youtube.com (nunca inventes una URL -- si de verdad no encuentras nada, no incluyas la sección). PRIMERO una oración corta confirmando qué encontraste (ej. "Aquí tienes esa canción.", nunca la dejes vacía), y DESPUÉS agrega una sección nueva que empiece EXACTAMENTE con la línea "Música:", y debajo una línea por cada resultado con el formato "Tipo: Título - Artista: URL exacta que encontraste en youtube.com" -- Tipo es EXACTAMENTE una de estas tres palabras: "Canción", "Álbum" o "Playlist" (según lo que haya pedido el usuario), la URL debe ser del video/playlist específico (youtube.com/watch?v=... o youtube.com/playlist?list=...), nunca una página de resultados de búsqueda. Ejemplo, si pidió una canción de Queen:
Aquí tienes esa canción.

Música:
Canción: Bohemian Rhapsody - Queen: https://www.youtube.com/watch?v=fJ9rUzIMcZQ

Si el usuario pide agregar, anotar o guardar algo en el calendario (ej. "agrega un concierto de X el 20 de agosto", "anótame que tengo cita el viernes"), tu ÚNICA respuesta debe ser EXACTAMENTE este formato, sin nada antes ni después, sin la frase técnica ni ningún otro texto:
{CALENDAR_MARKER}
Fecha: AAAA-MM-DD
Evento: <descripción breve del evento, sin la fecha adentro del texto>

Si el usuario PREGUNTA qué tiene pendiente/agendado/anotado en una fecha (ej. "¿qué tengo el viernes?", "¿tengo algo pendiente el 20 de agosto?", "qué hay en mi calendario mañana") -- esto es diferente de agregar, es solo consultar -- tu ÚNICA respuesta debe ser EXACTAMENTE este formato, sin nada más:
{CALENDAR_QUERY_MARKER}
Fecha: AAAA-MM-DD

Tu respuesta se lee en voz alta tal cual, palabra por palabra -- nunca incluyas enlaces ni URLs en el cuerpo de la respuesta, ni una lista de fuentes al final. Las secciones "Ubicaciones:" y "Música:" son la única excepción: en "Ubicaciones:" tampoco escribas URLs, solo nombre y ciudad/zona; en "Música:" SÍ escribe la URL real que encontraste con WebSearch (ver arriba), nunca inventada."""


_WEEKDAY_NAMES_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _build_system_prompt():
    # Se arma en cada llamada (no una vez al importar) para que una
    # preferencia guardada a mitad de sesión aplique ya. Día de la
    # semana explícito -- ver CLAUDE.md "Fechas relativas".
    today_date = datetime.date.today()
    today = today_date.isoformat()
    today_weekday = _WEEKDAY_NAMES_ES[today_date.weekday()]
    parts = [
        BASE_SYSTEM_PROMPT,
        f'Hoy es {today_weekday} {today} (formato AAAA-MM-DD) -- úsalo para resolver fechas relativas ("mañana", "el viernes", "el 20 de agosto") en pedidos de calendario; si el usuario no da año, calcula el que corresponda (el actual si esa fecha no pasó todavía este año, si no el que sigue).',
    ]
    context = assistant_config.as_prompt_context()
    if context:
        parts.append(context)
    return "\n\n".join(parts)


def ask(question):
    """None si falla (timeout, claude ausente) -- nunca excepciona,
    voice_assistant.py decide qué mostrar en ese caso."""
    try:
        result = subprocess.run(
            [
                "claude", "-p", question,
                "--model", MODEL,
                "--system-prompt", _build_system_prompt(),
                "--tools", "WebSearch",
                "--permission-mode", "bypassPermissions",
                "--output-format", "text",
            ],
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
        text = _strip_sources(result.stdout.strip())
        text = _render_locations(text)
        return text or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def is_technical(answer):
    return answer.startswith(MARKER)


def strip_marker(answer):
    if is_technical(answer):
        return answer[len(MARKER):].strip()
    return answer


def is_calendar_action(answer):
    return answer.strip().startswith(CALENDAR_MARKER)


def parse_calendar_action(answer):
    """(fecha, texto_evento) o None si el bloque no se pudo parsear
    (formato de fecha raro, falta alguno de los dos campos) -- nunca
    excepciona, voice_assistant.py decide qué mostrar en ese caso."""
    date_match = _CALENDAR_DATE_RE.search(answer)
    event_match = _CALENDAR_EVENT_RE.search(answer)
    if not date_match or not event_match:
        return None
    try:
        event_date = datetime.date.fromisoformat(date_match.group(1))
    except ValueError:
        return None
    event_text = event_match.group(1).strip()
    if not event_text:
        return None
    return event_date, event_text


def is_calendar_query(answer):
    return answer.strip().startswith(CALENDAR_QUERY_MARKER)


def parse_calendar_query(answer):
    """Fecha consultada, o None si no se pudo parsear -- nunca
    excepciona, voice_assistant.py decide qué mostrar en ese caso."""
    date_match = _CALENDAR_DATE_RE.search(answer)
    if not date_match:
        return None
    try:
        return datetime.date.fromisoformat(date_match.group(1))
    except ValueError:
        return None

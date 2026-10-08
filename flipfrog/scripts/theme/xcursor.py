"""Lectura de cursores Xcursor (los archivos de <tema>/cursors/) para las
vistas previas del selector de cursores. Sin GTK.

Formato: cabecera "Xcur" + tabla de contenido (tipo, tamaño nominal,
posición); cada imagen es ARGB premultiplicado little-endian, el mismo
orden de bytes que cairo.FORMAT_ARGB32 en x86 -- se pasa tal cual."""

import configparser
import os
import struct

import look_settings

IMAGE_TYPE = 0xFFFD0002
MAX_INHERIT_DEPTH = 4

# Un nombre por rol con sus alias: los temas viejos usan los nombres X11
# (left_ptr, hand2...), los nuevos los de CSS (default, pointer...).
PREVIEW_ROLES = (
    ("default", "left_ptr", "arrow"),
    ("pointer", "hand2", "hand1", "pointing_hand"),
    ("text", "xterm", "ibeam"),
    ("wait", "watch"),
    ("progress", "left_ptr_watch", "half-busy"),
    ("crosshair", "cross", "tcross"),
    ("move", "fleur", "all-scroll"),
    ("not-allowed", "crossed_circle", "forbidden"),
)


def theme_dirs(theme, bases=None):
    """Todas las carpetas del tema: libXcursor busca en cada una (aquí
    "default" está en ~/.icons sin Inherits y en /usr/share/icons con
    Inherits=Adwaita)."""
    paths = (os.path.join(base, theme) for base in bases or look_settings.CURSOR_DIRS)
    return [path for path in paths if os.path.isdir(path)]


def _inherits(path):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read(os.path.join(path, "index.theme"))
        value = parser.get("Icon Theme", "Inherits", fallback="")
    except configparser.Error:
        return []
    return [name.strip() for name in value.split(",") if name.strip()]


def find_cursor(theme, names, bases=None, _depth=0):
    """Ruta del primer nombre de `names` que exista en el tema o en los
    que hereda (index.theme, Inherits=)."""
    if _depth > MAX_INHERIT_DEPTH:
        return None
    paths = theme_dirs(theme, bases)
    for path in paths:
        for name in names:
            candidate = os.path.join(path, "cursors", name)
            if os.path.isfile(candidate):
                return candidate
    for path in paths:
        for parent in _inherits(path):
            found = find_cursor(parent, names, bases, _depth + 1)
            if found:
                return found
    return None


def resolved_theme(theme, bases=None):
    """Nombre del tema que realmente pone los cursores ("default" →
    "Adwaita"), o None si no hay ninguno."""
    found = find_cursor(theme, PREVIEW_ROLES[0], bases)
    return os.path.basename(os.path.dirname(os.path.dirname(found))) if found else None


def read_image(path, size):
    """(ancho, alto, xhot, yhot, bytes ARGB) de la imagen con el tamaño
    nominal más cercano a `size`; primer cuadro si es animado."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"Xcur":
        return None
    _header, _version, ntoc = struct.unpack_from("<III", data, 4)
    best = None
    for i in range(ntoc):
        chunk_type, nominal, position = struct.unpack_from("<III", data, 16 + i * 12)
        if chunk_type != IMAGE_TYPE:
            continue
        if best is None or abs(nominal - size) < abs(best[0] - size):
            best = (nominal, position)
    if best is None:
        return None
    position = best[1]
    width, height, xhot, yhot = struct.unpack_from("<IIII", data, position + 16)
    start = position + 36
    pixels = data[start:start + width * height * 4]
    if len(pixels) != width * height * 4:
        return None
    return width, height, xhot, yhot, pixels


def preview_images(theme, size, bases=None):
    """Una imagen por rol de PREVIEW_ROLES que el tema tenga. `bases`:
    carpetas donde buscar en vez de las del sistema (paquetes sin
    instalar, ver repo_themes.py)."""
    images = []
    for names in PREVIEW_ROLES:
        path = find_cursor(theme, names, bases)
        if path is None:
            continue
        try:
            image = read_image(path, size)
        except (OSError, struct.error):
            image = None
        if image:
            images.append(image)
    return images

"""Búsqueda de íconos dentro de un tema (index.theme + Directories) para
las vistas previas del selector de íconos, sin Gtk.IconTheme: corre en
hilos y sirve igual para temas sin instalar (carpetas en caché). Sin GTK.

Sigue Inherits= salvo hicolor: ahí viven los íconos que cada app instala,
la vista previa mostraría íconos que no son del tema."""

import configparser
import os

ICON_DIRS = (
    os.path.expanduser("~/.local/share/icons"),
    os.path.expanduser("~/.icons"),
    "/usr/share/icons",
)
EXTENSIONS = (".svg", ".png")
MAX_INHERIT_DEPTH = 3
SKIP_INHERIT = {"hicolor"}

PREVIEW_ROLES = (
    ("folder", "inode-directory"),
    ("user-home", "folder-home"),
    ("user-trash", "user-trash-full"),
    ("text-x-generic",),
    ("image-x-generic",),
    ("audio-x-generic",),
    ("video-x-generic",),
    ("utilities-terminal", "org.gnome.Terminal", "terminal"),
    ("web-browser", "internet-web-browser", "firefox"),
    ("system-file-manager", "org.gnome.Nautilus", "file-manager"),
)

_index_cache = {}


def _index(path):
    """(inherits, [(subcarpeta, distancia a un tamaño)]) de index.theme."""
    if path in _index_cache:
        return _index_cache[path]
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read(os.path.join(path, "index.theme"))
    except configparser.Error:
        parser = configparser.ConfigParser()
    section = "Icon Theme"
    inherits = [n.strip() for n in parser.get(section, "Inherits", fallback="").split(",") if n.strip()]
    dirs = []
    for key in ("Directories", "ScaledDirectories"):
        for sub in parser.get(section, key, fallback="").split(","):
            sub = sub.strip()
            if sub and parser.has_section(sub):
                info = parser[sub]
                dirs.append((sub, info.get("Type", "Threshold"), _int(info.get("Size")),
                             _int(info.get("MinSize")), _int(info.get("MaxSize"))))
    _index_cache[path] = (inherits, dirs)
    return _index_cache[path]


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _distance(entry, size):
    _sub, kind, nominal, min_size, max_size = entry
    if kind == "Scalable":
        low, high = min_size or nominal or 1, max_size or nominal or 512
        return 0 if low <= size <= high else min(abs(size - low), abs(size - high))
    return abs((nominal or 0) - size)


def theme_dirs(theme, bases=None):
    paths = (os.path.join(base, theme) for base in bases or ICON_DIRS)
    return [path for path in paths if os.path.isfile(os.path.join(path, "index.theme"))]


def find_icon(theme, names, size, bases=None, _depth=0):
    if _depth > MAX_INHERIT_DEPTH:
        return None
    paths = theme_dirs(theme, bases)
    for path in paths:
        _inherits, dirs = _index(path)
        ordered = sorted(dirs, key=lambda entry: _distance(entry, size))
        for name in names:
            for sub, *_rest in ordered:
                for ext in EXTENSIONS:
                    candidate = os.path.join(path, sub, name + ext)
                    if os.path.isfile(candidate):
                        return candidate
    for path in paths:
        for parent in _index(path)[0]:
            if parent not in SKIP_INHERIT:
                found = find_icon(parent, names, size, bases, _depth + 1)
                if found:
                    return found
    return None


def preview_files(theme, size, bases=None):
    """Un archivo por rol de PREVIEW_ROLES que el tema (o los que hereda)
    tenga."""
    files = []
    for names in PREVIEW_ROLES:
        found = find_icon(theme, names, size, bases)
        if found:
            files.append(found)
    return files

"""Temas de la pantalla de inicio de sesión (SDDM) para login_picker.py.
Sin GTK.

SDDM corre con su propio usuario y solo busca temas en THEMES_DIR, así que
instalar, quitar y elegir el tema pasan por pkexec (contraseña por el
agente de polkit). Los de la KDE Store se copian con un marcador
(kde_store.MARKER) para poder quitarlos después sin tocar los del sistema."""

import configparser
import glob
import os
import re
import shutil
import subprocess

THEMES_DIR = "/usr/share/sddm/themes"
CONF_FILE = "/etc/sddm.conf.d/10-flipfrog-theme.conf"
# Orden en que SDDM los lee: el último que diga Current= gana.
CONF_GLOBS = ("/usr/lib/sddm/sddm.conf.d/*.conf", "/etc/sddm.conf.d/*.conf")
MAIN_CONF = "/etc/sddm.conf"
MARKER = ".flipfrog-store"
QML_DIRS = {"5": "/usr/lib/qt/qml", "6": "/usr/lib/qt6/qml"}
GREETERS = {"5": "sddm-greeter", "6": "sddm-greeter-qt6"}
SAFE_NAME_RE = re.compile(r"^[\w.+-]+$")
IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z][\w.]*)(?:\s+(\d+))?", re.M)
AUTH_TIMEOUT = 120
TEST_TIMEOUT = 600


def available():
    return os.path.isdir(THEMES_DIR)


def _parser(*paths):
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read(paths)
    except configparser.Error:
        pass
    return parser


def _conf_files():
    files = [path for pattern in CONF_GLOBS for path in sorted(glob.glob(pattern))]
    return files + [MAIN_CONF]


def current():
    """(tema, archivo que lo elige). Tema vacío = el integrado de SDDM."""
    theme, source = "", None
    for path in _conf_files():
        value = _parser(path).get("Theme", "Current", fallback=None)
        if value is not None:
            theme, source = value.strip(), path
    return theme, source


def theme_dir(theme):
    return os.path.join(THEMES_DIR, theme)


def installed():
    try:
        names = os.listdir(THEMES_DIR)
    except OSError:
        return []
    return sorted((n for n in names if os.path.isfile(os.path.join(THEMES_DIR, n, "metadata.desktop"))),
                  key=str.lower)


def metadata(path):
    """Claves en minúsculas (configparser)."""
    parser = _parser(os.path.join(path, "metadata.desktop"))
    return dict(parser.items("SddmGreeterTheme")) if parser.has_section("SddmGreeterTheme") else {}


def qt_version(path):
    return "6" if metadata(path).get("qtversion", "").strip() == "6" else "5"


def preview_path(path):
    """Captura del autor (Screenshot=) o, sin ella, el fondo del tema."""
    meta = metadata(path)
    candidates = [meta.get("screenshot")]
    config = meta.get("configfile", "theme.conf")
    general = _parser(os.path.join(path, config), os.path.join(path, config + ".user"))
    candidates.append(general.get("General", "background", fallback=None))
    for candidate in candidates:
        if candidate:
            full = os.path.join(path, candidate.strip())
            if os.path.isfile(full):
                return full
    return None


def missing_modules(path):
    """Módulos QML que importa el tema y no están instalados para su
    versión de Qt (los temas de Plasma piden org.kde.*): sin ellos SDDM
    muestra una pantalla de error."""
    qml_dir = QML_DIRS[qt_version(path)]
    missing = set()
    for qml in glob.glob(os.path.join(path, "**", "*.qml"), recursive=True):
        try:
            with open(qml, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for module, major in IMPORT_RE.findall(text):
            base = os.path.join(qml_dir, *module.split("."))
            if not (os.path.isdir(base) or (major and os.path.isdir(f"{base}.{major}"))):
                missing.add(module)
    return sorted(missing)


def installed_from_store(theme):
    return os.path.isfile(os.path.join(THEMES_DIR, theme, MARKER))


def store_ids():
    ids = set()
    for theme in installed():
        try:
            with open(os.path.join(THEMES_DIR, theme, MARKER)) as f:
                ids.add(f.read().strip())
        except OSError:
            continue
    return ids


def _pkexec(script, *args):
    try:
        result = subprocess.run(["pkexec", "sh", "-c", script, "sh", *args],
                                capture_output=True, text=True, timeout=AUTH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def apply(theme):
    """None si quedó activo; si no, el archivo que lo impide (o "")."""
    if not SAFE_NAME_RE.match(theme):
        return ""
    if not _pkexec('mkdir -p "$(dirname "$2")" && printf "[Theme]\\nCurrent=%s\\n" "$1" > "$2"',
                   theme, CONF_FILE):
        return ""
    active, source = current()
    return None if active == theme else (source or "")


def _drop_outside_links(root):
    """Contenido de terceros: un symlink que sale de su carpeta dejaría
    leer cualquier archivo desde la carpeta del sistema."""
    real_root = os.path.realpath(root)
    for dirpath, dirnames, files in os.walk(root):
        for name in dirnames + files:
            path = os.path.join(dirpath, name)
            if os.path.islink(path) and os.path.commonpath(
                    [real_root, os.path.realpath(path)]) != real_root:
                os.unlink(path)


def install(themes, item_id, safe_name):
    """Copia cada tema a THEMES_DIR con el marcador, en una sola llamada a
    pkexec. Lista de temas instalados, o None si falló."""
    pairs = []
    for name, path in themes.items():
        dest = safe_name(name)
        if not os.path.exists(theme_dir(dest)):
            _drop_outside_links(path)
            pairs += [path, dest]
    if not pairs:
        return []
    script = ('set -e; dir=$1; id=$2; shift 2; while [ $# -gt 1 ]; do '
              'cp -r -- "$1" "$dir/$2"; printf %s "$id" > "$dir/$2/' + MARKER + '"; '
              'chmod -R u=rwX,go=rX "$dir/$2"; shift 2; done')
    if not _pkexec(script, THEMES_DIR, item_id, *pairs):
        return None
    return pairs[1::2]


def uninstall(theme):
    """Solo temas instalados desde aquí (con marcador)."""
    if not SAFE_NAME_RE.match(theme) or not installed_from_store(theme):
        return False
    return _pkexec('[ -f "$1/' + MARKER + '" ] && rm -rf -- "$1"', theme_dir(theme))


def test(path):
    """Abre el tema en el greeter en modo prueba y espera a que se cierre.
    False si no hay greeter para su versión de Qt."""
    greeter = shutil.which(GREETERS[qt_version(path)])
    if greeter is None:
        return False
    try:
        subprocess.run([greeter, "--test-mode", "--theme", path], capture_output=True,
                       timeout=TEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        pass
    except OSError:
        return False
    return True

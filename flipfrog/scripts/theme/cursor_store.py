"""Temas de cursor de la KDE Store (store.kde.org / pling) para el selector
de cursores (cursor_picker.py). Sin GTK.

Catálogo por la API pública OCS (categoría 107 = cursores), sin cuenta.
Contenido que sube cualquiera: solo se copian carpetas con cursors/ (nada
se ejecuta) y bsdtar sin -P ya rechaza rutas absolutas y "..". Instala en
~/.local/share/icons/<tema> con un marcador (MARKER) para poder quitarlo
después sin tocar temas puestos a mano."""

import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

API_URL = "https://api.kde-look.org/ocs/v1/content/data"
CATEGORY = 107
PAGE_SIZE = 20
CACHE_DIR = os.path.expanduser("~/.cache/flipfrog-cursor-store")
INSTALL_DIR = os.path.expanduser("~/.local/share/icons")
MARKER = ".flipfrog-store"
USER_AGENT = "Mozilla/5.0"
TIMEOUT = 15
DOWNLOAD_TIMEOUT = 60
MAX_FILE_KB = 40 * 1024
MAX_FILES_TRIED = 3
# Variantes para Windows (.cur/.ani) o solo hyprcursor: no sirven aquí.
SKIP_FILE_RE = re.compile(r"windows|\.exe$|\.msi$|\.inf$|hyprcursor", re.I)
XCURSOR_MAGIC = b"Xcur"


def _get(url, timeout=TIMEOUT):
    # Los enlaces de pling traen el nombre del archivo tal cual, con espacios.
    url = urllib.parse.quote(url, safe=":/?&=%#@+,;~")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def search(query="", page=0):
    """(items, total). Cada item: {id, name, downloads, files: [(nombre,
    url, kb, md5)]}, ordenados por descargas."""
    params = {"categories": CATEGORY, "sortmode": "down", "format": "json",
              "page": page, "pagesize": PAGE_SIZE}
    if query:
        params["search"] = query
    data = json.loads(_get(f"{API_URL}?{urllib.parse.urlencode(params)}"))
    items = []
    for entry in data.get("data", []):
        files = []
        n = 1
        while entry.get(f"downloadlink{n}"):
            name = entry.get(f"downloadname{n}") or ""
            kb = int(entry.get(f"downloadsize{n}") or 0)
            if not SKIP_FILE_RE.search(name) and kb <= MAX_FILE_KB:
                files.append((name, entry[f"downloadlink{n}"], kb, entry.get(f"downloadmd5sum{n}") or ""))
            n += 1
        if files:
            items.append({"id": str(entry["id"]), "name": entry.get("name") or "",
                          "downloads": int(entry.get("downloads") or 0), "files": files})
    return items, int(data.get("totalitems") or 0)


def _archive_stem(filename):
    return safe_name(re.sub(r"(\.tar)?\.[a-z0-9]+$", "", os.path.basename(filename), flags=re.I))


def safe_name(name):
    """Sin espacios ni paréntesis: `hyprctl setcursor` separa por espacios."""
    return re.sub(r"[^\w.+-]+", "-", name).strip("-") or "cursors"


def _nest_root(target, filename):
    """Archivos con cursors/ en la raíz (sin carpeta del tema): se mueven a
    <target>/<nombre del archivo>/, como el resto."""
    if not os.path.isdir(os.path.join(target, "cursors")):
        return
    nested = os.path.join(target, _archive_stem(filename))
    os.makedirs(nested, exist_ok=True)
    for entry in os.listdir(target):
        if entry != os.path.basename(nested):
            os.replace(os.path.join(target, entry), os.path.join(nested, entry))


def _cursor_themes(root):
    """{nombre: carpeta} de cada carpeta con cursors/ que tenga al menos un
    archivo Xcursor de verdad (no .cur de Windows ni hyprcursor)."""
    themes = {}
    for dirpath, dirnames, _files in os.walk(root):
        if "cursors" not in dirnames:
            continue
        cursors = os.path.join(dirpath, "cursors")
        for name in os.listdir(cursors):
            path = os.path.join(cursors, name)
            try:
                if os.path.isfile(path):
                    with open(path, "rb") as f:
                        if f.read(4) == XCURSOR_MAGIC:
                            themes.setdefault(os.path.basename(dirpath), dirpath)
                            break
            except OSError:
                continue
    return themes


def fetch(item):
    """Baja y extrae el primer archivo del item que traiga cursores
    Xcursor (hasta MAX_FILES_TRIED). {tema: carpeta extraída}, vacío si
    ninguno sirve. Caché por id + md5 del archivo."""
    for name, url, _kb, md5 in item["files"][:MAX_FILES_TRIED]:
        key = hashlib.sha1(f"{item['id']}:{md5 or url}".encode()).hexdigest()[:16]
        target = os.path.join(CACHE_DIR, key)
        if os.path.isdir(target):
            _nest_root(target, name)
            themes = _cursor_themes(target)
            if themes:
                return themes
            continue
        os.makedirs(CACHE_DIR, exist_ok=True)
        archive = os.path.join(CACHE_DIR, key + ".download")
        try:
            with open(archive, "wb") as f:
                f.write(_get(url, DOWNLOAD_TIMEOUT))
            os.makedirs(target, exist_ok=True)
            subprocess.run(["bsdtar", "-xf", archive, "-C", target],
                           capture_output=True, timeout=DOWNLOAD_TIMEOUT)
        except (OSError, ValueError, http.client.HTTPException, subprocess.TimeoutExpired):
            # Error de red: sin caché, se reintenta al volver a abrir.
            shutil.rmtree(target, ignore_errors=True)
            continue
        finally:
            if os.path.exists(archive):
                os.remove(archive)
        _nest_root(target, name)
        themes = _cursor_themes(target)
        if themes:
            return themes
        # Bajó pero no trae Xcursor: queda la carpeta vacía como caché
        # para no volver a bajarlo.
        shutil.rmtree(target, ignore_errors=True)
        os.makedirs(target, exist_ok=True)
    return {}


def install(themes, item_id):
    """Copia cada tema a INSTALL_DIR (salta los que ya existan) con el
    marcador. Lista de temas instalados."""
    done = []
    os.makedirs(INSTALL_DIR, exist_ok=True)
    for name, path in themes.items():
        dest = os.path.join(INSTALL_DIR, safe_name(name))
        if os.path.exists(dest):
            continue
        shutil.copytree(path, dest, symlinks=True)
        with open(os.path.join(dest, MARKER), "w") as f:
            f.write(item_id)
        done.append(os.path.basename(dest))
    return done


def installed_from_store(theme):
    return os.path.isfile(os.path.join(INSTALL_DIR, theme, MARKER))


def uninstall(theme):
    """Solo temas instalados desde aquí (con marcador)."""
    if installed_from_store(theme):
        shutil.rmtree(os.path.join(INSTALL_DIR, theme), ignore_errors=True)

"""Temas de la KDE Store (store.kde.org / pling) para los selectores de
cursor e íconos ("Obtener cursores" / "Obtener íconos"). Sin GTK.

Catálogo por la API pública OCS, sin cuenta; qué cuenta como tema lo
decide el `kind` (theme_kinds.py). Contenido que sube cualquiera: solo se
copian carpetas que sean temas de verdad (nada se ejecuta) y bsdtar sin
-P ya rechaza rutas absolutas y "..". Instala en ~/.local/share/icons/
<tema> con un marcador (MARKER) para poder quitarlo después sin tocar
temas puestos a mano."""

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
PAGE_SIZE = 20
INSTALL_DIR = os.path.expanduser("~/.local/share/icons")
MARKER = ".flipfrog-store"
USER_AGENT = "Mozilla/5.0"
TIMEOUT = 15
DOWNLOAD_TIMEOUT = 180
MAX_FILES_TRIED = 3
NETWORK_ERRORS = (OSError, ValueError, http.client.HTTPException, subprocess.TimeoutExpired)


def _get(url, timeout=TIMEOUT):
    # Los enlaces de pling traen el nombre del archivo tal cual, con espacios.
    url = urllib.parse.quote(url, safe=":/?&=%#@+,;~")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def search(kind, query="", page=0):
    """(items, total). Cada item: {id, name, downloads, preview, files:
    [(nombre, url, kb, md5)]}, ordenados por descargas."""
    params = {"categories": kind.store_category, "sortmode": "down", "format": "json",
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
            if not kind.skip_file_re.search(name) and kb <= kind.max_file_kb:
                files.append((name, entry[f"downloadlink{n}"], kb, entry.get(f"downloadmd5sum{n}") or ""))
            n += 1
        if files:
            items.append({"id": str(entry["id"]), "name": entry.get("name") or "",
                          "downloads": int(entry.get("downloads") or 0),
                          "preview": entry.get("smallpreviewpic1") or entry.get("previewpic1"),
                          "files": files})
    return items, int(data.get("totalitems") or 0)


def preview_image(kind, item):
    """Captura de la tienda (la que sube el autor), en caché. Ruta o None."""
    url = item.get("preview")
    if not url:
        return None
    path = os.path.join(kind.store_cache, "shots", hashlib.sha1(url.encode()).hexdigest()[:16])
    if os.path.isfile(path):
        return path
    try:
        data = _get(url)
    except NETWORK_ERRORS:
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    return path


def _archive_stem(filename):
    return safe_name(re.sub(r"(\.tar)?\.[a-z0-9]+$", "", os.path.basename(filename), flags=re.I))


def safe_name(name):
    """Sin espacios ni paréntesis: `hyprctl setcursor` separa por espacios."""
    return re.sub(r"[^\w.+-]+", "-", name).strip("-") or "theme"


def _nest_root(kind, target, filename):
    """Archivos con el tema en la raíz (cursors/ o index.theme sin carpeta
    propia): se mueven a <target>/<nombre del archivo>/, como el resto."""
    if not os.path.exists(os.path.join(target, kind.root_marker)):
        return
    nested = os.path.join(target, _archive_stem(filename))
    os.makedirs(nested, exist_ok=True)
    for entry in os.listdir(target):
        if entry != os.path.basename(nested):
            os.replace(os.path.join(target, entry), os.path.join(nested, entry))


def cached(kind, item):
    """Temas ya bajados de este item ({} si todavía no)."""
    for name, url, _kb, md5 in item["files"][:MAX_FILES_TRIED]:
        target = os.path.join(kind.store_cache, _key(item, url, md5))
        if os.path.isdir(target):
            _nest_root(kind, target, name)
            themes = kind.find_themes(target)
            if themes:
                return themes
    return {}


def _key(item, url, md5):
    return hashlib.sha1(f"{item['id']}:{md5 or url}".encode()).hexdigest()[:16]


def fetch(kind, item):
    """Baja y extrae el primer archivo del item que traiga un tema de este
    tipo (hasta MAX_FILES_TRIED). {tema: carpeta extraída}, vacío si
    ninguno sirve. Caché por id + md5 del archivo."""
    for name, url, _kb, md5 in item["files"][:MAX_FILES_TRIED]:
        target = os.path.join(kind.store_cache, _key(item, url, md5))
        if os.path.isdir(target):
            _nest_root(kind, target, name)
            themes = kind.find_themes(target)
            if themes:
                return themes
            continue
        os.makedirs(kind.store_cache, exist_ok=True)
        archive = target + ".download"
        try:
            with open(archive, "wb") as f:
                f.write(_get(url, DOWNLOAD_TIMEOUT))
            os.makedirs(target, exist_ok=True)
            subprocess.run(["bsdtar", "-xf", archive, "-C", target],
                           capture_output=True, timeout=DOWNLOAD_TIMEOUT)
        except NETWORK_ERRORS:
            # Error de red: sin caché, se reintenta al volver a abrir.
            shutil.rmtree(target, ignore_errors=True)
            continue
        finally:
            if os.path.exists(archive):
                os.remove(archive)
        _nest_root(kind, target, name)
        themes = kind.find_themes(target)
        if themes:
            return themes
        # Bajó pero no trae un tema de este tipo: queda la carpeta vacía
        # como caché para no volver a bajarlo.
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

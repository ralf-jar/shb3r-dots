"""Resolución de enlaces de MediaFire, sin GTK -- usado solo por
download_daemon.py. Archivos: nombre/tamaño vía API pública
(`file/get_info.php`), enlace directo raspado del `href` del botón de
descarga de la página (`downloadN.mediafire.com/...`, expira -- se
resuelve recién al empezar cada descarga). Carpetas: `folder/
get_content.php`, recursivo por subcarpeta."""

import base64
import re

import requests

API = "https://www.mediafire.com/api/1.5"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
TIMEOUT = 20

URL_RE = re.compile(r'(?:https?://)?(?:www\.)?mediafire\.com/[^\s"\'<>]+', re.I)
FILE_KEY_RE = re.compile(r'mediafire\.com/(?:file|file_premium|view|download)/([a-z0-9]+)', re.I)
FOLDER_KEY_RE = re.compile(r'mediafire\.com/folder/([a-z0-9]+)', re.I)
LEGACY_KEY_RE = re.compile(r'mediafire\.com/\?([a-z0-9]+)', re.I)
DIRECT_RE = re.compile(r'href="(https?://download\d*\.mediafire\.com/[^"]+)"')
SCRAMBLED_RE = re.compile(r'data-scrambled-url="([^"]+)"')


class MediafireError(Exception):
    """`code` es un código de i18n (módulo "descargas")."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


def parse_links(text):
    """[(kind, key)] en orden de aparición, sin repetidos -- kind es
    "file" o "folder". Todo lo que no sea MediaFire se ignora."""
    seen = set()
    links = []

    for match in URL_RE.finditer(text):
        url = match.group(0)
        m = FOLDER_KEY_RE.search(url)
        if m:
            link = ("folder", m.group(1))
        else:
            m = FILE_KEY_RE.search(url) or LEGACY_KEY_RE.search(url)
            if not m:
                continue
            link = ("file", m.group(1))

        if link not in seen:
            seen.add(link)
            links.append(link)

    return links


def _api(session, path, **params):
    params["response_format"] = "json"
    try:
        r = session.get(f"{API}/{path}", params=params, timeout=TIMEOUT)
        data = r.json().get("response", {})
    except (requests.RequestException, ValueError):
        raise MediafireError("error_red")
    if data.get("result") != "Success":
        raise MediafireError("error_no_encontrado")
    return data


def file_info(key, session=None):
    """{"key", "name", "size"} -- falla con MediafireError si el archivo
    no existe o tiene contraseña (no hay forma de descargarlo sin ella)."""
    session = session or _session()
    info = _api(session, "file/get_info.php", quick_key=key)["file_info"]
    if info.get("password_protected") == "yes":
        raise MediafireError("error_contrasena")
    return {"key": key, "name": info["filename"], "size": int(info.get("size") or 0)}


def folder_files(key, session=None, subdir=""):
    """Lista plana de {"key", "name", "size", "subdir"} de la carpeta y
    todas sus subcarpetas -- `subdir` replica la estructura de carpetas
    de MediaFire bajo la carpeta destino."""
    session = session or _session()
    name = _api(session, "folder/get_info.php", folder_key=key)["folder_info"]["name"]
    subdir = f"{subdir}/{safe_name(name)}" if subdir else safe_name(name)

    files = []
    subfolders = []
    for content_type in ("files", "folders"):
        chunk = 1
        while True:
            data = _api(session, "folder/get_content.php", folder_key=key,
                        content_type=content_type, chunk=chunk)["folder_content"]
            for entry in data.get(content_type) or []:
                if content_type == "folders":
                    subfolders.append(entry["folderkey"])
                elif entry.get("password_protected") != "yes":
                    files.append({"key": entry["quickkey"], "name": entry["filename"],
                                  "size": int(entry.get("size") or 0), "subdir": subdir})
            if data.get("more_chunks") != "yes":
                break
            chunk += 1

    for sub in subfolders:
        files.extend(folder_files(sub, session, subdir))
    return files


def direct_link(key, session=None):
    session = session or _session()
    try:
        r = session.get(f"https://www.mediafire.com/file/{key}/file", timeout=TIMEOUT)
    except requests.RequestException:
        raise MediafireError("error_red")

    m = DIRECT_RE.search(r.text)
    if m:
        return m.group(1).replace("&amp;", "&")

    m = SCRAMBLED_RE.search(r.text)
    if m:
        try:
            return base64.b64decode(m.group(1)).decode()
        except ValueError:
            pass

    raise MediafireError("error_sin_enlace")


def safe_name(name):
    name = name.replace("/", "_").replace("\0", "").strip()
    return name or "archivo"

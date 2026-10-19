"""Enlaces públicos de Google Drive, sin GTK -- usado solo por
download_daemon.py. Archivos: `drive.usercontent.google.com/download` con
`confirm=t` (salta el aviso de antivirus de los archivos grandes) y
`Range` para reanudar; nombre y tamaño salen de la respuesta a un `Range`
de 1 byte. Carpetas: `embeddedfolderview`, recursivo -- la página normal de
la carpeta solo trae los primeros 50 archivos. Documentos de Docs/Sheets
no son archivos descargables y se ignoran."""

import html
import re
from urllib.parse import unquote

import requests

DOWNLOAD = "https://drive.usercontent.google.com/download"
FOLDER_VIEW = "https://drive.google.com/embeddedfolderview"
TIMEOUT = 20

URL_RE = re.compile(r'(?:https?://)?(?:drive|docs|drive\.usercontent)\.google\.com/[^\s"\'<>]+', re.I)
FOLDER_ID_RE = re.compile(r'/folders/([\w-]{10,})')
FILE_ID_RE = re.compile(r'/file/d/([\w-]{10,})|[?&]id=([\w-]{10,})')
ENTRY_RE = re.compile(
    r'<a href="https://drive\.google\.com/(file/d|drive/folders)/([\w-]+)[^"]*"[^>]*>.*?'
    r'flip-entry-title">([^<]*)<', re.S)
TITLE_RE = re.compile(r'<title>([^<]*)</title>')
FILENAME_RE = re.compile(r"filename\*=UTF-8''([^;]+)|filename=\"([^\"]+)\"", re.I)


class GdriveError(Exception):
    """`code` es un código de i18n (módulo "descargas")."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_links(text):
    """[("gdrive" | "gdrive_folder", id)] en orden de aparición, sin repetidos."""
    links = []
    for match in URL_RE.finditer(text):
        url = match.group(0)
        m = FOLDER_ID_RE.search(url)
        if m:
            link = ("gdrive_folder", m.group(1))
        else:
            m = FILE_ID_RE.search(url)
            if not m:
                continue
            link = ("gdrive", m.group(1) or m.group(2))
        if link not in links:
            links.append(link)
    return links


def request(file_id, session, offset=0, size_probe=False):
    """Respuesta en streaming desde `offset`. Una página HTML en lugar del
    archivo es un error: cuota de descargas agotada, archivo privado o
    eliminado."""
    headers = {"Range": "bytes=0-0"} if size_probe else ({"Range": f"bytes={offset}-"} if offset else {})
    try:
        r = session.get(DOWNLOAD, params={"id": file_id, "export": "download", "confirm": "t"},
                        headers=headers, stream=True, timeout=(15, 60))
    except requests.RequestException:
        raise GdriveError("error_red")
    if r.status_code == 404:
        r.close()
        raise GdriveError("error_no_encontrado")
    if "text/html" in r.headers.get("content-type", ""):
        page = r.text
        r.close()
        if "quota" in page.lower() or "too many users" in page.lower():
            raise GdriveError("error_cuota")
        raise GdriveError("error_privado")
    if r.status_code >= 400 and r.status_code != 416:
        r.close()
        raise GdriveError("error_privado" if r.status_code in (401, 403) else "error_red")
    return r


def file_info(file_id, session):
    r = request(file_id, session, size_probe=True)
    with r:
        m = FILENAME_RE.search(r.headers.get("content-disposition", ""))
        name = unquote(m.group(1)) if m and m.group(1) else (m.group(2) if m else file_id)
        size = r.headers.get("content-range", "").rpartition("/")[2]
    return {"kind": "gdrive", "key": file_id, "name": name, "size": int(size) if size.isdigit() else 0}


def folder_files(folder_id, session, subdir=""):
    """Lista plana de {"kind", "key", "name", "size", "subdir"}; `subdir`
    replica las carpetas de Drive bajo la carpeta destino. El tamaño se
    conoce recién al empezar cada descarga."""
    try:
        r = session.get(FOLDER_VIEW, params={"id": folder_id}, timeout=TIMEOUT)
    except requests.RequestException:
        raise GdriveError("error_red")
    if r.status_code != 200:
        raise GdriveError("error_no_encontrado")
    m = TITLE_RE.search(r.text)
    name = _safe(html.unescape(m.group(1)) if m else folder_id)
    subdir = f"{subdir}/{name}" if subdir else name

    files = []
    for kind, entry_id, title in ENTRY_RE.findall(r.text):
        if kind == "drive/folders":
            files.extend(folder_files(entry_id, session, subdir))
        else:
            files.append({"kind": "gdrive", "key": entry_id, "name": html.unescape(title),
                          "size": 0, "subdir": subdir})
    return files


def _safe(name):
    return name.replace("/", "_").replace("\0", "").strip() or "carpeta"

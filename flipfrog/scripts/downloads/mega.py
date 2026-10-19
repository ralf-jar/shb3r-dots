"""Enlaces públicos de MEGA, sin GTK -- usado solo por download_daemon.py.
MEGA cifra todo en el navegador: la clave viaja después del `#` del
enlace y nunca llega al servidor. Nombre/tamaño y la URL de descarga
salen de su API (`g` para un archivo, `f` para el árbol de una carpeta);
el contenido se descifra con AES-CTR mientras se baja, y como CTR permite
empezar en cualquier bloque de 16 bytes, se puede reanudar. No se
verifica el MAC del archivo (TLS ya cubre la integridad del transporte).

Claves de la cola: "HANDLE#CLAVE" (archivo suelto), "CARPETA#CLAVE"
(carpeta) y "CARPETA!NODO#CLAVE" (archivo dentro de una carpeta, con la
clave del archivo ya descifrada)."""

import base64
import json
import random
import re

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

API = "https://g.api.mega.co.nz/cs"
TIMEOUT = 20

URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?mega(?:\.co)?\.nz/(?:(file|folder)/([\w-]{8})#([\w-]+)|#(F?)!([\w-]{8})!([\w-]+))',
    re.I)
ERRORS = {-2: "error_no_encontrado", -9: "error_no_encontrado", -11: "error_no_encontrado",
          -16: "error_no_encontrado", -17: "error_cuota", -3: "error_red", -18: "error_red"}


class MegaError(Exception):
    """`code` es un código de i18n (módulo "descargas")."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_links(text):
    """[("mega" | "mega_folder", "HANDLE#CLAVE")] en orden de aparición,
    sin repetidos (formatos /file/, /folder/ y los viejos #! y #F!)."""
    links = []
    for m in URL_RE.finditer(text):
        if m.group(1):
            folder, handle, key = m.group(1).lower() == "folder", m.group(2), m.group(3)
        else:
            folder, handle, key = m.group(4) == "F", m.group(5), m.group(6)
        link = ("mega_folder" if folder else "mega", f"{handle}#{key}")
        if link not in links:
            links.append(link)
    return links


def _b64(text):
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        raise MegaError("error_no_encontrado")


def _api(session, body, folder=None):
    params = {"id": random.randint(0, 2 ** 31)}
    if folder:
        params["n"] = folder
    try:
        data = session.post(API, params=params, json=[body], timeout=TIMEOUT).json()
    except (requests.RequestException, ValueError):
        raise MegaError("error_red")
    if isinstance(data, list):
        data = data[0]
    if isinstance(data, int):
        raise MegaError(ERRORS.get(data, "error_no_encontrado"))
    return data


def _file_key(raw):
    """Clave AES y nonce de CTR de una clave de archivo de 32 bytes."""
    if len(raw) != 32:
        raise MegaError("error_no_encontrado")
    return bytes(a ^ b for a, b in zip(raw[:16], raw[16:])), raw[16:24]


def _name(attrs, key):
    try:
        plain = Cipher(algorithms.AES(key), modes.CBC(bytes(16))).decryptor().update(_b64(attrs))
        if plain.startswith(b"MEGA"):
            return json.loads(plain[4:].rstrip(b"\0").decode())["n"]
    except (ValueError, KeyError, MegaError):
        pass
    return None


def _split(key):
    path, _, secret = key.partition("#")
    folder, _, node = path.rpartition("!")
    return folder, node, secret


def file_info(key, session):
    _folder, handle, secret = _split(key)
    aes, _nonce = _file_key(_b64(secret))
    data = _api(session, {"a": "g", "p": handle})
    return {"kind": "mega", "key": key, "name": _name(data.get("at", ""), aes) or handle,
            "size": int(data.get("s") or 0)}


def folder_files(key, session):
    """Lista plana de {"kind", "key", "name", "size", "subdir"} de toda la
    carpeta; `subdir` replica sus subcarpetas bajo la carpeta destino."""
    folder, _node, secret = _split(key)
    share_key = _b64(secret)[:16]
    if len(share_key) != 16:
        raise MegaError("error_no_encontrado")
    nodes = _api(session, {"a": "f", "c": 1, "r": 1, "ca": 1}, folder=folder).get("f") or []
    ecb = Cipher(algorithms.AES(share_key), modes.ECB())

    names, parents, files = {}, {}, []
    for node in nodes:
        enc = node.get("k", "").split("/")[0].partition(":")[2]
        if not enc:
            continue
        raw = ecb.decryptor().update(_b64(enc))
        if node.get("t") == 1:
            names[node["h"]] = _name(node.get("a", ""), raw[:16]) or node["h"]
            parents[node["h"]] = node.get("p")
        elif node.get("t") == 0 and len(raw) == 32:
            aes, _nonce = _file_key(raw)
            files.append((node, raw, _name(node.get("a", ""), aes) or node["h"]))

    def path_of(handle):
        parts = []
        while handle in names:
            parts.append(_safe(names[handle]))
            handle = parents.get(handle)
        return "/".join(reversed(parts))

    return [{"kind": "mega", "key": f"{folder}!{node['h']}#{base64.urlsafe_b64encode(raw).decode().rstrip('=')}",
             "name": name, "size": int(node.get("s") or 0), "subdir": path_of(node.get("p"))}
            for node, raw, name in files]


def request(key, session, offset=0):
    """(respuesta en streaming, función que descifra cada bloque, offset
    real). El offset se baja al múltiplo de 16 anterior: el contador de
    CTR avanza por bloque, el llamador recorta su .part a ese tamaño."""
    folder, node, secret = _split(key)
    aes, nonce = _file_key(_b64(secret))
    body = {"a": "g", "g": 1, "ssl": 2}
    body["n" if folder else "p"] = node
    url = _api(session, body, folder=folder or None).get("g")
    if not url:
        raise MegaError("error_sin_enlace")

    offset -= offset % 16
    try:
        r = session.get(f"{url}/{offset}-", stream=True, timeout=(15, 60))
    except requests.RequestException:
        raise MegaError("error_red")
    if r.status_code == 509:
        r.close()
        raise MegaError("error_cuota")
    if r.status_code >= 400:
        r.close()
        raise MegaError("error_red")
    counter = nonce + (offset // 16).to_bytes(8, "big")
    return r, Cipher(algorithms.AES(aes), modes.CTR(counter)).decryptor().update, offset


def _safe(name):
    return name.replace("/", "_").replace("\0", "").strip() or "carpeta"

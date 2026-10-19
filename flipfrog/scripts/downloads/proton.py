"""Enlaces públicos de Proton Drive, sin GTK -- usado solo por
download_daemon.py. Proton cifra con PGP de extremo a extremo y la CLI
oficial no baja enlaces públicos sin cuenta: se usa `proton` (proton-cli,
no oficial, MIT), que lo hace sin iniciar sesión. Un enlace es un solo
item de la cola, archivo o carpeta entera: se baja a una carpeta oculta
dentro del destino (progreso = lo que ya pesa esa carpeta) y al terminar
se mueve su contenido al destino. Sin reanudar: pausar o reintentar
vuelve a bajar todo."""

import json
import os
import re
import shutil
import subprocess
import time

URL_RE = re.compile(r'(?:https?://)?drive\.proton\.me/urls/[\w-]+#[\w-]+', re.I)
BIN = "proton"
BASE = [BIN, "--no-input", "--no-color", "-q"]
INFO_TIMEOUT = 60


class ProtonError(Exception):
    """`code` es un código de i18n (módulo "descargas")."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_links(text):
    """[("proton", url)] en orden de aparición, sin repetidos."""
    links = []
    for match in URL_RE.finditer(text):
        url = match.group(0)
        if not url.lower().startswith("http"):
            url = "https://" + url
        if ("proton", url) not in links:
            links.append(("proton", url))
    return links


def _json(args):
    if shutil.which(BIN) is None:
        raise ProtonError("error_sin_proton")
    try:
        r = subprocess.run(BASE + ["-o", "json"] + args, capture_output=True, text=True,
                           timeout=INFO_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ProtonError("error_red")
    if r.returncode != 0:
        err = r.stderr.lower()
        raise ProtonError("error_contrasena" if "password" in err else "error_no_encontrado")
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise ProtonError("error_no_encontrado")


def _entries(data):
    """Todos los objetos con "name" y "type" del JSON, sin depender de la
    forma exacta del sobre que los envuelve."""
    if isinstance(data, dict):
        if "name" in data and "type" in data:
            yield data
        for value in data.values():
            yield from _entries(value)
    elif isinstance(data, list):
        for value in data:
            yield from _entries(value)


def info(url):
    """{"name", "size", "folder"}; de una carpeta, el tamaño es la suma de
    sus archivos."""
    root = next(_entries(_json(["drive", "items", "get", "/", "--link", url])), None)
    if root is None:
        raise ProtonError("error_no_encontrado")
    folder = root["type"] == "folder"
    size = int(root.get("size") or 0)
    if folder:
        listing = _json(["drive", "items", "list", "/", "--link", url, "--recursive", "--limit", "0"])
        size = sum(int(e.get("size") or 0) for e in _entries(listing) if e["type"] == "file")
    return {"name": root["name"], "size": size, "folder": folder}


def staging_dir(dest, item_id):
    return os.path.join(dest, f".proton-{item_id}.part")


def _du(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def download(url, folder, staging, on_progress, should_stop, run_watched):
    """("done" | "paused" | "canceled" | "error", código de error, rutas
    bajadas dentro de `staging`)."""
    if shutil.which(BIN) is None:
        return "error", "error_sin_proton", []
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    cmd = BASE + ["drive", "items", "download", "/", "--link", url, "--dest-dir", staging]
    if folder:
        cmd.append("--recursive")
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError:
        return "error", "error_sin_proton", []

    stopped = run_watched(proc, should_stop)
    last, last_time = 0, time.time()
    while proc.poll() is None:
        time.sleep(1)
        done, now = _du(staging), time.time()
        on_progress(done, int((done - last) / (now - last_time)))
        last, last_time = done, now
    err = proc.stderr.read().lower() if proc.stderr else ""

    if stopped:
        shutil.rmtree(staging, ignore_errors=True)
        return stopped[0], None, []
    if proc.returncode != 0:
        shutil.rmtree(staging, ignore_errors=True)
        if "password" in err:
            return "error", "error_contrasena", []
        return "error", "error_proton", []
    on_progress(_du(staging), 0)
    return "done", None, sorted(os.listdir(staging))

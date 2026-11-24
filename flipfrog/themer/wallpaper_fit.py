#!/usr/bin/env python3
"""Ajuste del fondo por monitor (sin GTK): rellenar con recorte, ajustar,
estirar, centrar o doble capa (la misma imagen desenfocada a pantalla
completa detrás y encima completa). Lo usa wallpaper.py al aplicar y el
popup scripts/wallpaper/wallpaper_adjust.py para editarlo.

Ajustes en themer/wallpaper-fit.json (un escritor: el popup), en
"files" por archivo (ruta con `~`) y por salida: {"mode", "crop":
[x, y, w, h] en fracciones de la imagen, "blur", "dim", "size" (% de la
capa de encima en Ajustar/Doble capa), "feather" (% de sus bordes que
se degradan), "shadow" (opacidad de su sombra)}; además
"detect_palette", el switch de sacar la paleta del tema del fondo. Un archivo sin ajustes usa el de siempre (rellenar
centrado), así que el ajuste también vale cuando lo pone un tema.

Imágenes y gifs se renderizan con ffmpeg al tamaño físico de cada salida
(caché en ~/.cache/flipfrog-wallpaper-fit/). Un video lleva el mismo
grafo de filtros como vf de mpv mientras `encode` lo codifica una vez en
segundo plano (hevc_nvenc, o libx264 sin NVIDIA); al terminar, el fondo
pasa a ese archivo y el filtro deja de costar CPU en cada cuadro (la
doble capa en vivo: ~2 núcleos). Switch "prerender_video", prendido por
defecto.

    wallpaper_fit.py encode <video>
"""

import hashlib
import json
import os
import subprocess
import sys

THEMER_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(THEMER_DIR, "..", "scripts"))
import common

FIT_FILE = os.path.join(THEMER_DIR, "wallpaper-fit.json")
RENDER_CACHE = os.path.expanduser("~/.cache/flipfrog-wallpaper-fit")

MODES = ("fill", "fit", "blur", "stretch", "center")
DEFAULT_BLUR = 3.0     # sigma en % del ancho de la salida
DEFAULT_DIM = 30       # % de oscurecido de la capa de atrás
DEFAULT_SIZE = 100     # % del tamaño máximo de la imagen en Ajustar/Doble capa
MIN_SIZE = 20
MAX_FEATHER = 50   # % del lado corto de la imagen de encima que se degrada
# Sombra detrás de la imagen de encima: desenfoque y desplazamiento hacia
# abajo en fracción del alto de la salida; "shadow" = opacidad en %.
SHADOW_SIGMA = 0.03
SHADOW_OFFSET = 0.012
FULL = [0.0, 0.0, 1.0, 1.0]
# El desenfoque de la capa de atrás se calcula a 1/BLUR_SCALE de tamaño:
# a resolución completa un video de fondo se come varios núcleos.
BLUR_SCALE = 4


def collapse_home(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + "/") else path


RENDER_CACHE_MAX = 40
VIDEO_CACHE = os.path.join(RENDER_CACHE, "videos")
VIDEO_CACHE_MAX = 6
ENCODERS = (
    ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "30", "-b:v", "0", "-tag:v", "hvc1"],
    ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"],
)


def load_all():
    try:
        with open(FIT_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("files"), dict):
        data["files"] = {}
    return data


def _write(data):
    common.atomic_write(FIT_FILE, json.dumps(data, indent=1, ensure_ascii=False) + "\n")


def load(path):
    """Ajustes por salida de `path` ({} = el de siempre)."""
    return load_all()["files"].get(collapse_home(os.path.expanduser(path)), {})


def save(path, settings):
    data = load_all()
    key = collapse_home(os.path.expanduser(path))
    if settings:
        data["files"][key] = settings
    else:
        data["files"].pop(key, None)
    _write(data)


def get_option(name, default=None):
    return load_all().get(name, default)


def set_option(name, value):
    data = load_all()
    data[name] = value
    _write(data)


def _prune_cache(folder=RENDER_CACHE, keep=RENDER_CACHE_MAX):
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder)]
        files = [f for f in files if os.path.isfile(f) and ".tmp" not in f]
        files.sort(key=os.path.getmtime, reverse=True)
        for old in files[keep:]:
            os.remove(old)
    except OSError:
        pass


def monitors():
    """Salidas encendidas: nombre, tamaño físico ya rotado, posición y
    tamaño lógicos (para dibujar el acomodo)."""
    try:
        out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True,
                             text=True, timeout=3).stdout
        raw = json.loads(out)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    result = []
    for m in raw:
        if m.get("disabled"):
            continue
        w, h = m["width"], m["height"]
        if m.get("transform", 0) % 2:
            w, h = h, w
        scale = m.get("scale") or 1
        result.append({"name": m["name"], "width": w, "height": h,
                       "x": m["x"], "y": m["y"],
                       "lw": round(w / scale), "lh": round(h / scale)})
    return result


def media_size(path):
    """(ancho, alto) de una imagen, gif o video, o None."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
                              path], capture_output=True, text=True, timeout=10).stdout
        w, h = out.strip().splitlines()[0].split("x")[:2]
        return int(w), int(h)
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def normalized(setting):
    """Ajuste con todos los campos y valores dentro de rango."""
    setting = dict(setting or {})
    mode = setting.get("mode") if setting.get("mode") in MODES else "fill"
    crop = setting.get("crop") or FULL
    try:
        x, y, w, h = (min(1.0, max(0.0, float(v))) for v in crop)
    except (TypeError, ValueError):
        x, y, w, h = FULL
    w = max(0.01, min(w, 1 - x))
    h = max(0.01, min(h, 1 - y))
    return {"mode": mode, "crop": [x, y, w, h],
            "blur": float(setting.get("blur", DEFAULT_BLUR)),
            "dim": int(setting.get("dim", DEFAULT_DIM)),
            "size": min(100, max(MIN_SIZE, int(setting.get("size", DEFAULT_SIZE)))),
            "feather": min(MAX_FEATHER, max(0, int(setting.get("feather", 0)))),
            "shadow": min(100, max(0, int(setting.get("shadow", 0))))}


def cover_crop(src_w, src_h, out_w, out_h):
    """Recorte centrado más grande con la proporción de la salida."""
    src_ratio, out_ratio = src_w / src_h, out_w / out_h
    if src_ratio > out_ratio:
        w = out_ratio / src_ratio
        return [(1 - w) / 2, 0.0, w, 1.0]
    h = src_ratio / out_ratio
    return [0.0, (1 - h) / 2, 1.0, h]


def is_default(setting, src_size, out_size):
    """Igual a lo que hace awww por su cuenta (rellenar centrado)."""
    s = normalized(setting)
    if s["mode"] != "fill" or not src_size:
        return False
    ref = cover_crop(*src_size, *out_size)
    return all(abs(a - b) < 0.002 for a, b in zip(s["crop"], ref))


def _even(v):
    return max(2, int(round(v / 2)) * 2)


def filter_graph(setting, src_w, src_h, out_w, out_h, video=False):
    """Grafo de libavfilter de una entrada a una salida. Todo en números
    (sin expresiones con comas, que mpv y ffmpeg escapan distinto)."""
    s = normalized(setting)
    x, y, w, h = s["crop"]
    cw, ch = max(2, round(w * src_w)), max(2, round(h * src_h))
    cx, cy = round(x * src_w), round(y * src_h)
    cx, cy = min(cx, src_w - cw), min(cy, src_h - ch)
    pre = f"crop={cw}:{ch}:{cx}:{cy}," if (cw, ch) != (src_w, src_h) else ""
    # Lanczos en imágenes (se renderizan una vez); en video, bicúbico.
    lanczos = "flags=bicubic" if video else "flags=lanczos"
    scaler = lanczos
    mode = s["mode"]

    if mode == "fill":
        return (f"{pre}scale={out_w}:{out_h}:force_original_aspect_ratio=increase:{lanczos},"
                f"crop={out_w}:{out_h},setsar=1")
    if mode == "stretch":
        return f"{pre}scale={out_w}:{out_h}:{lanczos},setsar=1"

    if mode == "center":
        vw, vh = min(cw, out_w), min(ch, out_h)
        inner = f"crop={vw}:{vh}:{(cw - vw) // 2}:{(ch - vh) // 2},"
        return (f"{pre}{inner}pad={out_w}:{out_h}:{(out_w - vw) // 2}:{(out_h - vh) // 2}:black,"
                "setsar=1")

    scale = min(out_w / cw, out_h / ch) * s["size"] / 100
    fw, fh = min(out_w, _even(cw * scale)), min(out_h, _even(ch * scale))
    ox, oy = (out_w - fw) // 2, (out_h - fh) // 2
    feather, shadow = s["feather"], s["shadow"]
    if mode == "fit" and not feather and not shadow:
        return f"{pre}scale={fw}:{fh}:{lanczos},pad={out_w}:{out_h}:{ox}:{oy}:black,setsar=1"

    # Dos capas. blur: atrás la misma imagen rellenando la salida,
    # desenfocada y oscurecida. fit con bordes degradados: atrás negro.
    # Encima la imagen; con degradado lleva una máscara de alfa.
    bw, bh = _even(out_w / BLUR_SCALE), _even(out_h / BLUR_SCALE)
    bx, by, bcw, bch = cover_crop(cw, ch, bw, bh)
    bcw_px, bch_px = max(2, round(bcw * cw)), max(2, round(bch * ch))
    bcx, bcy = round(bx * cw), round(by * ch)
    # Oscurecer (lut sobre la luma) y desenfocar a tamaño reducido; en RGB
    # y a tamaño completo un video de fondo usaba ~3 núcleos. Una imagen
    # se queda en RGB (yuv420 suaviza el color de los bordes).
    if mode == "blur":
        sigma = max(0.1, s["blur"] * out_w / 100 / BLUR_SCALE)
        k = max(0.0, 1 - s["dim"] / 100)
        dim = (f"lutyuv=y=val*{k:.3f}" if video
               else f"lutrgb=r=val*{k:.3f}:g=val*{k:.3f}:b=val*{k:.3f}")
        back = f"gblur=sigma={sigma:.2f},{dim}"
    else:
        back = "lutyuv=y=16:u=128:v=128" if video else "lutrgb=r=0:g=0:b=0"
    fmt, over = ("yuv420p", "yuv420") if video else ("gbrp", "gbrp")

    front = f"[fgsrc]scale={fw}:{fh}:{scaler}[fg];"
    if feather:
        # Máscara: rectángulo blanco con borde negro, desenfocada a 1/4 y
        # escalada. gblur en gray deja la máscara entre 7 y 235 (el centro
        # quedaba 8 % transparente y el borde nunca en 0): el lut la lleva a
        # 0-255 y la eleva al cuadrado, para que lo claro se apague antes
        # de llegar al borde. Una sola imagen (color de 1 cuadro): alphamerge repite
        # la última, así no se recalcula en cada cuadro de un video.
        mw, mh = max(8, fw // BLUR_SCALE), max(8, fh // BLUR_SCALE)
        edge = max(1.0, feather / 100 * min(mw, mh))
        inset = max(1, round(edge / 2))
        front = (f"[fgsrc]scale={fw}:{fh}:{scaler},format={'yuva420p' if video else 'gbrap'}[fgc];"
                 f"color=c=black:s={mw}x{mh}:r=1:d=1,format=gray,"
                 f"drawbox=x={inset}:y={inset}:w={mw - 2 * inset}:h={mh - 2 * inset}:color=white:t=fill,"
                 f"gblur=sigma={max(0.5, edge / 4):.2f},lut=c0=(val-7)*(val-7)/204,"
                 f"scale={fw}:{fh}:flags=bilinear[mask];"
                 f"[fgc][mask]alphamerge[fg];")
    shade = ""
    if shadow:
        # Rectángulo negro desenfocado bajo la imagen, a 1/4 de tamaño y de
        # un solo cuadro (overlay repite el último). Mismo arreglo del
        # rango de gblur que en la máscara del degradado.
        sw, sh = bw, bh
        sx = round(ox / BLUR_SCALE)
        sy = round((oy + SHADOW_OFFSET * out_h) / BLUR_SCALE)
        sigma = max(0.5, SHADOW_SIGMA * out_h / BLUR_SCALE)
        alpha = shadow / 100 * 0.9 * 255 / 228
        rgba = "yuva420p" if video else "gbrap"
        shade = (f"color=c=black:s={out_w}x{out_h}:r=1:d=1,format={rgba}[shc];"
                 f"color=c=black:s={sw}x{sh}:r=1:d=1,format=gray,"
                 f"drawbox=x={sx}:y={sy}:w={max(1, fw // BLUR_SCALE)}:h={max(1, fh // BLUR_SCALE)}:"
                 f"color=white:t=fill,gblur=sigma={sigma:.2f},lut=c0=(val-7)*{alpha:.4f},"
                 f"scale={out_w}:{out_h}:flags=bilinear[shm];"
                 f"[shc][shm]alphamerge[sh];[bg0][sh]overlay=0:0:format={over}[bg];")
    return (f"{pre}format={fmt},split=2[bgsrc][fgsrc];"
            f"[bgsrc]crop={bcw_px}:{bch_px}:{bcx}:{bcy},scale={bw}:{bh}:flags=fast_bilinear,"
            f"{back},scale={out_w}:{out_h}:flags=bilinear[{'bg0' if shadow else 'bg'}];"
            f"{shade}"
            f"{front}"
            f"[bg][fg]overlay={ox}:{oy}:format={over},setsar=1")


def _cache_key(path, setting, out_size):
    key = json.dumps([path, os.path.getmtime(path), normalized(setting), out_size]).encode()
    return hashlib.sha1(key).hexdigest()


def _cache_path(path, setting, out_size, ext):
    return os.path.join(RENDER_CACHE, _cache_key(path, setting, out_size) + ext)


def render(path, setting, out_w, out_h, src_size=None):
    """Imagen (png) o gif ya ajustado para una salida; None si ffmpeg
    falla. Cacheado por archivo, fecha, ajuste y tamaño."""
    gif = path.lower().endswith(".gif")
    target = _cache_path(path, setting, (out_w, out_h), ".gif" if gif else ".png")
    if os.path.isfile(target):
        return target
    src_size = src_size or media_size(path)
    if not src_size:
        return None
    os.makedirs(RENDER_CACHE, exist_ok=True)
    graph = filter_graph(setting, *src_size, out_w, out_h)
    tmp = target + ".tmp" + os.path.splitext(target)[1]
    if gif:
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", path, "-filter_complex",
               f"[0:v]{graph},split[p0][p1];[p0]palettegen[pal];[p1][pal]paletteuse",
               "-loop", "0", tmp]
    else:
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", path, "-filter_complex",
               f"[0:v]{graph}", "-frames:v", "1", tmp]
    try:
        subprocess.run(cmd, check=True, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        if os.path.exists(tmp):
            os.remove(tmp)
        return None
    os.replace(tmp, target)
    _prune_cache()
    return target


def mpv_vf(setting, src_size, out_w, out_h):
    """Opción de mpv (sin espacios: mpvpaper separa -o por espacios)."""
    graph = filter_graph(setting, *src_size, out_w, out_h, video=True)
    return f"vf=lavfi=[{graph}]"


def plan(path):
    """[(monitor, ajuste o None)] para las salidas encendidas; None = el
    de siempre. Lista vacía si no hay ningún ajuste para `path`."""
    settings = load(path)
    if not settings:
        return []
    mons = monitors()
    src_size = media_size(path)
    result = []
    for m in mons:
        s = settings.get(m["name"])
        if s and src_size and is_default(s, src_size, (m["width"], m["height"])):
            s = None
        result.append((m, s, src_size))
    if all(s is None for _m, s, _src in result):
        return []
    return result


def prerendered(path, setting, out_w, out_h):
    """Video ya codificado con el ajuste, o None."""
    target = os.path.join(VIDEO_CACHE, _cache_key(path, setting, (out_w, out_h)) + ".mp4")
    return target if os.path.isfile(target) else None


def pending_encode(path):
    """Salidas de `path` con ajuste y sin video codificado todavía."""
    return [(m, st, src) for m, st, src in plan(path)
            if st and src and not prerendered(path, st, m["width"], m["height"])]


def _encode_unit(path):
    return "ff-wall-enc-" + hashlib.sha1(path.encode()).hexdigest()[:10]


def _partial_prefix(path):
    return hashlib.sha1(path.encode()).hexdigest()[:10] + "-"


def _remove_partials(path):
    try:
        for f in os.listdir(VIDEO_CACHE):
            if f.startswith(_partial_prefix(path)) and f.endswith(".tmp.mp4"):
                os.remove(os.path.join(VIDEO_CACHE, f))
    except OSError:
        pass


def start_encode(path):
    """Lanza `encode` como unidad transitoria de systemd (sobrevive al
    popup y al servicio de la rotación de temas), una por archivo."""
    if not get_option("prerender_video", True) or not pending_encode(path):
        return
    unit = _encode_unit(path)
    cmd = [sys.executable, os.path.realpath(__file__), "encode", path]
    # La descripción guarda el ajuste: reaplicar el mismo fondo (restore,
    # thaw, rotación) no reinicia una codificación que ya va a medias.
    tag = "flipfrog " + hashlib.sha1(json.dumps(load(path), sort_keys=True).encode()).hexdigest()[:12]
    try:
        out = subprocess.run(["systemctl", "--user", "show", f"{unit}.service",
                              "-p", "ActiveState", "-p", "Description", "--value"],
                             capture_output=True, text=True, timeout=10).stdout.split("\n")
        if tag in out and ("active" in out or "activating" in out):
            return
        # Un ajuste nuevo reemplaza la codificación del anterior: sin esto
        # cada "Aplicar" sumaba otra (11 a la vez sobre un video de 1 h).
        subprocess.run(["systemctl", "--user", "stop", f"{unit}.service"], timeout=15,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _remove_partials(path)
        subprocess.run(["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
                        f"--description={tag}", "--nice=10", *cmd], timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _encode_one(path, setting, src_size, out_w, out_h):
    os.makedirs(VIDEO_CACHE, exist_ok=True)
    target = os.path.join(VIDEO_CACHE, _cache_key(path, setting, (out_w, out_h)) + ".mp4")
    tmp = os.path.join(VIDEO_CACHE, _partial_prefix(path) + os.path.basename(target) + ".tmp.mp4")
    graph = filter_graph(setting, *src_size, out_w, out_h, video=True)
    for encoder in ENCODERS:
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", path, "-an", "-sn", "-dn",
               "-filter_complex", f"[0:v:0]{graph}", *encoder, "-movflags", "+faststart", tmp]
        if subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            os.replace(tmp, target)
            return True
    if os.path.exists(tmp):
        os.remove(tmp)
    return False


def encode(path):
    from i18n import t
    common.set_process_name("ff-wall-encode")
    jobs = pending_encode(path)
    if not jobs:
        return
    ok = all(_encode_one(path, st, src, m["width"], m["height"]) for m, st, src in jobs)
    _prune_cache(VIDEO_CACHE, VIDEO_CACHE_MAX)
    import wallpaper
    if ok and wallpaper.current_path() == path:
        subprocess.run([sys.executable, os.path.join(THEMER_DIR, "wallpaper.py"), "set", path])
    name = os.path.basename(path)
    subprocess.run(["notify-send", "-a", "flipfrog", t("fondo", "titulo"),
                    t("fondo", "video_listo" if ok else "video_error", nombre=name)])


if __name__ == "__main__":
    if sys.argv[1:2] == ["encode"] and len(sys.argv) == 3:
        encode(os.path.abspath(os.path.expanduser(sys.argv[2])))
    else:
        sys.exit("uso: wallpaper_fit.py encode <video>")

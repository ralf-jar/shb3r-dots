#!/usr/bin/env python3
"""Collage de ventanas (SUPER+CTRL+TAB) -- vista previa de TODAS las
ventanas abiertas de todos los workspaces, a pantalla completa en su propio
workspace (WORKSPACE, exclusivo: hypr/config/collage-workspace.lua), fondo
transparente.

- Ir a una ventana: clic, o teclado (flechas/Tab + Enter, escribir filtra por
  título/app). Volver a presionar SUPER+CTRL+TAB con el collage abierto
  selecciona la siguiente (SIGUSR1 al proceso vivo); soltar SUPER después de
  eso va a la seleccionada.
- Cerrar una ventana: clic medio o Supr.
- "Por workspace": una zona por workspace; arrastrar una ventana a otra
  zona la mueve a ese workspace (sin llevarse el foco).
- "Personalizado": canvas libre como el del configurador de monitores
  (arrastrar a donde sea con imán a bordes, esquina/rueda = tamaño, solo
  visual). Posiciones guardadas por APP (clase + orden de creación), no por
  ventana: sobreviven a cerrar y volver a abrir la app.

Capturas con `grim -T <stableId>` (ext-foreign-toplevel-list + image-copy-
capture de Hyprland): funciona también con ventanas de workspaces que no se
están mostrando. Se refrescan en vivo mientras el collage está abierto."""

import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, GLibUnix, GioUnix, Pango

GLib.set_prgname("flipfrog-window-collage")  # = APP_ID (app_id de Wayland)

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, os.path.join(os.path.dirname(SCRIPT_DIR), "theme"))
from waybar_lib import kill_group, load_module_css
import common
from i18n import t
import theme_module as tm

CSS_FILE = os.path.join(SCRIPT_DIR, "window_collage.css")
LOCK = "/tmp/window-collage.pid"
STATE_FILE = os.path.expanduser("~/.cache/flipfrog-window-collage.json")
APP_ID = "flipfrog-window-collage"
APP_ICON = "preferences-system-windows"
DESKTOP_FILE = os.path.expanduser(f"~/.local/share/applications/{APP_ID}.desktop")
WORKSPACE = "name:🪟"
GAP = 24
MAX_ROW_FRACTION = 0.55
CAPTURE_MAX_W = 1280
CAPTURE_WORKERS = 4
CAPTURE_TIMEOUT = 3
LIVE_INTERVAL_MS = 1000
LIVE_FULL_EVERY = 5
DRAG_THRESHOLD = 6
SNAP_THRESHOLD = 14
MIN_TILE_W = 140
RESIZE_STEP = 0.08
GRIP_SIZE = 26
REBUILD_DELAY_MS = 120
ZONE_HEADER = 34
ZONE_PAD = 14
ZONE_GAP = 12
CLOSE_RECHECK_S = (0.5, 2.0)

MODES = [
    ("custom", t("ventanas", "modo_personalizado")),
    ("workspace", t("ventanas", "modo_workspace")),
    ("app", t("ventanas", "modo_app")),
]

ARROWS = {
    Gdk.KEY_Left: (-1, 0), Gdk.KEY_Right: (1, 0),
    Gdk.KEY_Up: (0, -1), Gdk.KEY_Down: (0, 1),
}


def _hypr(lua):
    try:
        subprocess.run(["hyprctl", "dispatch", lua], timeout=2,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _hypr_json(*args):
    try:
        out = subprocess.run(["hyprctl", *args, "-j"], capture_output=True,
                             text=True, timeout=2).stdout
        return json.loads(out)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _ws_selector(ws):
    """Numéricos por id, con nombre por "name:" (mismo formato que los
    keybinds)."""
    return str(ws["id"]) if ws["id"] > 0 else f'"name:{ws["name"]}"'


def _focus_workspace(ws):
    _hypr(f"hl.dsp.focus({{ workspace = {_ws_selector(ws)} }})")


def _list_windows():
    clients = _hypr_json("clients")
    if clients is None:
        return None
    return [c for c in clients
            if c.get("mapped", True) and not c.get("hidden")
            and c.get("class") != APP_ID and c["size"][0] > 0 and c["size"][1] > 0]


def _ws_sort_key(win):
    """Numéricos primero en orden, después los de nombre (gaming, 🎬...)."""
    ws = win["workspace"]
    return (0, ws["id"], "") if ws["id"] > 0 else (1, 0, ws["name"])


def _app_keys(windows):
    """{address: "clase#n"} -- n = orden de creación entre las ventanas de
    esa clase (stableId crece con cada ventana nueva). Así "la segunda
    kitty" conserva su lugar aunque se cierre y se vuelva a abrir."""
    by_class = {}
    for win in windows:
        by_class.setdefault(win.get("class") or "?", []).append(win)
    keys = {}
    for cls, wins in by_class.items():
        wins.sort(key=lambda w: int(w.get("stableId") or "0", 16))
        for i, win in enumerate(wins):
            keys[win["address"]] = f"{cls}#{i}"
    return keys


def _capture(win):
    """PPM por stdout (más rápido que PNG), escalado a CAPTURE_MAX_W."""
    scale = min(1.0, CAPTURE_MAX_W / win["size"][0])
    try:
        data = subprocess.run(
            ["grim", "-t", "ppm", "-s", f"{scale:.3f}", "-T", win["stableId"], "-"],
            capture_output=True, timeout=CAPTURE_TIMEOUT).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not data:
        return None
    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(data)
        loader.close()
    except GLib.Error:
        return None
    return loader.get_pixbuf()


def justified_rows(ratios, width, height, gap, max_row_h, steps=200):
    """[(x, y, w, h)] por ratio, en orden. Filas llenadas de izquierda a
    derecha y estiradas al ancho completo (tope max_row_h, filas de menos
    quedan centradas); se prueban varias alturas base y gana la que más
    área cubre dentro de `height`. El bloque entero queda centrado."""
    if not ratios or width <= 0 or height <= 0:
        return [(0, 0, 1, 1)] * len(ratios)

    def pack(base_h):
        rows, row = [], []
        for i, r in enumerate(ratios):
            if row and sum(ratios[j] for j in row + [i]) * base_h + gap * len(row) > width:
                rows.append(row)
                row = []
            row.append(i)
        rows.append(row)
        sized = []
        for row in rows:
            total = sum(ratios[j] for j in row)
            row_h = min((width - gap * (len(row) - 1)) / total, max_row_h)
            sized.append((row, row_h))
        return sized, sum(h for _, h in sized) + gap * (len(sized) - 1)

    # La altura total no crece monótona con la base (cada salto de fila
    # reescala todas), así que barrido completo en vez de búsqueda binaria.
    # Un acomodo que se pasa de alto se encoge parejo; gana el que más área
    # de pantalla cubre ya encogido (dos filas encogidas suelen ganarle a
    # una sola fila chica).
    def area(candidate):
        sized, total = candidate
        shrink = min(1.0, height / total)
        return sum(ratios[j] * h * h for row, h in sized for j in row) * shrink * shrink

    low = min(20, max_row_h)
    candidates = [pack(low + (max_row_h - low) * i / (steps - 1)) for i in range(steps)]
    sized, total = max(candidates, key=area)
    shrink = min(1.0, height / total)

    rects = [None] * len(ratios)
    y = (height - total * shrink) / 2
    for row, row_h in sized:
        row_h *= shrink
        row_w = sum(ratios[j] * row_h for j in row) + gap * (len(row) - 1)
        x = (width - row_w) / 2
        for j in row:
            w = ratios[j] * row_h
            rects[j] = (int(x), int(y), max(1, int(w)), max(1, int(row_h)))
            x += w + gap
        y += row_h + gap * shrink
    return rects


def _rect_area(rects):
    return sum(w * h for _x, _y, w, h in rects)


def _load_state():
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return (data.get("mode", "workspace"), dict(data.get("places", {})),
                list(data.get("z", [])))
    except (OSError, ValueError, AttributeError, TypeError):
        return "workspace", {}, []


def _app_icon_name(win):
    for cls in (win.get("class"), win.get("initialClass")):
        if not cls:
            continue
        for desktop_id in (f"{cls}.desktop", f"{cls.lower()}.desktop"):
            try:
                info = GioUnix.DesktopAppInfo.new(desktop_id)
            except TypeError:
                info = None
            if info and info.get_icon():
                return info.get_icon().to_string()
        if Gtk.IconTheme.get_default().has_icon(cls.lower()):
            return cls.lower()
    return "application-x-executable"


def _running_pid():
    """PID del collage ya abierto (lock vivo y de este script), o None."""
    try:
        with open(LOCK) as f:
            pid = int(f.read().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            if b"window_collage" in f.read():
                return pid
    except (OSError, ValueError):
        pass
    return None


class Tile(Gtk.Box):
    """Una ventana del collage: marco con CSS (.wc-tile) y adentro la
    captura (o el ícono de la app mientras llega / si falla), con el
    workspace arriba-izquierda y el título abajo. Sin ventana GDK propia a
    propósito: los eventos los recibe el EventBox del canvas (hit-test a
    mano), así subirla de capa sacándola y volviéndola a meter al Fixed no
    rompe el grab de un arrastre en curso."""

    def __init__(self, win, active):
        super().__init__()
        self.win = win
        self.source = None
        self._scaled_key = None
        self.get_style_context().add_class("wc-tile")
        if active:
            self.get_style_context().add_class("active")
        self.fixed = Gtk.Fixed()
        self.pack_start(self.fixed, True, True, 0)

        self.image = Gtk.Image()
        self.image.get_style_context().add_class("wc-placeholder")
        self.fixed.put(self.image, 0, 0)

        self.ws_label = Gtk.Label(label=win["workspace"]["name"])
        self.ws_label.get_style_context().add_class("wc-ws")
        self.fixed.put(self.ws_label, 8, 8)

        self.title_row = Gtk.Box()
        self.title = Gtk.Label(label=win.get("title") or win.get("class") or "")
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.title.set_max_width_chars(1)
        self.title.get_style_context().add_class("wc-title")
        self.title_row.set_center_widget(self.title)
        self.fixed.put(self.title_row, 0, 0)

        # Esquina para cambiar el tamaño de la tarjeta (solo en el collage,
        # la ventana real no cambia) -- visible al pasar el mouse.
        self.grip = Gtk.Label(label="◢")
        self.grip.get_style_context().add_class("wc-grip")
        self.fixed.put(self.grip, 0, 0)

        self.icon_name = _app_icon_name(win)

    def set_class(self, name, on):
        ctx = self.get_style_context()
        (ctx.add_class if on else ctx.remove_class)(name)

    def set_workspace(self, ws):
        self.win["workspace"] = ws
        self.ws_label.set_text(ws["name"])

    def set_source(self, pixbuf):
        self.source = pixbuf
        self._scaled_key = None
        self.image.get_style_context().remove_class("wc-placeholder")
        self.resize_content(*self.get_size_request())

    def resize_content(self, w, h):
        border = 4  # 2px de borde por lado (.wc-tile)
        inner_w, inner_h = max(1, w - border), max(1, h - border)
        self.set_size_request(w, h)
        self.image.set_size_request(inner_w, inner_h)
        self.title_row.set_size_request(inner_w, -1)
        self.title.set_max_width_chars(max(1, inner_w // 9))
        self.fixed.move(self.title_row, 0, max(0, inner_h - 34))
        self.fixed.move(self.grip, max(0, inner_w - GRIP_SIZE), max(0, inner_h - GRIP_SIZE))
        key = (inner_w, inner_h, id(self.source))
        if key == self._scaled_key:
            return
        self._scaled_key = key
        if self.source is None:
            size = max(16, min(96, min(inner_w, inner_h) // 3))
            self.image.set_from_icon_name(self.icon_name, Gtk.IconSize.DIALOG)
            self.image.set_pixel_size(size)
            return
        scaled = self.source.scale_simple(inner_w, inner_h, GdkPixbuf.InterpType.BILINEAR)
        self.image.set_from_pixbuf(tm._round_corners(scaled, max(0, tm.current_radius() - 2)))


class WindowCollage:
    def __init__(self):
        # Primero que nada: sin manejador, un SIGUSR1 (atajo repetido) que
        # llegue mientras se arma la ventana mataría el proceso.
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_sigterm)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._on_cycle_signal)
        self.previous_ws = _hypr_json("activeworkspace")
        active = _hypr_json("activewindow") or {}
        self.active_addr = active.get("address")
        self.windows = {w["address"]: w for w in (_list_windows() or [])}
        self.keys = _app_keys(list(self.windows.values()))
        by_key = {k: a for a, k in self.keys.items()}
        self.mode, self.saved_places, saved_z = _load_state()
        # Posiciones por APP ("clase#n"), en fracciones del canvas (sirven en
        # un monitor de otro tamaño). saved_places conserva también las de
        # apps que hoy no están abiertas, para cuando vuelvan.
        self.places = {by_key[k]: tuple(p) for k, p in self.saved_places.items() if k in by_key}
        self.z_order = [by_key[k] for k in saved_z if k in by_key]
        self.z_order += [w["address"] for w in sorted(self.windows.values(), key=_ws_sort_key)
                         if w["address"] not in self.z_order]
        self.rects = {}
        self.zones = []
        self.zone_widgets = []
        self.layout_order = []
        self.filter_text = ""
        self.selected = None
        self._size = (0, 0)
        self._rebuild_id = None
        self._drag = None
        self._hover = None
        self._cycled = False
        self._leaving = False
        self._capturing = set()
        self._live_tick = 0
        self.pool = ThreadPoolExecutor(max_workers=CAPTURE_WORKERS)

        load_module_css(CSS_FILE)
        self.window = Gtk.Window(title=t("ventanas", "titulo"))
        visual = self.window.get_screen().get_rgba_visual()
        if visual:
            self.window.set_visual(visual)
        self.window.set_app_paintable(True)
        self.window.set_name("wc-window")
        self.window.set_icon_name(APP_ICON)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_name("wc-root")
        root.pack_start(self._build_toolbar(), False, False, 0)

        # Un solo EventBox recibe todo (mismo patrón que el canvas del
        # configurador de monitores): clic/arrastre/rueda sobre una tarjeta
        # por hit-test, clic en el vacío cierra (click afuera).
        canvas_box = Gtk.EventBox()
        self.canvas_box = canvas_box
        canvas_box.add_events(Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK
                              | Gdk.EventMask.SMOOTH_SCROLL_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        canvas_box.connect("button-press-event", self._on_press)
        canvas_box.connect("motion-notify-event", self._on_motion)
        canvas_box.connect("button-release-event", self._on_release)
        canvas_box.connect("scroll-event", self._on_scroll)
        canvas_box.connect("leave-notify-event", lambda *_a: self._set_hover(None))
        self.canvas = Gtk.Fixed()
        self.canvas.set_name("wc-canvas")
        self.canvas.connect("size-allocate", self._on_allocate)
        canvas_box.add(self.canvas)
        root.pack_start(canvas_box, True, True, 0)
        self.window.add(root)

        self.tiles = {}
        for addr in self.z_order:
            self._add_tile(addr)
        self.empty_label = Gtk.Label()
        self.empty_label.get_style_context().add_class("wc-empty")
        self.empty_label.set_no_show_all(True)
        self.canvas.put(self.empty_label, 0, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("key-release-event", self._on_key_release)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()
        self._sync_mode_buttons()
        self.search.grab_focus()

        for addr in self.windows:
            self._request_capture(addr)
        GLib.timeout_add(LIVE_INTERVAL_MS, self._on_live_tick)

    def _add_tile(self, addr):
        tile = Tile(self.windows[addr], addr == self.active_addr)
        self.tiles[addr] = tile
        self.canvas.put(tile, 0, 0)

    # ---- barra superior --------------------------------------------------------

    def _build_toolbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_name("wc-toolbar")
        bar.set_halign(Gtk.Align.CENTER)

        title = Gtk.Label(label=t("ventanas", "titulo"))
        title.set_name("wc-heading")
        title.set_tooltip_text(t("ventanas", "ayuda"))
        bar.pack_start(title, False, False, 0)

        self.search = Gtk.Entry()
        self.search.set_name("wc-search")
        self.search.set_placeholder_text(t("ventanas", "buscar_placeholder"))
        self.search.set_width_chars(22)
        self.search.set_tooltip_text(t("ventanas", "ayuda"))
        self.search.connect("changed", self._on_search_changed)
        bar.pack_start(self.search, False, False, 0)

        self.mode_buttons = {}
        for key, label in MODES:
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("wc-chip")
            btn.set_can_focus(False)
            btn.connect("clicked", self._on_mode, key)
            self.mode_buttons[key] = btn
            bar.pack_start(btn, False, False, 0)

        self.reset_btn = Gtk.Button()
        self.reset_btn.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.MENU))
        self.reset_btn.set_always_show_image(True)
        self.reset_btn.set_tooltip_text(t("ventanas", "reiniciar_acomodo"))
        self.reset_btn.get_style_context().add_class("wc-chip")
        self.reset_btn.set_can_focus(False)
        self.reset_btn.connect("clicked", self._on_reset)
        bar.pack_start(self.reset_btn, False, False, 0)

        self.count_label = Gtk.Label()
        self.count_label.set_name("wc-count")
        bar.pack_start(self.count_label, False, False, 0)
        return bar

    def _sync_mode_buttons(self):
        for key, btn in self.mode_buttons.items():
            ctx = btn.get_style_context()
            (ctx.add_class if key == self.mode else ctx.remove_class)("active")
        self.reset_btn.set_sensitive(self.mode == "custom" and bool(self.places))

    def _on_mode(self, _btn, key):
        if key == "custom" and self.mode != "custom" and not self.places:
            self._adopt_current_layout()
        self.mode = key
        self._sync_mode_buttons()
        self._save_state()
        self._layout()

    def _on_reset(self, _btn):
        """Vuelve a partir del acomodo automático (filas por workspace) y
        olvida también los lugares guardados de apps cerradas."""
        self.places = {}
        self.saved_places = {}
        self.rects = self._flat_rects(self._visible())
        self._adopt_current_layout()
        self._sync_mode_buttons()
        self._save_state()
        self._layout()

    def _on_search_changed(self, entry):
        self.filter_text = entry.get_text().strip().lower()
        self._layout()

    def _adopt_current_layout(self):
        """Lo que se ve ahora pasa a ser el punto de partida del canvas
        libre -- solo para las que no tienen lugar propio todavía."""
        width, height = self._size
        if width <= 0 or height <= 0:
            return
        for addr, (x, y, w, _h) in self.rects.items():
            self.places.setdefault(addr, (x / width, y / height, w / width))

    def _save_state(self):
        places = dict(self.saved_places)
        places.update({self.keys[a]: list(p) for a, p in self.places.items() if a in self.keys})
        self.saved_places = places
        try:
            common.atomic_write(STATE_FILE, json.dumps({
                "mode": self.mode, "places": places,
                "z": [self.keys[a] for a in self.z_order if a in self.keys]}))
        except OSError:
            pass

    # ---- acomodo ---------------------------------------------------------------

    def _on_allocate(self, _canvas, alloc):
        if abs(alloc.width - self._size[0]) > 4 or abs(alloc.height - self._size[1]) > 4:
            self._size = (alloc.width, alloc.height)
            if self._rebuild_id:
                GLib.source_remove(self._rebuild_id)
            self._rebuild_id = GLib.timeout_add(REBUILD_DELAY_MS, self._on_rebuild)

    def _on_rebuild(self):
        self._rebuild_id = None
        self._layout()
        return False

    def _ratio(self, addr):
        w, h = self.windows[addr]["size"]
        return w / h

    def _matches(self, addr):
        if not self.filter_text:
            return True
        win = self.windows[addr]
        haystack = " ".join((win.get("title") or "", win.get("class") or "",
                             win["workspace"]["name"])).lower()
        return all(word in haystack for word in self.filter_text.split())

    def _visible(self):
        return [a for a in self.z_order if self._matches(a)]

    def _flat_rects(self, addrs, by_app=False):
        wins = [self.windows[a] for a in addrs]
        if by_app:
            wins.sort(key=lambda w: ((w.get("class") or "").lower(), _ws_sort_key(w)))
        else:
            wins.sort(key=_ws_sort_key)
        order = [w["address"] for w in wins]
        width, height = self._size
        rects = justified_rows([self._ratio(a) for a in order], width, height, GAP,
                               height * MAX_ROW_FRACTION)
        return dict(zip(order, rects))

    def _zone_rects(self, addrs):
        """"Por workspace": una zona (celda de una rejilla pareja) por
        workspace con ventanas visibles, y adentro sus ventanas en filas
        justificadas. Se prueba cada número de columnas y gana el que más
        área de tarjetas deja. -> (rects, zones)."""
        groups = {}
        for addr in sorted(addrs, key=lambda a: _ws_sort_key(self.windows[a])):
            ws = self.windows[addr]["workspace"]
            groups.setdefault(ws["name"], (ws, []))[1].append(addr)
        groups = list(groups.values())
        if not groups:
            return {}, []
        width, height = self._size
        n = len(groups)

        def build(cols):
            rows = math.ceil(n / cols)
            cell_w = (width - GAP * (cols - 1)) / cols
            cell_h = (height - GAP * (rows - 1)) / rows
            inner_w = cell_w - 2 * ZONE_PAD
            inner_h = cell_h - ZONE_HEADER - ZONE_PAD
            if inner_w < 60 or inner_h < 40:
                return None
            rects, zones = {}, []
            for i, (ws, members) in enumerate(groups):
                row, col = divmod(i, cols)
                in_row = min(cols, n - row * cols)
                offset = (width - (in_row * cell_w + GAP * (in_row - 1))) / 2
                zx = offset + col * (cell_w + GAP)
                zy = row * (cell_h + GAP)
                zones.append({"ws": ws, "rect": (int(zx), int(zy), int(cell_w), int(cell_h))})
                inner = justified_rows([self._ratio(a) for a in members], inner_w, inner_h,
                                       ZONE_GAP, inner_h, steps=60)
                for addr, (x, y, w, h) in zip(members, inner):
                    rects[addr] = (int(zx + ZONE_PAD + x), int(zy + ZONE_HEADER + y), w, h)
            return rects, zones

        layouts = [lay for lay in (build(c) for c in range(1, n + 1)) if lay]
        if not layouts:
            return self._flat_rects(addrs), []
        return max(layouts, key=lambda lay: _rect_area(lay[0].values()))

    def _custom_rects(self, addrs):
        width, height = self._size
        missing = [a for a in addrs if a not in self.places]
        fallback = self._flat_rects(missing) if missing else {}
        rects = {}
        for addr in addrs:
            if addr not in self.places:
                rects[addr] = fallback[addr]
                continue
            fx, fy, fw = self.places[addr]
            w = max(MIN_TILE_W, int(fw * width))
            h = int(w / self._ratio(addr))
            rects[addr] = self._clamp(int(fx * width), int(fy * height), w, h)
        return rects

    def _clamp(self, x, y, w, h):
        width, height = self._size
        return (int(min(max(0, x), max(0, width - w))),
                int(min(max(0, y), max(0, height - h))), w, h)

    def _layout(self):
        width, height = self._size
        if width <= 0 or height <= 0:
            return
        visible = self._visible()
        for addr, tile in self.tiles.items():
            tile.set_visible(addr in visible)
        self.zones = []
        if self.mode == "custom":
            self.rects = self._custom_rects(visible)
        elif self.mode == "workspace":
            self.rects, self.zones = self._zone_rects(visible)
        else:
            self.rects = self._flat_rects(visible, by_app=True)
        self._build_zone_widgets()
        for addr, rect in self.rects.items():
            self._place(addr, rect)
        # Orden de lectura para Tab: zona por zona, y dentro de arriba hacia
        # abajo / izquierda a derecha.
        zone_index = {z["ws"]["name"]: i for i, z in enumerate(self.zones)}
        self.layout_order = sorted(self.rects, key=lambda a: (
            zone_index.get(self.windows[a]["workspace"]["name"], 0),
            self.rects[a][1] // 40, self.rects[a][0]))
        self._restack()

        self.count_label.set_text(f"{len(visible)}/{len(self.windows)}" if self.filter_text
                                  else str(len(self.windows)))
        if not visible:
            key = "sin_coincidencias" if self.windows else "sin_ventanas"
            self.empty_label.set_text(t("ventanas", key))
            self.empty_label.show()
            self.canvas.move(self.empty_label, width // 2 - 100, height // 2)
        else:
            self.empty_label.hide()
        if self.selected not in self.rects:
            first = self.active_addr if self.active_addr in self.rects else None
            self._select(first or (self.layout_order[0] if self.layout_order else None))
        self.canvas.queue_draw()

    def _build_zone_widgets(self):
        for widget in self.zone_widgets:
            self.canvas.remove(widget)
        self.zone_widgets = []
        for zone in self.zones:
            x, y, w, h = zone["rect"]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.get_style_context().add_class("wc-zone")
            box.set_size_request(w, h)
            label = Gtk.Label(label=t("ventanas", "zona_workspace", name=zone["ws"]["name"]))
            label.set_xalign(0)
            label.get_style_context().add_class("wc-zone-title")
            box.pack_start(label, False, False, 0)
            zone["widget"] = box
            self.zone_widgets.append(box)
            self.canvas.put(box, x, y)

    def _place(self, addr, rect):
        x, y, w, h = rect
        self.rects[addr] = rect
        tile = self.tiles[addr]
        tile.resize_content(w, h)
        self.canvas.move(tile, x, y)

    def _restack(self):
        """Gtk.Fixed pinta en orden de hijos: zonas hasta abajo, luego las
        tarjetas en z_order (la de hasta arriba, la última)."""
        for widget in self.zone_widgets:
            self.canvas.remove(widget)
        for zone in self.zones:
            x, y, _w, _h = zone["rect"]
            self.canvas.put(zone["widget"], x, y)
        for addr in self.z_order:
            tile = self.tiles[addr]
            x, y, _w, _h = self.rects.get(addr, (0, 0, 0, 0))
            self.canvas.remove(tile)
            self.canvas.put(tile, x, y)
            tile.set_visible(addr in self.rects)
            if addr in self.rects:
                tile.show_all()
        for widget in self.zone_widgets:
            widget.show_all()

    def _hit(self, x, y):
        for addr in reversed(self.z_order):
            if addr not in self.rects:
                continue
            rx, ry, rw, rh = self.rects[addr]
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return addr
        return None

    def _zone_at(self, x, y):
        for zone in self.zones:
            zx, zy, zw, zh = zone["rect"]
            if zx <= x <= zx + zw and zy <= y <= zy + zh:
                return zone
        return None

    def _in_grip(self, addr, x, y):
        rx, ry, rw, rh = self.rects[addr]
        return x >= rx + rw - GRIP_SIZE and y >= ry + rh - GRIP_SIZE

    def _set_cursor(self, name):
        gdk_window = self.canvas_box.get_window()
        if gdk_window is not None and name != getattr(self, "_cursor_name", None):
            self._cursor_name = name
            cursor = Gdk.Cursor.new_from_name(gdk_window.get_display(), name) if name else None
            gdk_window.set_cursor(cursor)

    def _set_hover(self, addr):
        if addr == self._hover:
            return False
        if self._hover in self.tiles:
            self.tiles[self._hover].set_class("hover", False)
        self._hover = addr
        if addr is not None:
            self.tiles[addr].set_class("hover", True)
            self._select(addr)
        return False

    def _select(self, addr):
        if self.selected in self.tiles:
            self.tiles[self.selected].set_class("selected", False)
        self.selected = addr
        if addr in self.tiles:
            self.tiles[addr].set_class("selected", True)
            self._request_capture(addr)

    def _snap(self, addr, x, y, w, h):
        """Imán a los bordes del canvas y de las otras ventanas (pegada con
        GAP de separación o alineada) -- mismo criterio que el canvas del
        configurador de monitores."""
        width, height = self._size
        xs, ys = [0, width - w], [0, height - h]
        for other, (ox, oy, ow, oh) in self.rects.items():
            if other == addr:
                continue
            xs += [ox - w - GAP, ox + ow + GAP, ox, ox + ow - w]
            ys += [oy - h - GAP, oy + oh + GAP, oy, oy + oh - h]
        best_x = min(xs, key=lambda c: abs(c - x))
        best_y = min(ys, key=lambda c: abs(c - y))
        if abs(best_x - x) < SNAP_THRESHOLD:
            x = best_x
        if abs(best_y - y) < SNAP_THRESHOLD:
            y = best_y
        return x, y

    def _raise(self, addr):
        self.z_order.remove(addr)
        self.z_order.append(addr)
        self._restack()

    def _to_custom(self, addr=None):
        """Mover/redimensionar desde un modo automático lo pasa a
        "Personalizado" partiendo de lo que se ve, y la ventana tocada queda
        encima de las demás."""
        if self.mode != "custom":
            self.places = {}
            self.mode = "custom"
            self.zones = []
            self._build_zone_widgets()
        self._adopt_current_layout()
        self._sync_mode_buttons()
        if addr is not None:
            self._raise(addr)

    def _commit(self, addr):
        width, height = self._size
        x, y, w, _h = self.rects[addr]
        self.places[addr] = (x / width, y / height, w / width)
        self._sync_mode_buttons()
        self._save_state()

    # ---- capturas en vivo ------------------------------------------------------

    def _request_capture(self, addr):
        if self._leaving or addr in self._capturing or addr not in self.windows:
            return
        self._capturing.add(addr)
        self.pool.submit(self._capture_one, dict(self.windows[addr]))

    def _capture_one(self, win):
        pixbuf = None if self._leaving else _capture(win)
        GLib.idle_add(self._set_capture, win["address"], pixbuf)

    def _set_capture(self, addr, pixbuf):
        self._capturing.discard(addr)
        tile = self.tiles.get(addr)
        if pixbuf is not None and tile is not None and not self._leaving:
            tile.set_source(pixbuf)
        return False

    def _on_live_tick(self):
        """Cada LIVE_INTERVAL_MS la seleccionada (y la del mouse); cada
        LIVE_FULL_EVERY ticks, todas las visibles. _capturing evita apilar
        capturas si una tarda más que el intervalo."""
        if self._leaving:
            return False
        self._live_tick += 1
        targets = {self.selected, self._hover}
        if self._live_tick % LIVE_FULL_EVERY == 0:
            targets |= set(self.rects)
        for addr in targets:
            if addr in self.rects:
                self._request_capture(addr)
        return True

    # ---- acciones sobre ventanas -----------------------------------------------

    def _go_to(self, win):
        """Enfocar una ventana de OTRO monitor solo cambia ese monitor: el
        del collage se quedaría mostrando "🪟" vacío (visible, así que
        Hyprland no lo destruye). Primero regresar este monitor a su
        workspace de antes."""
        self._leaving = True
        prev = self.previous_ws
        if prev and win.get("monitor") != prev.get("monitorID"):
            _focus_workspace(prev)
        _hypr(f'hl.dsp.focus({{ window = "address:{win["address"]}" }})')
        self.window.destroy()

    def _close_window(self, addr):
        """Clic medio / Supr. La ventana puede pedir confirmación (kitty con
        un proceso corriendo) -- se vuelve a revisar la lista un par de
        veces en vez de quitar la tarjeta a ciegas."""
        self.tiles[addr].set_class("closing", True)

        def worker():
            _hypr(f'hl.dsp.window.close({{ window = "address:{addr}" }})')
            for delay in CLOSE_RECHECK_S:
                time.sleep(delay)
                wins = _list_windows()
                if wins is not None:
                    GLib.idle_add(self._sync_closed, {w["address"] for w in wins}, addr)

        threading.Thread(target=worker, daemon=True).start()

    def _sync_closed(self, alive, addr):
        if self._leaving:
            return False
        for gone in [a for a in self.windows if a not in alive]:
            self._remove_tile(gone)
        if addr in self.tiles and addr in alive:
            self.tiles[addr].set_class("closing", False)
        self._layout()
        return False

    def _remove_tile(self, addr):
        tile = self.tiles.pop(addr)
        self.canvas.remove(tile)
        self.windows.pop(addr, None)
        self.places.pop(addr, None)
        self.rects.pop(addr, None)
        if addr in self.z_order:
            self.z_order.remove(addr)
        if self._hover == addr:
            self._hover = None
        if self.selected == addr:
            self.selected = None

    def _move_to_workspace(self, addr, ws):
        """Soltar en otra zona de "Por workspace": mueve la ventana sin
        llevarse el foco (follow = false) y reacomoda ya con el workspace
        nuevo; el monitor real se relee después (lo usa _go_to)."""
        self.tiles[addr].set_workspace(dict(ws))

        def worker():
            _hypr(f'hl.dsp.window.move({{ workspace = {_ws_selector(ws)}, '
                  f'window = "address:{addr}", follow = false }})')
            wins = _list_windows()
            if wins is not None:
                GLib.idle_add(self._refresh_window_info, {w["address"]: w for w in wins})

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_window_info(self, fresh):
        for addr, win in fresh.items():
            if addr in self.windows:
                self.windows[addr].update(monitor=win["monitor"], workspace=win["workspace"])
        return False

    # ---- mouse -------------------------------------------------------------------

    def _on_press(self, _box, event):
        if event.type != Gdk.EventType.BUTTON_PRESS or event.button not in (1, 2):
            return event.button == 1
        addr = self._hit(event.x, event.y)
        if event.button == 2:
            self._drag = {"addr": addr, "middle": True}
            return True
        if addr is None:
            self._drag = {"addr": None}
            return True
        x, y, w, _h = self.rects[addr]
        resize = self._in_grip(addr, event.x, event.y)
        self._drag = {"addr": addr, "sx": event.x, "sy": event.y,
                      "ox": x, "oy": y, "ow": w, "moving": False, "resize": resize,
                      "zone_move": self.mode == "workspace" and not resize}
        return True

    def _on_motion(self, _box, event):
        drag = self._drag
        if not drag or drag["addr"] is None or drag.get("middle"):
            hover = self._hit(event.x, event.y)
            self._set_hover(hover)
            grip = hover is not None and self._in_grip(hover, event.x, event.y)
            self._set_cursor("se-resize" if grip else None)
            return False
        addr = drag["addr"]
        dx, dy = event.x - drag["sx"], event.y - drag["sy"]
        if not drag["moving"]:
            if abs(dx) < DRAG_THRESHOLD and abs(dy) < DRAG_THRESHOLD:
                return True
            drag["moving"] = True
            self.tiles[addr].set_class("dragging", True)
            if drag["zone_move"]:
                self._raise(addr)
            else:
                self._to_custom(addr)
        if drag["resize"]:
            self._resize_from_grip(addr, drag, dx, dy)
            return True
        _x, _y, w, h = self.rects[addr]
        if drag["zone_move"]:
            self._place(addr, self._clamp(drag["ox"] + dx, drag["oy"] + dy, w, h))
            target = self._zone_at(event.x, event.y)
            for zone in self.zones:
                ctx = zone["widget"].get_style_context()
                (ctx.add_class if zone is target else ctx.remove_class)("drop")
            return True
        x, y = self._snap(addr, drag["ox"] + dx, drag["oy"] + dy, w, h)
        self._place(addr, self._clamp(x, y, w, h))
        return True

    def _resize_from_grip(self, addr, drag, dx, dy):
        """Esquina de arriba-izquierda fija, proporción de la ventana real
        (manda el eje que más se movió). Solo la tarjeta: nunca se toca el
        tamaño de la ventana en Hyprland."""
        width, height = self._size
        ratio = self._ratio(addr)
        x, y = drag["ox"], drag["oy"]
        new_w = drag["ow"] + max(dx, dy * ratio)
        new_w = max(MIN_TILE_W, min(new_w, width - x, (height - y) * ratio))
        self._place(addr, (x, y, int(new_w), int(new_w / ratio)))

    def _on_release(self, _box, event):
        drag, self._drag = self._drag, None
        if not drag or event.button not in (1, 2):
            return False
        addr = drag["addr"]
        if drag.get("middle"):
            if event.button == 2 and addr is not None and self._hit(event.x, event.y) == addr:
                self._close_window(addr)
            return True
        if event.button != 1:
            return False
        if addr is None:
            # Clic en el vacío (presionado Y soltado ahí) cierra.
            if self._hit(event.x, event.y) is None:
                self.window.destroy()
        elif drag["moving"]:
            self.tiles[addr].set_class("dragging", False)
            if drag["zone_move"]:
                target = self._zone_at(event.x, event.y)
                if target and target["ws"]["name"] != self.windows[addr]["workspace"]["name"]:
                    self._move_to_workspace(addr, target["ws"])
                self._layout()
            else:
                self._commit(addr)
        elif not drag["resize"]:
            self._go_to(self.windows[addr])
        return True

    def _on_scroll(self, _box, event):
        """Rueda sobre una tarjeta = su tamaño, con el centro fijo."""
        addr = self._hit(event.x, event.y)
        if self._drag or addr is None:
            return True
        ok, _dx, dy = event.get_scroll_deltas()
        if not ok:
            if event.direction == Gdk.ScrollDirection.UP:
                dy = -1
            elif event.direction == Gdk.ScrollDirection.DOWN:
                dy = 1
            else:
                return False
        if dy == 0:
            return True
        self._to_custom(addr)
        x, y, w, h = self.rects[addr]
        width, height = self._size
        ratio = self._ratio(addr)
        new_w = w * (1 - RESIZE_STEP * dy)
        new_w = max(MIN_TILE_W, min(new_w, width, height * ratio))
        new_h = new_w / ratio
        rect = self._clamp(x + (w - new_w) / 2, y + (h - new_h) / 2, int(new_w), int(new_h))
        self._place(addr, rect)
        self._commit(addr)
        return True

    # ---- teclado -------------------------------------------------------------------

    def _cycle(self, step):
        order = self.layout_order
        if not order:
            return
        idx = order.index(self.selected) if self.selected in order else -1
        self._select(order[(idx + step) % len(order)])

    def _move_spatial(self, dx, dy):
        """La tarjeta más cercana en esa dirección (distancia a lo largo +
        el doble de la desviación lateral)."""
        if self.selected not in self.rects:
            self._cycle(1)
            return
        sx, sy, sw, sh = self.rects[self.selected]
        cx, cy = sx + sw / 2, sy + sh / 2
        best, best_score = None, None
        for addr, (x, y, w, h) in self.rects.items():
            if addr == self.selected:
                continue
            vx, vy = x + w / 2 - cx, y + h / 2 - cy
            along = vx * dx + vy * dy
            if along <= 1:
                continue
            score = along + 2 * abs(vx * dy - vy * dx)
            if best_score is None or score < best_score:
                best, best_score = addr, score
        if best:
            self._select(best)

    def _on_key(self, _widget, event):
        key = event.keyval
        if key == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.window.destroy()
            return True
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.selected in self.rects:
                self._go_to(self.windows[self.selected])
            return True
        if key == Gdk.KEY_Tab:
            self._cycle(1)
            return True
        if key == Gdk.KEY_ISO_Left_Tab:
            self._cycle(-1)
            return True
        if key in ARROWS:
            self._move_spatial(*ARROWS[key])
            return True
        if key == Gdk.KEY_Delete and not self.search.get_text() and self.selected in self.rects:
            self._close_window(self.selected)
            return True
        return False

    def _on_cycle_signal(self):
        """SUPER+CTRL+TAB con el collage ya abierto (ver main())."""
        if not hasattr(self, "tiles"):
            return True
        self._cycled = True
        self._cycle(1)
        return True

    def _on_key_release(self, _widget, event):
        # Estilo Alt+Tab: solo después de haber avanzado con el atajo, así
        # soltar SUPER justo al abrirlo no manda a ningún lado.
        if (self._cycled and event.keyval in (Gdk.KEY_Super_L, Gdk.KEY_Super_R)
                and self.selected in self.rects):
            self._go_to(self.windows[self.selected])
            return True
        return False

    # ---- ciclo de vida -----------------------------------------------------------

    def _on_sigterm(self):
        self.window.destroy()
        return False

    def _on_destroy(self, *_args):
        # Al ir a una ventana, el foco ya se movió solo; si no, regresar al
        # workspace de antes (si el usuario sigue en el del collage).
        if not self._leaving:
            current = _hypr_json("activeworkspace")
            if self.previous_ws and current and current.get("name") == WORKSPACE[len("name:"):]:
                _focus_workspace(self.previous_ws)
        self._leaving = True
        self.pool.shutdown(wait=False, cancel_futures=True)
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def ensure_desktop_entry():
    """Waybar (workspace-taskbar / wlr/taskbar) busca <app_id>.desktop para
    el ícono -- mismo criterio que theme_gallery.py."""
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={t('ventanas', 'titulo')}\n"
        f"Exec=python3 {os.path.realpath(__file__)}\n"
        f"Icon={APP_ICON}\n"
        f"StartupWMClass={APP_ID}\n"
        "NoDisplay=true\n"
    )
    try:
        with open(DESKTOP_FILE) as f:
            if f.read() == content:
                return
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(DESKTOP_FILE), exist_ok=True)
        common.atomic_write(DESKTOP_FILE, content)
    except OSError:
        pass


def main():
    # Ya abierto: el atajo repetido avanza la selección en vez de cerrarlo
    # (se cierra con Esc / clic afuera / Enter).
    pid = _running_pid()
    if pid:
        os.kill(pid, signal.SIGUSR1)
        return
    ensure_desktop_entry()
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    WindowCollage()
    Gtk.main()


if __name__ == "__main__":
    main()

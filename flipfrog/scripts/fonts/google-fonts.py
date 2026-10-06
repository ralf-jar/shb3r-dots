#!/usr/bin/env python3
"""Popup para explorar Google Fonts: busca, previsualiza en vivo, e
instala para todo el sistema. Ver CLAUDE.md "Google Fonts" (catálogo,
vista previa transitoria, instalación real)."""

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("PangoFc", "1.0")
from gi.repository import Gtk, Gdk, GLib, PangoCairo, PangoFc
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
import common
from i18n import t
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CSS_FILE = os.path.join(SCRIPT_DIR, "google-fonts.css")
LOCK = "/tmp/google-fonts.pid"

CACHE_DIR = os.path.expanduser("~/.cache/waybar-google-fonts")
METADATA_CACHE = os.path.join(CACHE_DIR, "metadata.json")
METADATA_MAX_AGE = 7 * 24 * 3600  # el catálogo no cambia tan seguido
PREVIEW_CACHE_DIR = os.path.join(CACHE_DIR, "preview")
INSTALL_DIR = os.path.expanduser("~/.local/share/fonts/GoogleFonts")

METADATA_URL = "https://fonts.google.com/metadata/fonts"
CSS_API_URL = "https://fonts.googleapis.com/css2"

PAGE_SIZE = 60  # cuántos resultados se muestran por tanda -- "Cargar más" pide la siguiente
SAMPLE_SPECIMEN = t("fuentes", "muestra_especimen")
SAMPLE_PANGRAM = t("fuentes", "muestra_pangrama")

FONT_FACE_RE = re.compile(r"@font-face\s*{([^}]*)}", re.S)
WEIGHT_RE = re.compile(r"font-weight:\s*(\d+)")
STYLE_RE = re.compile(r"font-style:\s*(\w+)")
URL_RE = re.compile(r"url\(([^)]+)\)")


# ---------- Red / catálogo ----------

def _http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _read_metadata_cache():
    try:
        with open(METADATA_CACHE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _fetch_metadata_remote():
    raw = _http_get(METADATA_URL).decode("utf-8")
    # Saltar hasta el primer '{' por si el endpoint antepone basura
    # anti-hijacking (no confirmado que este lo haga, es gratis igual).
    data = json.loads(raw[raw.index("{"):])
    families = data.get("familyMetadataList", [])
    os.makedirs(CACHE_DIR, exist_ok=True)
    common.atomic_write(METADATA_CACHE, json.dumps(families))
    return families


def load_metadata():
    """Cache-first: si hay cache y tiene menos de 7 días, la usa tal cual.
    Si hay que refrescar pero no hay internet, prefiere la cache vieja
    (aunque esté vencida) antes que dejar el popup sin catálogo."""
    cached = _read_metadata_cache()
    if cached is not None:
        try:
            age = time.time() - os.path.getmtime(METADATA_CACHE)
        except OSError:
            age = METADATA_MAX_AGE + 1
        if age < METADATA_MAX_AGE:
            return cached
    try:
        return _fetch_metadata_remote()
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        if cached is not None:
            return cached
        raise


def filter_families(all_families, query):
    """Devuelve TODOS los matches (sin recortar) -- la paginación ("Cargar
    más") la maneja GoogleFontsPopup._render_results sobre esta lista."""
    q = query.strip().lower()
    matches = all_families if not q else [
        f for f in all_families if q in f["family"].lower()
    ]
    return sorted(matches, key=lambda f: f.get("popularity") or 999999)


# ---------- CSS API de Google Fonts (descarga de archivos reales) ----------

def _parse_weight_key(key):
    italic = key.endswith("i")
    weight = int(key[:-1] if italic else key)
    return italic, weight


def _css_family_param(family, weight_keys):
    pairs = sorted({_parse_weight_key(k) for k in weight_keys})
    axis = ";".join(f"{1 if italic else 0},{weight}" for italic, weight in pairs)
    return f"{family}:ital,wght@{axis}"


def _fetch_font_faces(family, weight_keys=None):
    """CSS de la familia (todos los pesos en weight_keys, o solo 400) ->
    [(weight, italic, url), ...]. Sirve .ttf con el User-Agent default,
    sin fingir uno viejo."""
    family_param = (
        _css_family_param(family, weight_keys) if weight_keys else family
    )
    url = f"{CSS_API_URL}?family={urllib.parse.quote_plus(family_param)}&display=swap"
    css_text = _http_get(url).decode("utf-8")
    faces = []
    for block in FONT_FACE_RE.findall(css_text):
        weight_m = WEIGHT_RE.search(block)
        style_m = STYLE_RE.search(block)
        url_m = URL_RE.search(block)
        if not (weight_m and url_m):
            continue
        weight = int(weight_m.group(1))
        italic = bool(style_m and style_m.group(1) == "italic")
        faces.append((weight, italic, url_m.group(1)))
    return faces


def _download_to(url, dest_path):
    data = _http_get(url)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    common.atomic_write(dest_path, data, binary=True)


# ---------- Vista previa (fuente transitoria, no instalada) ----------

def fetch_preview_file(family):
    """Baja (o reusa de cache) el regular 400 de `family` y devuelve la
    ruta local. Cache persistente por nombre de familia -- si ya se
    previsualizó antes no hace falta volver a bajarlo."""
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", family)
    dest = os.path.join(PREVIEW_CACHE_DIR, f"{safe_name}.ttf")
    if os.path.exists(dest):
        return dest
    faces = _fetch_font_faces(family)
    if not faces:
        raise RuntimeError(t("fuentes", "sin_archivo_preview", family=family))
    # Preferir 400 no-italic si está, si no el primero que haya.
    face = next((f for f in faces if f[0] == 400 and not f[1]), faces[0])
    _download_to(face[2], dest)
    return dest


def register_preview_font(path):
    """App-font transitorio de ESTE proceso, sin instalar ni tocar
    fontconfig -- ver CLAUDE.md "Google Fonts" (por qué no el ctypes
    equivalente)."""
    fontmap = PangoCairo.FontMap.get_default()
    fontmap.add_font_file(path)


# ---------- Instalación real (todo el sistema) ----------

def is_installed(family):
    d = os.path.join(INSTALL_DIR, family)
    return os.path.isdir(d) and any(
        fn.lower().endswith((".ttf", ".otf")) for fn in os.listdir(d)
    )


def install_family(family, weight_keys, progress_cb=None):
    """Baja todos los estilos de `family` a
    ~/.local/share/fonts/GoogleFonts/<Familia>/ y corre fc-cache.
    progress_cb(done, total) se llama tras cada archivo, pensado para
    actualizar un label desde GLib.idle_add mientras corre en un hilo
    aparte."""
    faces = _fetch_font_faces(family, weight_keys)
    if not faces:
        raise RuntimeError(t("fuentes", "sin_archivos_instalar", family=family))
    dest_dir = os.path.join(INSTALL_DIR, family)
    total = len(faces)
    for i, (weight, italic, url) in enumerate(faces, start=1):
        ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1] or ".ttf"
        suffix = "italic" if italic else ""
        dest = os.path.join(dest_dir, f"{family}-{weight}{suffix}{ext}")
        _download_to(url, dest)
        if progress_cb:
            progress_cb(i, total)
    subprocess.run(["fc-cache", "-f", dest_dir],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return total


class GoogleFontsPopup:
    def __init__(self):
        self.window, container = build_layer_window("google-fonts", CSS_FILE)
        container.set_name("container")
        position_fixed_top(container, margin=60)

        header = Gtk.Label(label=t("fuentes", "header_titulo"))
        header.set_name("header")
        header.set_halign(Gtk.Align.CENTER)
        container.pack_start(header, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text(t("fuentes", "buscar_placeholder"))
        self.search_entry.connect("search-changed", self._on_search_changed)
        container.pack_start(self.search_entry, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(280)
        scroller.set_propagate_natural_height(True)
        scroller.set_name("results-scroller")

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        scroller.add(self.list_box)
        container.pack_start(scroller, False, False, 0)

        # ---- Vista previa ----
        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        preview_box.set_name("preview-box")

        self.family_label = Gtk.Label(label=t("fuentes", "elige_fuente"))
        self.family_label.set_name("family-label")
        self.family_label.set_halign(Gtk.Align.START)
        preview_box.pack_start(self.family_label, False, False, 0)

        # max_width_chars=1: sin esto un Label con wrap=True igual pide
        # su ancho natural sin wrappear, que varía con cada fuente y
        # hace crecer/achicar la ventana. halign=FILL (no START) para
        # que aun así reciba el ancho completo del padre.
        self.specimen_label = Gtk.Label(label="")
        self.specimen_label.get_style_context().add_class("gf-specimen")
        self.specimen_label.set_halign(Gtk.Align.FILL)
        self.specimen_label.set_line_wrap(True)
        self.specimen_label.set_max_width_chars(1)
        preview_box.pack_start(self.specimen_label, False, False, 0)

        self.pangram_label = Gtk.Label(label="")
        self.pangram_label.get_style_context().add_class("gf-pangram")
        self.pangram_label.set_halign(Gtk.Align.FILL)
        self.pangram_label.set_line_wrap(True)
        self.pangram_label.set_max_width_chars(1)
        preview_box.pack_start(self.pangram_label, False, False, 0)

        container.pack_start(preview_box, False, False, 0)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        action_row.set_name("action-row")

        self.status_label = Gtk.Label(label="")
        self.status_label.set_name("status-label")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_hexpand(True)
        action_row.pack_start(self.status_label, True, True, 0)

        self.install_btn = Gtk.Button(label=t("fuentes", "descargar_instalar"))
        self.install_btn.get_style_context().add_class("install-btn")
        self.install_btn.set_sensitive(False)
        self.install_btn.connect("clicked", self._on_install_clicked)
        action_row.pack_start(self.install_btn, False, False, 0)

        container.pack_start(action_row, False, False, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

        self._all_families = []
        self._by_name = {}
        self._result_btns = {}
        self._current_matches = []
        self._shown_count = 0
        self._load_more_btn = None
        self._selected = None
        self._specimen_provider = None
        self._load_catalog_async()

    # ---- Catálogo ----

    def _load_catalog_async(self):
        self._set_status(t("fuentes", "cargando_catalogo"))

        def worker():
            try:
                families = load_metadata()
                error = None
            except Exception as e:
                families = []
                error = str(e)
            GLib.idle_add(self._on_catalog_loaded, families, error)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _on_catalog_loaded(self, families, error):
        if error:
            self._set_status(t("fuentes", "error_catalogo", error=error))
            return False
        self._all_families = families
        self._by_name = {f["family"]: f for f in families}
        self._set_status("")
        self._apply_filter("")
        return False

    def _on_search_changed(self, entry):
        if not self._all_families:
            return
        self._apply_filter(entry.get_text())

    def _apply_filter(self, query):
        """Nueva búsqueda: recalcula los matches completos y rearranca la
        paginación desde cero (a diferencia de _render_results(reset=False),
        que Cargar más usa para sumar la próxima tanda sin perder lo ya
        pintado)."""
        self._current_matches = filter_families(self._all_families, query)
        self._shown_count = 0
        self._render_results(reset=True)

    def _render_results(self, reset):
        """reset=True: búsqueda nueva, vacía la lista y arranca la
        paginación desde cero. reset=False: "Cargar más" clickeado, suma la
        próxima tanda de PAGE_SIZE sin tocar lo ya pintado (así no se pierde
        la posición del scroll)."""
        if reset:
            for child in self.list_box.get_children():
                self.list_box.remove(child)
            self._result_btns = {}
            self._load_more_btn = None
        elif self._load_more_btn is not None:
            self.list_box.remove(self._load_more_btn)
            self._load_more_btn = None

        matches = self._current_matches
        if not matches:
            empty = Gtk.Label(label=t("fuentes", "sin_resultados"))
            empty.get_style_context().add_class("gf-empty")
            self.list_box.pack_start(empty, False, False, 0)
            self.list_box.show_all()
            return

        page = matches[self._shown_count:self._shown_count + PAGE_SIZE]
        for f in page:
            name = f["family"]
            btn = Gtk.Button()
            btn.set_halign(Gtk.Align.FILL)
            btn.get_style_context().add_class("gf-result-btn")
            if name == self._selected:
                btn.get_style_context().add_class("active")

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=name)
            label.set_halign(Gtk.Align.START)
            label.set_hexpand(True)
            row.pack_start(label, True, True, 0)

            tooltip = f"{name} — {f.get('category', '')}"
            if is_installed(name):
                check = Gtk.Label(label="✓")
                check.get_style_context().add_class("gf-installed-check")
                row.pack_start(check, False, False, 0)
                tooltip += " " + t("fuentes", "ya_instalada_tooltip")
            btn.set_tooltip_text(tooltip)

            btn.add(row)
            btn.connect("clicked", self._on_family_selected, name)
            btn.connect("focus-in-event", self._on_family_focus, name)
            self.list_box.pack_start(btn, False, False, 0)
            self._result_btns[name] = btn
        self._shown_count += len(page)

        if self._shown_count < len(matches):
            remaining = len(matches) - self._shown_count
            self._load_more_btn = Gtk.Button(label=t("fuentes", "cargar_mas", n=remaining))
            self._load_more_btn.get_style_context().add_class("gf-load-more-btn")
            self._load_more_btn.connect(
                "clicked", lambda _b: self._render_results(reset=False)
            )
            self.list_box.pack_start(self._load_more_btn, False, False, 0)

        self.list_box.show_all()

    # ---- Selección + vista previa ----

    def _on_family_focus(self, btn, _event, name):
        """Mover el foco con las flechas arriba/abajo entre los botones de
        resultado ya funcionaba (navegación estándar de GTK), pero no
        disparaba la vista previa como sí hace un click -- este handler
        cubre ambos casos con la misma lógica de selección."""
        self._on_family_selected(btn, name)
        return False

    def _on_family_selected(self, _btn, name):
        if name == self._selected:
            return  # ya seleccionada -- evita relanzar el hilo de preview
        self._selected = name
        for btn_name, btn in self._result_btns.items():
            ctx = btn.get_style_context()
            if btn_name == name:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")

        meta = self._by_name.get(name, {})
        category = meta.get("category", "")
        self.family_label.set_text(f"{name} — {category}" if category else name)
        self.specimen_label.set_text("")
        self.pangram_label.set_text("")
        self._set_status(t("fuentes", "cargando_preview"))
        self.install_btn.set_sensitive(False)

        def worker():
            try:
                path = fetch_preview_file(name)
                error = None
            except Exception as e:
                path = None
                error = str(e)
            GLib.idle_add(self._on_preview_ready, name, path, error)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _on_preview_ready(self, name, path, error):
        if name != self._selected:
            return False  # el usuario ya eligió otra cosa mientras bajaba
        if error:
            self._set_status(t("fuentes", "error_preview", error=error))
            return False
        register_preview_font(path)
        self._apply_specimen_font(name)
        self.specimen_label.set_text(SAMPLE_SPECIMEN)
        self.pangram_label.set_text(SAMPLE_PANGRAM)

        if is_installed(name):
            self._set_status(t("fuentes", "instalada_sistema"))
            self.install_btn.set_sensitive(False)
            self.install_btn.set_label(t("fuentes", "instalada_check"))
        else:
            self._set_status("")
            self.install_btn.set_sensitive(True)
            self.install_btn.set_label(t("fuentes", "descargar_instalar"))
        return False

    def _apply_specimen_font(self, family):
        """Saca el provider anterior antes de agregar uno nuevo (no
        acumular). Selector `label.gf-specimen` (elemento + clase, no
        clase de un contenedor) para ganarle en especificidad al `*`
        de global.css -- ver CLAUDE.md, gotcha de font-size."""
        screen = Gdk.Screen.get_default()
        if self._specimen_provider is not None:
            Gtk.StyleContext.remove_provider_for_screen(screen, self._specimen_provider)
        escaped = family.replace('"', '\\"')
        css = (
            f'label.gf-specimen {{ font-family: "{escaped}"; }} '
            f'label.gf-pangram {{ font-family: "{escaped}"; }}'
        ).encode()
        self._specimen_provider = Gtk.CssProvider()
        self._specimen_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            screen, self._specimen_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
        )

    # ---- Instalar ----

    def _on_install_clicked(self, _btn):
        name = self._selected
        meta = self._by_name.get(name)
        if not name or not meta:
            return
        weight_keys = list(meta.get("fonts", {}).keys()) or ["400"]
        self.install_btn.set_sensitive(False)
        self.install_btn.set_label(t("fuentes", "instalando"))

        def progress_cb(done, total):
            GLib.idle_add(self._set_status, t("fuentes", "instalando_progreso", done=done, total=total))

        def worker():
            try:
                total = install_family(name, weight_keys, progress_cb=progress_cb)
                error = None
            except Exception as e:
                total = 0
                error = str(e)
            GLib.idle_add(self._on_install_done, name, total, error)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _on_install_done(self, name, total, error):
        if name != self._selected:
            return False
        if error:
            self._set_status(t("fuentes", "error_instalar", error=error))
            self.install_btn.set_sensitive(True)
            self.install_btn.set_label(t("fuentes", "reintentar"))
            return False
        self._set_status(t("fuentes", "instalada_archivos", total=total, suffix="s" if total != 1 else ""))
        self.install_btn.set_label(t("fuentes", "instalada_check"))
        self.install_btn.set_sensitive(False)
        self._mark_row_installed(name)
        return False

    def _mark_row_installed(self, name):
        """Agrega el check a la fila de resultados de `name` sin
        rerenderizar toda la lista -- se llama justo después de instalar,
        para que quede marcada sin tener que cerrar y volver a abrir el
        popup."""
        btn = self._result_btns.get(name)
        if btn is None:
            return
        row = btn.get_child()
        for child in row.get_children():
            if "gf-installed-check" in child.get_style_context().list_classes():
                return  # ya tenía el check (no debería pasar, por las dudas)
        check = Gtk.Label(label="✓")
        check.get_style_context().add_class("gf-installed-check")
        row.pack_start(check, False, False, 0)
        check.show()

    def _set_status(self, text):
        self.status_label.set_text(text)

    def _on_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    os.makedirs(PREVIEW_CACHE_DIR, exist_ok=True)
    os.makedirs(INSTALL_DIR, exist_ok=True)
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    GoogleFontsPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

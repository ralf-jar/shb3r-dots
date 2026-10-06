#!/usr/bin/env python3
"""Galería de temas (SUPER+P) -- selector alterno al de la pestaña
"Personalización", a pantalla casi completa. Ventana normal (no
layer-shell) en su propio workspace (WORKSPACE): al abrir cambia a él, al
cerrar vuelve al workspace de antes y Hyprland destruye el vacío solo.

Rejilla de GRID_ROWS tarjetas de alto con scroll horizontal (máximo
GRID_COLUMNS visibles), las columnas impares (2a, 4a...) bajadas media
tarjeta -- mismo acomodo "panal" que la referencia del usuario, con
rectángulos. Márgenes y gaps viven en theme_gallery.css; el script solo
los lee para calcular el tamaño de tarjeta. Reusa del selector del dashboard solo funciones (miniaturas,
favoritos, apply_theme), sin tocar su UI."""

import os
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, GLibUnix, Pango

GLib.set_prgname("flipfrog-theme-gallery")  # = APP_ID (app_id de Wayland)

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
from waybar_lib import kill_existing, kill_group, load_module_css, svg_icon_image, _icon_text_button
import common
from i18n import t
import theme_module as tm

CSS_FILE = os.path.join(SCRIPT_DIR, "theme_gallery.css")
TOGGLE_CSS = os.path.join(os.path.dirname(SCRIPT_DIR), "toggles", "toggle_common.css")
LOCK = "/tmp/theme-gallery.pid"
GALLERY_CACHE_DIR = os.path.join(tm.THUMB_CACHE_DIR, "gallery")
APP_ID = "flipfrog-theme-gallery"
APP_ICON = "preferences-desktop-color"
# La barra (bar/bar_icons.py) resuelve el ícono de una ventana buscando
# su .desktop por StartupWMClass/nombre -- sin él muestra "?". NoDisplay: no
# aparece en el lanzador ni en la pestaña "Aplicaciones". Vive fuera del
# repo (~/.local), así que se regenera acá si falta o cambió.
DESKTOP_FILE = os.path.expanduser(f"~/.local/share/applications/{APP_ID}.desktop")
WORKSPACE = "name:🎨"
GRID_COLUMNS = 7
GRID_ROWS = 4
CARD_RATIO = 0.62
THUMB_WORKERS = 4
REBUILD_DELAY_MS = 150
FAV_BADGE_W = 22

FILTERS = [
    ("all", t("temas", "galeria_todos")),
    ("pic", t("temas", "galeria_imagenes")),
    ("vid", t("temas", "galeria_videos")),
    ("fav", t("temas", "galeria_favoritos")),
]


def _hypr(lua):
    try:
        subprocess.run(["hyprctl", "dispatch", lua], timeout=2,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _active_workspace():
    try:
        out = subprocess.run(["hyprctl", "activeworkspace", "-j"], capture_output=True,
                             text=True, timeout=2).stdout
        import json
        return json.loads(out)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _focus_workspace(ws):
    """ws: dict de `hyprctl activeworkspace -j`. Numéricos por id, con
    nombre por "name:" (mismo formato que los keybinds)."""
    target = ws["id"] if ws["id"] > 0 else f'"name:{ws["name"]}"'
    _hypr(f"hl.dsp.focus({{ workspace = {target} }})")


def _format_hours(hours):
    """Sin decimales de sobra: 1.0 -> "1", 0.25 -> "0.25"."""
    text = f"{hours:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _only_decimal(entry, text, _length, _position):
    has_dot = "." in entry.get_text()
    for ch in text:
        if ch == "." and not has_dot:
            has_dot = True
        elif not ch.isdigit():
            entry.stop_emission_by_name("insert-text")
            return


def _theme_kind(fname):
    path = tm.theme_wallpaper_path(fname)
    if not path:
        return None
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return "vid" if ext in tm.VIDEO_EXTS else "pic"


class ThemeGallery:
    def __init__(self):
        # El cambio al workspace lo hace la regla de ventana de
        # windowrules.lua (workspace = WORKSPACE) al mapear.
        self.previous_ws = _active_workspace()

        load_module_css(TOGGLE_CSS)
        load_module_css(CSS_FILE)
        self.window = Gtk.Window(title=t("temas", "galeria_titulo"))
        visual = self.window.get_screen().get_rgba_visual()
        if visual:
            self.window.set_visual(visual)
        self.window.set_app_paintable(True)
        self.window.set_name("tg-window")
        self.window.set_icon_name(APP_ICON)

        self.themes = tm.list_themes()
        self.favorites = tm.load_favorites()
        self.kinds = {f: _theme_kind(f) for f in self.themes}
        self.active = tm.active_theme_name()
        self.filter = "all"
        self.cards = {}
        self.card_w = self.card_h = 0
        self._built_size = (0, 0)
        self._rebuild_id = None
        self._generation = 0

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_name("tg-root")
        root.pack_start(self._build_toolbar(), False, False, 0)

        self.grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.grid.set_halign(Gtk.Align.CENTER)
        self.grid.set_name("tg-grid")
        scroller = Gtk.ScrolledWindow()
        scroller.set_name("tg-scroller")
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.add(self.grid)
        scroller.connect("scroll-event", self._on_scroll)
        scroller.set_halign(Gtk.Align.CENTER)
        scroller.set_valign(Gtk.Align.CENTER)
        self.scroller = scroller
        # Se mide el área de afuera (no el scroller, que se angosta a
        # GRID_COLUMNS tarjetas y realimentaría el cálculo).
        area = Gtk.Box()
        area.set_name("tg-area")
        self.area = area
        area.connect("size-allocate", self._on_allocate)
        area.pack_start(scroller, True, False, 0)
        root.pack_start(area, True, True, 0)
        self.window.add(root)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_sigterm)
        self.window.show_all()
        self._sync_filter_buttons()

    # ---- tamaño ----------------------------------------------------------------

    def _on_allocate(self, _scroller, alloc):
        """Tamaño de tarjeta según el área REAL (gaps/bordes de Hyprland
        incluidos), no el monitor. Rearma en idle -- nunca dentro de
        size-allocate."""
        old_w, old_h = self._built_size
        if abs(alloc.width - old_w) > 4 or abs(alloc.height - old_h) > 4:
            self._built_size = (alloc.width, alloc.height)
            # Al mapear la ventana el tamaño cambia varias veces seguidas --
            # rearmar solo cuando se queda quieto REBUILD_DELAY_MS.
            self._schedule_rebuild(REBUILD_DELAY_MS)

    def _schedule_rebuild(self, delay_ms):
        if self._rebuild_id:
            GLib.source_remove(self._rebuild_id)
        self._rebuild_id = GLib.timeout_add(delay_ms, self._rebuild)

    @staticmethod
    def _css_box(widget_or_ctx):
        """(horizontal, vertical) de margin+border+padding según el CSS --
        márgenes y gaps viven en theme_gallery.css, acá solo se leen."""
        ctx = widget_or_ctx
        if isinstance(ctx, Gtk.Widget):
            ctx = ctx.get_style_context()
        state = ctx.get_state()
        h = v = 0
        for border in (ctx.get_margin(state), ctx.get_border(state), ctx.get_padding(state)):
            h += border.left + border.right
            v += border.top + border.bottom
        return h, v

    def _rebuild(self):
        """Lo que quepa primero: GRID_COLUMNS de ancho, o GRID_ROWS de alto
        más el medio desplazamiento de las columnas impares. El "gap" entre
        tarjetas es el margin de .tg-card; el alrededor, el de #tg-area."""
        self._rebuild_id = None
        width, height = self._built_size
        area_h, area_v = self._css_box(self.area)
        width, height = width - area_h, height - area_v
        probe = Gtk.Button()
        probe.get_style_context().add_class("tg-card")
        self.card_extra_w, self.card_extra_h = self._css_box(probe)
        by_width = width // GRID_COLUMNS - self.card_extra_w
        by_height = int((height / (GRID_ROWS + 0.5) - self.card_extra_h) / CARD_RATIO)
        card_w = max(80, min(by_width, by_height))
        if card_w == self.card_w and self.cards:
            return False
        self.card_w = card_w
        self.card_h = int(self.card_w * CARD_RATIO)
        # Nunca más de GRID_COLUMNS visibles a la vez, aunque el alto deje
        # tarjetas más chicas que las que cabrían a lo ancho.
        visible_w = GRID_COLUMNS * (self.card_w + self.card_extra_w)
        self.scroller.set_size_request(min(width, visible_w), -1)
        self._clear_grid()
        self.cards = {fname: self._build_card(fname) for fname in self.themes}
        self._layout()
        self._generation += 1
        threading.Thread(target=self._load_thumbnails, args=(self._generation,), daemon=True).start()
        return False

    def _on_scroll(self, scroller, event):
        """Rueda vertical del mouse = scroll horizontal."""
        adj = scroller.get_hadjustment()
        ok, dx, dy = event.get_scroll_deltas()
        if ok:
            delta = (dx + dy) * self.card_w * 0.5
        elif event.direction in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
            delta = self.card_w
        elif event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
            delta = -self.card_w
        else:
            return False
        upper = adj.get_upper() - adj.get_page_size()
        adj.set_value(min(max(0, adj.get_value() + delta), upper))
        return True

    # ---- barra superior --------------------------------------------------------

    def _build_toolbar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_name("tg-toolbar")
        bar.set_halign(Gtk.Align.CENTER)

        title = Gtk.Label(label=t("temas", "galeria_titulo"))
        title.set_name("tg-title")
        bar.pack_start(title, False, False, 0)

        self.filter_buttons = {}
        for key, label in FILTERS:
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("tg-chip")
            btn.connect("clicked", self._on_filter, key)
            self.filter_buttons[key] = btn
            bar.pack_start(btn, False, False, 0)

        self.count_label = Gtk.Label()
        self.count_label.set_name("tg-count")
        bar.pack_start(self.count_label, False, False, 0)

        bar.pack_start(self._divider(), False, False, 0)
        editor_btn = _icon_text_button(tm.PALETTE_ICON, t("temas", "editor_temas"), t("temas", "editor_colores"))
        editor_btn.get_style_context().add_class("tg-tool-btn")
        editor_btn.connect("clicked", lambda _b: tm.open_theme_editor())
        bar.pack_start(editor_btn, False, False, 0)

        bar.pack_start(self._divider(), False, False, 0)
        self._build_rotation(bar)
        return bar

    @staticmethod
    def _divider():
        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        sep.get_style_context().add_class("tg-divider")
        return sep

    def _build_rotation(self, bar):
        """Rotación automática -- misma lógica que tenía la pestaña
        "Personalización" (theme_module.start_rotator/stop_rotator,
        themer/rotation.json), movida acá."""
        icon = svg_icon_image(tm.CLOCK_ICON, size=14, color_name="myforeground")
        bar.pack_start(icon, False, False, 0)
        label = Gtk.Label(label=t("temas", "rotacion_automatica"))
        label.get_style_context().add_class("tg-tool-label")
        bar.pack_start(label, False, False, 0)

        self.rotate_switch = Gtk.Switch()
        self.rotate_switch.set_valign(Gtk.Align.CENTER)
        self.rotate_switch.get_style_context().add_class("toggle-row-switch")
        self.rotate_switch.set_sensitive(len(self.themes) >= 2)
        self.rotate_switch.set_active(tm.rotator_enabled())
        self.rotate_switch.connect("state-set", self._on_rotate_state_set)
        bar.pack_start(self.rotate_switch, False, False, 0)

        _, minutes = tm.load_rotation_config()
        self.interval_entry = Gtk.Entry()
        self.interval_entry.set_name("tg-interval")
        self.interval_entry.set_text(_format_hours(round(minutes / 60, 2) if minutes > 0 else 1))
        self.interval_entry.set_width_chars(4)
        self.interval_entry.set_max_length(6)
        self.interval_entry.set_alignment(1.0)
        self.interval_entry.set_valign(Gtk.Align.CENTER)
        self.interval_entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
        self.interval_entry.set_tooltip_text(t("temas", "tooltip_horas_intervalo"))
        self.interval_entry.connect("insert-text", _only_decimal)
        self.interval_entry.connect("activate", self._on_interval_committed)
        self.interval_entry.connect("focus-out-event", self._on_interval_committed)
        bar.pack_start(self.interval_entry, False, False, 0)

        hours = Gtk.Label(label=t("temas", "horas"))
        hours.get_style_context().add_class("tg-tool-label")
        bar.pack_start(hours, False, False, 0)

        apply_btn = Gtk.Button()
        apply_btn.set_image(Gtk.Image.new_from_icon_name("object-select-symbolic", Gtk.IconSize.BUTTON))
        apply_btn.set_always_show_image(True)
        apply_btn.set_tooltip_text(t("temas", "aplicar"))
        apply_btn.get_style_context().add_class("tg-chip")
        apply_btn.connect("clicked", self._on_interval_committed)
        bar.pack_start(apply_btn, False, False, 0)

    def _hours_from_entry(self):
        try:
            hours = float(self.interval_entry.get_text().strip())
            if hours <= 0:
                raise ValueError
        except ValueError:
            hours = 1
        self.interval_entry.set_text(_format_hours(hours))
        return hours

    def _on_rotate_state_set(self, _switch, state):
        if state:
            tm.start_rotator(self._hours_from_entry() * 60)
        else:
            tm.stop_rotator()
        return False

    def _on_interval_committed(self, *_args):
        hours = self._hours_from_entry()
        _, current_minutes = tm.load_rotation_config()
        if round(current_minutes / 60, 2) == round(hours, 2):
            return False
        if self.rotate_switch.get_active():
            tm.start_rotator(hours * 60)
        else:
            tm.save_rotation_config(False, hours * 60)
        return False

    def _on_filter(self, _btn, key):
        self.filter = key
        self._sync_filter_buttons()
        self._layout()

    def _sync_filter_buttons(self):
        for key, btn in self.filter_buttons.items():
            ctx = btn.get_style_context()
            (ctx.add_class if key == self.filter else ctx.remove_class)("active")

    # ---- tarjetas ------------------------------------------------------------------

    def _build_card(self, fname):
        """Fixed con el botón que aplica el tema y, ENCIMA como hermanos (no
        adentro, o su clic también aplicaría el tema), los badges de
        eliminar (arriba-izquierda) y favorito (arriba-derecha) -- mismo
        patrón que el selector del dashboard."""
        name = fname[: -len(".theme")]
        apply_btn = Gtk.Button()
        apply_btn.get_style_context().add_class("tg-card")
        if fname == self.active:
            apply_btn.get_style_context().add_class("active")
        apply_btn.set_tooltip_text(name)
        apply_btn.connect("clicked", self._on_card_clicked, fname)

        inner = Gtk.Fixed()
        inner.set_size_request(self.card_w, self.card_h)
        slot = Gtk.Box()
        slot.set_size_request(self.card_w, self.card_h)
        slot.get_style_context().add_class("tg-placeholder")
        inner.put(slot, 0, 0)

        kind = self.kinds[fname]
        if kind:
            tag = Gtk.Label(label=t("temas", "galeria_tag_" + kind))
            tag.get_style_context().add_class("tg-tag")
            inner.put(tag, 36, 9)

        name_row = Gtk.Box()
        name_row.set_size_request(self.card_w, -1)
        label = Gtk.Label(label=name)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(28)
        label.get_style_context().add_class("tg-name")
        name_row.set_center_widget(label)
        inner.put(name_row, 0, self.card_h - 32)
        apply_btn.add(inner)

        card = Gtk.Fixed()
        card.put(apply_btn, 0, 0)
        # Esquina de adentro de la tarjeta: margin+borde de .tg-card (mitad
        # de lo que suma _css_box a lo ancho) + 4px de aire.
        inset = self.card_extra_w // 2 + 4

        delete_btn = Gtk.Button()
        delete_btn.get_style_context().add_class("tg-badge")
        delete_btn.set_image(Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.MENU))
        delete_btn.set_always_show_image(True)
        delete_btn.set_tooltip_text(t("temas", "tooltip_eliminar_tema"))
        delete_btn.connect("clicked", self._on_delete_clicked, fname)
        card.put(delete_btn, inset, inset)

        fav_btn = Gtk.Button(label="★")
        fav_btn.get_style_context().add_class("tg-badge")
        fav_btn.get_style_context().add_class("tg-fav")
        fav_btn.set_tooltip_text(t("temas", "galeria_favorito_tooltip"))
        fav_btn.connect("clicked", self._on_fav_clicked, fname)
        card.put(fav_btn, inset + self.card_w - FAV_BADGE_W - 8, inset)

        card.apply_btn, card.slot, card.fav_btn = apply_btn, slot, fav_btn
        self._sync_fav(fname, card)
        return card

    def _sync_fav(self, fname, card=None):
        card = card or self.cards[fname]
        ctx = card.fav_btn.get_style_context()
        is_fav = fname[: -len(".theme")] in self.favorites
        (ctx.add_class if is_fav else ctx.remove_class)("active")

    def _on_fav_clicked(self, _btn, fname):
        """favorites.json guarda el nombre SIN .theme (mismo contrato que
        el dashboard y theme-rotator.py)."""
        name = fname[: -len(".theme")]
        favs = tm.load_favorites()
        if name in favs:
            favs.discard(name)
        else:
            favs.add(name)
        tm.save_favorites(favs)
        self.favorites = favs
        self._sync_fav(fname)
        if self.filter == "fav":
            self._layout()

    def _on_delete_clicked(self, _btn, fname):
        name = fname[: -len(".theme")]
        dialog = Gtk.MessageDialog(
            transient_for=self.window, flags=0, message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=t("temas", "confirmar_eliminar_tema", name=name),
        )
        dialog.format_secondary_text(t("temas", "confirmar_eliminar_tema_secundario"))
        # Mismo prgname que el dashboard: matchea la regla float de
        # .*dialog.* en windowrules.lua.
        original_prgname = GLib.get_prgname()
        GLib.set_prgname("theme-delete-dialog")
        dialog.show_all()
        GLib.set_prgname(original_prgname)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.YES:
            return

        try:
            os.remove(os.path.join(tm.THEMES_DIR, fname))
        except FileNotFoundError:
            pass
        favs = tm.load_favorites()
        if name in favs:
            favs.discard(name)
            tm.save_favorites(favs)
        self.favorites = favs
        self.themes.remove(fname)
        self.kinds.pop(fname, None)
        self.cards.pop(fname, None)
        self._layout()

    def _visible_themes(self):
        if self.filter == "fav":
            return [f for f in self.themes if f[: -len(".theme")] in self.favorites]
        if self.filter in ("pic", "vid"):
            return [f for f in self.themes if self.kinds[f] == self.filter]
        return list(self.themes)

    def _clear_grid(self):
        for column in self.grid.get_children():
            for card in column.get_children():
                column.remove(card)
            self.grid.remove(column)

    def _layout(self):
        """GRID_ROWS tarjetas por columna, llenando columna por columna; tantas
        columnas como haga falta (scroll horizontal). Las columnas impares
        bajan media tarjeta."""
        self._clear_grid()

        visible = self._visible_themes()
        columns = max(1, -(-len(visible) // GRID_ROWS))
        for c in range(columns):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            column.set_valign(Gtk.Align.START)
            column.set_margin_top((self.card_h + self.card_extra_h) // 2 if c % 2 else 0)
            for fname in visible[c * GRID_ROWS:(c + 1) * GRID_ROWS]:
                column.pack_start(self.cards[fname], False, False, 0)
            self.grid.pack_start(column, False, False, 0)
        self.count_label.set_text(str(len(visible)))
        self.grid.show_all()
        self.scroller.get_hadjustment().set_value(0)

    def _load_thumbnails(self, generation):
        radius = tm.current_radius()
        width, height = self.card_w, self.card_h

        def one(fname):
            if generation != self._generation:
                return
            path = tm.theme_wallpaper_path(fname)
            if not path or not os.path.isfile(path):
                return
            image_path = path
            if self.kinds[fname] == "vid":
                image_path = tm._video_thumbnail_path(path, fname[: -len(".theme")])
                if not image_path:
                    return
            # Miniatura ya escalada/redondeada en disco: clave por tamaño,
            # radio y mtime del wallpaper (cambiarlo invalida sola la caché).
            key = f"{fname[: -len('.theme')]}_{width}x{height}_r{radius}_{int(os.path.getmtime(path))}.png"
            cached = os.path.join(GALLERY_CACHE_DIR, key)
            if os.path.exists(cached):
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(cached)
                except GLib.Error:
                    pixbuf = None
                if pixbuf is not None:
                    GLib.idle_add(self._set_thumbnail, fname, pixbuf, generation)
                    return
            pixbuf = tm._cover_pixbuf(image_path, width, height)
            if pixbuf is not None:
                pixbuf = tm._round_corners(pixbuf, radius)
                try:
                    os.makedirs(GALLERY_CACHE_DIR, exist_ok=True)
                    pixbuf.savev(cached, "png", [], [])
                except GLib.Error:
                    pass
                GLib.idle_add(self._set_thumbnail, fname, pixbuf, generation)

        with ThreadPoolExecutor(max_workers=THUMB_WORKERS) as pool:
            list(pool.map(one, self.themes))

    def _set_thumbnail(self, fname, pixbuf, generation):
        card = self.cards.get(fname)
        if generation != self._generation or card is None:
            return False
        card.slot.add(Gtk.Image.new_from_pixbuf(pixbuf))
        card.slot.get_style_context().remove_class("tg-placeholder")
        card.slot.show_all()
        return False

    def _on_card_clicked(self, _btn, fname):
        if self.active in self.cards:
            self.cards[self.active].apply_btn.get_style_context().remove_class("active")
        self.active = fname
        self.cards[fname].apply_btn.get_style_context().add_class("active")
        tm.apply_theme(fname)

    # ---- ciclo de vida -----------------------------------------------------------------

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        return False

    def _on_sigterm(self):
        self.window.destroy()
        return False

    def _on_destroy(self, *_args):
        # Solo regresar si el usuario sigue en la galería -- si ya se movió
        # a otro workspace a mano, no jalarlo de vuelta.
        current = _active_workspace()
        if self.previous_ws and current and current.get("name") == WORKSPACE[len("name:"):]:
            _focus_workspace(self.previous_ws)
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def ensure_desktop_entry():
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={t('temas', 'galeria_titulo')}\n"
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
    ensure_desktop_entry()
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    ThemeGallery()
    Gtk.main()


if __name__ == "__main__":
    main()

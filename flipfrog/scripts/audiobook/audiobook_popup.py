#!/usr/bin/env python3
"""Popup "Audiolibros" (SUPER+A) -- biblioteca de EPUB convertidos a MP3
+ lector/reproductor con el texto sincronizado. Sin estado propio: la
cola de conversión y el reproductor viven en audiobook_daemon.py (cerrar
el popup no corta nada); este sondea `get_state` cada POLL_MS y manda
comandos. book.json (texto + tiempos por frase) se lee directo de disco."""

import bisect
import json
import math
import os
import subprocess
import sys
import threading
import time

import cairo
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top
from common import theme_color_rgba
from i18n import t
import audiobook_ipc
import audiobook_library as library
import audiobook_voices as voices

CSS_FILE = os.path.join(SCRIPT_DIR, "audiobook_popup.css")
LOCK = "/tmp/audiobook-popup.pid"
RADIUS_FILE = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "themer", "corner-radius.json"))
POLL_MS = 250
REWIND, FORWARD = 15, 30
SPEEDS = (0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0)
COVER_W, COVER_H = 132, 190
FOLLOW_GRACE = 5
CONVERSION_STATUS = {
    "queued": "estado_en_cola", "reading": "estado_leyendo", "voice": "estado_voz",
    "speaking": "estado_narrando", "encoding": "estado_codificando", "error": "estado_error",
}


def fmt_clock(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_left(seconds):
    minutes = max(0, int(round(seconds / 60)))
    h, m = divmod(minutes, 60)
    return t("audiolibros", "tiempo_hm", h=h, m=m) if h else t("audiolibros", "tiempo_m", m=m)


def _rgba(name, alpha=None):
    try:
        r, g, b, a = theme_color_rgba(name)
    except (OSError, AttributeError, ValueError):
        r, g, b, a = 255, 255, 255, 1.0
    return r / 255, g / 255, b / 255, a if alpha is None else alpha


def _radius():
    try:
        with open(RADIUS_FILE) as f:
            return int(json.load(f).get("radius", 5))
    except (OSError, ValueError):
        return 5


def _rounded_cover(path, width, height):
    """Portada escalada a cubrir width x height, esquinas horneadas en el
    pixbuf (GTK3 no recorta hijos por border-radius, ver CLAUDE.md)."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
    except (GLib.Error, TypeError):
        return None
    scale = max(width / pixbuf.get_width(), height / pixbuf.get_height())
    scaled = pixbuf.scale_simple(max(1, int(pixbuf.get_width() * scale)),
                                 max(1, int(pixbuf.get_height() * scale)),
                                 GdkPixbuf.InterpType.BILINEAR)
    x = (scaled.get_width() - width) // 2
    y = (scaled.get_height() - height) // 2
    cropped = scaled.new_subpixbuf(x, y, width, height)

    radius = _radius()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    ctx = cairo.Context(surface)
    deg = math.pi / 180
    ctx.new_sub_path()
    ctx.arc(width - radius, radius, radius, -90 * deg, 0)
    ctx.arc(width - radius, height - radius, radius, 0, 90 * deg)
    ctx.arc(radius, height - radius, radius, 90 * deg, 180 * deg)
    ctx.arc(radius, radius, radius, 180 * deg, 270 * deg)
    ctx.close_path()
    ctx.clip()
    Gdk.cairo_set_source_pixbuf(ctx, cropped, 0, 0)
    ctx.paint()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)


def _icon_button(icon, tooltip, css_class="ab-icon-btn", size=Gtk.IconSize.SMALL_TOOLBAR):
    btn = Gtk.Button()
    btn.add(Gtk.Image.new_from_icon_name(icon, size))
    btn.set_tooltip_text(tooltip)
    btn.set_valign(Gtk.Align.CENTER)
    btn.get_style_context().add_class(css_class)
    return btn


def _run_dialog(window, dialog, prgname):
    """Diálogo nativo sobre la ventana layer-shell -- mismo hide/show que
    theme-editor.py; prgname con "dialog" para la regla float."""
    original = GLib.get_prgname()
    GLib.set_prgname(prgname)
    dialog.show_all()
    GLib.set_prgname(original)
    window.hide()
    response = dialog.run()
    return response


class ChapterSeekBar(Gtk.DrawingArea):
    """Barra de progreso partida en un segmento por capítulo (ancho
    proporcional a su duración). Click/arrastre busca; tooltip con el
    capítulo y el tiempo bajo el cursor."""

    GAP = 3
    HEIGHT = 4
    KNOB = 6

    def __init__(self, on_seek):
        super().__init__()
        self.on_seek = on_seek
        self.duration = 0
        self.starts = []
        self.titles = []
        self.position = 0
        self.drag_position = None
        self.set_size_request(-1, 20)
        self.set_hexpand(True)
        self.set_has_tooltip(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._press)
        self.connect("motion-notify-event", self._motion)
        self.connect("button-release-event", self._release)
        self.connect("query-tooltip", self._tooltip)
        self.refresh_colors()

    def refresh_colors(self):
        self.c_played = _rgba("myforegroundhover")
        self.c_rest = _rgba("myforeground", 0.16)
        self.c_knob = _rgba("myborders")

    def set_book(self, duration, chapters):
        self.duration = duration or 0
        self.starts = [c["start"] for c in chapters] or [0]
        self.titles = [c["title"] for c in chapters] or [""]
        self.queue_draw()

    def set_position(self, position):
        if self.drag_position is None and abs(position - self.position) > 0.05:
            self.position = position
            self.queue_draw()

    def _x_to_time(self, x):
        width = self.get_allocated_width() - 2 * self.KNOB
        return max(0.0, min(1.0, (x - self.KNOB) / max(width, 1))) * self.duration

    def _draw(self, _widget, ctx):
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if not self.duration:
            return
        usable = width - 2 * self.KNOB
        y = height / 2 - self.HEIGHT / 2
        position = self.drag_position if self.drag_position is not None else self.position
        edges = self.starts + [self.duration]
        for i in range(len(self.starts)):
            x0 = self.KNOB + edges[i] / self.duration * usable
            x1 = self.KNOB + edges[i + 1] / self.duration * usable - (self.GAP if i + 1 < len(self.starts) else 0)
            if x1 - x0 < 1:
                x1 = x0 + 1
            played_to = self.KNOB + position / self.duration * usable
            self._bar(ctx, x0, x1, y, self.c_rest)
            if played_to > x0:
                self._bar(ctx, x0, min(x1, played_to), y, self.c_played)
        knob_x = self.KNOB + position / self.duration * usable
        ctx.set_source_rgba(*self.c_knob)
        ctx.arc(knob_x, height / 2, self.KNOB, 0, 2 * math.pi)
        ctx.fill()

    def _bar(self, ctx, x0, x1, y, color):
        r = self.HEIGHT / 2
        ctx.set_source_rgba(*color)
        if x1 - x0 <= 2 * r:
            ctx.rectangle(x0, y, x1 - x0, self.HEIGHT)
        else:
            ctx.new_sub_path()
            ctx.arc(x0 + r, y + r, r, math.pi / 2, 3 * math.pi / 2)
            ctx.arc(x1 - r, y + r, r, -math.pi / 2, math.pi / 2)
            ctx.close_path()
        ctx.fill()

    def _press(self, _w, event):
        if event.button == 1 and self.duration:
            self.drag_position = self._x_to_time(event.x)
            self.queue_draw()
        return True

    def _motion(self, _w, event):
        if self.drag_position is not None:
            self.drag_position = self._x_to_time(event.x)
            self.queue_draw()
        return False

    def _release(self, _w, event):
        if self.drag_position is not None:
            self.position = self._x_to_time(event.x)
            self.drag_position = None
            self.queue_draw()
            self.on_seek(self.position)
        return True

    def _tooltip(self, _w, x, _y, _kb, tooltip):
        if not self.duration:
            return False
        when = self._x_to_time(x)
        idx = max(0, bisect.bisect_right(self.starts, when) - 1)
        tooltip.set_text(f"{self.titles[idx]}\n{fmt_clock(when)}")
        return True


class AudiobookPopup:
    def __init__(self):
        self.window, container = build_layer_window("audiobook-popup", CSS_FILE)
        container.set_name("ab-container")
        position_fixed_top(container, margin=50)

        self.player = None
        self.library_version = None
        self.conversions_sig = None
        self.bookmarks_sig = None
        self.book_id = None
        self.book = None
        self.s_times = []
        self.s_offsets = []
        self.chapter_starts = []
        self.current_sentence = -1
        self.current_chapter = -1
        self.last_user_scroll = 0
        self.volume_lock = False
        self._poll_in_flight = False

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._build_library(), "library")
        self.stack.add_named(self._build_reader(), "reader")
        container.pack_start(self.stack, True, True, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()
        self.stack.set_visible_child_name("library")

        self._reload_library()
        threading.Thread(target=self._start_daemon, daemon=True).start()

    # ---- biblioteca --------------------------------------------------------

    def _build_library(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_name("ab-library")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_halign(Gtk.Align.CENTER)
        title = Gtk.Label(label=t("audiolibros", "titulo"))
        title.set_name("ab-title")
        header.pack_start(title, False, False, 0)
        self.count_label = Gtk.Label(label="0")
        self.count_label.set_name("ab-count")
        self.count_label.set_valign(Gtk.Align.CENTER)
        header.pack_start(self.count_label, False, False, 0)
        page.pack_start(header, False, False, 0)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_name("ab-toolbar")

        add_btn = Gtk.Button()
        add_btn.get_style_context().add_class("ab-add-btn")
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        inner.pack_start(Gtk.Image.new_from_icon_name("list-add-symbolic", Gtk.IconSize.SMALL_TOOLBAR), False, False, 0)
        inner.pack_start(Gtk.Label(label=t("audiolibros", "agregar_epub")), False, False, 0)
        add_btn.add(inner)
        add_btn.set_tooltip_text(t("audiolibros", "agregar_epub_tooltip"))
        add_btn.connect("clicked", self._on_add_epub)
        toolbar.pack_start(add_btn, False, False, 0)

        self.voice_combo = Gtk.ComboBoxText()
        self.voice_combo.set_tooltip_text(t("audiolibros", "voz_tooltip"))
        self.voice_combo.get_style_context().add_class("ab-voice-combo")
        self.voice_ids = []
        for voice in voices.installed():
            self.voice_combo.append_text(voice["label"])
            self.voice_ids.append(voice["id"])
        preferred = library.load_player().get("voice")
        default = voices.pick("es", preferred)
        if default and default["id"] in self.voice_ids:
            self.voice_combo.set_active(self.voice_ids.index(default["id"]))
        self.voice_combo.connect("changed", self._on_voice_changed)
        toolbar.pack_end(self.voice_combo, False, False, 0)
        page.pack_start(toolbar, False, False, 0)

        self.conversions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.conversions_box.set_name("ab-conversions")
        self.conversions_box.set_no_show_all(True)
        page.pack_start(self.conversions_box, False, False, 0)

        self.flow = Gtk.FlowBox()
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.set_homogeneous(True)
        self.flow.set_min_children_per_line(6)
        self.flow.set_max_children_per_line(6)
        self.flow.set_row_spacing(18)
        self.flow.set_column_spacing(14)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.connect("child-activated", self._on_book_activated)

        self.empty_label = Gtk.Label(label=t("audiolibros", "biblioteca_vacia"))
        self.empty_label.get_style_context().add_class("ab-empty")
        self.empty_label.set_no_show_all(True)

        scroller = Gtk.ScrolledWindow()
        scroller.set_name("ab-library-scroller")
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(self.empty_label, False, False, 0)
        box.pack_start(self.flow, False, False, 0)
        scroller.add(box)
        page.pack_start(scroller, True, True, 0)
        return page

    def _reload_library(self):
        def work():
            books = library.list_books()
            for book in books:
                cover = library.cover_path(book["id"], {"cover": book["cover"]})
                book["pixbuf"] = _rounded_cover(cover, COVER_W, COVER_H) if cover else None
            GLib.idle_add(self._fill_library, books)
        threading.Thread(target=work, daemon=True).start()

    def _fill_library(self, books):
        for child in self.flow.get_children():
            self.flow.remove(child)
        for book in books:
            self.flow.add(self._book_card(book))
        self.count_label.set_text(str(len(books)))
        self.empty_label.set_visible(not books)
        self.flow.show_all()
        return False

    def _book_card(self, book):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class("ab-card")
        card.book_id = book["id"]

        if book["pixbuf"]:
            cover = Gtk.Image.new_from_pixbuf(book["pixbuf"])
        else:
            cover = Gtk.Box()
            cover.set_size_request(COVER_W, COVER_H)
            cover.get_style_context().add_class("ab-cover-placeholder")
            icon = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic", Gtk.IconSize.DIALOG)
            cover.set_center_widget(icon)
        cover.set_halign(Gtk.Align.CENTER)
        card.pack_start(cover, False, False, 0)

        title = Gtk.Label(label=book["title"], xalign=0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(16)
        title.set_tooltip_text(book["title"])
        title.get_style_context().add_class("ab-card-title")
        card.pack_start(title, False, False, 0)

        author = Gtk.Label(label=book["author"] or " ", xalign=0)
        author.set_ellipsize(Pango.EllipsizeMode.END)
        author.set_max_width_chars(16)
        author.get_style_context().add_class("ab-card-author")
        card.pack_start(author, False, False, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        left = book["duration"] - book["position"]
        if book["position"] < 1:
            status = fmt_left(book["duration"])
        elif left < 5:
            status = t("audiolibros", "terminado")
        else:
            status = t("audiolibros", "restante", time=fmt_left(left))
        status_label = Gtk.Label(label=status, xalign=0)
        status_label.get_style_context().add_class("ab-card-status")
        footer.pack_start(status_label, True, True, 0)
        trash = _icon_button("user-trash-symbolic", t("audiolibros", "eliminar"))
        trash.connect("clicked", lambda _b: self._confirm_delete(book["id"], book["title"]))
        footer.pack_end(trash, False, False, 0)
        card.pack_start(footer, False, False, 0)
        return card

    def _on_book_activated(self, _flow, child):
        book_id = child.get_child().book_id
        self.command("open", then=lambda: GLib.idle_add(self._open_reader, book_id), id=book_id)

    def _update_conversions(self, conversions):
        sig = [(c["id"], c["status"], round(c["progress"], 3), c.get("name")) for c in conversions]
        if sig == self.conversions_sig:
            return
        self.conversions_sig = sig
        for child in self.conversions_box.get_children():
            self.conversions_box.remove(child)
        for conv in conversions:
            row = self._conversion_row(conv)
            row.show_all()
            self.conversions_box.pack_start(row, False, False, 0)
        self.conversions_box.set_visible(bool(conversions))

    def _conversion_row(self, conv):
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row.get_style_context().add_class("ab-conv-row")
        if conv["status"] == "error":
            row.get_style_context().add_class("ab-conv-error")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=conv["name"], xalign=0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(60)
        name.get_style_context().add_class("ab-conv-name")
        top.pack_start(name, True, True, 0)

        if conv["status"] == "error":
            detail = t("audiolibros", conv.get("error") or "error_conversion")
        else:
            detail = t("audiolibros", CONVERSION_STATUS.get(conv["status"], "estado_en_cola"))
            if conv["status"] in ("speaking", "encoding"):
                detail += f"  ·  {int(conv['progress'] * 100)}%"
                if conv.get("eta"):
                    detail += "  ·  " + t("audiolibros", "restante", time=fmt_left(conv["eta"]))
        status = Gtk.Label(label=detail, xalign=1.0)
        status.get_style_context().add_class("ab-conv-status")
        top.pack_start(status, False, False, 0)

        icon = "window-close-symbolic" if conv["status"] == "error" else "process-stop-symbolic"
        tip = t("audiolibros", "descartar" if conv["status"] == "error" else "cancelar_conversion")
        cancel = _icon_button(icon, tip)
        cancel.connect("clicked", lambda _b: self.command("cancel_conversion", id=conv["id"]))
        top.pack_start(cancel, False, False, 0)
        row.pack_start(top, False, False, 0)

        bar = Gtk.ProgressBar()
        bar.set_fraction(conv["progress"])
        row.pack_start(bar, False, False, 0)
        return row

    def _on_add_epub(self, _btn):
        dialog = Gtk.FileChooserDialog(title=t("audiolibros", "elegir_epub"), transient_for=self.window,
                                       action=Gtk.FileChooserAction.OPEN)
        dialog.add_button(t("audiolibros", "cancelar"), Gtk.ResponseType.CANCEL)
        dialog.add_button(t("audiolibros", "convertir"), Gtk.ResponseType.OK)
        dialog.set_select_multiple(True)
        epub_filter = Gtk.FileFilter()
        epub_filter.set_name("EPUB")
        epub_filter.add_pattern("*.epub")
        epub_filter.add_mime_type("application/epub+zip")
        dialog.add_filter(epub_filter)
        downloads = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if downloads:
            dialog.set_current_folder(downloads)

        response = _run_dialog(self.window, dialog, "audiobook-epub-dialog")
        paths = dialog.get_filenames()
        dialog.destroy()
        self.window.show()
        if response == Gtk.ResponseType.OK:
            for path in paths:
                self.command("add_epub", path=path)

    def _on_voice_changed(self, combo):
        idx = combo.get_active()
        if 0 <= idx < len(self.voice_ids):
            self.command("set_voice", voice=self.voice_ids[idx])

    def _confirm_delete(self, book_id, title):
        dialog = Gtk.MessageDialog(transient_for=self.window, flags=0,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.YES_NO,
                                   text=t("audiolibros", "eliminar_confirmar", title=title))
        response = _run_dialog(self.window, dialog, "audiobook-delete-dialog")
        dialog.destroy()
        self.window.show()
        if response == Gtk.ResponseType.YES:
            def after():
                GLib.idle_add(self._after_delete, book_id)
            self.command("delete_book", then=after, id=book_id)

    def _after_delete(self, book_id):
        if book_id == self.book_id:
            self.book_id = None
            self.stack.set_visible_child_name("library")
        self._reload_library()
        return False

    # ---- lector --------------------------------------------------------------

    def _build_reader(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_name("ab-reader")

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        self.sidebar = Gtk.Revealer()
        self.sidebar.set_transition_type(Gtk.RevealerTransitionType.SLIDE_RIGHT)
        self.sidebar.set_reveal_child(True)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        side.set_name("ab-sidebar")

        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        tabs.set_name("ab-side-tabs")
        self.tab_chapters = Gtk.ToggleButton(label=t("audiolibros", "capitulos"))
        self.tab_marks = Gtk.ToggleButton(label=t("audiolibros", "marcadores"))
        for btn in (self.tab_chapters, self.tab_marks):
            btn.get_style_context().add_class("ab-tab")
            btn.connect("toggled", self._on_tab_toggled)
            tabs.pack_start(btn, True, True, 0)
        side.pack_start(tabs, False, False, 0)

        self.side_stack = Gtk.Stack()
        self.chapter_list = Gtk.ListBox()
        self.chapter_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.chapter_list.get_style_context().add_class("ab-list")
        self.chapter_list.connect("row-activated", self._on_chapter_activated)
        self.chapter_scroller = Gtk.ScrolledWindow()
        self.chapter_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.chapter_scroller.add(self.chapter_list)
        self.side_stack.add_named(self.chapter_scroller, "chapters")

        self.mark_list = Gtk.ListBox()
        self.mark_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.mark_list.get_style_context().add_class("ab-list")
        self.mark_list.connect("row-activated", self._on_mark_activated)
        marks_empty = Gtk.Label(label=t("audiolibros", "sin_marcadores"))
        marks_empty.get_style_context().add_class("ab-empty-small")
        marks_empty.show()
        self.mark_list.set_placeholder(marks_empty)
        marks_scroller = Gtk.ScrolledWindow()
        marks_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        marks_scroller.add(self.mark_list)
        self.side_stack.add_named(marks_scroller, "marks")
        side.pack_start(self.side_stack, True, True, 0)

        self.sidebar.add(side)
        body.pack_start(self.sidebar, False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toggle_side = _icon_button("view-list-symbolic", t("audiolibros", "mostrar_indice"), "ab-round-btn")
        toggle_side.connect("clicked", lambda _b: self.sidebar.set_reveal_child(not self.sidebar.get_reveal_child()))
        top.pack_start(toggle_side, False, False, 0)
        main.pack_start(top, False, False, 0)

        self.textview = Gtk.TextView()
        self.textview.set_name("ab-text")
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD)
        self.textview.set_left_margin(36)
        self.textview.set_right_margin(36)
        self.textview.set_top_margin(8)
        self.textview.set_bottom_margin(40)
        self.textview.connect("button-release-event", self._on_text_click)
        self.textview.connect("scroll-event", self._on_text_scroll)
        self._build_tags()
        text_scroller = Gtk.ScrolledWindow()
        text_scroller.set_name("ab-text-scroller")
        text_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        text_scroller.add(self.textview)
        main.pack_start(text_scroller, True, True, 0)
        body.pack_start(main, True, True, 0)
        page.pack_start(body, True, True, 0)

        page.pack_start(self._build_player_bar(), False, False, 0)
        self.tab_chapters.set_active(True)
        return page

    def _build_tags(self):
        buf = self.textview.get_buffer()
        accent = Gdk.RGBA(*_rgba("myforegroundhover", 0.14))
        highlight = Gdk.RGBA(*_rgba("myforegroundhover", 0.30))
        buf.create_tag("title", scale=2.0, weight=Pango.Weight.BOLD,
                       paragraph_background_rgba=accent, pixels_above_lines=6,
                       pixels_below_lines=14)
        buf.create_tag("author", style=Pango.Style.ITALIC, scale=0.95, pixels_below_lines=22)
        buf.create_tag("h", scale=1.35, weight=Pango.Weight.BOLD,
                       pixels_above_lines=18, pixels_below_lines=12)
        buf.create_tag("p", pixels_below_lines=14)
        buf.create_tag("current", background_rgba=highlight)

    def _build_player_bar(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bar.set_name("ab-player")

        seek_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.elapsed_label = Gtk.Label(label="00:00", xalign=0)
        self.elapsed_label.set_width_chars(8)
        self.elapsed_label.get_style_context().add_class("ab-time")
        seek_row.pack_start(self.elapsed_label, False, False, 0)
        self.seekbar = ChapterSeekBar(lambda pos: self.command("seek", position=pos))
        seek_row.pack_start(self.seekbar, True, True, 0)
        self.remaining_label = Gtk.Label(label="-00:00", xalign=1.0)
        self.remaining_label.set_width_chars(9)
        self.remaining_label.get_style_context().add_class("ab-time")
        seek_row.pack_start(self.remaining_label, False, False, 0)
        bar.pack_start(seek_row, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        back = _icon_button("go-previous-symbolic", t("audiolibros", "volver_biblioteca"))
        back.connect("clicked", lambda _b: self._show_library())
        left.pack_start(back, False, False, 0)
        self.book_title_label = Gtk.Label(xalign=0)
        self.book_title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.book_title_label.set_max_width_chars(30)
        self.book_title_label.get_style_context().add_class("ab-book-title")
        left.pack_start(self.book_title_label, False, False, 0)
        controls.pack_start(left, True, True, 0)

        center = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        rewind = _icon_button("media-seek-backward-symbolic", t("audiolibros", "retroceder", s=REWIND),
                              size=Gtk.IconSize.LARGE_TOOLBAR)
        rewind.connect("clicked", lambda _b: self.command("seek_rel", delta=-REWIND))
        center.pack_start(rewind, False, False, 0)
        self.play_btn = _icon_button("media-playback-start-symbolic", t("audiolibros", "reproducir"),
                                     "ab-play-btn", Gtk.IconSize.DND)
        self.play_btn.connect("clicked", lambda _b: self.command("toggle_pause"))
        center.pack_start(self.play_btn, False, False, 0)
        forward = _icon_button("media-seek-forward-symbolic", t("audiolibros", "adelantar", s=FORWARD),
                               size=Gtk.IconSize.LARGE_TOOLBAR)
        forward.connect("clicked", lambda _b: self.command("seek_rel", delta=FORWARD))
        center.pack_start(forward, False, False, 0)
        controls.set_center_widget(center)

        right = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        right.set_halign(Gtk.Align.END)

        self.volume_revealer = Gtk.Revealer()
        self.volume_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 130, 1)
        self.volume_scale.set_draw_value(False)
        self.volume_scale.set_size_request(110, -1)
        self.volume_scale.connect("value-changed", self._on_volume_changed)
        self.volume_revealer.add(self.volume_scale)
        right.pack_start(self.volume_revealer, False, False, 0)
        volume_btn = _icon_button("audio-volume-high-symbolic", t("audiolibros", "volumen"))
        volume_btn.connect("clicked", lambda _b: self.volume_revealer.set_reveal_child(
            not self.volume_revealer.get_reveal_child()))
        right.pack_start(volume_btn, False, False, 0)

        mark_btn = _icon_button("bookmark-new-symbolic", t("audiolibros", "agregar_marcador"))
        mark_btn.connect("clicked", self._on_add_bookmark)
        right.pack_start(mark_btn, False, False, 0)

        self.left_label = Gtk.Label(xalign=1.0)
        self.left_label.set_width_chars(15)
        self.left_label.get_style_context().add_class("ab-left")
        right.pack_start(self.left_label, False, False, 0)

        self.speed_btn = Gtk.Button(label="1.0x")
        self.speed_btn.set_tooltip_text(t("audiolibros", "velocidad"))
        self.speed_btn.get_style_context().add_class("ab-speed-btn")
        self.speed_btn.connect("clicked", self._on_speed_clicked)
        right.pack_start(self.speed_btn, False, False, 0)

        menu_btn = _icon_button("view-more-symbolic", t("audiolibros", "menu"))
        menu_btn.connect("clicked", self._on_menu_clicked)
        right.pack_start(menu_btn, False, False, 0)
        controls.pack_end(right, True, True, 0)

        bar.pack_start(controls, False, False, 0)
        return bar

    def _open_reader(self, book_id):
        if book_id == self.book_id and self.book:
            self.stack.set_visible_child_name("reader")
            return False

        def work():
            book = library.load_book(book_id)
            GLib.idle_add(self._show_book, book_id, book)
        threading.Thread(target=work, daemon=True).start()
        return False

    def _show_book(self, book_id, book):
        if not book:
            return False
        self.book_id, self.book = book_id, book
        self.book_title_label.set_text(book["title"])
        self.book_title_label.set_tooltip_text(book["title"])
        self.seekbar.set_book(book["duration"], book["chapters"])
        self._fill_text(book)
        self._fill_chapters(book)
        self.current_sentence = self.current_chapter = -1
        self.bookmarks_sig = None
        self.last_user_scroll = 0
        self.stack.set_visible_child_name("reader")
        return False

    def _fill_text(self, book):
        buf = self.textview.get_buffer()
        buf.set_text("")
        self.s_times = []
        self.s_offsets = []
        it = buf.get_end_iter()
        for block in book["blocks"]:
            tag = block["t"] if block["t"] in ("title", "author", "h") else "p"
            for n, (text, start, end) in enumerate(block["s"]):
                if n:
                    buf.insert_with_tags_by_name(it, " ", tag)
                so = it.get_offset()
                buf.insert_with_tags_by_name(it, text, tag)
                self.s_times.append(start)
                self.s_offsets.append((so, it.get_offset()))
            buf.insert_with_tags_by_name(it, "\n", tag)
        buf.place_cursor(buf.get_start_iter())
        GLib.idle_add(lambda: self.textview.get_parent().get_vadjustment().set_value(0))

    def _fill_chapters(self, book):
        for child in self.chapter_list.get_children():
            self.chapter_list.remove(child)
        self.chapter_starts = []
        for chapter in book["chapters"]:
            row = Gtk.ListBoxRow()
            row.start = chapter["start"]
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.get_style_context().add_class("ab-chapter")
            box.set_margin_start(8 + 14 * min(chapter["depth"], 3))
            title = Gtk.Label(label=chapter["title"], xalign=0)
            title.set_line_wrap(True)
            title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            title.set_max_width_chars(1)
            title.set_hexpand(True)
            title.get_style_context().add_class("ab-chapter-title")
            box.pack_start(title, True, True, 0)
            when = Gtk.Label(label=fmt_clock(chapter["start"]), xalign=1.0)
            when.set_width_chars(7)
            when.set_valign(Gtk.Align.START)
            when.get_style_context().add_class("ab-chapter-time")
            box.pack_start(when, False, False, 0)
            row.add(box)
            self.chapter_list.add(row)
            self.chapter_starts.append(chapter["start"])
        self.chapter_list.show_all()

    def _fill_bookmarks(self, marks):
        sig = [(m["position"], m["text"]) for m in marks]
        if sig == self.bookmarks_sig:
            return
        self.bookmarks_sig = sig
        for child in self.mark_list.get_children():
            self.mark_list.remove(child)
        for idx, mark in enumerate(marks):
            row = Gtk.ListBoxRow()
            row.position = mark["position"]
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            box.get_style_context().add_class("ab-chapter")
            when = Gtk.Label(label=fmt_clock(mark["position"]), xalign=1.0)
            when.set_width_chars(7)
            when.set_valign(Gtk.Align.START)
            when.get_style_context().add_class("ab-chapter-time")
            box.pack_start(when, False, False, 0)
            text = Gtk.Label(label=mark["text"], xalign=0)
            text.set_line_wrap(True)
            text.set_max_width_chars(1)
            text.set_lines(3)
            text.set_ellipsize(Pango.EllipsizeMode.END)
            text.set_hexpand(True)
            text.get_style_context().add_class("ab-chapter-title")
            box.pack_start(text, True, True, 0)
            remove = _icon_button("user-trash-symbolic", t("audiolibros", "quitar_marcador"))
            remove.set_valign(Gtk.Align.START)
            remove.connect("clicked", lambda _b, i=idx: self.command("remove_bookmark", index=i))
            box.pack_start(remove, False, False, 0)
            row.add(box)
            self.mark_list.add(row)
        self.mark_list.show_all()

    def _show_library(self):
        self.stack.set_visible_child_name("library")
        self._reload_library()

    # ---- sincronía texto/audio ----------------------------------------------

    def _sync_reader(self, player):
        position = player["position"]
        self.seekbar.set_position(position)
        self.elapsed_label.set_text(fmt_clock(position))
        self.remaining_label.set_text("-" + fmt_clock(player["duration"] - position))
        left = (player["duration"] - position) / (player["speed"] or 1)
        self.left_label.set_text(t("audiolibros", "restante", time=fmt_left(left)))
        self.speed_btn.set_label(f"{player['speed']:g}x")

        playing = not player["paused"]
        icon = "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
        self.play_btn.get_child().set_from_icon_name(icon, Gtk.IconSize.DND)
        self.play_btn.set_tooltip_text(t("audiolibros", "pausar" if playing else "reproducir"))

        if not self.volume_revealer.get_reveal_child():
            self.volume_lock = True
            self.volume_scale.set_value(player["volume"])
            self.volume_lock = False

        self._fill_bookmarks(player.get("bookmarks") or [])

        idx = bisect.bisect_right(self.s_times, position + 0.05) - 1
        if idx != self.current_sentence and 0 <= idx < len(self.s_offsets):
            buf = self.textview.get_buffer()
            buf.remove_tag_by_name("current", buf.get_start_iter(), buf.get_end_iter())
            so, eo = self.s_offsets[idx]
            start_iter = buf.get_iter_at_offset(so)
            buf.apply_tag_by_name("current", start_iter, buf.get_iter_at_offset(eo))
            self.current_sentence = idx
            if time.time() - self.last_user_scroll > FOLLOW_GRACE:
                self.textview.scroll_to_iter(start_iter, 0.1, True, 0.0, 0.3)

        chapter = bisect.bisect_right(self.chapter_starts, position + 0.05) - 1
        if chapter != self.current_chapter:
            rows = self.chapter_list.get_children()
            if 0 <= self.current_chapter < len(rows):
                rows[self.current_chapter].get_child().get_style_context().remove_class("ab-chapter-current")
            if 0 <= chapter < len(rows):
                rows[chapter].get_child().get_style_context().add_class("ab-chapter-current")
                GLib.idle_add(self._scroll_chapter_into_view, rows[chapter])
            self.current_chapter = chapter

    def _scroll_chapter_into_view(self, row):
        alloc = row.get_allocation()
        adj = self.chapter_scroller.get_vadjustment()
        if alloc.y < adj.get_value() or alloc.y + alloc.height > adj.get_value() + adj.get_page_size():
            adj.set_value(max(0, alloc.y - adj.get_page_size() / 3))
        return False

    def _on_text_scroll(self, *_args):
        self.last_user_scroll = time.time()
        return False

    def _on_text_click(self, textview, event):
        if event.button != 1 or textview.get_buffer().get_has_selection() or not self.s_offsets:
            return False
        x, y = textview.window_to_buffer_coords(Gtk.TextWindowType.TEXT, int(event.x), int(event.y))
        found, it = textview.get_iter_at_location(x, y)
        if not found:
            return False
        offset = it.get_offset()
        idx = max(0, bisect.bisect_right([o[0] for o in self.s_offsets], offset) - 1)
        self.last_user_scroll = 0
        self.command("seek", position=self.s_times[idx])
        return False

    def _on_chapter_activated(self, _list, row):
        self.last_user_scroll = 0
        self.command("seek", position=row.start)

    def _on_mark_activated(self, _list, row):
        self.last_user_scroll = 0
        self.command("seek", position=row.position)

    def _on_tab_toggled(self, btn):
        if not btn.get_active():
            if not (self.tab_chapters.get_active() or self.tab_marks.get_active()):
                btn.set_active(True)
            return
        other = self.tab_marks if btn is self.tab_chapters else self.tab_chapters
        other.set_active(False)
        self.side_stack.set_visible_child_name("chapters" if btn is self.tab_chapters else "marks")

    def _on_add_bookmark(self, _btn):
        self.command("add_bookmark")
        self.tab_marks.set_active(True)
        self.sidebar.set_reveal_child(True)

    def _on_volume_changed(self, scale):
        if not self.volume_lock:
            self.command("set_volume", volume=int(scale.get_value()))

    def _popup_menu(self, widget, items):
        menu = Gtk.Menu()
        menu.get_style_context().add_class("ab-menu")
        for label, callback, checked in items:
            if checked is None:
                item = Gtk.MenuItem(label=label)
            else:
                item = Gtk.CheckMenuItem(label=label)
                item.set_active(checked)
                item.set_draw_as_radio(True)
            item.connect("activate", lambda _i, cb=callback: cb())
            menu.append(item)
        menu.show_all()
        menu.attach_to_widget(widget, None)
        menu.popup_at_widget(widget, Gdk.Gravity.NORTH, Gdk.Gravity.SOUTH, None)

    def _on_speed_clicked(self, btn):
        current = self.player["speed"] if self.player else 1.0
        self._popup_menu(btn, [(f"{s:g}x", lambda s=s: self.command("set_speed", speed=s),
                                abs(s - current) < 0.01) for s in SPEEDS])

    def _on_menu_clicked(self, btn):
        book_id = self.book_id
        title = self.book["title"] if self.book else ""
        self._popup_menu(btn, [
            (t("audiolibros", "abrir_carpeta"), lambda: subprocess.Popen(
                ["xdg-open", library.book_dir(book_id)], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True), None),
            (t("audiolibros", "eliminar"), lambda: self._confirm_delete(book_id, title), None),
        ])

    # ---- daemon ----------------------------------------------------------------

    def _start_daemon(self):
        audiobook_ipc.ensure_daemon_running()
        state = audiobook_ipc.send_command("get_state")
        if state and state.get("player"):
            GLib.idle_add(self._open_reader, state["player"]["book_id"])
        GLib.idle_add(self._poll)
        GLib.timeout_add(POLL_MS, self._poll)

    def _poll(self):
        if not self._poll_in_flight:
            self._poll_in_flight = True
            threading.Thread(target=self._fetch_state, daemon=True).start()
        return True

    def _fetch_state(self):
        GLib.idle_add(self._apply_state, audiobook_ipc.send_command("get_state"))

    def _apply_state(self, state):
        self._poll_in_flight = False
        if state is None:
            return False
        if self.library_version is not None and state["library_version"] != self.library_version:
            self._reload_library()
        self.library_version = state["library_version"]
        self._update_conversions(state["conversions"])

        self.player = state.get("player")
        if self.player and self.player["book_id"] == self.book_id and self.book:
            self._sync_reader(self.player)
        return False

    def command(self, cmd, then=None, **params):
        def work():
            audiobook_ipc.send_command(cmd, timeout=5, **params)
            if then:
                then()
            GLib.idle_add(self._poll)
        threading.Thread(target=work, daemon=True).start()

    # ---- ciclo de vida ------------------------------------------------------------

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()
            return True
        if self.stack.get_visible_child_name() != "reader":
            return False
        if event.keyval == Gdk.KEY_space:
            self.command("toggle_pause")
            return True
        if event.keyval == Gdk.KEY_Left:
            self.command("seek_rel", delta=-REWIND)
            return True
        if event.keyval == Gdk.KEY_Right:
            self.command("seek_rel", delta=FORWARD)
            return True
        return False

    def _on_destroy(self, *_args):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    AudiobookPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

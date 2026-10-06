#!/usr/bin/env python3
"""Popup "Descargas" (SUPER+D) -- gestor de descargas tipo JDownloader
(MediaFire y YouTube con `ytd`/`ytdv`; un enlace de YouTube espera en
"choosing" a que se elija el formato en su fila). Sin estado propio: la cola vive en
download_daemon.py (cerrar el popup no corta nada), este solo sondea
`get_state` cada 1s y manda comandos."""

import json
import os
import subprocess
import sys
import threading
from collections import deque

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))
from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top
from common import atomic_write, run_async
from i18n import t
import downloads_ipc

CSS_FILE = os.path.join(SCRIPT_DIR, "download_popup.css")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "downloads-config.json")
LOCK = "/tmp/download-popup.pid"
POLL_MS = 1000
SPEED_WINDOW = 30

STATUS_CODES = {
    "pending": "estado_analizando", "choosing": "estado_elegir", "queued": "estado_en_cola",
    "downloading": "estado_descargando", "done": "estado_completado",
    "error": "estado_error", "canceled": "estado_cancelado",
}
ACTIVE = ("pending", "choosing", "queued", "downloading")


def _default_dest():
    try:
        out = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True,
                             text=True, timeout=2).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.TimeoutExpired):
        pass
    return os.path.expanduser("~/Descargas")


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    except (OSError, ValueError):
        config = {}
    if not (config.get("dest") and os.path.isdir(config["dest"])):
        config["dest"] = _default_dest()
    return config


def save_config(config):
    atomic_write(CONFIG_FILE, json.dumps(config))


def format_label(option):
    fid = option["id"]
    if fid == "audio":
        text = t("descargas", "formato_audio")
    elif fid == "best":
        text = t("descargas", "formato_mejor")
    else:
        text = t("descargas", "formato_video", height=fid)
    return f"{text}  ·  ~{human_size(option['size'])}" if option.get("size") else text


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def human_eta(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _icon_button(icon, tooltip, css_class, callback):
    btn = Gtk.Button()
    btn.add(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR))
    btn.set_tooltip_text(tooltip)
    btn.set_valign(Gtk.Align.CENTER)
    btn.get_style_context().add_class(css_class)
    btn.connect("clicked", lambda _b: callback())
    return btn


class DownloadRow(Gtk.ListBoxRow):
    def __init__(self, item, popup):
        super().__init__()
        self.set_selectable(False)
        self.set_activatable(False)
        self.item_id = item["id"]
        self.popup = popup
        self.signature = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.get_style_context().add_class("dl-row")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.name_label = Gtk.Label(xalign=0)
        self.name_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.name_label.set_max_width_chars(48)
        self.name_label.get_style_context().add_class("dl-name")
        top.pack_start(self.name_label, True, True, 0)

        self.status_label = Gtk.Label(xalign=1.0)
        self.status_label.set_width_chars(12)
        self.status_label.get_style_context().add_class("dl-status")
        top.pack_start(self.status_label, False, False, 0)

        self.open_btn = _icon_button("folder-open-symbolic", t("descargas", "abrir_carpeta"),
                                     "dl-row-btn", self._open_folder)
        self.retry_btn = _icon_button("view-refresh-symbolic", t("descargas", "reintentar"),
                                      "dl-row-btn", lambda: popup.command("retry", id=self.item_id))
        self.cancel_btn = _icon_button("process-stop-symbolic", t("descargas", "cancelar"),
                                       "dl-row-btn", lambda: popup.command("cancel", id=self.item_id))
        self.remove_btn = _icon_button("user-trash-symbolic", t("descargas", "quitar"),
                                       "dl-row-btn", lambda: popup.command("remove", id=self.item_id))
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        actions.set_halign(Gtk.Align.END)
        for btn in (self.open_btn, self.retry_btn, self.cancel_btn, self.remove_btn):
            btn.get_child().show()
            btn.set_no_show_all(True)
            actions.pack_start(btn, False, False, 0)
        popup.actions_group.add_widget(actions)
        top.pack_start(actions, False, False, 0)

        box.pack_start(top, False, False, 0)

        self.format_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.format_combo = Gtk.ComboBoxText()
        self.format_combo.get_style_context().add_class("dl-format-combo")
        self.format_row.pack_start(self.format_combo, True, True, 0)
        start_btn = Gtk.Button(label=t("descargas", "descargar"))
        start_btn.get_style_context().add_class("dl-add-btn")
        start_btn.connect("clicked", self._on_pick_format)
        self.format_row.pack_start(start_btn, False, False, 0)
        self.format_row.show_all()
        self.format_row.set_no_show_all(True)
        self.formats = None
        box.pack_start(self.format_row, False, False, 0)

        self.progress = Gtk.ProgressBar()
        box.pack_start(self.progress, False, False, 0)

        self.detail_label = Gtk.Label(xalign=0)
        self.detail_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.detail_label.get_style_context().add_class("dl-detail")
        box.pack_start(self.detail_label, False, False, 0)

        self.add(box)
        self.item = item
        self.update(item)

    def update(self, item):
        self.item = item
        sig = (item["name"], item["status"], item["done"], item["size"], item["speed"], item.get("error"))
        if sig == self.signature:
            return
        self.signature = sig

        status = item["status"]
        self.name_label.set_text(item["name"])
        self.name_label.set_tooltip_text(item.get("path") or item["name"])
        self.status_label.set_text(t("descargas", STATUS_CODES.get(status, "estado_en_cola")))

        ctx = self.get_style_context()
        for s in STATUS_CODES:
            ctx.remove_class(f"dl-{s}")
        ctx.add_class(f"dl-{status}")

        size, done = item["size"], item["done"]
        fraction = done / size if size else 0
        if status == "done":
            fraction = 1
        self.progress.set_fraction(min(fraction, 1))

        if status == "error":
            detail = t("descargas", item.get("error") or "error_red")
        elif status == "downloading":
            parts = [f"{human_size(done)} / {human_size(size)}" if size else human_size(done)]
            if item["speed"]:
                parts.append(f"{human_size(item['speed'])}/s")
                if size:
                    parts.append(human_eta((size - done) / item["speed"]))
            detail = "  ·  ".join(parts)
        elif status == "pending":
            detail = t("descargas", "detalle_analizando")
        else:
            detail = human_size(size) if size else ""
        self.detail_label.set_text(detail)

        choosing = status == "choosing"
        if choosing and item.get("formats") != self.formats:
            self.formats = item["formats"]
            self.format_combo.remove_all()
            for option in self.formats:
                self.format_combo.append(option["id"], format_label(option))
            ids = [o["id"] for o in self.formats]
            last = self.popup.config.get("format")
            self.format_combo.set_active_id(last if last in ids else ids[0])
        self.format_row.set_visible(choosing)
        self.progress.set_visible(not choosing)
        self.detail_label.set_visible(not choosing and bool(detail))

        self.open_btn.set_visible(status == "done")
        self.retry_btn.set_visible(status in ("error", "canceled"))
        self.cancel_btn.set_visible(status in ACTIVE)
        self.remove_btn.set_visible(status not in ACTIVE)

    def _on_pick_format(self, _btn):
        fmt = self.format_combo.get_active_id()
        if fmt:
            self.popup.remember_format(fmt)
            self.popup.command("set_format", id=self.item_id, format=fmt)

    def _open_folder(self):
        path = self.item.get("path")
        folder = os.path.dirname(path) if path else self.item.get("dest")
        if folder:
            subprocess.Popen(["xdg-open", folder], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)


class DownloadPopup:
    def __init__(self):
        self.window, container = build_layer_window("download-popup", CSS_FILE)
        container.set_name("dl-container")
        position_fixed_top(container, margin=60)

        self.config = load_config()
        self.dest = self.config["dest"]
        self.rows = {}
        self.order = []
        self.paused = False
        self._poll_in_flight = False
        self.speed_samples = deque(maxlen=SPEED_WINDOW)
        self.actions_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_halign(Gtk.Align.CENTER)
        title = Gtk.Label(label=t("descargas", "titulo"))
        title.set_name("dl-title")
        header.pack_start(title, False, False, 0)
        self.count_label = Gtk.Label(label="0")
        self.count_label.set_name("dl-count")
        self.count_label.set_valign(Gtk.Align.CENTER)
        header.pack_start(self.count_label, False, False, 0)
        container.pack_start(header, False, False, 0)

        links_title = Gtk.Label(label=t("descargas", "enlaces"), xalign=0)
        links_title.get_style_context().add_class("dl-section-title")
        container.pack_start(links_title, False, False, 0)

        self.textview = Gtk.TextView()
        self.textview.set_name("dl-textview")
        self.textview.set_wrap_mode(Gtk.WrapMode.CHAR)
        self.textview.set_tooltip_text(t("descargas", "enlaces_tooltip"))
        text_scroller = Gtk.ScrolledWindow()
        text_scroller.set_name("dl-text-scroller")
        text_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        text_scroller.set_min_content_height(110)
        text_scroller.set_max_content_height(110)
        text_scroller.add(self.textview)
        container.pack_start(text_scroller, False, False, 0)

        dest_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dest_row.set_name("dl-dest-row")

        dest_btn = Gtk.Button()
        dest_btn.get_style_context().add_class("dl-dest-btn")
        dest_btn.set_tooltip_text(t("descargas", "carpeta_tooltip"))
        dest_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dest_inner.pack_start(Gtk.Image.new_from_icon_name("folder-symbolic", Gtk.IconSize.SMALL_TOOLBAR),
                              False, False, 0)
        self.dest_label = Gtk.Label(xalign=0)
        self.dest_label.set_ellipsize(Pango.EllipsizeMode.START)
        self.dest_label.set_max_width_chars(40)
        dest_inner.pack_start(self.dest_label, True, True, 0)
        dest_btn.add(dest_inner)
        dest_btn.connect("clicked", self._on_pick_dest)
        dest_row.pack_start(dest_btn, True, True, 0)

        add_btn = Gtk.Button(label=t("descargas", "agregar"))
        add_btn.get_style_context().add_class("dl-add-btn")
        add_btn.connect("clicked", self._on_add)
        dest_row.pack_start(add_btn, False, False, 0)
        container.pack_start(dest_row, False, False, 0)

        self.feedback_label = Gtk.Label(xalign=0)
        self.feedback_label.get_style_context().add_class("dl-feedback")
        container.pack_start(self.feedback_label, False, False, 0)

        queue_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        queue_row.set_name("dl-queue-row")
        queue_title = Gtk.Label(label=t("descargas", "cola"), xalign=0)
        queue_title.get_style_context().add_class("dl-section-title")
        queue_row.pack_start(queue_title, False, False, 0)

        self.summary_label = Gtk.Label(xalign=1.0)
        self.summary_label.get_style_context().add_class("dl-summary")
        queue_row.pack_start(self.summary_label, True, True, 8)

        self.pause_btn = _icon_button("media-playback-pause-symbolic", t("descargas", "pausar"),
                                      "dl-row-btn", self._on_toggle_pause)
        queue_row.pack_start(self.pause_btn, False, False, 0)
        queue_row.pack_start(_icon_button("edit-clear-all-symbolic", t("descargas", "limpiar"),
                                          "dl-row-btn", lambda: self.command("clear_finished")),
                             False, False, 0)
        container.pack_start(queue_row, False, False, 0)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.listbox.get_style_context().add_class("dl-list")
        self.empty_label = Gtk.Label(label=t("descargas", "cola_vacia"))
        self.empty_label.get_style_context().add_class("dl-empty")
        self.listbox.set_placeholder(self.empty_label)
        self.empty_label.show()

        list_scroller = Gtk.ScrolledWindow()
        list_scroller.set_name("dl-list-scroller")
        list_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroller.set_min_content_height(300)
        list_scroller.set_max_content_height(300)
        list_scroller.add(self.listbox)
        container.pack_start(list_scroller, True, True, 0)

        self._sync_dest_label()

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()
        self.textview.grab_focus()

        threading.Thread(target=self._start_daemon, daemon=True).start()

    # ---- daemon ----------------------------------------------------------

    def _start_daemon(self):
        downloads_ipc.ensure_daemon_running()
        GLib.idle_add(self._poll)
        GLib.timeout_add(POLL_MS, self._poll)

    def _poll(self):
        if not self._poll_in_flight:
            self._poll_in_flight = True
            threading.Thread(target=self._fetch_state, daemon=True).start()
        return True

    def _fetch_state(self):
        state = downloads_ipc.send_command("get_state")
        GLib.idle_add(self._apply_state, state)

    def _apply_state(self, state):
        self._poll_in_flight = False
        if state is None:
            self.summary_label.set_text(t("descargas", "daemon_sin_respuesta"))
            return False

        items = state["items"]
        self.paused = state.get("paused", False)
        self._sync_pause_button()

        ids = [i["id"] for i in items]
        if ids != self.order:
            for child in self.listbox.get_children():
                self.listbox.remove(child)
            old = self.rows
            self.rows = {}
            for item in items:
                row = old.get(item["id"]) or DownloadRow(item, self)
                row.update(item)
                self.rows[item["id"]] = row
                self.listbox.add(row)
            self.listbox.show_all()
            for row in self.rows.values():
                row.signature = None
                row.update(row.item)
            self.order = ids
        else:
            for item in items:
                self.rows[item["id"]].update(item)

        self._update_summary(items)
        return False

    def _update_summary(self, items):
        total = len(items)
        done = sum(1 for i in items if i["status"] == "done")
        speed = sum(i["speed"] for i in items)
        self.count_label.set_text(str(total))

        if speed:
            self.speed_samples.append(speed)
        if not any(i["status"] == "downloading" for i in items):
            self.speed_samples.clear()

        parts = [t("descargas", "resumen", done=done, total=total)]
        if self.paused and any(i["status"] in ACTIVE for i in items):
            parts.append(t("descargas", "en_pausa"))
        elif self.speed_samples:
            avg = sum(self.speed_samples) / len(self.speed_samples)
            remaining = sum(max(i["size"] - i["done"], 0) for i in items
                            if i["status"] in ("queued", "downloading"))
            parts.append(f"{human_size(avg)}/s")
            if remaining:
                key = "restante_global_min" if any(i["status"] == "pending" for i in items) else "restante_global"
                parts.append(t("descargas", key, eta=human_eta(remaining / avg)))
        self.summary_label.set_text("  ·  ".join(parts))

    def command(self, cmd, **params):
        def work():
            downloads_ipc.send_command(cmd, **params)
            GLib.idle_add(self._poll)
        threading.Thread(target=work, daemon=True).start()

    # ---- acciones --------------------------------------------------------

    def _on_add(self, _btn):
        buf = self.textview.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        if not text.strip():
            return
        dest = self.dest

        def work():
            downloads_ipc.ensure_daemon_running()
            reply = downloads_ipc.send_command("add", text=text, dest=dest)
            GLib.idle_add(self._on_added, reply)
        threading.Thread(target=work, daemon=True).start()

    def _on_added(self, reply):
        if reply is None:
            self.feedback_label.set_text(t("descargas", "daemon_sin_respuesta"))
        elif reply.get("added"):
            self.textview.get_buffer().set_text("")
            self.feedback_label.set_text(t("descargas", "agregados", count=reply["added"]))
        else:
            self.feedback_label.set_text(t("descargas", "sin_enlaces"))
        self._poll()
        return False

    def _on_toggle_pause(self):
        self.paused = not self.paused
        self._sync_pause_button()
        self.command("set_paused", paused=self.paused)

    def _sync_pause_button(self):
        icon = "media-playback-start-symbolic" if self.paused else "media-playback-pause-symbolic"
        self.pause_btn.get_child().set_from_icon_name(icon, Gtk.IconSize.SMALL_TOOLBAR)
        self.pause_btn.set_tooltip_text(t("descargas", "reanudar" if self.paused else "pausar"))

    def _sync_dest_label(self):
        home = os.path.expanduser("~")
        shown = "~" + self.dest[len(home):] if self.dest.startswith(home) else self.dest
        self.dest_label.set_text(shown)

    def _on_pick_dest(self, _btn):
        dialog = Gtk.FileChooserDialog(title=t("descargas", "carpeta_dialogo"),
                                       transient_for=self.window,
                                       action=Gtk.FileChooserAction.SELECT_FOLDER)
        dialog.add_button(t("descargas", "cancelar"), Gtk.ResponseType.CANCEL)
        dialog.add_button(t("descargas", "elegir"), Gtk.ResponseType.OK)
        dialog.set_current_folder(self.dest)

        original_prgname = GLib.get_prgname()
        GLib.set_prgname("downloads-folder-dialog")
        dialog.show_all()
        GLib.set_prgname(original_prgname)

        self.window.hide()
        response = dialog.run()
        folder = dialog.get_filename()
        dialog.destroy()
        self.window.show()

        if response == Gtk.ResponseType.OK and folder:
            self.dest = self.config["dest"] = folder
            self._sync_dest_label()
            config = dict(self.config)
            run_async(lambda: save_config(config))

    def remember_format(self, fmt):
        self.config["format"] = fmt
        config = dict(self.config)
        run_async(lambda: save_config(config))

    # ---- ciclo de vida ---------------------------------------------------

    def _on_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_args):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    DownloadPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

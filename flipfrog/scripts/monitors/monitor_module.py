#!/usr/bin/env python3
"""monitor_module.py
Tab "Monitores" del dashboard (build_monitor_tab) -- canvas dibujado
(cairo) con un rectángulo por salida conectada, mismo criterio visual
que el panel de pantallas de Windows: arrastrar reposiciona un monitor
respecto de los otros (con snap a los bordes), un ícono en la esquina
rota. Clic en un rectángulo carga sus controles (Resolución/Escala/
Modo/Encendido/Principal) en el panel de abajo -- nunca se muestran
todos los monitores a la vez. Estado real vía `hyprctl monitors -j -a`
(monitor_config.py); "Guardar y aplicar" escribe hypr/monitor-config.lua
y dispara `hyprctl reload` -- monitors.lua (hypr/config/) es quien
reaplica esto en cada reload real, ver ese archivo para el porqué de
"reaplicar siempre, sin excepción" (modo cine).
"""

import math
import os
import sys
from contextlib import contextmanager

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "toggles"))
from waybar_lib import load_module_css, _icon_text_button, svg_icon_image, build_switch_row
import common
from i18n import t
from oled_shader_toggle import build_oled_shader_toggle

import monitor_config

MODULE_CSS = os.path.join(SCRIPT_DIR, "monitor_module.css")
SAVE_ICON = os.path.join(SCRIPT_DIR, "icons", "save.svg")
MONITOR_ICON = os.path.join(SCRIPT_DIR, "icons", "monitor.svg")
STAR_ICON = os.path.join(SCRIPT_DIR, "icons", "star.svg")
TOUCH_ICON = os.path.join(SCRIPT_DIR, "icons", "touch.svg")

CANVAS_HEIGHT = 180
CANVAS_MARGIN = 12
ROTATE_ICON_RADIUS = 9
SNAP_THRESHOLD = 40  # px lógicos


def _set_combo_active_text(combo, text):
    """Gtk.ComboBoxText no tiene un "seleccionar por texto" nativo --
    busca `text` en el modelo y cae al primer ítem si no lo encuentra
    (modo actual no listado, ver CLAUDE.md "DP-3 usa un modo de refresh
    custom no listado" -- monitor_config.available_mode_keywords ya lo
    inserta a mano, así que esto siempre debería encontrarlo)."""
    for i, row in enumerate(combo.get_model()):
        if row[0] == text:
            combo.set_active(i)
            return
    combo.set_active(0)


class MonitorConfigurator:
    def __init__(self, container):
        self.monitors = monitor_config.live_monitors()
        self.outputs = [m["name"] for m in self.monitors]
        self.by_output = {m["name"]: m for m in self.monitors}
        self.touch_devices = monitor_config.live_touch_devices()
        self.touch_assignments = monitor_config.load_touch_assignments()

        # Sin label "Monitores" -- redundante con el nombre de la pestaña
        # (mismo criterio que "Bluetooth"/"Red", ver bluetooth_tab.css).
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.get_style_context().add_class("module")
        box.get_style_context().add_class("monitor-module")

        if not self.monitors:
            empty = Gtk.Label(label=t("monitores", "sin_hyprctl"))
            empty.get_style_context().add_class("monitor-empty")
            box.pack_start(empty, True, True, 0)
            container.pack_start(box, True, True, 0)
            return

        # Estado editable en memoria -- un solo panel visible a la vez
        # (el seleccionado), así que los cambios de CUALQUIER monitor
        # (posición/rotación por canvas, resolución/escala/modo por el
        # panel) tienen que persistir acá, no solo en widgets vivos.
        self.pending = {}
        for mon in self.monitors:
            self.pending[mon["name"]] = {
                "mode": monitor_config.current_mode_keyword(mon),
                "scale": mon.get("scale", 1.0),
                "transform": mon.get("transform", 0),
                "disabled": mon.get("disabled", False),
                "mirror_of": None,
                "x": mon["x"],
                "y": mon["y"],
            }
        self.primary_output = next(
            (m["name"] for m in self.monitors if m.get("focused")), self.outputs[0])
        self.selected_output = self.primary_output
        self._drag = None
        self._canvas_scale = 1.0
        self._mirror_options = [None]

        # ---- Global (switches que no son por-monitor) ----------------
        box.pack_start(self._build_section_header(t("monitores", "seccion_global")), False, False, 0)

        # "Alto Contraste" -- movido acá desde "Sistema" (pedido explícito
        # del usuario, 2026-09-06). Switch GLOBAL, no por monitor --
        # decoration:screen_shader de Hyprland es un efecto único sobre
        # TODO el compositor, sin variante por salida (confirmado contra
        # el binario real: CHyprOpenGLImpl::applyScreenShader toma un
        # solo string, sin parámetro de monitor, y
        # "decoration:screen_shader" es la única clave de config
        # relacionada). Va en su propia sección "Global" (pedido
        # explícito del usuario) para que no se lea como un control por
        # salida como el resto de la pestaña.
        contrast_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        contrast_row.set_name("monitor-contrast-row")
        build_oled_shader_toggle(contrast_row)
        box.pack_start(contrast_row, False, False, 0)

        # ---- Individual (canvas + panel por monitor seleccionado) ----
        box.pack_start(self._build_section_header(t("monitores", "seccion_individual")), False, False, 0)

        self.canvas = Gtk.DrawingArea()
        self.canvas.set_name("monitor-canvas")
        self.canvas.set_size_request(-1, CANVAS_HEIGHT)
        self.canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK)
        self.canvas.connect("draw", self._on_canvas_draw)
        self.canvas.connect("button-press-event", self._on_canvas_press)
        self.canvas.connect("motion-notify-event", self._on_canvas_motion)
        self.canvas.connect("button-release-event", self._on_canvas_release)
        box.pack_start(self.canvas, True, True, 0)

        box.pack_start(self._build_panel(), False, False, 0)

        apply_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        apply_row.set_name("monitor-apply-row")

        self.status_label = Gtk.Label(label="")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.get_style_context().add_class("monitor-status-label")
        apply_row.pack_start(self.status_label, True, True, 0)

        self.apply_btn = _icon_text_button(SAVE_ICON, t("monitores", "guardar_aplicar"), "")
        self.apply_btn.get_style_context().add_class("monitor-apply-btn")
        self.apply_btn.set_tooltip_text(t("monitores", "aplicar_tooltip"))
        self.apply_btn.connect("clicked", self._on_apply)
        apply_row.pack_start(self.apply_btn, False, False, 0)

        box.pack_start(apply_row, False, False, 0)

        container.pack_start(box, True, True, 0)

        self._refresh_panel()

    def _build_section_header(self, text):
        """Título + línea -- mismo patrón que theme_module.py
        (title_row/theme-title/theme-title-divider), separa "Global" de
        "Individual" (pedido explícito del usuario, 2026-09-06: Alto
        Contraste es global, el resto de la pestaña es por monitor)."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_name("monitor-section-row")

        title = Gtk.Label(label=text)
        title.set_name("monitor-section-title")
        row.pack_start(title, False, False, 0)

        divider = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        divider.set_name("monitor-section-divider")
        divider.set_valign(Gtk.Align.CENTER)
        row.pack_start(divider, True, True, 0)

        return row

    # ---- Panel del monitor seleccionado -----------------------------------

    def _build_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.get_style_context().add_class("monitor-panel")

        # Encendido/Principal/Touch en una sola fila, columnas homogéneas
        # -- mismo componente compartido que los toggles de "Sistema"/
        # "Panel" (ícono en chip + etiqueta + Gtk.Switch, click en
        # cualquier parte de la fila togglea). Touch es una columna más
        # por touchscreen detectado (normalmente una sola) -- mismo
        # patrón que Principal: un solo dueño a la vez, el switch del
        # dueño actual queda insensible (togglear "afuera" se hace
        # prendiendo el de OTRO monitor, no apagando el propio).
        toggles_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        toggles_row.set_homogeneous(True)
        panel.pack_start(toggles_row, False, False, 0)

        enabled_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toggles_row.pack_start(enabled_col, True, True, 0)
        enabled_wrapper, self.enabled_switch, self._enabled_handler = build_switch_row(
            enabled_col, svg_icon_image(MONITOR_ICON, size=15),
            "", False, self._on_enabled_state_set)
        self.enabled_label = enabled_wrapper.get_children()[1]

        primary_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toggles_row.pack_start(primary_col, True, True, 0)
        primary_wrapper, self.primary_switch, self._primary_handler = build_switch_row(
            primary_col, svg_icon_image(STAR_ICON, size=15),
            t("monitores", "principal"), False, self._on_primary_state_set)

        self.touch_switches = {}
        for device in self.touch_devices:
            touch_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            toggles_row.pack_start(touch_col, True, True, 0)
            label_text = t("monitores", "touch") if len(self.touch_devices) == 1 else device
            touch_wrapper, touch_switch, touch_handler = build_switch_row(
                touch_col, svg_icon_image(TOUCH_ICON, size=15),
                label_text, False, lambda state, d=device: self._on_touch_state_set(state, d))
            if len(self.touch_devices) > 1:
                touch_wrapper.get_children()[1].set_tooltip_text(device)
            self.touch_switches[device] = (touch_switch, touch_handler)

        # Resolución + Escala + Modo en una sola fila, 1/3 cada una
        # (homogeneous) -- antes eran 3 filas de Gtk.Grid separadas con
        # ancho natural dispar (el spinbutton de Escala mucho más
        # angosto que los combobox).
        fields_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        fields_row.set_homogeneous(True)
        fields_row.get_style_context().add_class("monitor-fields-row")

        def add_field(text, widget):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            label = Gtk.Label(label=text)
            label.set_halign(Gtk.Align.START)
            label.get_style_context().add_class("monitor-field-label")
            col.pack_start(label, False, False, 0)
            widget.set_hexpand(True)
            col.pack_start(widget, True, True, 0)
            fields_row.pack_start(col, True, True, 0)

        self.mode_combo = Gtk.ComboBoxText()
        self.mode_combo.get_style_context().add_class("monitor-mode-combo")
        self._mode_handler = self.mode_combo.connect("changed", self._on_mode_changed)
        add_field(t("monitores", "resolucion"), self.mode_combo)

        self.scale_spin = Gtk.SpinButton.new_with_range(0.5, 3.0, 0.05)
        self.scale_spin.set_digits(2)
        # Sin esto el valor queda pegado arriba cuando la fila le da más
        # alto del que pide (empareja con Resolución/Modo, más altos por
        # su padding nativo de button.combo) -- no es CSS, valign es una
        # propiedad de Gtk.Widget, no del CSS de GTK3.
        self.scale_spin.set_valign(Gtk.Align.CENTER)
        self._scale_handler = self.scale_spin.connect("value-changed", self._on_scale_changed)
        add_field(t("monitores", "escala"), self.scale_spin)

        self.mirror_combo = Gtk.ComboBoxText()
        self.mirror_combo.get_style_context().add_class("monitor-mirror-combo")
        self._mirror_handler = self.mirror_combo.connect("changed", self._on_mirror_changed)
        add_field(t("monitores", "modo"), self.mirror_combo)

        panel.pack_start(fields_row, False, False, 0)
        return panel

    @contextmanager
    def _blocked(self, widget, handler_id):
        widget.handler_block(handler_id)
        try:
            yield
        finally:
            widget.handler_unblock(handler_id)

    def _refresh_panel(self):
        output = self.selected_output
        mon = self.by_output[output]
        p = self.pending[output]

        self.enabled_label.set_text(output)
        make_model = f"{mon.get('make', '')} {mon.get('model', '')}".strip()
        self.enabled_label.set_tooltip_text(make_model or output)

        with self._blocked(self.enabled_switch, self._enabled_handler):
            self.enabled_switch.set_active(not p["disabled"])

        with self._blocked(self.primary_switch, self._primary_handler):
            self.primary_switch.set_active(output == self.primary_output)
        self.primary_switch.set_sensitive(output != self.primary_output)

        for device, (switch, handler_id) in self.touch_switches.items():
            owner = self.touch_assignments.get(device)
            with self._blocked(switch, handler_id):
                switch.set_active(output == owner)
            switch.set_sensitive(output != owner)

        with self._blocked(self.mode_combo, self._mode_handler):
            self.mode_combo.remove_all()
            for kw in monitor_config.available_mode_keywords(mon):
                self.mode_combo.append_text(kw)
            _set_combo_active_text(self.mode_combo, p["mode"])

        with self._blocked(self.scale_spin, self._scale_handler):
            self.scale_spin.set_value(p["scale"])

        others = [o for o in self.outputs if o != output]
        with self._blocked(self.mirror_combo, self._mirror_handler):
            self.mirror_combo.remove_all()
            self.mirror_combo.append_text(t("monitores", "extender"))
            self._mirror_options = [None]
            for other in others:
                self.mirror_combo.append_text(t("monitores", "duplicar", other=other))
                self._mirror_options.append(other)
            idx = self._mirror_options.index(p["mirror_of"]) if p["mirror_of"] in self._mirror_options else 0
            self.mirror_combo.set_active(idx)
        self.mirror_combo.set_sensitive(bool(others))
        self.mirror_combo.set_tooltip_text(None if others else t("monitores", "un_solo_monitor"))

        mirroring = p["mirror_of"] is not None
        self.mode_combo.set_sensitive(not mirroring)
        self.scale_spin.set_sensitive(not mirroring)

    def _on_enabled_state_set(self, state):
        self.pending[self.selected_output]["disabled"] = not state
        self.canvas.queue_draw()

    def _on_primary_state_set(self, state):
        if not state:
            return
        self.primary_output = self.selected_output
        self.primary_switch.set_sensitive(False)

    def _on_touch_state_set(self, state, device):
        if not state:
            return
        self.touch_assignments[device] = self.selected_output
        self.touch_switches[device][0].set_sensitive(False)

    def _on_mode_changed(self, combo):
        text = combo.get_active_text()
        if text:
            self.pending[self.selected_output]["mode"] = text
        self.canvas.queue_draw()

    def _on_scale_changed(self, spin):
        self.pending[self.selected_output]["scale"] = round(spin.get_value(), 2)
        self.canvas.queue_draw()

    def _on_mirror_changed(self, combo):
        idx = combo.get_active()
        mirror_of = self._mirror_options[idx] if idx > 0 else None
        self.pending[self.selected_output]["mirror_of"] = mirror_of
        mirroring = mirror_of is not None
        self.mode_combo.set_sensitive(not mirroring)
        self.scale_spin.set_sensitive(not mirroring)
        self.canvas.queue_draw()

    # ---- Canvas: layout, dibujo, drag & drop, rotación --------------------

    def _compute_canvas_rects(self):
        """{output: (x, y, w, h)} en píxeles de pantalla del canvas --
        recalculado en cada draw/evento (pocos monitores, barato) en vez
        de cachear, para no depender de invalidar a mano en cada cambio
        de pending."""
        alloc = self.canvas.get_allocation()
        avail_w = max(1, alloc.width - 2 * CANVAS_MARGIN)
        avail_h = max(1, alloc.height - 2 * CANVAS_MARGIN)

        boxes = {}
        for output in self.outputs:
            p = self.pending[output]
            lw, lh = monitor_config.logical_size(p["mode"], p["scale"], p["transform"])
            boxes[output] = (p["x"], p["y"], lw, lh)

        min_x = min(x for x, y, w, h in boxes.values())
        min_y = min(y for x, y, w, h in boxes.values())
        max_x = max(x + w for x, y, w, h in boxes.values())
        max_y = max(y + h for x, y, w, h in boxes.values())
        total_w = max(1, max_x - min_x)
        total_h = max(1, max_y - min_y)

        scale = min(avail_w / total_w, avail_h / total_h)
        self._canvas_scale = scale
        off_x = CANVAS_MARGIN + (avail_w - total_w * scale) / 2
        off_y = CANVAS_MARGIN + (avail_h - total_h * scale) / 2

        rects = {}
        for output, (x, y, w, h) in boxes.items():
            rx = off_x + (x - min_x) * scale
            ry = off_y + (y - min_y) * scale
            rects[output] = (rx, ry, w * scale, h * scale)
        return rects

    def _rgba(self, color_name, alpha=None):
        r, g, b, a = common.theme_color_rgba(color_name)
        return (r / 255, g / 255, b / 255, alpha if alpha is not None else a)

    def _rounded_rect(self, cr, x, y, w, h, r):
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _on_canvas_draw(self, _widget, cr):
        rects = self._compute_canvas_rects()
        for output, (rx, ry, rw, rh) in rects.items():
            self._draw_monitor_rect(cr, output, rx, ry, rw, rh)

    def _draw_monitor_rect(self, cr, output, rx, ry, rw, rh):
        selected = output == self.selected_output
        disabled = self.pending[output]["disabled"]
        dim = 0.4 if disabled else 1.0

        self._rounded_rect(cr, rx, ry, rw, rh, 8)
        cr.set_source_rgba(*self._rgba("myforeground", (0.18 if selected else 0.08) * dim))
        cr.fill_preserve()
        cr.set_source_rgba(*self._rgba("myforegroundhover", (1.0 if selected else 0.5) * dim))
        cr.set_line_width(2 if selected else 1)
        cr.stroke()

        cr.set_source_rgba(*self._rgba("myforeground", dim))
        cr.select_font_face("sans-serif")
        cr.set_font_size(13)
        extents = cr.text_extents(output)
        tx = rx + (rw - extents.width) / 2 - extents.x_bearing
        ty = ry + (rh - extents.height) / 2 - extents.y_bearing
        cr.move_to(tx, ty)
        cr.show_text(output)

        icon_cx, icon_cy = rx + rw - ROTATE_ICON_RADIUS - 4, ry + ROTATE_ICON_RADIUS + 4
        cr.set_source_rgba(*self._rgba("myforegroundhover", 0.9 * dim))
        cr.arc(icon_cx, icon_cy, ROTATE_ICON_RADIUS, 0, 2 * math.pi)
        cr.fill()
        cr.set_source_rgba(*self._rgba("mybackground", 1.0))
        cr.set_line_width(1.6)
        cr.arc(icon_cx, icon_cy, 4, 0.3, 5.5)
        cr.stroke()

    def _hit_test(self, mx, my):
        for output, (rx, ry, rw, rh) in self._compute_canvas_rects().items():
            icon_cx = rx + rw - ROTATE_ICON_RADIUS - 4
            icon_cy = ry + ROTATE_ICON_RADIUS + 4
            if (mx - icon_cx) ** 2 + (my - icon_cy) ** 2 <= ROTATE_ICON_RADIUS ** 2:
                return output, "rotate"
            if rx <= mx <= rx + rw and ry <= my <= ry + rh:
                return output, "body"
        return None, None

    def _on_canvas_press(self, _widget, event):
        output, zone = self._hit_test(event.x, event.y)
        if output is None:
            return False
        self.selected_output = output
        self._refresh_panel()

        if zone == "rotate":
            p = self.pending[output]
            p["transform"] = (p["transform"] + 1) % 4
            self.canvas.queue_draw()
            return True

        rx, ry, _rw, _rh = self._compute_canvas_rects()[output]
        self._drag = {
            "output": output,
            "start_mouse": (event.x, event.y),
            "start_pos": (self.pending[output]["x"], self.pending[output]["y"]),
        }
        self.canvas.queue_draw()
        return True

    def _on_canvas_motion(self, _widget, event):
        if self._drag is None:
            return False
        d = self._drag
        scale = self._canvas_scale or 1
        dx = (event.x - d["start_mouse"][0]) / scale
        dy = (event.y - d["start_mouse"][1]) / scale
        new_x = d["start_pos"][0] + dx
        new_y = d["start_pos"][1] + dy
        new_x, new_y = self._snap(d["output"], new_x, new_y)
        self.pending[d["output"]]["x"] = new_x
        self.pending[d["output"]]["y"] = new_y
        self.canvas.queue_draw()
        return True

    def _on_canvas_release(self, _widget, _event):
        self._drag = None
        return False

    def _snap(self, output, x, y):
        """Bordes que quedan a menos de SNAP_THRESHOLD px lógicos de
        tocarse o alinearse con otro monitor saltan a calzar exacto --
        mismo comportamiento que el panel de pantallas de Windows, y
        evita el bug real de dejar un hueco lógico entre dos monitores
        (ver CLAUDE.md, sección de posición física)."""
        p = self.pending[output]
        lw, lh = monitor_config.logical_size(p["mode"], p["scale"], p["transform"])
        best_dx = best_dy = None
        for other in self.outputs:
            if other == output:
                continue
            op = self.pending[other]
            ow, oh = monitor_config.logical_size(op["mode"], op["scale"], op["transform"])
            ox, oy = op["x"], op["y"]
            for cand in (ox - lw, ox + ow, ox, ox + ow - lw):
                d = cand - x
                if abs(d) < SNAP_THRESHOLD and (best_dx is None or abs(d) < abs(best_dx)):
                    best_dx = d
            for cand in (oy - lh, oy + oh, oy, oy + oh - lh):
                d = cand - y
                if abs(d) < SNAP_THRESHOLD and (best_dy is None or abs(d) < abs(best_dy)):
                    best_dy = d
        if best_dx is not None:
            x += best_dx
        if best_dy is not None:
            y += best_dy
        return round(x), round(y)

    # ---- Guardar y aplicar ------------------------------------------------

    def _on_apply(self, _btn):
        resolved = {}
        for output in self.outputs:
            p = self.pending[output]
            resolved[output] = {
                "output": output,
                "mode": p["mode"],
                "scale": p["scale"],
                "transform": p["transform"],
                "disabled": p["disabled"],
                "mirror_of": p["mirror_of"],
                "position": f"{round(p['x'])}x{round(p['y'])}",
            }

        ordered = [resolved[self.primary_output]] + [
            resolved[o] for o in self.outputs if o != self.primary_output]

        monitor_config.save_and_apply(ordered, self.touch_assignments)
        self.status_label.set_text(t("monitores", "guardado_recargando"))


def build_monitor_tab(container):
    load_module_css(MODULE_CSS)
    MonitorConfigurator(container)

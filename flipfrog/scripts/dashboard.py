#!/usr/bin/env python3
"""Panel con módulos que no queremos permanentes en la barra de Waybar.
Se abre con SUPER + plus (ver hypr/config/keybinds.lua). Orquestador
puro -- cada módulo/toggle vive en su propio archivo, importado más
abajo. Ver CLAUDE.md "dashboard.py" para el mapa de las 7 pestañas y
por qué son lazy (Personalización/Menu/Procesos/Red/Bluetooth/Sonido).

Para sumar un toggle nuevo: build_<nombre>_toggle(row) en su archivo,
importar aquí, sumarlo a SISTEMA_TOGGLE_BUILDERS o PANEL_TOGGLE_BUILDERS."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import os
import sys

from waybar_lib import kill_group, kill_existing, build_layer_window, position_fixed_top, load_module_css
from common import set_process_name
from i18n import t

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
CSS_FILE = os.path.join(SCRIPT_DIR, "dashboard.css")
TOGGLE_COMMON_CSS = os.path.join(SCRIPT_DIR, "toggles", "toggle_common.css")
COMPONENTS_CSS = os.path.join(SCRIPT_DIR, "..", "themer", "components.css")
LOCK = "/tmp/dashboard.pid"

for _sub in ("brightness", "theme", "toggles", "updates", "menu", "process", "firewall", "bluetooth", "audio", "monitors"):
    sys.path.insert(0, os.path.join(SCRIPT_DIR, _sub))

from brightness_module import build_brightness
from theme_module import build_theme_selector
from updates_module import build_updates_badge
from menu_module import build_menu_selector
from process_module import build_process_tab
from network_tab import build_network_tab
from bluetooth_tab import build_bluetooth_tab
from sound_module import build_sound_tab
from monitor_module import build_monitor_tab
from vpn_toggle import build_vpn_toggle
from dnd_toggle import build_dnd_toggle
from clock_format_toggle import build_clock_format_toggle
from clock_gradient_toggle import build_clock_gradient_toggle
from bar_toggles import (
    build_bandcamp_toggle, build_bar_full_width_toggle, build_bar_transparent_toggle,
    build_bar_visible_toggle, build_bluetooth_icon_toggle, build_compact_workspaces_toggle,
    build_flipnote_frog_toggle, build_group_apps_toggle, build_layout_mode_toggle,
    build_network_icon_toggle, build_workspace_icons_toggle, build_workspaces_toggle,
)

# Pestaña "Sistema", junto a brillo/actualizaciones -- hardware/sesión,
# no específico de la barra (eso va en PANEL_TOGGLE_BUILDERS). Blur se
# movió a "Temas" (pedido explícito del usuario, 2026-09-06) -- vivía
# acá desde 2026-08-21. Alto Contraste se movió a "Monitores" el mismo
# día (sigue siendo un switch global, ver monitor_module.py -- no hay
# forma de aislarlo por salida, screen_shader de Hyprland es único para
# todo el compositor).
SISTEMA_TOGGLE_BUILDERS = [
    build_vpn_toggle,
    build_dnd_toggle,
]

# "Configuración del Panel" (debajo del separador en "Sistema"): solo
# afectan qué se ve/cómo se ve la barra.
PANEL_TOGGLE_BUILDERS = [
    build_bar_visible_toggle,
    build_layout_mode_toggle,
    build_bar_transparent_toggle,
    build_bar_full_width_toggle,
    build_workspaces_toggle,
    build_workspace_icons_toggle,
    build_compact_workspaces_toggle,
    build_group_apps_toggle,
    build_flipnote_frog_toggle,
    build_network_icon_toggle,
    build_bluetooth_icon_toggle,
    build_bandcamp_toggle,
    build_clock_format_toggle,
    build_clock_gradient_toggle
]


def _build_toggle_grid(builders, columns=2):
    """Grilla de N columnas de filas ícono+etiqueta+switch
    (waybar_lib.build_switch_row)."""
    grid = Gtk.Grid()
    grid.set_name("toggle-row")
    grid.set_column_homogeneous(True)
    # 28/20 -- sin caja detrás de cada fila, el espacio en blanco es la
    # única señal de dónde termina un toggle y empieza el siguiente.
    grid.set_column_spacing(28)
    grid.set_row_spacing(20)
    for i, builder in enumerate(builders):
        cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        builder(cell)
        grid.attach(cell, i % columns, i // columns, 1, 1)
    return grid


def _build_sistema_tab():
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    page.set_name("tab-page")

    # Mitad y mitad: brillo / badge de actualizaciones.
    top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    top_row.set_name("top-row")
    top_row.set_homogeneous(True)

    brightness_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    build_brightness(brightness_slot)
    top_row.pack_start(brightness_slot, True, True, 0)

    updates_slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    build_updates_badge(updates_slot)
    top_row.pack_start(updates_slot, True, True, 0)

    page.pack_start(top_row, False, False, 0)
    page.pack_start(_build_toggle_grid(SISTEMA_TOGGLE_BUILDERS, columns=4), False, False, 0)

    # Subtítulo + línea en la misma fila -- separador expand=True/fill=True
    # se estira solo para llenar lo que sobra a la derecha del label.
    panel_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    panel_header.set_name("panel-header")

    panel_subtitle = Gtk.Label(label=t("dashboard", "panel_config"))
    panel_subtitle.set_name("panel-subtitle")
    panel_header.pack_start(panel_subtitle, False, False, 0)

    separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    separator.set_name("panel-separator")
    separator.set_valign(Gtk.Align.CENTER)
    panel_header.pack_start(separator, True, True, 0)

    page.pack_start(panel_header, False, False, 0)

    page.pack_start(_build_toggle_grid(PANEL_TOGGLE_BUILDERS), False, False, 0)
    return page


def _build_lazy_tab(build_fn, loading_text):
    """Placeholder para pestañas caras de construir -- build_fn queda
    colgado del page como atributo, DashboardPopup._on_switch_page lo
    dispara recién la primera vez que se entra a esa pestaña."""
    page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    page.set_name("tab-page")
    placeholder = Gtk.Label(label=loading_text)
    placeholder.set_name("tab-loading")
    placeholder.set_halign(Gtk.Align.CENTER)
    placeholder.set_valign(Gtk.Align.CENTER)
    page.pack_start(placeholder, True, True, 0)
    page.lazy_pending = (build_fn, placeholder)
    return page


def _build_personalizacion_tab():
    return _build_lazy_tab(build_theme_selector, t("dashboard", "cargando_temas"))


def _build_menu_tab():
    return _build_lazy_tab(build_menu_selector, t("dashboard", "cargando_apps"))


def _build_procesos_tab():
    return _build_lazy_tab(build_process_tab, t("dashboard", "cargando_procesos"))


def _build_red_tab():
    return _build_lazy_tab(build_network_tab, t("dashboard", "cargando_red"))


def _build_bluetooth_tab():
    return _build_lazy_tab(build_bluetooth_tab, t("dashboard", "cargando_bluetooth"))


def _build_sonido_tab():
    return _build_lazy_tab(build_sound_tab, t("dashboard", "cargando_ecualizador"))


def _build_monitores_tab():
    return _build_lazy_tab(build_monitor_tab, t("dashboard", "cargando_monitores"))


# Orden real de las pestañas del Notebook (ver tabs, más abajo) -- argv[1]
# (ej. "dashboard.py bluetooth") abre directo en esa pestaña en vez de
# "Sistema", para íconos de la barra que quieren llevar a un lugar puntual
# del dashboard (ver bar/bar_modules.py). Nombres fijos en
# español, independientes del idioma activo (i18n.current_lang()) -- así
# el comando funciona igual sin importar qué idioma tenga elegido el
# usuario.
TAB_NAMES = ["sistema", "temas", "aplicaciones", "procesos", "red", "bluetooth", "sonido", "monitores"]


def _initial_tab_index():
    if len(sys.argv) > 1:
        name = sys.argv[1].strip().lower()
        if name in TAB_NAMES:
            return TAB_NAMES.index(name)
    return 0


# Piso, no tope -- Gtk.Notebook (homogéneo) puede crecer según la
# pestaña más alta ya construida; esto solo evita que arranque más
# chico que esto en "Sistema". 539 de alto (valor original, restaurado
# 2026-08-21): el toggle nuevo de "Espacios de Trabajo" en
# PANEL_TOGGLE_BUILDERS había sumado una fila (+64px), pero mover
# build_blur_toggle de PANEL_TOGGLE_BUILDERS a SISTEMA_TOGGLE_BUILDERS
# (pedido explícito del usuario -- Blur afecta a los popups en general,
# no solo a Waybar) devolvió PANEL a 4 filas otra vez, cancelando el
# delta -- confirmado offscreen, alto natural de "Sistema" quedó en
# 342px, bien por debajo de este piso.
CONTAINER_SIZE = (872, 539)


class DashboardPopup:
    def __init__(self):
        self.window, container = build_layer_window("dashboard", CSS_FILE)
        load_module_css(TOGGLE_COMMON_CSS)
        load_module_css(COMPONENTS_CSS)
        container.set_name("container")
        position_fixed_top(container, margin=60)
        container.set_size_request(*CONTAINER_SIZE)

        notebook = Gtk.Notebook()
        notebook.set_name("dashboard-notebook")
        # Tira nativa oculta -- reemplazada por _build_tab_header abajo,
        # el nodo CSS "tab" de Notebook no anima transition (confirmado
        # con Gtk.OffscreenWindow), un Gtk.ToggleButton real sí.
        notebook.set_show_tabs(False)

        tabs = [
            (_build_sistema_tab(), t("dashboard", "tab_sistema")),
            (_build_personalizacion_tab(), t("dashboard", "tab_temas")),
            (_build_menu_tab(), t("dashboard", "tab_aplicaciones")),
            (_build_procesos_tab(), t("dashboard", "tab_procesos")),
            (_build_red_tab(), t("dashboard", "tab_red")),
            (_build_bluetooth_tab(), t("dashboard", "tab_bluetooth")),
            (_build_sonido_tab(), t("dashboard", "tab_sonido")),
            (_build_monitores_tab(), t("dashboard", "tab_monitores")),
        ]
        for page, _title in tabs:
            notebook.append_page(page, Gtk.Label(label=""))
        notebook.connect("switch-page", self._on_switch_page)

        initial_index = _initial_tab_index()
        container.pack_start(self._build_tab_header(notebook, [t for _, t in tabs], initial_index), False, False, 0)
        container.pack_start(notebook, True, True, 0)

        self.window.connect("key-press-event", self._on_key)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()

        # Gtk.Notebook.set_current_page() es un no-op silencioso sobre un
        # notebook todavía sin mostrar (confirmado a mano: current_page se
        # queda en 0 pese a llamarlo, sin excepción) -- el que dispara
        # _build_tab_header vía el "toggled" del botón activo (arriba)
        # corre ANTES de show_all(), así que para active_index != 0 nunca
        # aplicaba de verdad (el botón quedaba marcado pero la página
        # visible seguía siendo "Sistema"). Repetirlo ACÁ, ya con la
        # ventana mostrada, sí surte efecto y dispara "switch-page" de
        # verdad -- puebla la pestaña lazy correspondiente por el mismo
        # camino que un click real (_on_switch_page).
        if initial_index != 0:
            notebook.set_current_page(initial_index)

        # Precarga en segundo plano de las pestañas lazy, una por ciclo de
        # idle (no todas de una) para no congelar la ventana.
        self._preload_queue = [page for page, _ in tabs[1:]]
        GLib.idle_add(self._preload_next_tab)

    def _build_tab_header(self, notebook, titles, active_index=0):
        """Cápsula segmentada propia (Gtk.ToggleButton por pestaña).
        active_index != 0 dispara su propio "toggled" acá mismo
        (set_active(True) sobre un botón que arrancó en False), pero eso
        solo dispara el ESTADO VISUAL del botón -- el notebook.set_current_page
        que corre dentro de _on_tab_toggled en este punto todavía es un
        no-op (notebook sin mostrar, ver __init__: la página real se
        confirma aparte, después de show_all())."""
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        header.set_name("dashboard-tab-header")
        header.set_homogeneous(True)
        self._tab_buttons = []
        for i, title in enumerate(titles):
            btn = Gtk.ToggleButton(label=title)
            btn.get_style_context().add_class("dashboard-tab-btn")
            # connect ANTES de set_active -- si no, un active_index != 0
            # dispara "toggled" sin ningún handler todavía escuchando (el
            # botón queda visualmente marcado pero notebook.set_current_page
            # nunca se llama). Con index 0 esto no se notaba (ya era la
            # página default del Notebook), confirmado a mano al agregar
            # argv[1] -- ver _initial_tab_index.
            btn.connect("toggled", self._on_tab_toggled, notebook, i)
            btn.set_active(i == active_index)
            header.pack_start(btn, True, True, 0)
            self._tab_buttons.append(btn)
        return header

    def _on_tab_toggled(self, btn, notebook, index):
        if btn.get_active():
            # Apagar el resto sin re-disparar su propio "toggled" en cascada.
            for other in self._tab_buttons:
                if other is not btn and other.get_active():
                    other.handler_block_by_func(self._on_tab_toggled)
                    other.set_active(False)
                    other.handler_unblock_by_func(self._on_tab_toggled)
            notebook.set_current_page(index)
        elif not any(b.get_active() for b in self._tab_buttons):
            # No dejar que un click sobre el botón YA activo lo apague --
            # se comporta como pestañas reales, siempre hay una activa.
            btn.set_active(True)

    def _on_switch_page(self, _notebook, page, _page_num):
        pending = getattr(page, "lazy_pending", None)
        if pending is None:
            return
        del page.lazy_pending
        # idle_add: deja terminar el cambio de pestaña antes de pagar el
        # costo real, para que el click se sienta instantáneo.
        GLib.idle_add(self._populate_lazy_tab, page, *pending)

    def _populate_lazy_tab(self, page, build_fn, placeholder):
        page.remove(placeholder)
        build_fn(page)
        page.show_all()
        return False  # GLib.idle_add: correr una sola vez

    def _preload_next_tab(self):
        """Construye una pestaña lazy pendiente y se re-agenda para la
        siguiente. `pending is None` cubre la que el usuario ya visitó a
        mano mientras esperaba su turno -- nunca la reconstruye dos veces."""
        if not self._preload_queue:
            return False
        page = self._preload_queue.pop(0)
        pending = getattr(page, "lazy_pending", None)
        if pending is not None:
            del page.lazy_pending
            self._populate_lazy_tab(page, *pending)
        if self._preload_queue:
            GLib.idle_add(self._preload_next_tab)
        return False

    def _on_key(self, _, event):
        if event.keyval == Gdk.KEY_Escape:
            self.window.destroy()

    def _on_destroy(self, *_):
        if os.path.exists(LOCK):
            os.remove(LOCK)
        Gtk.main_quit()


def main():
    set_process_name("ff-dash")
    kill_existing(LOCK)
    kill_group(LOCK)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    DashboardPopup()
    Gtk.main()


if __name__ == "__main__":
    main()

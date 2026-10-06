#!/usr/bin/env python3
"""Toggles de "Configuración del Panel" (y "Métricas de Uso" en
Procesos): cada uno solo escribe su clave en bar-settings.json; la barra
vigila el archivo y se rearma sola, sin reiniciar nada (ver CLAUDE.md,
"Barra propia")."""

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "bar"))
import bar_settings
import common
from i18n import t
from waybar_lib import build_switch_row, svg_icon_image


def _icon(*parts):
    return svg_icon_image(os.path.join(SCRIPT_DIR, *parts))


def _builder(key, label_code, icon_parts, to_setting=lambda state: state, from_setting=lambda value: value):
    def build(row):
        def on_toggle(state):
            common.run_async(lambda: bar_settings.set_value(key, to_setting(state)))

        build_switch_row(row, _icon(*icon_parts), t("sistema", label_code),
                         from_setting(bar_settings.get(key)), on_toggle)
    return build


build_flipnote_frog_toggle = _builder("frog", "icono_menu", ("icons", "menu.svg"))
build_workspaces_toggle = _builder("workspaces", "espacios_trabajo", ("icons", "workspaces.svg"))
build_workspace_icons_toggle = _builder("ws_icons", "iconos_workspaces", ("icons", "workspace_icons.svg"))
build_group_apps_toggle = _builder("group_apps", "agrupar_apps", ("icons", "group_apps.svg"))
build_compact_workspaces_toggle = _builder("compact_ws", "compactar_workspaces", ("icons", "compact_workspaces.svg"))
build_layout_mode_toggle = _builder("layout", "modo_islas", ("icons", "layout_mode.svg"),
                                    to_setting=lambda state: "islas" if state else "capsula",
                                    from_setting=lambda value: value == "islas")
build_network_icon_toggle = _builder("red", "icono_red", ("icons", "network.svg"))
build_bluetooth_icon_toggle = _builder("bluetooth", "icono_bluetooth", ("..", "bluetooth", "icons", "bluetooth.svg"))
build_bandcamp_toggle = _builder("bandcamp", "descubre_bandcamp", ("icons", "bandcamp.svg"))
build_bar_transparent_toggle = _builder("transparent", "barra_transparente", ("icons", "bar_transparent.svg"))
build_bar_full_width_toggle = _builder("full_width", "barra_completa", ("icons", "bar_full_width.svg"))
build_sysmon_toggle = _builder("sysmon", "metricas_uso", ("icons", "sysmon.svg"))


def build_bar_visible_toggle(row):
    """Mostrar/ocultar la barra (mismo efecto que SUPER+W); si el proceso
    no corre, prenderlo la arranca."""
    def on_toggle(state):
        def apply():
            if state and not bar_settings.bar_running():
                bar_settings.set_value("visible", True)
                bar_settings.start_bar()
            else:
                bar_settings.set_value("visible", state)
        common.run_async(apply)

    active = bar_settings.bar_running() and bar_settings.get("visible")
    build_switch_row(row, _icon("icons", "waybar.svg"), t("sistema", "barra"), active, on_toggle)

#!/usr/bin/env python3
"""Selector de la pantalla de inicio de sesión (tema de SDDM) con vista
previa (fila "Inicio de sesión" de "Personalización") y, con --get,
"Obtener pantallas de inicio" (KDE Store), como el de Plasma. Base común en
theme_picker.py; lo propio de SDDM (pkexec, greeter de prueba) en
sddm_themes.py. La vista previa es la captura del tema (o su fondo);
"Probar" lo abre en el greeter en modo prueba, también sin instalarlo."""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import kde_store
import sddm_themes
import theme_kinds
import theme_picker  # antes de gi.repository: fija Gtk 3.0
from i18n import t
from gi.repository import GLib, Gtk

PREVIEW_W, PREVIEW_H = 240, 135


def _requires_note(path):
    """Los temas de Plasma importan decenas de módulos org.kde.*: se
    resumen como "Plasma"."""
    missing = sddm_themes.missing_modules(path)
    names = sorted({"Plasma" if m.startswith("org.kde.") else m for m in missing})
    return t("temas", "login_faltan", mods=", ".join(names)) if names else None


class LoginPicker(theme_picker.ThemePicker):
    KIND = theme_kinds.SDDM
    NAMESPACE = "login-picker"
    LOCK = "/tmp/login-picker.pid"
    TABS = ("store",)
    STORE_SHOT_ONLY = True
    NEEDS_AUTH = True
    TEXTS = {
        "titulo": "login_titulo",
        "obtener": "obtener_login",
        "card_tooltip": "login_card_tooltip",
        "tienda_tooltip": "login_tienda_tooltip",
        "quitar_tooltip": "login_quitar_tooltip",
    }

    def current(self):
        return sddm_themes.current()[0]

    def list_installed(self):
        return sddm_themes.installed()

    def apply(self, theme):
        blocker = sddm_themes.apply(theme)
        if blocker is None:
            return None
        if blocker:
            return t("temas", "login_error_pisado", file=blocker)
        return t("temas", "login_error_aplicar")

    def load_preview(self, theme, bases=None):
        path = sddm_themes.theme_dir(theme)
        notes = []
        name = sddm_themes.metadata(path).get("name", "")
        if name and name.lower() != theme.lower():
            notes.append(name)
        requires = _requires_note(path)
        if requires:
            notes.append(requires)
        return sddm_themes.preview_path(path), " · ".join(notes) or None

    def preview_widgets(self, path):
        surface = path and theme_picker.shot_surface(path, self.scale, PREVIEW_W, PREVIEW_H)
        return [Gtk.Image.new_from_surface(surface)] if surface else []

    def installed_actions(self, theme):
        return [self._test_button(lambda: self._test(sddm_themes.theme_dir(theme)))]

    def store_actions(self, item):
        return [self._test_button(lambda: self._test_store(item))]

    def store_item_installed(self, item):
        return item["id"] in self.store_ids

    def store_installed(self, theme):
        return sddm_themes.installed_from_store(theme)

    def store_install(self, themes, item_id):
        return sddm_themes.install(themes, item_id, kde_store.safe_name)

    def store_uninstall(self, theme):
        sddm_themes.uninstall(theme)

    # ---- probar ---------------------------------------------------------------

    def _test_button(self, on_click):
        return theme_picker._small_button(t("temas", "login_probar"), t("temas", "login_probar_tooltip"),
                                          on_click)

    def _test(self, path):
        def done(ok):
            if not ok:
                self._notify_error(t("temas", "login_sin_greeter",
                                     greeter=sddm_themes.GREETERS[sddm_themes.qt_version(path)]))
        self._run_hidden(lambda: sddm_themes.test(path), done)

    def _test_store(self, item):
        """Baja el tema a la caché (sin instalar) y lo prueba desde ahí."""
        entry = self.cards.get("s:" + item["id"])
        if entry is None or self.busy:
            return
        entry["actions"][0].set_sensitive(False)
        theme_picker._set_note(entry, t("temas", "login_descargando"))

        def work():
            themes = entry.get("themes") or kde_store.fetch(self.KIND, item)
            GLib.idle_add(self._store_fetched, item, themes)

        self.pool.submit(work)

    def _store_fetched(self, item, themes):
        entry = self.cards.get("s:" + item["id"])
        if entry is None:
            return False
        entry["actions"][0].set_sensitive(True)
        if not themes:
            theme_picker._set_note(entry, t("temas", "cursor_sin_vista"))
            return False
        self._show_requires(entry, item, themes)
        self._test(themes[sorted(themes)[0]])
        return False

    def _fetch_store_shot(self, generation, item):
        """Además de la captura, baja el tema (pocos MB) para avisar antes
        de instalarlo si le faltan módulos."""
        super()._fetch_store_shot(generation, item)
        if generation == self.generation["store"]:
            themes = kde_store.fetch(self.KIND, item)
            GLib.idle_add(self._store_checked, generation, item, themes)

    def _store_checked(self, generation, item, themes):
        entry = self.cards.get("s:" + item["id"])
        if generation == self.generation["store"] and entry is not None and themes:
            self._show_requires(entry, item, themes)
        return False

    def _show_requires(self, entry, item, themes):
        entry["themes"] = themes
        requires = _requires_note(themes[sorted(themes)[0]])
        note = self._store_note(item, themes)
        theme_picker._set_note(entry, f"{requires} · {note}" if requires else note)

    def _show_store_page(self, *args):
        self.store_ids = sddm_themes.store_ids()
        return super()._show_store_page(*args)


if __name__ == "__main__":
    theme_picker.run(LoginPicker, "ff-login-pick")

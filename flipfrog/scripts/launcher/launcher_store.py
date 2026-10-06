"""launcher_store.py
Persistencia sin GTK del launcher (launcher.py): conteo de aperturas
por app (usage.json, para la fila "más abiertas") y categorías
editables + asignación app->categoría (categories.json, para la vista
"Por categoría"). Un solo proceso de launcher.py corre a la vez (mismo
kill_existing/kill_group que el resto de popups de este repo), así que
alcanza con common.atomic_write (un solo escritor esperado) en vez del
patrón mkstemp de dos escritores concurrentes."""

import json
import os
import sys

STORE_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(STORE_DIR, ".."))
import common
from i18n import t, all_translations

USAGE_FILE = os.path.join(STORE_DIR, "usage.json")
CATEGORIES_FILE = os.path.join(STORE_DIR, "categories.json")
# Preferencias del launcher que se editan desde el dashboard (pestaña
# "Personalización") -- un solo escritor: el dashboard.
SETTINGS_FILE = os.path.join(STORE_DIR, "launcher-settings.json")

DEFAULT_CATEGORIES = [
    t("launcher", "cat_juegos"),
    t("launcher", "cat_oficina"),
    t("launcher", "cat_herramientas"),
    t("launcher", "cat_personalizacion"),
    t("launcher", "cat_sistema"),
]
UNCATEGORIZED = t("launcher", "sin_categoria")
# categories.json guarda el nombre en el idioma de cuando se creó.
GAMES_CATEGORY_NAMES = all_translations("launcher", "cat_juegos")


def load_usage():
    try:
        with open(USAGE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record_launch(desktop_id):
    usage = load_usage()
    usage[desktop_id] = usage.get(desktop_id, 0) + 1
    common.atomic_write(USAGE_FILE, json.dumps(usage))


def top_apps(apps, limit=5):
    """Hasta `limit` desktop_id (más abiertos primero) de entre los que
    siguen existiendo en `apps` -- una app desinstalada/blacklisteada no
    debe dejar un hueco en la fila."""
    usage = load_usage()
    ranked = sorted(
        (d for d in usage if d in apps and usage[d] > 0),
        key=lambda d: (-usage[d], apps[d]["name"].lower()),
    )
    return ranked[:limit]


def load_categories():
    """(lista_de_categorías, {desktop_id: categoría}) -- crea el archivo
    con las categorías por default en el primer uso."""
    try:
        with open(CATEGORIES_FILE) as f:
            data = json.load(f)
        categories = data.get("categories", [])
        assignments = data.get("assignments", {})
        if not isinstance(categories, list) or not isinstance(assignments, dict):
            raise ValueError
        return categories, assignments
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return list(DEFAULT_CATEGORIES), {}


def _save_categories(categories, assignments):
    common.atomic_write(
        CATEGORIES_FILE,
        json.dumps({"categories": categories, "assignments": assignments}, ensure_ascii=False, indent=2))


def add_category(name):
    name = name.strip()
    if not name or name == UNCATEGORIZED:
        return
    categories, assignments = load_categories()
    if name not in categories:
        categories.append(name)
        _save_categories(categories, assignments)


def remove_category(name):
    """Las apps que tenían esta categoría vuelven a "Sin categoría"
    (quitarlas de `assignments`, no dejarlas apuntando a un nombre que
    ya no existe)."""
    categories, assignments = load_categories()
    if name not in categories:
        return
    categories.remove(name)
    assignments = {d: c for d, c in assignments.items() if c != name}
    _save_categories(categories, assignments)


def rename_category(old_name, new_name):
    new_name = new_name.strip()
    if not new_name or new_name == old_name:
        return
    categories, assignments = load_categories()
    if old_name not in categories or new_name in categories:
        return
    categories[categories.index(old_name)] = new_name
    for desktop_id, cat in assignments.items():
        if cat == old_name:
            assignments[desktop_id] = new_name
    _save_categories(categories, assignments)


def set_app_category(desktop_id, category):
    """category=None (o UNCATEGORIZED) saca la asignación."""
    categories, assignments = load_categories()
    if category in (None, UNCATEGORIZED):
        assignments.pop(desktop_id, None)
    elif category in categories:
        assignments[desktop_id] = category
    _save_categories(categories, assignments)


def is_game(desktop_id):
    _categories, assignments = load_categories()
    return assignments.get(desktop_id) in GAMES_CATEGORY_NAMES


def grouped_by_category(apps_by_id):
    """{categoría: [(desktop_id, name), ...]} en el orden de
    load_categories(), con "Sin categoría" siempre al final -- solo para
    ids presentes en `apps_by_id` (visibles hoy)."""
    categories, assignments = load_categories()
    groups = {c: [] for c in categories}
    groups[UNCATEGORIZED] = []
    for desktop_id, name in apps_by_id:
        cat = assignments.get(desktop_id, UNCATEGORIZED)
        groups.setdefault(cat, groups[UNCATEGORIZED]).append((desktop_id, name))
    ordered = [(c, groups[c]) for c in categories if groups[c]]
    if groups[UNCATEGORIZED]:
        ordered.append((UNCATEGORIZED, groups[UNCATEGORIZED]))
    return ordered



SETTINGS_DEFAULTS = {
    # Íconos junto al nombre en la lista principal.
    "show_list_icons": False,
    # Fila de las más abiertas (arriba de la lista).
    "show_top_apps": True,
}


def get_setting(key):
    try:
        with open(SETTINGS_FILE) as f:
            return bool(json.load(f).get(key, SETTINGS_DEFAULTS[key]))
    except (OSError, ValueError, AttributeError):
        return SETTINGS_DEFAULTS[key]


def set_setting(key, value):
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data[key] = bool(value)
    common.atomic_write(SETTINGS_FILE, json.dumps(data))

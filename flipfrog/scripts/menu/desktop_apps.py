"""
desktop_apps.py
Helpers usados por menu_module.py (build_menu_selector, pestaña "Menu"
del dashboard): escanea los .desktop del sistema (mismo orden de
prioridad que Wofi -- ver
waybar-image(5)... digo, wofi(7): $XDG_DATA_HOME/applications primero,
$XDG_DATA_DIRS/applications después) y maneja blacklist + nombre/ícono
custom escribiendo en una copia local del .desktop -- NoDisplay=true (el
mismo campo que Wofi ya respeta nativamente para decidir qué mostrar en
drun, confirmado a mano), Name= (texto de la entrada) y/o Icon=. Un mismo
id puede combinar cualquiera de las tres a la vez, así que
blacklist_app/unblacklist_app y rename_app/set_app_icon (+ sus
reset_*) comparten el mismo archivo local sin pisarse (ver
_write_local_override/get_customizations).

add_script_entry() es distinto a todo lo anterior: no parte de ningún
.desktop de sistema (no hay ninguno que "des-blacklistear"), arma uno
nuevo desde cero para poder agregar un script .sh como entrada del menú.

No se toca nunca el .desktop de /usr/share/applications directo (no hay
permisos de escritura ahí de por sí) -- siempre se escribe una copia en
~/.local/share/applications/<id>, que por el orden de XDG_DATA_HOME
sobre XDG_DATA_DIRS pisa a la de sistema sin duplicar el menú.
"""

import configparser
import json
import os
import re
import shutil
import sys

MENU_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(MENU_DIR, ".."))
import common

BLACKLIST_FILE = os.path.join(MENU_DIR, "blacklist.json")
CUSTOM_FIELDS_FILE = os.path.join(MENU_DIR, "customizations.json")
BACKUPS_DIR = os.path.join(MENU_DIR, "backups")
LOCAL_APPS_DIR = os.path.expanduser("~/.local/share/applications")

# Mismo orden que usa wofi(7) para resolver desktop-file-id -- el primero
# que define un id dado gana, los demás se ignoran para ese id.
APP_DIRS = [
    LOCAL_APPS_DIR,
    "/usr/local/share/applications",
    "/usr/share/applications",
]


def _parse_desktop_entry(path):
    """None si no es un .desktop de aplicación válido/parseable --
    algunos tienen '%' en Exec (interpolación de configparser normal
    rompe con eso, por eso RawConfigParser) o secciones repetidas raras
    que no vale la pena hacer explotar todo el escaneo por una sola."""
    cp = configparser.RawConfigParser(strict=False)
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError, OSError):
        return None
    if not cp.has_section("Desktop Entry"):
        return None
    entry = cp["Desktop Entry"]
    if entry.get("Type", "Application") != "Application":
        return None
    return entry


def list_all_apps():
    """desktop-file-id -> {"name", "path", "no_display", "icon"}.
    `no_display` refleja el estado ACTUAL en disco (si ya está
    blacklisteada, esto da True porque nosotros mismos le agregamos
    NoDisplay=true -- por eso quien llame a esto necesita blacklist.json
    aparte para saber si ese True es "nativo" o "por la blacklist", ver
    menu_module.py)."""
    apps = {}
    for base in APP_DIRS:
        if not os.path.isdir(base):
            continue
        for fname in sorted(os.listdir(base)):
            if not fname.endswith(".desktop") or fname in apps:
                continue
            entry = _parse_desktop_entry(os.path.join(base, fname))
            if entry is None:
                continue
            name = entry.get("Name", fname[:-len(".desktop")])
            no_display = entry.getboolean("NoDisplay", fallback=False) or \
                entry.getboolean("Hidden", fallback=False)
            apps[fname] = {
                "name": name,
                "path": os.path.join(base, fname),
                "no_display": no_display,
                "icon": entry.get("Icon", ""),
            }
    return apps


def get_blacklist():
    try:
        with open(BLACKLIST_FILE) as f:
            return set(json.load(f).get("blacklisted", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _set_blacklist(ids):
    common.atomic_write(BLACKLIST_FILE, json.dumps({"blacklisted": sorted(ids)}, indent=2))


def get_customizations():
    """desktop-file-id -> {"Name": original, "Icon": original} -- solo
    los campos efectivamente customizados por este editor (rename_app/
    set_app_icon), con su valor ORIGINAL (para poder resetear cada campo
    de forma independiente, ver _reset_custom_field). El valor CUSTOM en
    sí no vive acá, ya está en el Name=/Icon= del .desktop local."""
    try:
        with open(CUSTOM_FIELDS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _set_customizations(data):
    common.atomic_write(CUSTOM_FIELDS_FILE, json.dumps(data, indent=2, ensure_ascii=False))


def _set_desktop_field(content, field, value):
    escaped = value.replace("\n", " ")
    if re.search(rf"(?m)^{field}=", content):
        return re.sub(rf"(?m)^{field}=.*$", f"{field}={escaped}", content, count=1)
    return content.replace("[Desktop Entry]", f"[Desktop Entry]\n{field}={escaped}", 1)


def _remove_desktop_field(content, field):
    return re.sub(rf"(?m)^{field}=.*\n?", "", content)


def _write_local_override(desktop_id, apps):
    """Devuelve el contenido de partida para escribir la copia local del
    .desktop -- blacklist_app y rename_app/set_app_icon comparten este
    mismo archivo (un id puede estar oculto Y con nombre/ícono custom a
    la vez), así que el backup del override previo (p.ej.
    steam-icons-sync.py) solo se hace en el PRIMER touch de cualquiera de
    las features sobre este id -- si ya lo maneja alguna, el local_path
    de ahora es NUESTRO, no un override externo real, y backupearlo de
    nuevo pisaría (o inventaría) un backup incorrecto que después
    _reset_custom_field/unblacklist_app restaurarían mal."""
    os.makedirs(LOCAL_APPS_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)

    local_path = os.path.join(LOCAL_APPS_DIR, desktop_id)
    backup_path = os.path.join(BACKUPS_DIR, desktop_id)
    already_managed = desktop_id in get_blacklist() or desktop_id in get_customizations()
    if not already_managed and os.path.exists(local_path) and not os.path.exists(backup_path):
        shutil.copy2(local_path, backup_path)

    with open(apps[desktop_id]["path"], encoding="utf-8") as f:
        return f.read()


def _cleanup_local_override(desktop_id):
    """Restaura el backup (override previo real) o borra el override --
    solo válido cuando NINGUNA feature sigue necesitando este archivo
    para este id. Quien llame a esto ya chequeó eso antes (ver
    unblacklist_app/_reset_custom_field)."""
    local_path = os.path.join(LOCAL_APPS_DIR, desktop_id)
    backup_path = os.path.join(BACKUPS_DIR, desktop_id)
    if os.path.exists(backup_path):
        shutil.copy2(backup_path, local_path)
        os.remove(backup_path)
    elif os.path.exists(local_path):
        os.remove(local_path)


def blacklist_app(desktop_id, apps):
    """Agrega NoDisplay=true a la copia local del .desktop (ver
    _write_local_override)."""
    content = _write_local_override(desktop_id, apps)
    content = _set_desktop_field(content, "NoDisplay", "true")
    common.atomic_write(os.path.join(LOCAL_APPS_DIR, desktop_id), content)

    ids = get_blacklist()
    ids.add(desktop_id)
    _set_blacklist(ids)


def unblacklist_app(desktop_id):
    """Si el id sigue con Name/Icon custom (get_customizations()), el
    override sigue haciendo falta para eso -- se le saca solo la línea
    NoDisplay= en vez de restaurar/borrar el archivo entero (eso tiraría
    los cambios custom también). Si no, limpieza completa como antes."""
    ids = get_blacklist()
    ids.discard(desktop_id)
    _set_blacklist(ids)

    if desktop_id in get_customizations():
        local_path = os.path.join(LOCAL_APPS_DIR, desktop_id)
        try:
            with open(local_path, encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            return
        content = _remove_desktop_field(content, "NoDisplay")
        common.atomic_write(local_path, content)
    else:
        _cleanup_local_override(desktop_id)


def _set_custom_field(desktop_id, apps, field, app_key, new_value):
    """Escribe `field` (Name o Icon) en la copia local, guardando el
    valor ORIGINAL en customizations.json la PRIMERA vez que ese campo
    puntual se toca para este id -- apps[id][app_key] en este punto
    todavía refleja el valor real (blacklist_app nunca toca Name=/Icon=),
    así que sirve tal cual como "a qué volver" sin importar si este id ya
    estaba oculto o con el otro campo ya customizado de antes."""
    customizations = get_customizations()
    entry = customizations.setdefault(desktop_id, {})
    if field not in entry:
        entry[field] = apps[desktop_id][app_key]

    content = _write_local_override(desktop_id, apps)
    content = _set_desktop_field(content, field, new_value)
    common.atomic_write(os.path.join(LOCAL_APPS_DIR, desktop_id), content)

    _set_customizations(customizations)


def _reset_custom_field(desktop_id, field):
    """Inverso de _set_custom_field -- mismo criterio que
    unblacklist_app: si el id sigue necesitando el override por otro
    motivo (blacklisteado, o el OTRO campo sigue customizado), se
    reescribe/borra solo la línea de este campo en vez de tocar el
    archivo entero. Si no, limpieza completa."""
    customizations = get_customizations()
    entry = customizations.get(desktop_id, {})
    if field not in entry:
        return
    original = entry.pop(field)
    if entry:
        customizations[desktop_id] = entry
    else:
        customizations.pop(desktop_id, None)
    _set_customizations(customizations)

    still_needed = desktop_id in get_blacklist() or desktop_id in customizations
    if not still_needed:
        _cleanup_local_override(desktop_id)
        return

    local_path = os.path.join(LOCAL_APPS_DIR, desktop_id)
    try:
        with open(local_path, encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return
    # Icon= puede no haber existido nunca en el original (fallback "") --
    # ahí no tiene sentido escribir una línea vacía, se saca directo.
    if original:
        content = _set_desktop_field(content, field, original)
    else:
        content = _remove_desktop_field(content, field)
    common.atomic_write(local_path, content)


def rename_app(desktop_id, apps, new_name):
    _set_custom_field(desktop_id, apps, "Name", "name", new_name)


def reset_app_name(desktop_id):
    _reset_custom_field(desktop_id, "Name")


def set_app_icon(desktop_id, apps, new_icon):
    _set_custom_field(desktop_id, apps, "Icon", "icon", new_icon)


def reset_app_icon(desktop_id):
    _reset_custom_field(desktop_id, "Icon")


def _slugify(text):
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug or "script"


def _unique_desktop_id(base_name):
    """custom-<slug>.desktop, con sufijo numérico si ya existe (mismo id
    agregado dos veces, o colisión con una app real) -- se chequea contra
    los tres APP_DIRS, no solo LOCAL_APPS_DIR, para no tapar sin querer
    un .desktop de sistema con el mismo nombre de archivo."""
    slug = _slugify(base_name)
    existing = set()
    for base in APP_DIRS:
        if os.path.isdir(base):
            existing.update(os.listdir(base))

    candidate = f"custom-{slug}.desktop"
    if candidate not in existing:
        return candidate
    n = 2
    while f"custom-{slug}-{n}.desktop" in existing:
        n += 1
    return f"custom-{slug}-{n}.desktop"


def _quote_exec_arg(path):
    """Escapado mínimo para el valor de Exec= según la sintaxis propia
    del Desktop Entry Spec (no es una shell) -- alcanza para rutas con
    espacios, que es el caso real que importa acá."""
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def add_script_entry(name, icon, script_path):
    """Crea un .desktop nuevo en LOCAL_APPS_DIR para que un script .sh
    aparezca como entrada del menú -- a diferencia de blacklist/rename,
    no parte de ningún .desktop de sistema, se arma desde cero.
    `Exec=bash "<script>"` en vez del path directo: no depende de que el
    script ya tenga el bit +x puesto. Devuelve el desktop-file-id
    generado."""
    os.makedirs(LOCAL_APPS_DIR, exist_ok=True)
    desktop_id = _unique_desktop_id(name)
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        f"Exec=bash {_quote_exec_arg(script_path)}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Custom;\n"
    )
    common.atomic_write(os.path.join(LOCAL_APPS_DIR, desktop_id), content)
    return desktop_id


def visible_and_blacklisted(apps=None):
    """(visible, blacklisted): listas de (id, name) ordenadas por
    nombre. "Ocultas nativamente" (NoDisplay/Hidden ya en el .desktop de
    origen, nunca tocadas por este editor) no aparecen en ninguna de las
    dos -- ya estaban afuera del menú antes de que existiera esta
    feature, no hay nada que gestionar ahí.

    Acepta `apps` ya escaneado (menu_module.py ya tiene self.apps de
    refresh()) para no volver a parsear TODOS los .desktop del sistema
    en cada tecla tipeada en el buscador -- antes _populate() llamaba acá
    sin pasar nada y esto reescaneaba desde cero."""
    if apps is None:
        apps = list_all_apps()
    blacklist = get_blacklist()

    visible = [(i, a["name"]) for i, a in apps.items()
               if i not in blacklist and not a["no_display"]]
    blacklisted = [(i, apps[i]["name"]) for i in blacklist if i in apps]

    visible.sort(key=lambda t: t[1].lower())
    blacklisted.sort(key=lambda t: t[1].lower())
    return visible, blacklisted

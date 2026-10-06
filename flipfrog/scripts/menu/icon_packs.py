"""
icon_packs.py
Helper para el selector de "Pack de íconos" de la pestaña
"Personalización" (theme/theme_module.py; antes vivía en "Aplicaciones"). Instala un pack de ~/.config/flipfrog/themer/icons-extra/ como
tema de íconos GTK real -- el mecanismo estándar de Linux (symlink en
~/.local/share/icons/ + gtk-icon-theme-name en gtk-3.0/settings.ini), no
un override por-app: así lo ve tanto este dashboard como el launcher y cualquier
otra app GTK, igual que ya pasa hoy con Papirus-Dark (pedido explícito
del usuario -- "aplica los íconos como con normalidad se aplicarían los
paquetes", no una heurística de matching por-app).

Cada .tar.xz/.tar.gz/.tgz en icons-extra/ se extrae una sola vez (si no
existe ya una carpeta con su mismo nombre base) y se escanea recursivo
por index.theme -- un pack puede traer más de un tema real adentro
(Miya-black trae "Miya-black"/"Miya-black-dark", Pixelitos trae
"pixelitos-light"/"pixelitos-dark"), cada index.theme encontrado es una
entrada propia del selector, con el nombre real que declara ese archivo
(campo Name=, el mismo que GTK necesita en gtk-icon-theme-name).
"""

import configparser
import os
import shutil
import subprocess
import tarfile

ICONS_EXTRA_DIR = os.path.expanduser("~/.config/flipfrog/themer/icons-extra")
LOCAL_ICONS_DIR = os.path.expanduser("~/.local/share/icons")

# GTK3/GTK4 resuelven gtk-icon-theme-name desde GSettings (backend dconf
# activo en esta máquina) ANTES que desde gtk-3.0/settings.ini -- escribir
# solo el .ini no alcanza, cualquier proceso nuevo seguía viendo
# Papirus-Dark. gsettings/dconf es la fuente real; los .ini quedan como
# respaldo para procesos sin backend GSettings.
GSETTINGS_SCHEMA = "org.gnome.desktop.interface"
GSETTINGS_KEY = "icon-theme"

# Apps Qt/KDE no leen gsettings: qt6ct/qt5ct (QT_QPA_PLATFORMTHEME) tienen
# su propio icon_theme, y KDE Frameworks lee [Icons] de kdeglobals. Sin
# esto el pack solo cambiaba en apps GTK.
QT_ICON_SETTINGS = (
    (os.path.expanduser("~/.config/qt6ct/qt6ct.conf"), "Appearance", "icon_theme"),
    (os.path.expanduser("~/.config/qt5ct/qt5ct.conf"), "Appearance", "icon_theme"),
    (os.path.expanduser("~/.config/kdeglobals"), "Icons", "Theme"),
)

GTK_SETTINGS_FILES = (
    os.path.expanduser("~/.config/gtk-3.0/settings.ini"),
    os.path.expanduser("~/.config/gtk-4.0/settings.ini"),
)

# Papirus-Dark es el tema activo del sistema hoy (ver PAPIRUS_APPS_DIR en
# menu_module.py) -- "Sistema" en el selector vuelve acá, ya instalado en
# /usr/share/icons, sin necesidad de symlink.
SYSTEM_DEFAULT = "Papirus-Dark"

_ARCHIVE_SUFFIXES = (".tar.xz", ".tar.gz", ".tgz")


def ensure_extracted():
    """Se corre cada vez que se abre la pestaña -- barato si ya está todo
    extraído (un listdir + comparar contra carpetas existentes)."""
    if not os.path.isdir(ICONS_EXTRA_DIR):
        return
    for fname in os.listdir(ICONS_EXTRA_DIR):
        suffix = next((s for s in _ARCHIVE_SUFFIXES if fname.endswith(s)), None)
        if not suffix:
            continue
        stem = fname[: -len(suffix)]
        dest = os.path.join(ICONS_EXTRA_DIR, stem)
        if os.path.isdir(dest):
            continue
        _extract_archive(os.path.join(ICONS_EXTRA_DIR, fname), dest)


def _extract_archive(archive_path, dest):
    """Miembro a miembro en vez de tf.extractall() de una sola pasada --
    confirmado a mano que candy-icons.tar.xz trae un symlink a ruta
    absoluta (places/16/folder-library.svg) que el filtro "data" (la
    protección estándar contra symlinks que escapan del destino) rechaza
    con AbsoluteLinkError. extractall() aborta TODO el archivo ante ese
    primer error -- y de paso deja la carpeta destino a medio escribir
    (sin apps/, que en el tar viene después de places/ alfabéticamente),
    lo que además bloqueaba cualquier reintento futuro: ensure_extracted
    ve la carpeta ya creada y la da por completa. Extraer a una carpeta
    temporal y saltar SOLO el miembro que falla deja instalable el resto
    del pack (miles de íconos reales); recién se mueve a `dest` si salió
    al menos un archivo, y se descarta si no salió ninguno."""
    tmp = dest + ".tmp"
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp, exist_ok=True)

    extracted_any = False
    try:
        with tarfile.open(archive_path) as tf:
            for member in tf.getmembers():
                try:
                    tf.extract(member, tmp, filter="data")
                    extracted_any = True
                except (tarfile.TarError, OSError):
                    continue
    except (tarfile.TarError, OSError):
        pass

    if extracted_any:
        os.replace(tmp, dest)
    else:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_theme_name(index_theme_path):
    parser = configparser.ConfigParser(strict=False)
    try:
        parser.read(index_theme_path)
        return parser.get("Icon Theme", "Name", fallback=None)
    except configparser.Error:
        return None


def discover_packs():
    """Lista de {"name": <Name= real>, "path": <carpeta con index.theme>},
    ordenada por nombre. Un index.theme encontrado poda esa rama (no hace
    falta seguir bajando dentro de un tema ya identificado, ahí abajo solo
    hay miles de .svg/.png de íconos, no otro index.theme)."""
    ensure_extracted()
    packs = []
    if not os.path.isdir(ICONS_EXTRA_DIR):
        return packs

    base_depth = ICONS_EXTRA_DIR.rstrip("/").count("/")
    max_depth = 4  # icons-extra/<archivo-extraído>/[variante/]<Tema>/index.theme

    for root, dirs, files in os.walk(ICONS_EXTRA_DIR):
        if "index.theme" in files:
            name = _read_theme_name(os.path.join(root, "index.theme"))
            if name:
                packs.append({"name": name, "path": root})
            dirs[:] = []
            continue
        depth = root.rstrip("/").count("/") - base_depth
        if depth >= max_depth:
            dirs[:] = []

    packs.sort(key=lambda p: p["name"].lower())
    return packs


def ensure_installed(pack):
    """Symlink ~/.local/share/icons/<Name> -> carpeta real del tema.
    Swap atómico (symlink a un nombre temporal + os.replace) -- mismo
    criterio de escritura atómica que el resto del repo, para no dejar
    a GTK mirando un symlink a medio crear."""
    os.makedirs(LOCAL_ICONS_DIR, exist_ok=True)
    link = os.path.join(LOCAL_ICONS_DIR, pack["name"])
    target = os.path.abspath(pack["path"])
    if os.path.islink(link) and os.readlink(link) == target:
        return
    tmp = link + ".tmp"
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, link)


def _gsettings_get():
    try:
        out = subprocess.run(
            ["gsettings", "get", GSETTINGS_SCHEMA, GSETTINGS_KEY],
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None
    return out.strip("'\"") or None


def _gsettings_set(name):
    try:
        subprocess.run(
            ["gsettings", "set", GSETTINGS_SCHEMA, GSETTINGS_KEY, name],
            capture_output=True, timeout=3, check=True,
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def current_theme():
    """gsettings primero (fuente real en esta máquina, ver nota arriba);
    si no hay backend disponible, cae al .ini de gtk-3.0. Sin JSON propio
    -- una sola fuente de verdad de qué pack está activo."""
    name = _gsettings_get()
    if name:
        return name
    parser = configparser.ConfigParser(strict=False)
    if os.path.exists(GTK_SETTINGS_FILES[0]):
        parser.read(GTK_SETTINGS_FILES[0])
    return parser.get("Settings", "gtk-icon-theme-name", fallback=SYSTEM_DEFAULT)


def _write_theme_name(name):
    _gsettings_set(name)  # mecanismo principal en esta máquina

    # Respaldo para cualquier proceso GTK sin backend GSettings -- se
    # escriben los dos (gtk-3.0 Y gtk-4.0, ver nota arriba) aunque
    # gsettings haya funcionado, es barato y no debería quedar ninguno
    # desalineado del tema realmente activo.
    for settings_file in GTK_SETTINGS_FILES:
        parser = configparser.ConfigParser(strict=False)
        if os.path.exists(settings_file):
            parser.read(settings_file)
        if not parser.has_section("Settings"):
            parser.add_section("Settings")
        parser.set("Settings", "gtk-icon-theme-name", name)

        os.makedirs(os.path.dirname(settings_file), exist_ok=True)
        tmp = settings_file + ".tmp"
        with open(tmp, "w") as f:
            parser.write(f, space_around_delimiters=False)
        os.replace(tmp, settings_file)

    for path, section, key in QT_ICON_SETTINGS:
        _set_ini_key(path, section, key, name)


def _set_ini_key(path, section, key, value):
    """Edita una sola línea `key=` dentro de `[section]` (la crea si no
    está), sin reescribir el resto -- configparser perdería comentarios y
    mayúsculas de qt6ct.conf/kdeglobals. Archivo inexistente: no se crea
    (esa app no está configurada en esta máquina)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        lines = f.read().splitlines()
    header = f"[{section}]"
    out, in_section, done = [], False, False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not done:
                out.append(f"{key}={value}")
                done = True
            in_section = stripped == header
        elif in_section and stripped.split("=", 1)[0].strip() == key:
            line = f"{key}={value}"
            done = True
        out.append(line)
    if not done:
        if not in_section:
            out += ["", header]
        out.append(f"{key}={value}")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, path)


def apply_pack(pack):
    """pack=None -> "Sistema" (SYSTEM_DEFAULT). pack=entrada de
    discover_packs() -> la instala (symlink) y la activa. Devuelve el
    nombre real que quedó activo, para setear gtk-icon-theme-name en vivo
    sobre Gtk.Settings.get_default() del lado de menu_module.py."""
    if pack is None:
        _write_theme_name(SYSTEM_DEFAULT)
        return SYSTEM_DEFAULT
    ensure_installed(pack)
    _write_theme_name(pack["name"])
    return pack["name"]

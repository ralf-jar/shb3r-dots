"""
app_uninstall.py
Sin GTK. Desinstalar una app de la pestaña "Aplicaciones" (zona "Arrastra
aquí para desinstalar" de menu_module.py) sin que haga falta recordar cómo
se instaló: detect() mira el .desktop y decide el tipo, uninstall() lo
quita con la herramienta de ese tipo.

Tipos, en este orden (el primero que aplica gana):
  flatpak   X-Flatpak= en el .desktop -> flatpak uninstall
  appimage  Exec= apunta a un .AppImage -> se borra el archivo, sus
            .desktop locales y el ícono que guardó install-appimage.sh
  steam     steam://rungameid/N -> steam://uninstall/N (Steam confirma)
  waydroid  waydroid app launch <pkg> -> waydroid app remove <pkg>
  pacman    el .desktop de sistema o el ejecutable de Exec= pertenece a
            un paquete (repos o AUR) -> pkexec pacman -Rn con la lista de
            pacman -Rs --print menos PROTECTED
  shortcut  nada de lo anterior, pero hay un .desktop en
            ~/.local/share/applications -> solo se quita ese acceso
"""

import os
import re
import shlex
import shutil
import subprocess

import desktop_apps

APPIMAGE_ICONS_DIR = os.path.expanduser("~/.local/share/flipfrog/appimages/icons")

# Base de install.sh y lo que el sistema necesita para arrancar: nunca se
# quitan desde aquí, ni como dependencia sobrante de otra app (DaVinci se
# llevaba fuse2, que usan las AppImage).
PROTECTED = {
    "hyprland", "kitty", "xdg-desktop-portal-hyprland", "gtk-layer-shell",
    "python-gobject", "python-pillow", "python-cairo", "pacman-contrib",
    "ffmpegthumbnailer", "icoutils", "bluez", "bluez-utils", "ddcutil", "dunst",
    "wl-clipboard", "wl-clip-persist", "thunar", "tumbler", "gvfs", "fish", "micro",
    "adw-gtk-theme", "papirus-icon-theme", "awww", "mpvpaper", "noto-fonts-emoji",
    "ttf-jetbrains-mono-nerd", "radicale", "python-caldav", "python-icalendar",
    "python-httpx", "hyprpolkitagent", "nethogs", "python-psutil", "mpv",
    "python-requests", "yt-dlp", "libtorrent-rasterbar", "imagemagick",
    "libjpeg-turbo", "fuse2", "sddm", "paru", "gamescope",
    "base", "linux", "linux-cachyos", "systemd", "glibc", "bash", "sudo", "pacman",
    "python", "gtk3", "polkit", "networkmanager", "pipewire", "wireplumber", "mesa",
    "flatpak", "steam", "waydroid",
}

# Ejecutables que solo lanzan otra cosa: su paquete no es la app.
LAUNCHERS = {"bash", "sh", "env", "python", "python3", "xdg-open", "flatpak",
             "steam", "waydroid", "wine", "java", "gtk-launch", "kitty"}

TIMEOUT = 600


def _run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)
    return r.returncode, r.stdout, r.stderr


def _source_paths(desktop_id, apps):
    """Rutas donde vive el .desktop: la que gana (apps[...]["path"],
    puede ser la copia local de la blacklist) y, si es local, la de
    sistema que tapa."""
    paths = [apps[desktop_id]["path"]]
    for base in desktop_apps.APP_DIRS[1:]:
        p = os.path.join(base, desktop_id)
        if os.path.exists(p) and p not in paths:
            paths.append(p)
    return paths


def _entry(path):
    return desktop_apps._parse_desktop_entry(path) or {}


def _exec_argv(entry):
    try:
        return [a for a in shlex.split(entry.get("Exec", "")) if not a.startswith("%")]
    except ValueError:
        return []


def _pacman_owner(path):
    rc, out, _ = _run(["pacman", "-Qoq", path])
    return out.split()[0] if rc == 0 and out.strip() else None


def _local_desktops_for(appimage):
    """Todos los .desktop locales que lanzan ese AppImage (la acción de
    Thunar crea uno, y algunas AppImage se registran solas con otro)."""
    found = []
    base = desktop_apps.LOCAL_APPS_DIR
    for fname in os.listdir(base):
        if not fname.endswith(".desktop"):
            continue
        entry = _entry(os.path.join(base, fname))
        if entry.get("X-Flipfrog-AppImage") == appimage or appimage in _exec_argv(entry):
            found.append(fname)
    return found


def detect(desktop_id, apps):
    """{"kind", "name", ...datos del tipo}; "blocked" (código de i18n) +
    "detail" si no se puede quitar."""
    app = apps[desktop_id]
    plan = {"id": desktop_id, "name": app["name"], "ids": [desktop_id]}
    paths = _source_paths(desktop_id, apps)
    entries = [_entry(p) for p in paths]

    for entry in entries:
        if entry.get("X-Flatpak"):
            ref = entry["X-Flatpak"]
            _, out, _ = _run(["flatpak", "list", "--app", "--columns=application,installation"])
            installation = "system"
            for line in out.splitlines():
                parts = line.split("\t")
                if parts[0] == ref and len(parts) > 1:
                    installation = parts[1]
            return {**plan, "kind": "flatpak", "ref": ref, "installation": installation,
                    "needs_auth": installation == "system"}

    for entry in entries:
        argv = _exec_argv(entry)
        target = entry.get("X-Flipfrog-AppImage") or (argv[0] if argv else "")
        if target.lower().endswith(".appimage") and os.path.isfile(target):
            return {**plan, "kind": "appimage", "file": target,
                    "ids": sorted(set([desktop_id] + _local_desktops_for(target)))}

    for entry in entries:
        exec_line = entry.get("Exec", "")
        m = re.search(r"steam://rungameid/(\d+)", exec_line)
        if m:
            return {**plan, "kind": "steam", "appid": m.group(1)}
        m = re.search(r"waydroid app launch (\S+)", exec_line)
        if m:
            return {**plan, "kind": "waydroid", "package": m.group(1)}

    owner = None
    for path in paths:
        if not path.startswith(desktop_apps.LOCAL_APPS_DIR):
            owner = _pacman_owner(path)
            if owner:
                break
    if not owner:
        for entry in entries:
            argv = _exec_argv(entry)
            if not argv or os.path.basename(argv[0]) in LAUNCHERS:
                continue
            binary = argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0])
            if binary:
                owner = _pacman_owner(os.path.realpath(binary))
                if owner:
                    break
    if owner:
        return _pacman_plan(plan, owner)

    if paths[0].startswith(desktop_apps.LOCAL_APPS_DIR):
        return {**plan, "kind": "shortcut", "file": paths[0]}
    return {**plan, "kind": "unknown", "blocked": "bloqueo_desconocido", "detail": paths[0]}


def _pacman_plan(plan, owner):
    plan = {**plan, "kind": "pacman", "package": owner, "needs_auth": True}
    rc, out, err = _run(["pacman", "-Rs", "--print", "--print-format", "%n", owner])
    packages = out.split()
    if rc != 0 or not packages:
        detail = "\n".join(l.lstrip(": ") for l in err.splitlines() if l.startswith("::")) or err.strip()
        return {**plan, "blocked": "bloqueo_dependencia", "detail": detail}
    if owner in PROTECTED:
        return {**plan, "blocked": "bloqueo_protegido", "detail": owner}
    packages = [p for p in packages if p not in PROTECTED]

    _, files, _ = _run(["pacman", "-Qlq", *packages])
    owned = {os.path.basename(f) for f in files.split()
             if "/applications/" in f and f.endswith(".desktop")}
    return {**plan, "packages": packages, "ids": sorted(set(plan["ids"]) | owned)}


def uninstall(plan):
    """(ok, detalle). Lento (pacman/flatpak): llamar fuera del hilo de GTK."""
    kind = plan["kind"]
    if kind == "flatpak":
        rc, out, err = _run(["flatpak", "uninstall", "-y", "--noninteractive",
                             f"--{plan['installation']}", plan["ref"]], timeout=TIMEOUT)
    elif kind == "pacman":
        rc, out, err = _run(["pkexec", "pacman", "-Rn", "--noconfirm", *plan["packages"]],
                            timeout=TIMEOUT)
    elif kind == "waydroid":
        rc, out, err = _run(["waydroid", "app", "remove", plan["package"]], timeout=60)
    elif kind == "steam":
        subprocess.Popen(["steam", f"steam://uninstall/{plan['appid']}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True, ""
    elif kind == "appimage":
        try:
            os.remove(plan["file"])
        except OSError as e:
            return False, str(e)
        stem = os.path.basename(plan["file"]).rsplit(".", 1)[0].replace(" ", "_")
        for ext in ("png", "svg"):
            try:
                os.remove(os.path.join(APPIMAGE_ICONS_DIR, f"{stem}.{ext}"))
            except FileNotFoundError:
                pass
        rc, out, err = 0, "", ""
    elif kind == "shortcut":
        rc, out, err = 0, "", ""
    else:
        return False, plan.get("detail", "")

    if rc != 0:
        return False, (err or out).strip().splitlines()[-1] if (err or out).strip() else str(rc)
    _forget(plan["ids"], drop_local=kind in ("appimage", "shortcut"))
    return True, ""


def _forget(ids, drop_local):
    """Limpia blacklist/nombres/íconos custom y la copia local de cada id.
    Tras pacman/flatpak la copia local solo se borra si su original de
    sistema ya no existe (si no, era un override propio de otra app)."""
    blacklist = desktop_apps.get_blacklist()
    customs = desktop_apps.get_customizations()
    for desktop_id in ids:
        blacklist.discard(desktop_id)
        customs.pop(desktop_id, None)
        backup = os.path.join(desktop_apps.BACKUPS_DIR, desktop_id)
        local = os.path.join(desktop_apps.LOCAL_APPS_DIR, desktop_id)
        still_installed = any(os.path.exists(os.path.join(base, desktop_id))
                              for base in desktop_apps.APP_DIRS[1:])
        if drop_local or not still_installed:
            for p in (backup, local):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass
    desktop_apps._set_blacklist(blacklist)
    desktop_apps._set_customizations(customs)
    _run(["update-desktop-database", desktop_apps.LOCAL_APPS_DIR])

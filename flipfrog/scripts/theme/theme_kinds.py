"""Qué es un tema de cursor y qué es un tema de íconos, para las fuentes
de temas sin instalar (repo_themes.py, kde_store.py). Sin GTK."""

import configparser
import os
import re
from collections import namedtuple

XCURSOR_MAGIC = b"Xcur"

Kind = namedtuple("Kind", "name store_category store_cache repo_cache repo_packages "
                          "max_file_kb skip_file_re root_marker find_themes")


def _has_xcursor(cursors_dir):
    for name in os.listdir(cursors_dir):
        path = os.path.join(cursors_dir, name)
        try:
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    if f.read(4) == XCURSOR_MAGIC:
                        return True
        except OSError:
            continue
    return False


def find_cursor_themes(root):
    """{nombre: carpeta} de cada carpeta con cursors/ que tenga al menos un
    archivo Xcursor de verdad (no .cur de Windows ni hyprcursor)."""
    themes = {}
    for dirpath, dirnames, _files in os.walk(root):
        if "cursors" in dirnames and _has_xcursor(os.path.join(dirpath, "cursors")):
            themes.setdefault(os.path.basename(dirpath), dirpath)
    return themes


def is_icon_theme(path):
    """index.theme con Directories= (los temas solo de cursor no lo
    traen) y sin Hidden=true."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        if not parser.read(os.path.join(path, "index.theme")):
            return False
        section = "Icon Theme"
        return (parser.has_option(section, "Directories")
                and parser.get(section, "Hidden", fallback="false").lower() != "true")
    except configparser.Error:
        return False


def find_icon_themes(root):
    """{nombre: carpeta}; un index.theme encontrado poda esa rama (debajo
    solo hay miles de íconos)."""
    themes = {}
    for dirpath, dirnames, files in os.walk(root):
        if "index.theme" in files:
            if is_icon_theme(dirpath):
                themes.setdefault(os.path.basename(dirpath), dirpath)
            dirnames[:] = []
    return themes


CURSORS = Kind(
    name="cursors",
    store_category=107,
    store_cache=os.path.expanduser("~/.cache/flipfrog-cursor-store"),
    repo_cache=os.path.expanduser("~/.cache/flipfrog-cursor-previews"),
    repo_packages=(
        "capitaine-cursors", "breeze-cursors", "oxygen-cursors", "vimix-cursors",
        "dracula-cursors-git", "xcursor-vanilla-dmz", "xcursor-vanilla-dmz-aa",
        "xcursor-comix", "xcursor-themes", "xcursor-neutral",
    ),
    max_file_kb=40 * 1024,
    # Variantes para Windows (.cur/.ani) o solo hyprcursor: no sirven aquí.
    skip_file_re=re.compile(r"windows|\.exe$|\.msi$|\.inf$|hyprcursor", re.I),
    root_marker="cursors",
    find_themes=find_cursor_themes,
)

ICONS = Kind(
    name="icons",
    store_category=132,
    store_cache=os.path.expanduser("~/.cache/flipfrog-icon-store"),
    repo_cache=os.path.expanduser("~/.cache/flipfrog-icon-previews"),
    repo_packages=(
        "papirus-icon-theme", "breeze-icons", "qogir-icon-theme", "elementary-icon-theme",
        "pop-icon-theme", "cosmic-icon-theme", "mint-y-icons", "mint-x-icons",
        "tela-circle-icon-theme-standard", "tela-circle-icon-theme-dracula",
        "tela-circle-icon-theme-nord", "obsidian-icon-theme", "deepin-icon-theme",
        "oxygen-icons", "mate-icon-theme", "mate-icon-theme-faenza", "lxde-icon-theme",
    ),
    max_file_kb=60 * 1024,
    skip_file_re=re.compile(r"windows|\.exe$|\.msi$|\.ico$", re.I),
    root_marker="index.theme",
    find_themes=find_icon_themes,
)

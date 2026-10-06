#!/usr/bin/env bash
# Instalación en una sola línea, pensada para alguien que recién instaló
# CachyOS (edición Hyprland):
#
#   curl -fsSL https://raw.githubusercontent.com/ralf-jar/shb3r-dots/master/bootstrap.sh | bash
#
# Clona el repo, mueve a ~/.config-respaldo-<fecha>/ lo que ya hubiera en
# ~/.config con el mismo nombre (nada se borra) y corre install.sh.
# Para probar otra rama/fork: FLIPFROG_REPO=<url> FLIPFROG_BRANCH=<rama>.
set -euo pipefail

REPO="${FLIPFROG_REPO:-https://github.com/ralf-jar/shb3r-dots.git}"
BRANCH="${FLIPFROG_BRANCH:-master}"
TARGET="$HOME/.config"

if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; RESET=$'\033[0m'; GREEN=$'\033[32m'; RED=$'\033[31m'
else
    BOLD=""; RESET=""; GREEN=""; RED=""
fi

die() { echo "${RED}✘ $1${RESET}" >&2; exit 1; }

[[ $EUID -ne 0 ]] || die "Córrelo con tu usuario normal, sin sudo (la contraseña se pide cuando haga falta)."
command -v pacman >/dev/null || die "Esto es solo para CachyOS/Arch."
{ : < /dev/tty; } 2>/dev/null || die "Córrelo desde una terminal."

echo "${BOLD}${GREEN}==> Instalando flipfrog (Hyprland + barra + panel)${RESET}"

if ! command -v git >/dev/null; then
    sudo pacman -S --needed git < /dev/tty
fi

# Ya instalado antes (p. ej. se cortó a la mitad): solo actualiza y sigue.
if git -C "$TARGET" remote get-url origin 2>/dev/null | grep -q "${REPO%.git}"; then
    echo "==> Ya estaba descargado, actualizando"
    git -C "$TARGET" pull --ff-only || echo "No se pudo actualizar, sigo con lo que hay"
else
    [[ -e "$TARGET/.git" ]] && die "$TARGET ya es otro repositorio git, no lo toco."

    CLONE="$(mktemp -d)"
    trap 'rm -rf "$CLONE"' EXIT
    echo "==> Descargando"
    git clone --depth 1 --branch "$BRANCH" "$REPO" "$CLONE/repo"

    BACKUP="$HOME/.config-respaldo-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$TARGET"
    shopt -s dotglob
    for entry in "$CLONE"/repo/*; do
        name="$(basename "$entry")"
        [[ "$name" == ".git" ]] && continue
        if [[ -e "$TARGET/$name" ]]; then
            mkdir -p "$BACKUP"
            mv "$TARGET/$name" "$BACKUP/"
        fi
    done
    shopt -u dotglob
    [[ -d "$BACKUP" ]] && echo "==> Tu configuración anterior quedó en $BACKUP"

    cp -a "$CLONE/repo/." "$TARGET/"
fi

exec bash "$TARGET/install.sh" < /dev/tty

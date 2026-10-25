#!/usr/bin/env bash
# Guarda el colors.css actual (u otro archivo) como un .theme nuevo en
# themer/themes/, sin pasar por Thunar. Registra además el wallpaper
# activo (wallpaper.py current) como comentario CSS al principio y,
# debajo, la configuración de la barra y de "Personalización"
# (`/* preset: ... */`, ver scripts/theme/theme_preset.py).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PRESET_SCRIPT="$SCRIPT_DIR/../scripts/theme/theme_preset.py"

# --preset guarda la configuración actual; sin él (acción de Thunar,
# o --no-preset) la línea queda en null y aplicar el tema no la toca.
PRESET_ARGS=(--none)
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --preset) PRESET_ARGS=() ;;
        --no-preset) PRESET_ARGS=(--none) ;;
        *) ARGS+=("$arg") ;;
    esac
done
set -- "${ARGS[@]}"

THEMER_DIR="$SCRIPT_DIR"
THEMES_DIR="$THEMER_DIR/themes"

# Dos formas de uso:
#   save-theme.sh <nombre> [archivo.css]   -> guarda por nombre (dashboard, CLI)
#   save-theme.sh <archivo.css>            -> acción de Thunar (%f), nombre
#                                              derivado del archivo
if [[ $# -eq 1 && -f "$1" ]]; then
    CSS_FILE="$1"
    NAME="$(basename "$CSS_FILE" .css)"
else
    NAME="${1:-}"
    CSS_FILE="${2:-$THEMER_DIR/colors.css}"
fi

if [[ -z "$NAME" ]]; then
    notify-send "Themer" "Falta el nombre del tema" -i dialog-error
    exit 1
fi

if [[ ! -f "$CSS_FILE" ]]; then
    notify-send "Themer" "Archivo CSS no válido: $CSS_FILE" -i dialog-error
    exit 1
fi

mkdir -p "$THEMES_DIR"

DEST="$THEMES_DIR/$NAME.theme"

# Si ya existe, añade sufijo con fecha para no sobrescribir sin querer
if [[ -e "$DEST" ]]; then
    DEST="$THEMES_DIR/${NAME}_$(date +%Y%m%d-%H%M%S).theme"
fi

WALLPAPER="$(python3 "$SCRIPT_DIR/wallpaper.py" current)"

PRESET_LINE="$(python3 "$PRESET_SCRIPT" line "${PRESET_ARGS[@]}")"

# Descarta los comentarios de wallpaper/preset preexistentes en el origen
# (si se guarda directo desde un .theme viejo) para no duplicarlos.
CSS_CONTENT="$(sed -e '1,2{/^\/\* wallpaper:.*\*\/$/d}' -e '1,2{/^\/\* preset:.*\*\/$/d}' "$CSS_FILE")"

{
    [[ -n "$WALLPAPER" ]] && echo "/* wallpaper: $WALLPAPER */"
    printf '%s\n' "$PRESET_LINE"
    printf '%s\n' "$CSS_CONTENT"
} > "$DEST"

notify-send "Themer" "Tema guardado: $(basename "$DEST")" -i document-save

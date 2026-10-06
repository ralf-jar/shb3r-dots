#!/usr/bin/env bash
# Aplica un archivo .theme como el CSS global de Themer y reinicia las apps
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

THEME_FILE="${1:-}"
COLORS_CSS="$SCRIPT_DIR/colors.css"
LAST_GOOD="$SCRIPT_DIR/.colors.css.lastgood"
RELOAD_SCRIPT="$SCRIPT_DIR/reload.sh"

# Mismas 8 propiedades que theme-editor.py (PROPERTIES) -- un .theme sin
# alguna de estas está incompleto (guardado a mano, copiado de otro
# sistema, truncado por una escritura interrumpida) y no debe llegar a
# pisar colors.css.
REQUIRED_PROPERTIES=(
    mybackground
    mybackgroundhover
    myborders
    myborders2
    myborderinactive
    myforeground
    myforegroundhover
    myforegroundhover2
)

if [[ -z "$THEME_FILE" ]]; then
    notify-send "Themer" "No se recibió ningún archivo .theme" -i dialog-error
    exit 1
fi

if [[ ! -f "$THEME_FILE" ]]; then
    notify-send "Themer" "El archivo no existe: $THEME_FILE" -i dialog-error
    exit 1
fi

# Chequea que $1 tenga un @define-color válido para cada propiedad
# requerida. Devuelve (por stdout) la lista de las que faltan, vacío si
# está completo.
missing_properties() {
    local file="$1" prop
    for prop in "${REQUIRED_PROPERTIES[@]}"; do
        if ! grep -Eq "@define-color[[:space:]]+${prop}[[:space:]]+rgba?\([^)]+\)" "$file"; then
            printf '%s ' "$prop"
        fi
    done
}

# --- Safe-Mode, paso 1: colors.css ya en disco tiene que ser válido ---
# Si algo dejó colors.css corrupto entre una corrida y la siguiente (p.ej.
# una escritura no atómica interrumpida a mitad de camino -- ya pasó una
# vez con theme-editor.py, ver CLAUDE.md), no seguir construyendo sobre
# ese estado roto: restaurar desde el último snapshot bueno conocido antes
# de intentar aplicar nada nuevo.
if [[ -f "$COLORS_CSS" ]]; then
    current_missing="$(missing_properties "$COLORS_CSS")"
    if [[ -n "$current_missing" ]]; then
        if [[ -f "$LAST_GOOD" ]]; then
            cp "$LAST_GOOD" "$COLORS_CSS"
            notify-send "Themer" "colors.css estaba corrupto (faltaban: $current_missing) -- restaurado desde el último estable" -i dialog-warning
        else
            notify-send "Themer" "colors.css estaba corrupto (faltaban: $current_missing) y no hay backup para restaurar" -i dialog-error
        fi
    fi
fi

# --- Safe-Mode, paso 2: valida el .theme ANTES de tocar colors.css ---
validation_errors=()

new_missing="$(missing_properties "$THEME_FILE")"
if [[ -n "$new_missing" ]]; then
    validation_errors+=("faltan propiedades: $new_missing")
fi

WALLPAPER_LINE="$(head -1 "$THEME_FILE")"
if [[ "$WALLPAPER_LINE" == "/* wallpaper"* ]] && [[ ! "$WALLPAPER_LINE" =~ ^/\*\ wallpaper:\ .+\ \*/$ ]]; then
    validation_errors+=("comentario de wallpaper con formato inválido: $WALLPAPER_LINE")
fi

if [[ ${#validation_errors[@]} -gt 0 ]]; then
    reason="$(printf '%s; ' "${validation_errors[@]}")"
    notify-send "Themer" "Tema inválido, no se aplicó: $reason" -i dialog-error
    echo "apply-theme.sh: tema inválido ($THEME_FILE): $reason" >&2
    exit 1
fi

# --- Aplicación real (tema ya validado) ---
# Escritura atómica (temp + mv en el mismo directorio, mv es atómico en el
# mismo filesystem): un lector concurrente (reload.sh, sync_dunst.py, otra
# instancia del editor) siempre ve colors.css completo, viejo o nuevo,
# nunca a medio escribir.
TMP_CSS="$(mktemp "${COLORS_CSS}.XXXXXX")"

if [[ "$WALLPAPER_LINE" =~ ^/\*\ wallpaper:\ (.+)\ \*/$ ]]; then
    # No copiar el comentario a colors.css: es CSS puro que se @import-ea.
    tail -n +2 "$THEME_FILE" > "$TMP_CSS"
    WALLPAPER_PATH="${BASH_REMATCH[1]}"
    WALLPAPER_PATH="${WALLPAPER_PATH/#\~/$HOME}"

    python3 "$SCRIPT_DIR/wallpaper.py" set "$WALLPAPER_PATH"
else
    cp "$THEME_FILE" "$TMP_CSS"
fi

mv "$TMP_CSS" "$COLORS_CSS"
# Snapshot del último estado bueno conocido, para el paso 1 de la próxima
# corrida -- ya pasó por la validación de arriba, así que es seguro
# guardarlo como referencia de rollback.
cp "$COLORS_CSS" "$LAST_GOOD"

if [[ -x "$RELOAD_SCRIPT" ]]; then
    bash "$RELOAD_SCRIPT"
else
    notify-send "Themer" "Aviso: reload.sh no es ejecutable o no existe" -i dialog-warning
fi

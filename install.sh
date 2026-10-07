#!/usr/bin/env bash
# Instalador de este repo de dotfiles (flipfrog/hypr + resto de
# config de shell/WM). Pensado para Arch/CachyOS.
#
# Uso en una máquina nueva: bootstrap.sh (una línea, clona + respalda +
# corre esto). A mano, este repo vive en ~/.config, no en una carpeta
# aparte, así que "clonar" significa fusionarlo con lo que ya haya ahí:
#
#   git clone --depth 1 <url-del-repo> /tmp/dotfiles-clone
#   cp -a /tmp/dotfiles-clone/. ~/.config/
#   rm -rf /tmp/dotfiles-clone
#   bash ~/.config/install.sh
#
# Pregunta dos cosas (teclado y extras); sin terminal o para no preguntar:
#   FLIPFROG_KB=latam|es|us|us-intl   FLIPFROG_EXTRAS=0|1
#
# Salida: una barra de progreso con lo que está haciendo; todo lo demás
# va a ~/.cache/flipfrog-install.log. Idempotente (instala con --needed).
set -euo pipefail

TARGET="$HOME/.config"

if [[ "$(cd "$(dirname "$(readlink -f "$0")")" && pwd)" != "$TARGET" ]]; then
    echo "Este script asume que ya vive en ~/.config (ver comentario de uso arriba)." >&2
    exit 1
fi

if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; RESET=$'\033[0m'; DIM=$'\033[2m'
    GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'
    BAR_MODE=1
else
    BOLD=""; RESET=""; DIM=""; GREEN=""; YELLOW=""; RED=""; CYAN=""
    BAR_MODE=0
fi

LOG="$HOME/.cache/flipfrog-install.log"
mkdir -p "$(dirname "$LOG")"
: > "$LOG"
log() { printf '%s\n' "$*" >> "$LOG"; }

WARNINGS=()
warn() { WARNINGS+=("$1"); log "AVISO: $1"; }

# Preguntas por /dev/tty: con `curl ... | bash` (bootstrap.sh) stdin es el
# propio script y `read` se lo comería.
if { : < /dev/tty; } 2>/dev/null; then
    HAS_TTY=1
else
    HAS_TTY=0
fi

ask() {  # ask <variable> <texto>
    local answer=""
    [[ $HAS_TTY == 1 ]] && read -r -p "$2" answer < /dev/tty
    printf -v "$1" '%s' "$answer"
}

# Pantalla: cada pregunta se borra al contestarla y queda solo el resumen
# (título + respuestas), con la barra debajo.
SUMMARY=()
[[ -n "${FLIPFROG_BACKUP:-}" ]] && SUMMARY+=("Tu configuración anterior quedó en ${FLIPFROG_BACKUP/#$HOME/\~}")
screen() {
    [[ $BAR_MODE == 1 ]] && printf '\033[H\033[2J\033[3J'
    echo "${BOLD}${GREEN}Instalando flipfrog (Hyprland + barra + panel)${RESET}"
    local line
    for line in "${SUMMARY[@]}"; do echo "$line"; done
}

KEYBOARD_FILE="$TARGET/hypr/keyboard.lua"
KB="${FLIPFROG_KB:-}"
if [[ -z "$KB" && ! -f "$KEYBOARD_FILE" && $HAS_TTY == 1 ]]; then
    screen
    echo
    echo "${BOLD}¿Qué teclado tienes?${RESET}"
    echo "  1) Español Latinoamérica (tiene Ñ, y ¿ junto al 0)"
    echo "  2) Español España (tiene Ñ, y ' junto al 0)"
    echo "  3) Inglés (sin Ñ)"
    echo "  4) Inglés internacional (sin Ñ, acentos con ' + letra)"
    ask kb_choice "Elige un número [1]: "
    case "$kb_choice" in
        2) KB=es ;;
        3) KB=us ;;
        4) KB=us-intl ;;
        *) KB=latam ;;
    esac
fi
case "$KB" in
    latam)   SUMMARY+=("Teclado: Español Latinoamérica") ;;
    es)      SUMMARY+=("Teclado: Español España") ;;
    us)      SUMMARY+=("Teclado: Inglés") ;;
    us-intl) SUMMARY+=("Teclado: Inglés internacional") ;;
    "")      SUMMARY+=("Teclado: sin cambios") ;;
    *)       SUMMARY+=("Teclado: $KB") ;;
esac

EXTRAS="${FLIPFROG_EXTRAS:-}"
if [[ -z "$EXTRAS" ]]; then
    EXTRAS=0
    if [[ $HAS_TTY == 1 ]]; then
        screen
        echo
        echo "${BOLD}¿Instalar extras?${RESET} Asistente de voz, audiolibros narrados y temas"
        echo "para Steam. Bajan ~1 GB más y no hacen falta para empezar."
        ask extras_choice "¿Instalar extras? [s/N]: "
        [[ "$extras_choice" =~ ^[sSyY] ]] && EXTRAS=1
    fi
fi
if [[ $EXTRAS == 1 ]]; then SUMMARY+=("Extras: Sí"); else SUMMARY+=("Extras: No"); fi

# Contraseña una sola vez: las compilaciones de AUR tardan más que el
# tiempo que sudo recuerda la contraseña.
screen
echo
if [[ $HAS_TTY == 1 ]]; then
    sudo -v -p "${BOLD}Escribe tu contraseña${RESET} (solo se pide una vez): " < /dev/tty
else
    sudo -v
fi
( while sleep 50; do sudo -n true 2>/dev/null || exit; kill -0 $$ 2>/dev/null || exit; done ) &
SUDO_KEEPALIVE=$!
trap 'kill $SUDO_KEEPALIVE 2>/dev/null; [[ $BAR_MODE == 1 ]] && printf "\033[?25h"' EXIT
SUMMARY+=("Contraseña ingresada, iniciando proceso...")
screen
echo

# --- Barra de progreso. Cada etapa tiene un peso; dentro de las largas
# (pacman/paru) el avance y la etiqueta salen de su salida en el log
# (LC_ALL=C para leerla en inglés): "(12/58) installing foo", "foo
# downloading...", "==> Making package: foo".
W_BASE=30; W_LOGIN=3; W_SERVICES=2; W_PARU=3; W_APPS=25; W_AUR=20
W_EXTRAS=20; W_MODELS=10; W_CONFIG=4; W_FONTS=2
TOTAL_W=$((W_BASE + W_LOGIN + W_SERVICES + W_PARU + W_APPS + W_AUR + W_CONFIG + W_FONTS))
[[ $EXTRAS == 1 ]] && TOTAL_W=$((TOTAL_W + W_EXTRAS + W_MODELS))
DONE_W=0; STAGE_W=0; STAGE_FRAC=0; LABEL=""; LAST_LINE=""
SPIN=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏); SPIN_I=0

draw() {  # draw <fracción 0-1000 dentro de la etapa> <etiqueta>
    # Nunca hacia atrás: con varios paquetes, la lectura de cada uno empieza de cero.
    (( $1 > STAGE_FRAC )) && STAGE_FRAC=$1
    local pct=$(( (DONE_W * 1000 + STAGE_W * STAGE_FRAC) / (TOTAL_W * 10) ))
    (( pct > 100 )) && pct=100
    if [[ $BAR_MODE == 0 ]]; then
        local line="[$pct%] $2"
        [[ "$line" != "$LAST_LINE" ]] && echo "$line"
        LAST_LINE="$line"
        return
    fi
    local width=30 filled=$(( pct * 30 / 100 ))
    local bar="${GREEN}$(printf '█%.0s' $(seq 1 $filled 2>/dev/null))${RESET}${DIM}$(printf '░%.0s' $(seq 1 $((width - filled)) 2>/dev/null))${RESET}"
    (( filled == 0 )) && bar="${DIM}$(printf '░%.0s' $(seq 1 $width))${RESET}"
    (( filled == width )) && bar="${GREEN}$(printf '█%.0s' $(seq 1 $width))${RESET}"
    local cols; cols=$(tput cols 2>/dev/null || echo 80)
    local text="$2"
    (( ${#text} > cols - 42 )) && text="${text:0:$((cols - 43))}…"
    SPIN_I=$(( (SPIN_I + 1) % ${#SPIN[@]} ))
    printf '\r\033[K\033[?25l[%s] %3d%%  %s %s' "$bar" "$pct" "${CYAN}${SPIN[$SPIN_I]}${RESET}" "$text"
}

stage() {  # stage <peso> <etiqueta>
    DONE_W=$((DONE_W + STAGE_W))
    STAGE_W=$1; STAGE_FRAC=0; LABEL="$2"
    log ""; log "=== $2"
    draw 0 "$LABEL"
}

# Avance y etiqueta a partir de lo que pacman/paru escribieron desde `off`.
parse_progress() {  # parse_progress <offset>
    tail -c +"$(( $1 + 1 ))" "$LOG" 2>/dev/null | tr '\r' '\n' | awk -v base="$LABEL" '
        /^Packages \([0-9]+\)/ { match($0, /\(([0-9]+)\)/, m); total = m[1]; dl = 0 }
        / downloading\.\.\.$/  { dl++; state = "dl" }
        /^\( *[0-9]+\/[0-9]+\) checking/ { state = "check" }
        /^\( *[0-9]+\/[0-9]+\) (installing|upgrading|reinstalling) / {
            match($0, /\( *([0-9]+)\/([0-9]+)\) [a-z]+ ([^ ]+)/, m); n = m[1]; t = m[2]; pkg = m[3]; state = "inst" }
        /==> Making package: /  { split($0, a, "Making package: "); split(a[2], b, " "); build = b[1]; state = "build" }
        /==> Retrieving sources/ { if (build != "") state = "src" }
        END {
            if (state == "dl" && total > 0) printf "%d|Descargando paquetes (%d/%d)\n", 500 * dl / total, dl, total
            else if (state == "check") print "500|Verificando paquetes"
            else if (state == "inst" && t > 0) printf "%d|Instalando %s (%d/%d)\n", 500 + 500 * n / t, pkg, n, t
            else if (state == "build") printf "300|Compilando %s\n", build
            else if (state == "src") printf "150|Descargando el código de %s\n", build
            else printf "0|%s\n", base
        }'
}

run() {  # run <comando...> -- en segundo plano, con la barra viva
    local off; off=$(stat -c %s "$LOG")
    LC_ALL=C "$@" >> "$LOG" 2>&1 < /dev/null &
    local pid=$! info frac text
    while kill -0 "$pid" 2>/dev/null; do
        info=$(parse_progress "$off" || true)
        frac=${info%%|*}; text=${info#*|}
        draw "${frac:-0}" "${text:-$LABEL}"
        sleep 0.3
    done
    wait "$pid"
}

# Para la barra para mostrar algo en pantalla (un error o una pregunta).
break_bar() { [[ $BAR_MODE == 1 ]] && printf '\r\033[K\033[?25h'; return 0; }

fail() {  # fail <mensaje> -- error que impide seguir
    break_bar
    echo "${RED}✘ $1${RESET}" >&2
    echo "${DIM}Últimas líneas del registro ($LOG):${RESET}" >&2
    tail -n 15 "$LOG" >&2
    exit 1
}

# pacman sin "¿Continuar? [S/n]". --noconfirm contesta "no" si hay que
# quitar un paquete en conflicto y la instalación falla: solo entonces se
# repite preguntando, fuera de la barra.
pac() {
    run sudo pacman -S --needed --noconfirm "$@" && return
    break_bar
    echo "${YELLOW}⚠ pacman necesita que confirmes algo:${RESET}"
    sudo pacman -S --needed "$@" < /dev/tty
}

stage $W_BASE "Instalando el escritorio y sus dependencias"
pac \
    hyprland kitty \
    xdg-desktop-portal-hyprland gtk-layer-shell \
    python-gobject python-pillow python-cairo \
    pacman-contrib ffmpegthumbnailer icoutils \
    bluez bluez-utils ddcutil dunst wl-clipboard wl-clip-persist \
    thunar tumbler gvfs fish micro \
    adw-gtk-theme papirus-icon-theme awww mpvpaper \
    noto-fonts-emoji ttf-jetbrains-mono-nerd \
    radicale python-caldav python-icalendar python-httpx \
    hyprpolkitagent nethogs python-psutil \
    mpv python-requests yt-dlp imagemagick libjpeg-turbo \
    || fail "No se pudieron instalar los paquetes del escritorio"

# Sin gestor de login la PC arranca en una terminal de texto: pasa al
# instalar CachyOS con Hyprland sin el paquete de noctalia (probado en VM).
stage $W_LOGIN "Revisando la pantalla de inicio de sesión"
if [[ -e /etc/systemd/system/display-manager.service ]]; then
    log "Ya hay gestor de login: $(readlink -f /etc/systemd/system/display-manager.service)"
elif pac sddm && run sudo systemctl enable sddm.service; then
    # Sesión preseleccionada: Hyprland (no la variante con uwsm).
    printf '[Last]\nSession=/usr/share/wayland-sessions/hyprland.desktop\n' | sudo tee /var/lib/sddm/state.conf >/dev/null
    sudo chown sddm:sddm /var/lib/sddm/state.conf 2>/dev/null || true
else
    warn "No se pudo instalar SDDM -- al reiniciar entra con tu usuario y escribe: Hyprland"
fi
# Instalación normal de CachyOS: noctalia viene incluido y su panel lo
# arranca la config de Hyprland de fábrica (qs -c noctalia-shell), que
# bootstrap.sh ya movió al respaldo -- queda instalado pero sin arrancar.
if pacman -Q noctalia-shell >/dev/null 2>&1 && grep -rqs 'noctalia-shell' "$TARGET/hypr/"; then
    warn "Tu config de Hyprland todavía arranca noctalia -- quita esa línea de hypr/config/autostart.lua"
fi

# hyprpolkitagent: sin agente de polkit, pkexec (UFW en el panel, uso de
# disco como administrador) falla con "No authentication agent found".
# filter-chain: ecualizador de la pestaña "Sonido" (sink-eq6.conf), proceso
# aparte de pipewire.service -- habilitarlo no corta el audio.
stage $W_SERVICES "Activando servicios (contraseñas, ecualizador)"
run systemctl --user enable --now hyprpolkitagent.service \
    || warn "No se pudo habilitar hyprpolkitagent -- las acciones con contraseña del panel no van a funcionar"
run systemctl --user enable --now filter-chain.service \
    || warn "No se pudo habilitar el ecualizador (filter-chain.service)"

stage $W_PARU "Preparando el instalador de AUR (paru)"
if ! command -v paru >/dev/null; then
    pac paru || fail "paru no está en tus repos -- instálalo desde AUR: git clone https://aur.archlinux.org/paru.git && cd paru && makepkg -si"
fi

# Apps del día a día (pedido explícito del usuario, rutina para amigos).
# Cada una se salta si su comando ya existe: en la máquina de origen
# vesktop vino de AUR y vesktop-bin chocaría con él.
stage $W_APPS "Instalando Brave, Steam y Discord"
APPS_PACMAN=()
command -v brave-origin >/dev/null || APPS_PACMAN+=(brave-origin-bin)
command -v steam        >/dev/null || APPS_PACMAN+=(steam)
command -v vesktop      >/dev/null || APPS_PACMAN+=(vesktop-bin)
if [[ ${#APPS_PACMAN[@]} -gt 0 ]]; then
    pac "${APPS_PACMAN[@]}" \
        || warn "Falló la instalación de ${APPS_PACMAN[*]} -- reinténtalo con: sudo pacman -S ${APPS_PACMAN[*]}"
fi

# realesrgan: acción "Escalar imagen con IA" de Thunar (Thunar/uca.xml).
stage $W_AUR "Instalando WhatsApp, cursor y escalado de imágenes (AUR)"
APPS_AUR=(bibata-cursor-theme-bin realesrgan-ncnn-vulkan-bin)
command -v zapzap >/dev/null || APPS_AUR+=(zapzap)
run paru -S --needed --noconfirm --skipreview "${APPS_AUR[@]}" \
    || warn "Falló algo de AUR (${APPS_AUR[*]}) -- reinténtalo con: paru -S ${APPS_AUR[*]}"

if [[ $EXTRAS == 1 ]]; then
    # python-onnxruntime-cpu ANTES de piper-tts a propósito -- piper-tts
    # depende de un proveedor de python-onnxruntime y, sin resolverlo
    # primero, paru pregunta de forma interactiva (cpu/cuda/rocm...).
    stage $W_EXTRAS "Instalando extras (voz, audiolibros, temas de Steam)"
    { pac whisper-cpp python-webrtcvad python-onnxruntime-cpu \
        && run paru -S --needed --noconfirm --skipreview millennium-bin piper-tts; } \
        || warn "Falló algo de los extras -- instala manualmente lo que quedó pendiente"

    # Modelos del asistente de voz (flipfrog/scripts/assistant/): ~465 MiB
    # + ~73 MiB, a un .part y renombrados al final para no dejar un archivo
    # truncado si se corta a la mitad.
    VOICE_ASSISTANT_CACHE="$HOME/.cache/waybar-voice-assistant"
    stage $W_MODELS "Descargando modelos de voz"
    mkdir -p "$VOICE_ASSISTANT_CACHE"
    _dl() {
        local url="$1" dest="$2"
        [[ -f "$dest" ]] && return
        LABEL="Descargando $(basename "$dest")"
        if run curl -fsSL -o "$dest.part" "$url"; then
            mv "$dest.part" "$dest"
        else
            rm -f "$dest.part"
            warn "Falló la descarga de $(basename "$dest") -- el asistente de voz no va a funcionar hasta bajarlo a mano en $VOICE_ASSISTANT_CACHE"
        fi
    }
    _dl "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin" \
        "$VOICE_ASSISTANT_CACHE/whisper-small.bin"
    _dl "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/sharvard/medium/es_ES-sharvard-medium.onnx" \
        "$VOICE_ASSISTANT_CACHE/es_ES-sharvard-medium.onnx"
    _dl "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/sharvard/medium/es_ES-sharvard-medium.onnx.json" \
        "$VOICE_ASSISTANT_CACHE/es_ES-sharvard-medium.onnx.json"
fi

stage $W_CONFIG "Copiando la configuración"
if [[ -n "$KB" ]]; then
    case "$KB" in
        us-intl) kb_layout=us; kb_variant=intl ;;
        *)       kb_layout="$KB"; kb_variant="" ;;
    esac
    printf 'return { layout = "%s", variant = "%s" }\n' "$kb_layout" "$kb_variant" > "$KEYBOARD_FILE"
    log "Teclado: $kb_layout $kb_variant"
fi

# Desde la terminal de texto (sin sesión gráfica) el fondo y dunst no se
# pueden recargar en vivo: el tema queda guardado y se aplica al entrar.
if [[ ! -f "$TARGET/flipfrog/themer/colors.css" ]]; then
    LABEL="Aplicando el tema forest-road"
    run bash "$TARGET/flipfrog/themer/apply-theme.sh" "$TARGET/flipfrog/themer/themes/forest-road.theme" \
        || warn "No se pudo aplicar el tema por defecto -- detalles en $LOG"

    # Apariencia de las apps GTK (botones, listas desplegables): sin esto
    # usan el Adwaita claro de fábrica. Mismo valor en gsettings (lo lee
    # GTK primero) y en settings.ini. Solo en la primera instalación: luego
    # se cambia desde "Personalización" del panel.
    draw 300 "Aplicando el tema oscuro a las apps"
    set_ini() {  # set_ini <archivo> <clave> <valor>
        mkdir -p "$(dirname "$1")"
        [[ -f "$1" ]] || printf '[Settings]\n' > "$1"
        if grep -q "^$2=" "$1"; then
            sed -i "s|^$2=.*|$2=$3|" "$1"
        else
            printf '%s=%s\n' "$2" "$3" >> "$1"
        fi
    }
    for ini in "$TARGET/gtk-3.0/settings.ini" "$TARGET/gtk-4.0/settings.ini"; do
        set_ini "$ini" gtk-theme-name adw-gtk3-dark
        set_ini "$ini" gtk-icon-theme-name Papirus-Dark
        set_ini "$ini" gtk-font-name "Adwaita Sans 11"
        set_ini "$ini" gtk-application-prefer-dark-theme 1
    done
    if command -v gsettings >/dev/null; then
        gsettings set org.gnome.desktop.interface gtk-theme adw-gtk3-dark 2>> "$LOG" || true
        gsettings set org.gnome.desktop.interface color-scheme prefer-dark 2>> "$LOG" || true
        gsettings set org.gnome.desktop.interface icon-theme Papirus-Dark 2>> "$LOG" || true
        gsettings set org.gnome.desktop.interface font-name "Adwaita Sans 11" 2>> "$LOG" || true
    fi
fi

if [[ ! -f "$TARGET/radicale/config" ]]; then
    draw 500 "Creando la configuración del calendario"
    mkdir -p "$TARGET/radicale" "$HOME/.local/share/radicale/collections"
    cat > "$TARGET/radicale/config" <<'RADICALE_CONFIG'
[server]
hosts = 127.0.0.1:5232

[auth]
type = none

[rights]
type = from_file
file = ~/.config/radicale/rights

[storage]
filesystem_folder = ~/.local/share/radicale/collections
RADICALE_CONFIG
    cat > "$TARGET/radicale/rights" <<'RADICALE_RIGHTS'
[public]
user: .*
collection: .*
permissions: RrWw
RADICALE_RIGHTS
fi

# Accesos a los popups en el lanzador (SUPER+Espacio). Solo si faltan:
# una copia local puede estar oculta o renombrada desde la pestaña
# "Aplicaciones" del panel. Los que piden un extra no instalado se omiten.
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$APPS_DIR"
for entry in "$TARGET"/flipfrog/desktop/*.desktop; do
    dest="$APPS_DIR/$(basename "$entry")"
    req="$(sed -n 's/^X-Flipfrog-Requires=//p' "$entry")"
    [[ -e "$dest" ]] && continue
    [[ -n "$req" ]] && ! command -v "$req" >/dev/null && continue
    draw 800 "Copiando acceso: $(sed -n 's/^Name=//p' "$entry")"
    sed "s|@HOME@|$HOME|g" "$entry" > "$dest"
done

WELCOME_FILE="$TARGET/flipfrog/scripts/keybinds/welcome.json"
[[ -f "$WELCOME_FILE" ]] || echo '{"show_on_login": true}' > "$WELCOME_FILE"

stage $W_FONTS "Actualizando las fuentes"
run fc-cache -f || true

DONE_W=$TOTAL_W; STAGE_W=0; STAGE_FRAC=0
draw 0 "${GREEN}Listo${RESET}"
break_bar
echo "[${GREEN}$(printf '█%.0s' $(seq 1 30))${RESET}] 100%  ${GREEN}${BOLD}Instalación completa${RESET}"

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
    echo
    echo "${BOLD}${YELLOW}Avisos:${RESET}"
    for w in "${WARNINGS[@]}"; do
        echo "  ${YELLOW}⚠${RESET} $w"
    done
    echo "  ${DIM}Detalles en $LOG${RESET}"
fi

echo
echo "${BOLD}Siguiente paso:${RESET} reinicia la PC."

if [[ $HAS_TTY == 1 ]]; then
    echo
    ask reboot_choice "¿Reiniciar ahora? (S/n) "
    if [[ ! "$reboot_choice" =~ ^[nN] ]]; then
        systemctl reboot
    fi
fi

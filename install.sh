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
# En la propia máquina de origen, correrlo de nuevo es inofensivo (todo
# aquí es idempotente: instala con --needed).
set -euo pipefail

TARGET="$HOME/.config"

if [[ "$(cd "$(dirname "$(readlink -f "$0")")" && pwd)" != "$TARGET" ]]; then
    echo "Este script asume que ya vive en ~/.config (ver comentario de uso arriba)." >&2
    exit 1
fi

# --- Salida: colores solo si hay una terminal real detrás (no al redirigir
# a un log) -- y contador de pasos para que se vea cuánto falta. Si sumas
# o quitas un step() actualiza TOTAL_STEPS a mano (abajo, tras las
# preguntas), son pocos y así no hace falta un pre-cálculo más elaborado.
STEP=0

if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; RESET=$'\033[0m'
    GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'
else
    BOLD=""; RESET=""; GREEN=""; YELLOW=""; RED=""; CYAN=""
fi

WARNINGS=()

step() {
    STEP=$((STEP + 1))
    echo
    echo "${BOLD}${CYAN}[$STEP/$TOTAL_STEPS]${RESET} ${BOLD}$1${RESET}"
}

ok()   { echo "      ${GREEN}✔${RESET} $1"; }
info() { echo "      ${CYAN}→${RESET} $1"; }
warn() { echo "      ${YELLOW}⚠${RESET} $1"; WARNINGS+=("$1"); }
err()  { echo "      ${RED}✘${RESET} $1" >&2; }

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

KEYBOARD_FILE="$TARGET/hypr/keyboard.lua"
KB="${FLIPFROG_KB:-}"
if [[ -z "$KB" && ! -f "$KEYBOARD_FILE" && $HAS_TTY == 1 ]]; then
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

EXTRAS="${FLIPFROG_EXTRAS:-}"
if [[ -z "$EXTRAS" ]]; then
    EXTRAS=0
    if [[ $HAS_TTY == 1 ]]; then
        echo
        echo "${BOLD}¿Instalar extras?${RESET} Asistente de voz, audiolibros narrados y temas"
        echo "para Steam. Bajan ~1 GB más y no hacen falta para empezar."
        ask extras_choice "¿Instalar extras? [s/N]: "
        [[ "$extras_choice" =~ ^[sSyY] ]] && EXTRAS=1
    fi
fi

TOTAL_STEPS=10
[[ $EXTRAS == 1 ]] && TOTAL_STEPS=12

if [[ $HAS_TTY == 1 ]]; then
    echo
    echo "${BOLD}Escribe tu contraseña (solo se pide una vez). Si después pregunta algo,"
    echo "presiona Enter: la opción por defecto está bien.${RESET}"
fi

# Contraseña una sola vez: las compilaciones de AUR tardan más que el
# tiempo que sudo recuerda la contraseña.
if [[ $HAS_TTY == 1 ]]; then sudo -v < /dev/tty; else sudo -v; fi
( while sleep 50; do sudo -n true 2>/dev/null || exit; kill -0 $$ 2>/dev/null || exit; done ) &
SUDO_KEEPALIVE=$!
trap 'kill $SUDO_KEEPALIVE 2>/dev/null' EXIT

# pacman sin "¿Continuar? [S/n]". --noconfirm contesta "no" si hay que
# quitar un paquete en conflicto y la instalación falla: solo entonces se
# repite preguntando.
pac() {
    sudo pacman -S --needed --noconfirm "$@" && return
    echo "      ${YELLOW}⚠${RESET} pacman necesita que confirmes algo, repitiendo con preguntas:"
    sudo pacman -S --needed "$@"
}

step "Paquetes oficiales (pacman) -- requiere repos estilo CachyOS para awww/mpvpaper"
pac \
    hyprland kitty \
    xdg-desktop-portal-hyprland gtk-layer-shell \
    python-gobject python-pillow python-cairo \
    pacman-contrib ffmpegthumbnailer icoutils \
    bluez bluez-utils ddcutil dunst wl-clipboard \
    thunar tumbler gvfs fish micro \
    adw-gtk-theme papirus-icon-theme awww mpvpaper \
    noto-fonts-emoji ttf-jetbrains-mono-nerd \
    radicale python-caldav python-icalendar python-httpx \
    hyprpolkitagent nethogs python-psutil \
    mpv python-requests yt-dlp imagemagick libjpeg-turbo
ok "Paquetes oficiales listos"

# hyprpolkitagent: agente de autenticación de polkit -- sin esto, pkexec
# (usado por firewall/ufw_actions.py para prender/apagar UFW y agregar/
# borrar reglas) falla en seco con "No authentication agent found" antes
# de mostrar cualquier diálogo (confirmado a mano). Se habilita como
# servicio de usuario, WantedBy=graphical-session.target -- persiste
# solo en cada login, no hace falta tocar autostart.lua.
# Sin gestor de login la PC arranca en una terminal de texto: pasa al
# instalar CachyOS con Hyprland sin el paquete de noctalia (probado en VM).
step "Pantalla de inicio de sesión"
if [[ -e /etc/systemd/system/display-manager.service ]]; then
    ok "Ya hay una: $(basename "$(readlink -f /etc/systemd/system/display-manager.service)" .service)"
elif pac sddm && sudo systemctl enable sddm.service; then
    # Sesión preseleccionada: Hyprland (no la variante con uwsm).
    printf '[Last]\nSession=/usr/share/wayland-sessions/hyprland.desktop\n' | sudo tee /var/lib/sddm/state.conf >/dev/null
    sudo chown sddm:sddm /var/lib/sddm/state.conf 2>/dev/null || true
    ok "SDDM instalado y habilitado"
else
    warn "No se pudo instalar SDDM -- al reiniciar entra con tu usuario y escribe: Hyprland"
fi
# Instalación normal de CachyOS: noctalia viene incluido y su panel lo
# arranca la config de Hyprland de fábrica (qs -c noctalia-shell), que
# bootstrap.sh ya movió al respaldo -- queda instalado pero sin arrancar.
if pacman -Q noctalia-shell >/dev/null 2>&1; then
    if grep -rqs 'noctalia-shell' "$TARGET/hypr/"; then
        warn "Tu config de Hyprland todavía arranca noctalia -- quita esa línea de hypr/config/autostart.lua"
    else
        ok "noctalia está instalado pero ya no arranca (su panel lo reemplaza esta barra)"
    fi
fi

step "Habilitar agente de polkit (hyprpolkitagent)"
if systemctl --user enable --now hyprpolkitagent.service; then
    ok "hyprpolkitagent habilitado"
else
    warn "No se pudo habilitar hyprpolkitagent.service -- pkexec (UFW en el dashboard) no va a funcionar hasta resolverlo a mano"
fi

# filter-chain.service: proceso APARTE del pipewire.service principal
# (BindsTo=pipewire.service) que carga el ecualizador de 6 bandas de la
# pestaña "Sonido" (~/.config/pipewire/filter-chain.conf.d/sink-eq6.conf,
# plantilla de fábrica de PipeWire -- sin plugins externos). Habilitarlo
# no reinicia pipewire ni corta audio en curso (confirmado a mano).
step "Habilitar ecualizador de audio (filter-chain.service)"
if systemctl --user enable --now filter-chain.service; then
    ok "filter-chain.service habilitado"
else
    warn "No se pudo habilitar filter-chain.service -- la pestaña 'Sonido' del dashboard no va a tener sink que controlar hasta resolverlo a mano"
fi

step "paru (AUR helper)"
if command -v paru >/dev/null; then
    ok "paru ya está instalado"
else
    if pac paru; then
        ok "paru instalado"
    else
        err "paru no está en tus repos -- bootstrap manual desde AUR:"
        echo "        git clone https://aur.archlinux.org/paru.git && cd paru && makepkg -si" >&2
        exit 1
    fi
fi

# Apps del día a día (pedido explícito del usuario, rutina para amigos).
# Cada una se salta si su comando ya existe: en la máquina de origen
# vesktop vino de AUR y vesktop-bin chocaría con él.
step "Apps: Brave Origin, Steam y Vesktop (Discord)"
APPS_PACMAN=()
command -v brave-origin >/dev/null || APPS_PACMAN+=(brave-origin-bin)
command -v steam        >/dev/null || APPS_PACMAN+=(steam)
command -v vesktop      >/dev/null || APPS_PACMAN+=(vesktop-bin)
if [[ ${#APPS_PACMAN[@]} -eq 0 ]]; then
    ok "Ya estaban instaladas"
elif pac "${APPS_PACMAN[@]}"; then
    ok "Instalado: ${APPS_PACMAN[*]}"
else
    warn "Falló la instalación de ${APPS_PACMAN[*]} -- reinténtalo con: sudo pacman -S ${APPS_PACMAN[*]}"
fi

# realesrgan: acción "Escalar imagen con IA" de Thunar (Thunar/uca.xml).
step "Paquetes AUR: ZapZap (WhatsApp), cursor, escalado de imágenes con IA"
APPS_AUR=(bibata-cursor-theme-bin realesrgan-ncnn-vulkan-bin)
command -v zapzap >/dev/null || APPS_AUR+=(zapzap)
if paru -S --needed --noconfirm --skipreview "${APPS_AUR[@]}"; then
    ok "Instalado: ${APPS_AUR[*]}"
else
    warn "Falló algo de AUR (${APPS_AUR[*]}) -- reinténtalo con: paru -S ${APPS_AUR[*]}"
fi

if [[ $EXTRAS == 1 ]]; then
    # python-onnxruntime-cpu ANTES de piper-tts a propósito -- piper-tts
    # depende de un proveedor de python-onnxruntime y, sin resolverlo
    # primero, paru pregunta de forma interactiva (cpu/cuda/opt-cuda/rocm/
    # opt-rocm). CPU alcanza de sobra para Piper -- CUDA además puede no
    # cargar según el estado del driver de NVIDIA en ese momento.
    step "Extras: asistente de voz, audiolibros, temas de Steam"
    if pac whisper-cpp python-webrtcvad python-onnxruntime-cpu \
        && paru -S --needed --noconfirm --skipreview millennium-bin piper-tts; then
        ok "Extras listos"
    else
        warn "Falló algo de los extras -- instala manualmente lo que quedó pendiente, no bloquea el resto"
    fi

    # Modelos del asistente de voz (flipfrog/scripts/assistant/) -- no
    # vienen con los paquetes whisper-cpp/piper-tts, se bajan aparte a
    # ~/.cache. Tamaños grandes (~465MiB + ~73MiB) -- solo si faltan, cada
    # uno primero a un .part y recién al final se renombra (para no dejar
    # un archivo truncado si se corta a la mitad).
    VOICE_ASSISTANT_CACHE="$HOME/.cache/waybar-voice-assistant"
    step "Modelos de voz (whisper GGML + voz de Piper en español)"
    mkdir -p "$VOICE_ASSISTANT_CACHE"
    _dl() {
        local url="$1" dest="$2"
        if [[ -f "$dest" ]]; then
            ok "$(basename "$dest") ya existe"
            return
        fi
        info "Descargando $(basename "$dest")…"
        if curl -fL --progress-bar -o "$dest.part" "$url"; then
            mv "$dest.part" "$dest"
            ok "$(basename "$dest") listo"
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

step "Distribución de teclado"
if [[ -n "$KB" ]]; then
    case "$KB" in
        us-intl) kb_layout=us; kb_variant=intl ;;
        *)       kb_layout="$KB"; kb_variant="" ;;
    esac
    printf 'return { layout = "%s", variant = "%s" }\n' "$kb_layout" "$kb_variant" > "$KEYBOARD_FILE"
    ok "Teclado: $kb_layout${kb_variant:+ ($kb_variant)}"
elif [[ -f "$KEYBOARD_FILE" ]]; then
    ok "hypr/keyboard.lua ya existe, no se toca"
else
    ok "Sin elegir: queda Español Latinoamérica (latam)"
fi

step "Bootstrap de estado runtime (no versionado por diseño, ver .gitignore)"
if [[ ! -f "$TARGET/flipfrog/themer/colors.css" ]]; then
    info "Aplicando tema por defecto (dragon-blue) para tener un colors.css válido"
    bash "$TARGET/flipfrog/themer/apply-theme.sh" "$TARGET/flipfrog/themer/themes/dragon-blue.theme" || \
        warn "No se pudo aplicar el tema por defecto, revisa flipfrog/themer/apply-theme.sh a mano"
else
    ok "colors.css ya existe, no se toca"
fi
if [[ ! -f "$TARGET/radicale/config" ]]; then
    info "Config de Radicale no existe, creando (calendario CalDAV local, sin auth)"
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
    info "Radicale solo escucha en esta PC. Para sincronizar el celular (DAVx5): hosts = 0.0.0.0:5232 en radicale/config + regla de firewall solo-LAN (ver CLAUDE.md, sección calendario)"
else
    ok "Config de Radicale ya existe, no se toca"
fi
WELCOME_FILE="$TARGET/flipfrog/scripts/keybinds/welcome.json"
if [[ ! -f "$WELCOME_FILE" ]]; then
    echo '{"show_on_login": true}' > "$WELCOME_FILE"
    ok "La ventana de atajos se abrirá al iniciar sesión"
fi

step "Recargando fuentes"
fc-cache -f >/dev/null
ok "Cache de fuentes actualizada"

echo
echo "${BOLD}${GREEN}==> Instalación completa ($TOTAL_STEPS/$TOTAL_STEPS)${RESET}"

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
    echo
    echo "${BOLD}${YELLOW}Avisos durante la instalación:${RESET}"
    for w in "${WARNINGS[@]}"; do
        echo "  ${YELLOW}⚠${RESET} $w"
    done
fi

cat <<EOF

${BOLD}Siguiente paso:${RESET} reinicia la PC. Al volver a entrar se abre una
ventana con los atajos de teclado (también con SUPER + H cuando quieras).
Lo más útil: SUPER + Espacio abre apps, SUPER + + abre el panel de ajustes.
EOF

if [[ $HAS_TTY == 1 ]]; then
    ask reboot_choice "¿Reiniciar ahora? [S/n]: "
    if [[ ! "$reboot_choice" =~ ^[nN] ]]; then
        systemctl reboot
    fi
fi

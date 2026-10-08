# Dependencias de los popups

Paquetes que **no** vienen con la instalación por defecto de CachyOS (edición Hyprland, calculado contra lo que instaló Calamares en `/var/log/pacman.log`). Todo lo de aquí lo instala `~/.config/install.sh` (lo marcado *extra* solo si se marca en el menú de extras).

Ya vienen por defecto y no se listan: `ffmpeg`, `ffmpegthumbnailer`, `grim`, `ufw`, `polkit` (`pkexec`), `fish`, PipeWire (`pw-*`, `pactl`), `libnotify`, `wl-clipboard`, `xdg-utils`, `xdg-user-dirs`, `python-psutil`, `python-cairo`, `python-gobject`, `gtk3`.

## Comunes a todos

| Paquete | Para qué |
|---|---|
| `gtk-layer-shell` | Ventana tipo popup sobre Wayland (`waybar_lib.build_layer_window`) y la barra (`bar/bar.py`) |
| `sddm` | Pantalla de inicio de sesión (tema `flipfrog/sddm/flipfrog`, usa `QtQuick.Effects` de `qt6-declarative`, que trae SDDM), solo se instala si no hay otro gestor (`display-manager.service`); sin él CachyOS arranca en terminal de texto si se quitó noctalia al instalar |
| `wl-clip-persist` | Que lo copiado no se pierda al cerrar la app de donde salió (autostart, "Portapapeles persistente") |
| `hyprpolkitagent` | Agente de polkit (servicio de usuario, `systemctl --user enable --now hyprpolkitagent`): sin él, todo `pkexec` falla con "No authentication agent found" antes de pedir contraseña |

## Por popup

| Popup | Paquetes | Uso |
|---|---|---|
| Descubre Bandcamp | `python-requests`, `mpv` | Scraping de Bandcamp; reproductor del daemon |
| Galería de temas | `python-pillow`, `awww`, `mpvpaper` | Miniaturas; `apply-theme.sh` pone el fondo (imagen/video) |
| Editor de temas | `python-pillow` | Detectar colores desde el wallpaper |
| Google Fonts | -- | |
| Selectores de cursor e íconos | `hyprpolkitagent` | "Instalar" de los repositorios en "Obtener cursores"/"Obtener íconos" (`pkexec pacman -S`); las vistas previas y la KDE Store extraen con `bsdtar`, que trae pacman |
| Descargas | `python-requests`, `yt-dlp` | MediaFire; YouTube (vía la función `ytd` de fish) |
| Audiolibros | `python-requests`, `mpv`, `piper-tts` (AUR, *extra*) | Bajar voces; reproductor; narración |
| Notificaciones | `dunst` | `dunstctl history` |
| Atajos de teclado | -- | |
| Collage de ventanas | -- | |
| Uso de disco | `hyprpolkitagent`, `thunar` | "Escanear como administrador"; "Abrir en Thunar" |
| Servicios | -- | |
| Firewall / Registro del firewall | `hyprpolkitagent` | Diálogo de contraseña de `pkexec ufw` |
| Calendario | `python-caldav`, `radicale` | Cliente CalDAV; servidor local de notas |
| Alarmas | -- | |
| Volumen / Mezclador / Salida de audio | -- | |

`piper-tts` depende de un proveedor de `python-onnxruntime`: si no está instalado antes, paru pregunta cuál usar y un `paru -S piper-tts` sin atender se queda esperando. `install.sh` instala `python-onnxruntime-cpu` primero por eso.

### Archivos que no son paquetes

- `~/.local/share/flipfrog/audiobooks/voices/es_ES-sharvard-medium.onnx(.json)` -- voz de Piper en español para Audiolibros (la baja `install.sh` con los extras; Audiolibros baja las que falten).

## Acciones de Thunar (`Thunar/uca.xml`)

`thunar` + `tumbler` (miniaturas) + `gvfs` (papelera, USB). "Subir a Proton Drive" necesita el CLI `proton-drive` y una cuenta, no lo instala `install.sh`.

| Acción | Paquetes |
|---|---|
| Rotar a la izquierda / derecha (`Thunar/scripts/rotate-image.sh`) | `libjpeg-turbo` (`jpegtran`, JPEG sin pérdida), `imagemagick` (resto de formatos y JPEG con orientación EXIF) |
| Poner como fondo de pantalla (`flipfrog/themer/wallpaper.py`) | `awww` (imágenes y gif), `mpvpaper` (videos) |
| Escalar imagen con IA (`Thunar/scripts/upscale-image.sh`) | `realesrgan-ncnn-vulkan-bin` (AUR, trae el modelo `realesrgan-x4plus`), `imagemagick` (entrada/EXIF y reducir x4 a x2) |

## Apps de la instalación (no son dependencias de ningún popup)

Pedido explícito del usuario para que una instalación nueva quede lista para usar. Cada una se salta si su comando ya existe.

| Paquete | Para qué |
|---|---|
| `brave-origin-bin` | Navegador (SUPER+B) |
| `steam` (multilib, *extra*) | Juegos; los íconos de juegos los genera `steam/steam-icons-sync.py` |
| `vesktop-bin` (*extra*) | Discord (autostart). `vesktop` sin `-bin` no está en los repos, solo en AUR |
| `gamescope` | Resolución y escalado por juego |
| `bibata-cursor-theme-bin` (AUR) | Cursor que espera `uwsm/env` |
| `millennium-bin` (AUR, *extra*) | Temas de Steam (`sync_millennium.py`) |
| `zapzap` (AUR, *extra*) | WhatsApp (autostart) |

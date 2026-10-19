# Paquetes eliminados

Histórico de paquetes desinstalados del sistema durante la limpieza de dependencias. Si algo deja de funcionar, buscar aquí primero si falta alguno de estos.

- Registro completo: `grep removed /var/log/pacman.log`
- Cada desinstalación tiene snapshot de snapper (pre/post): `snapper -c root list`. También se puede arrancar uno desde el menú de Limine.
- Reinstalar: `sudo pacman -S <paquete>`. Los que vienen del AUR: `paru -S <paquete>`.

## 2026-10-04

### Reemplazados por código propio

| Paquete | Versión | Motivo | Reemplazo |
|---|---|---|---|
| `wlogout` | 1.2.2-0 | Menú apagar/reiniciar | `flipfrog/scripts/power/power_menu.py` |
| `nwg-look` | 1.1.1-3.1 | Ajustes de GTK | Pestaña "Personalización" (fuente, cursor, íconos) |
| `waypaper` | 2.9-1 | Poner y restaurar el fondo | `flipfrog/themer/wallpaper.py` |

Se fueron con ellos, porque solo esos los usaban: `gobject-introspection`, `python-markdown`, `python-mako` (wlogout); `xcur2png` (nwg-look); `python-screeninfo`, `python-imageio`, `python-imageio-ffmpeg` (waypaper).

### Herramientas de compilación huérfanas (snapshot post: 498)

Quedaron de compilar paquetes del AUR y ningún paquete instalado las requería. paru las vuelve a instalar solas si un paquete del AUR las necesita para compilar.

| Paquete | Versión |
|---|---|
| `rust` | 1:1.98.1-1.1 |
| `go` | 2:1.27.1-2 |
| `gcc15`, `gcc15-libs` | 15.3.0+r0.g4db0e8df15be-2 |
| `llvm21-libs` | 21.1.8-1.1 |
| `meson` | 1.12.1-1 |
| `cmake` | 4.4.3-2.1 |
| `ninja` | 1.13.2-3.1 |
| `pnpm` | 11.26.0-1 |
| `fontforge` | 20251009-5.1 |
| `blueprint-compiler` | 0.22.2-1 |
| `hyprwayland-scanner` | 0.4.6-1.1 |
| `appstream-glib` | 0.8.4-1.1 |
| `chrpath` | 0.18-1.1 |
| `svt-hevc` | 1.5.1-4.1 |
| `potrace` | 1.16-5.1 |
| `libuninameslist` | 20260107-1.1 |
| `rhash` | 1.4.6-1.1 |
| `cppdap` | 1.58.0-3.1 |
| `python-build` | 1.6.0-1 |
| `python-installer` | 1.0.1-1 |
| `python-pyproject-hooks` | 1.3.3-1 |
| `python-scikit-build` | 0.19.1-1 |
| `python-pkg_resources` | 81.0.0-1 |
| `python-pathvalidate` | 3.3.1-2 (reinstalado, ver abajo) |
| `python-tqdm` | 4.70.1-1 |

`~/.cargo/` (1.5 GB, caché de crates de esas compilaciones) no se borró.

### Sin uso

| Paquete | Versión | Motivo |
|---|---|---|
| `kvantum` | 1.1.8-1.1 | Motor de temas Qt sin activar: las apps Qt usan `QT_QPA_PLATFORMTHEME=gtk3` (`uwsm/env`) con estilo Fusion. Se borró también `~/.config/Kvantum/` (solo el tema "Default" autogenerado) y `kvantummanager` de la regla de ventanas flotantes en `windowrules.lua`. Snapshot post: 506 |

### Reinstalado

`piper-tts` 1.8.0-1 (AUR, snapshot post: 504) -- faltaba desde 2026-09-28: un `pacman -S piper` (otro paquete, configurador de mouses gamer, en conflicto) lo quitó y la reinstalación de ese día no llegó a completarse. Trajo de vuelta `python-pathvalidate`, que es dependencia suya. paru instaló y volvió a quitar sus herramientas de compilación (`--removemake`).

`python-onnxruntime-cpu` aparecía como huérfano pero Piper lo necesita (audiolibros, asistente de voz): se marcó como instalado explícitamente (`pacman -D --asexplicit`) para que una limpieza de huérfanos no lo quite.

## 2026-10-19

### Sin uso

| Paquete | Versión | Motivo |
|---|---|---|
| `dolphin` | 26.08.2-1.1 | Administrador de archivos de KDE, venía con CachyOS; el repo usa Thunar (`inode/directory` en `mimeapps.list`). Se quitó también su regla de ventana flotante de `windowrules.lua` |

Se fueron con él, porque solo él los usaba: `baloo` 6.31.0-2.1 y `baloo-widgets` 26.08.2-1.1 (indexador de archivos de KDE), `ripgrep-all` 0.10.10-3.1 (`rga`), `poppler-qt6`, `qt6-multimedia`, `qt6-multimedia-ffmpeg`, `qt6-speech`, `kdsoap`, `kdsoap-ws-discovery-client`, `libkexiv2`, `ebook-tools`, `convertlit`, `libtommath`, `media-player-info`, `plasma-activities` y librerías de KDE Frameworks 6.31.0 (`kio`, `kio-extras`, `kparts`, `kxmlgui`, `kcmutils`, `kirigami`, `knewstuff`, `kfilemetadata`, `solid`, `kservice`, `kiconthemes`, `karchive`, `kcodecs`, `sonnet`, `syndication`, `attica`, `kpackage`, `kuserfeedback`, `ktextwidgets`, `syntax-highlighting`, `kdnssd`, `kglobalaccel`, `kconfigwidgets`, `kidletime`, `kitemviews`, `kjobwidgets`, `kcompletion`, `kbookmarks`). 44 paquetes en total.

`breeze-icons` se marcó como instalado explícitamente antes (`pacman -D --asexplicit`): se iba con Dolphin, pero Papirus toma de Breeze los íconos que no trae (`Inherits=breeze-dark` en Papirus-Dark).

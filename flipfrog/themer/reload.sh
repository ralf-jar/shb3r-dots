#!/usr/bin/env bash
# Ajusta estos comandos a como recargas tú cada componente.
# Este script se ejecuta automáticamente cada vez que guardas desde la UI.

set -uo pipefail

echo "Recargando Hyprland..."
hyprctl reload >/dev/null 2>&1

echo "Recargando dunst..."
python3 "$(dirname "$0")/sync_dunst.py"

echo "Recargando GTK3 (apps del sistema)..."
python3 "$(dirname "$0")/sync_gtk3.py"

echo "Recargando Qt/KDE (apps del sistema)..."
python3 "$(dirname "$0")/sync_qt.py"

echo "Recargando Kitty (fondo + paleta ANSI)..."
python3 "$(dirname "$0")/sync_kitty.py"

echo "Recoloreando fastfetch (logo + keys/title)..."
python3 "$(dirname "$0")/sync_fastfetch.py"

echo "Recoloreando btop (tema generado, recarga en caliente con SIGUSR2)..."
python3 "$(dirname "$0")/sync_btop.py"

echo "Sincronizando Vesktop (color de fondo, aplica la próxima vez que abras/reinicies la app)..."
python3 "$(dirname "$0")/sync_vesktop.py"

echo "Sincronizando Steam/Millennium (color de fondo, aplica la próxima vez que reinicies Steam)..."
python3 "$(dirname "$0")/sync_millennium.py"

echo "Sincronizando Zen Browser (color de fondo, aplica la próxima vez que reinicies Zen -- cerralo pronto, prefs.js se puede pisar solo si Zen sigue abierto)..."
python3 "$(dirname "$0")/sync_zen.py"

echo "Sincronizando Brave Origin (extensión de tema, aplica la próxima vez que reinicies Brave)..."
python3 "$(dirname "$0")/sync_brave.py"

# Tus configs .lua de Hyprland leen colors.css a través de color.lua,
# que se ejecuta como parte de la carga del config de Hyprland. Si
# `hyprctl reload` ya vuelve a ejecutar ese .lua (típico si usas un
# plugin tipo hyprlua), no necesitas nada más aquí. Si en tu caso
# color.lua se "compila" aparte (por ejemplo, genera un archivo que
# luego se importa), agrega ese paso aquí:
# /ruta/a/tu/script-que-corre-color.lua

echo "Listo."

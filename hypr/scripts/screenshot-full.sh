#!/usr/bin/env bash
# Captura completa del monitor activo (grim via hyprshot, todo el
# workspace visible ahí). Mismo criterio que screenshot.sh: si el
# escritorio especial "gaming" está activo en el monitor con foco,
# además de copiar al portapapeles guarda el archivo en ~/Imágenes.

workspace="$(hyprctl activeworkspace -j | jq -r '.name')"

if [ "$workspace" = "gaming" ]; then
    exec hyprshot -m output -m active -o "$HOME/Imágenes"
else
    exec hyprshot -m output -m active --clipboard-only
fi

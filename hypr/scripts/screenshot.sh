#!/usr/bin/env bash
# Captura de región (grim/slurp via hyprshot). Si el escritorio especial
# "gaming" está activo en el monitor con foco, además de copiar al
# portapapeles guarda el archivo en ~/Imágenes -- fuera de Gaming se
# mantiene el comportamiento anterior (solo portapapeles, sin archivo).

workspace="$(hyprctl activeworkspace -j | jq -r '.name')"

if [ "$workspace" = "gaming" ]; then
    exec hyprshot -m region -o "$HOME/Imágenes"
else
    exec hyprshot -m region --clipboard-only
fi

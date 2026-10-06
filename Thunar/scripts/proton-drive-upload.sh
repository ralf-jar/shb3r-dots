#!/usr/bin/env bash
# Subida rápida a Proton Drive desde el menú de click derecho de Thunar
# (Editar > Acciones personalizadas -- ver ~/.config/Thunar/uca.xml).
#
# -f rename -d merge: la acción corre sin terminal visible -- sin fijar
# una estrategia de conflicto, `proton-drive` se queda esperando una
# respuesta interactiva que nunca llega.
set -euo pipefail

DEST="/my-files/thunar-uploader"
LOG="/tmp/proton-drive-upload.log"

names=()
for f in "$@"; do
    names+=("$(basename "$f")")
done
joined=$(IFS=', '; echo "${names[*]}")

if proton-drive filesystem upload -f rename -d merge "$@" "$DEST" > "$LOG" 2>&1; then
    notify-send -a "Proton Drive" -i document-send "Subida completa" "$joined"
else
    notify-send -a "Proton Drive" -i dialog-error -u critical "Error al subir" "$joined — revisa $LOG"
fi

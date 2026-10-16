#!/bin/bash
# Uso: convert-mov-to-mp4.sh archivo.mov...
# Recodifica a H.264 + AAC en .mp4, calidad casi igual al original, listo para YouTube/Facebook.
# No pisa nada: si <nombre>.mp4 ya existe, guarda <nombre>_web.mp4.

done_files=()
failed=()

notify-send -i video-x-generic "Convirtiendo a MP4" "$# archivo(s)"

for f in "$@"; do
    out="${f%.*}.mp4"
    [[ -e "$out" ]] && out="${f%.*}_web.mp4"

    if ffmpeg -nostdin -n -loglevel error -i "$f" \
        -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p \
        -c:a aac -b:a 192k -movflags +faststart "$out"; then
        done_files+=("$(basename "$out")")
    else
        rm -f "$out"; failed+=("$(basename "$f")")
    fi
done

if (( ${#done_files[@]} )); then
    notify-send -i video-x-generic "Conversión a MP4 lista" "$(printf '%s\n' "${done_files[@]}")"
fi

if (( ${#failed[@]} )); then
    notify-send -i dialog-error "No se pudo convertir" "$(printf '%s\n' "${failed[@]}")"
fi

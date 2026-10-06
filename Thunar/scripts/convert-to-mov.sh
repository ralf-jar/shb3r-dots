#!/bin/bash
# Uso: convert-to-mov.sh archivo...
# Recodifica a DNxHR HQ + PCM en .mov (lo que DaVinci Resolve gratis lee en Linux).
# No pisa nada: si <nombre>.mov ya existe, guarda <nombre>_resolve.mov.

done_files=()
failed=()

notify-send -i video-x-generic "Convirtiendo a MOV" "$# archivo(s)"

for f in "$@"; do
    out="${f%.*}.mov"
    [[ -e "$out" ]] && out="${f%.*}_resolve.mov"

    if ffmpeg -nostdin -n -loglevel error -i "$f" \
        -c:v dnxhd -profile:v dnxhr_hq -pix_fmt yuv422p \
        -c:a pcm_s16le "$out"; then
        done_files+=("$(basename "$out")")
    else
        rm -f "$out"; failed+=("$(basename "$f")")
    fi
done

if (( ${#done_files[@]} )); then
    notify-send -i video-x-generic "Conversión a MOV lista" "$(printf '%s\n' "${done_files[@]}")"
fi

if (( ${#failed[@]} )); then
    notify-send -i dialog-error "No se pudo convertir" "$(printf '%s\n' "${failed[@]}")"
fi

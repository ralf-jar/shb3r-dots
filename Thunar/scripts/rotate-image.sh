#!/bin/bash
# Uso: rotate-image.sh <90|270> archivo...
# JPEG sin orientación EXIF: jpegtran sin pérdida. Lo demás: ImageMagick,
# con -auto-orient para que la orientación EXIF no se sume al giro.

angle="$1"; shift
failed=()

for f in "$@"; do
    tmp="$(mktemp --suffix=".${f##*.}" -p "$(dirname "$f")")"
    orientation="$(magick identify -format '%[orientation]' "$f[0]" 2>/dev/null)"
    mime="$(file -b --mime-type "$f")"

    if [[ "$mime" == image/jpeg && ( -z "$orientation" || "$orientation" == TopLeft || "$orientation" == Undefined ) ]] \
        && jpegtran -perfect -copy all -rotate "$angle" -outfile "$tmp" "$f" 2>/dev/null; then
        :
    elif magick "$f" -auto-orient -rotate "$angle" "$tmp" 2>/dev/null; then
        :
    else
        rm -f "$tmp"; failed+=("$(basename "$f")"); continue
    fi

    chmod --reference="$f" "$tmp"
    mv -f "$tmp" "$f"
done

if (( ${#failed[@]} )); then
    notify-send -i dialog-error "No se pudo rotar" "$(printf '%s\n' "${failed[@]}")"
fi

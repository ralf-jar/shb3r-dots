#!/bin/bash
# Uso: extract-7z.sh archivo.7z...
# Extrae cada .7z con bsdtar (libarchive, sin p7zip) en una carpeta con su nombre, junto al original.
# No pisa nada: si la carpeta ya existe, usa <nombre>_2, _3...

done_dirs=()
failed=()

notify-send -i cm_extractfiles "Extrayendo 7z" "$# archivo(s)"

for f in "$@"; do
    base="${f%.*}"
    out="$base"
    n=2
    while [[ -e "$out" ]]; do out="${base}_$n"; ((n++)); done

    mkdir -p "$out"
    if bsdtar -xf "$f" -C "$out"; then
        done_dirs+=("$(basename "$out")")
    else
        rm -rf "$out"; failed+=("$(basename "$f")")
    fi
done

if (( ${#done_dirs[@]} )); then
    notify-send -i cm_extractfiles "7z extraído" "$(printf '%s\n' "${done_dirs[@]}")"
fi

if (( ${#failed[@]} )); then
    notify-send -i dialog-error "No se pudo extraer" "$(printf '%s\n' "${failed[@]}")"
fi

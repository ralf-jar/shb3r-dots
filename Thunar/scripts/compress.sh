#!/bin/bash
# Uso: compress.sh rar|zip|tar ruta...
# Comprime lo seleccionado en un solo archivo junto al original (tar = .tar.gz).
# Nombre: el del elemento si es uno solo, si no el de la carpeta que los contiene.
# No pisa nada: si el nombre ya existe, agrega _2, _3...

fmt=$1; shift
case $fmt in
    rar) ext=rar ;;
    zip) ext=zip ;;
    tar) ext=tar.gz ;;
    *) exit 1 ;;
esac

if [[ $fmt == rar ]] && ! command -v rar >/dev/null; then
    notify-send -i dialog-error "No se pudo comprimir" "Falta rar: paru -S rar"
    exit 1
fi

dir=$(dirname "$1")
names=()
for f in "$@"; do names+=("$(basename "$f")"); done

if (( $# == 1 )); then base=${names[0]}; else base=$(basename "$dir"); fi
out="$base.$ext"
n=2
while [[ -e "$dir/$out" ]]; do out="${base}_$n.$ext"; ((n++)); done

notify-send -i package-x-generic "Comprimiendo a $ext" "$out"

cd "$dir" || exit 1
case $fmt in
    rar) rar a -r -idq -- "$out" "${names[@]}" ;;
    zip) bsdtar -a -cf "$out" -- "${names[@]}" ;;
    tar) tar -czf "$out" -- "${names[@]}" ;;
esac

if (( $? == 0 )); then
    notify-send -i package-x-generic "Archivo comprimido listo" "$out"
else
    rm -f -- "$out"
    notify-send -i dialog-error "No se pudo comprimir" "$out"
fi

#!/bin/bash
# Uso: paste-image.sh <dirección de la ventana> <título de la ventana de Thunar>
# Ctrl+V en Thunar (lo llama keybinds.lua, que consume la tecla): con una
# imagen en el portapapeles, y no archivos copiados, la guarda en la carpeta
# abierta; con cualquier otra cosa le reenvía el Ctrl+V a Thunar.
# La carpeta sale del título: misc-window-title-style = full-path-with-suffix
# (lo fija install.sh).

address="$1"
dir="${2% - Thunar}"

pass_to_thunar() {
    hyprctl dispatch "hl.dsp.send_shortcut({ mods = 'CTRL', key = 'V', window = 'address:$address' })" >/dev/null
    exit 0
}

types="$(wl-paste --list-types 2>/dev/null)"
[[ -d "$dir" ]] || pass_to_thunar
grep -qxE 'text/uri-list|x-special/gnome-copied-files' <<<"$types" && pass_to_thunar
type="$(grep -m1 -x 'image/png' <<<"$types" || grep -m1 '^image/' <<<"$types")" || pass_to_thunar

case "$type" in
    image/jpeg) ext=jpg ;;
    image/svg+xml) ext=svg ;;
    *) ext="${type#image/}" ;;
esac

base="Imagen pegada $(date '+%Y-%m-%d %H-%M-%S')"
name="$base.$ext"
n=2
while [[ -e "$dir/$name" ]]; do
    name="$base ($n).$ext"; ((n++))
done

tmp="$(mktemp -p "$dir" .paste-image-XXXXXX 2>/dev/null)"
if [[ -n "$tmp" ]] && wl-paste --no-newline --type "$type" > "$tmp" && [[ -s "$tmp" ]]; then
    chmod "$(printf '%o' $(( 0666 & ~0$(umask) )))" "$tmp"
    mv -n "$tmp" "$dir/$name"
else
    [[ -n "$tmp" ]] && rm -f "$tmp"
    notify-send -i dialog-error "No se pudo pegar la imagen" "$dir"
fi

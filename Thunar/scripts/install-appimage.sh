#!/bin/bash
# Uso: install-appimage.sh archivo.AppImage...
# Mueve cada AppImage a ~/Applications, la hace ejecutable y crea su acceso en
# ~/.local/share/applications (nombre, ícono y StartupWMClass del .desktop que trae adentro).
# Si ya existe una con el mismo nombre de archivo, la reemplaza (actualizar).

APPS_DIR="$HOME/Applications"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/flipfrog/appimages/icons"
mkdir -p "$APPS_DIR" "$DESKTOP_DIR" "$ICON_DIR"

installed=()
failed=()

# Extrae <ruta> del AppImage $dest a $tmp/squashfs-root siguiendo symlinks
# (el .desktop y el .DirIcon suelen apuntar a usr/share/...); imprime la ruta real.
extract_real() {
    local rel="$1" n=0
    while (( n++ < 5 )); do
        (cd "$tmp" && "$dest" --appimage-extract "$rel") >/dev/null 2>&1
        local path="$tmp/squashfs-root/$rel"
        [[ -L "$path" ]] || { [[ -e "$path" ]] && echo "$path"; return; }
        local target="$(readlink "$path")"
        [[ "$target" == /* ]] && return
        rel="$(realpath -m --relative-to="$tmp/squashfs-root" "$(dirname "$path")/$target")"
    done
}

for f in "$@"; do
    file="$(basename "$f")"
    id="$(basename "${file%.*}" | tr ' ' '_')"
    dest="$APPS_DIR/$file"

    [[ "$(realpath "$f")" == "$(realpath -m "$dest")" ]] || mv -f "$f" "$dest" || { failed+=("$file"); continue; }
    chmod +x "$dest"

    tmp="$(mktemp -d)"
    # El runtime tipo 2 extrae sin FUSE; solo el .desktop y el ícono.
    (cd "$tmp" && "$dest" --appimage-extract '*.desktop') >/dev/null 2>&1
    inner="$(find "$tmp/squashfs-root" -maxdepth 1 -name '*.desktop' 2>/dev/null | head -1)"
    [[ -n "$inner" ]] && inner="$(extract_real "$(basename "$inner")")"

    name="" icon_name="" wmclass="" categories=""
    if [[ -n "$inner" ]]; then
        name="$(grep -m1 '^Name=' "$inner" | cut -d= -f2-)"
        icon_name="$(grep -m1 '^Icon=' "$inner" | cut -d= -f2-)"
        wmclass="$(grep -m1 '^StartupWMClass=' "$inner" | cut -d= -f2-)"
        wmclass="${wmclass%;}"
        categories="$(grep -m1 '^Categories=' "$inner" | cut -d= -f2-)"
    fi
    [[ -n "$name" ]] || name="${file%.*}"

    icon="application-x-executable"
    dir_icon="$(extract_real .DirIcon)"
    if [[ -z "$dir_icon" && -n "$icon_name" ]]; then
        for ext in png svg; do
            dir_icon="$(extract_real "$icon_name.$ext")" && [[ -n "$dir_icon" ]] && break
        done
    fi
    if [[ -n "$dir_icon" ]]; then
        ext="png"
        file -L "$dir_icon" | grep -q SVG && ext="svg"
        cp "$dir_icon" "$ICON_DIR/$id.$ext" && icon="$ICON_DIR/$id.$ext"
    fi
    rm -rf "$tmp"

    {
        echo "[Desktop Entry]"
        echo "Type=Application"
        echo "Name=$name"
        echo "Exec=\"$dest\" %U"
        echo "Icon=$icon"
        echo "Terminal=false"
        [[ -n "$wmclass" ]] && echo "StartupWMClass=$wmclass"
        [[ -n "$categories" ]] && echo "Categories=$categories"
        echo "X-Flipfrog-AppImage=$dest"
    } > "$DESKTOP_DIR/appimage-$id.desktop"

    installed+=("$name")
done

update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1

if (( ${#installed[@]} )); then
    notify-send -i application-x-executable "AppImage instalada" "$(printf '%s\n' "${installed[@]}")"
fi

if (( ${#failed[@]} )); then
    notify-send -i dialog-error "No se pudo instalar" "$(printf '%s\n' "${failed[@]}")"
fi

#!/bin/bash
# Uso: upscale-image.sh archivo...
# Real-ESRGAN solo trae modelos x4 para fotos (realesrgan-x4plus); se
# escala x4 y se reduce a la mitad con ImageMagick -> <nombre>_x2.png.
# La entrada pasa antes por ImageMagick (-auto-orient) para que la
# orientación EXIF y formatos que Real-ESRGAN no lee (gif, bmp) no fallen.

tag="string:x-dunst-stack-tag:upscale-image"
done_list=()
failed=()

notify-send -h "$tag" -i upscayl "Escalando con IA" "$(printf '%s\n' "${@##*/}")"

for f in "$@"; do
    work="$(mktemp -d)"
    out="${f%.*}_x2.png"

    if magick "$f[0]" -auto-orient "$work/in.png" 2>/dev/null \
        && realesrgan-ncnn-vulkan -i "$work/in.png" -o "$work/x4.png" -n realesrgan-x4plus >/dev/null 2>&1 \
        && magick "$work/x4.png" -resize 50% "$out" 2>/dev/null; then
        done_list+=("$(basename "$out")")
    else
        failed+=("$(basename "$f")")
    fi

    rm -rf "$work"
done

if (( ${#done_list[@]} )); then
    notify-send -h "$tag" -i upscayl "Escalado completado" "$(printf '%s\n' "${done_list[@]}")"
fi
if (( ${#failed[@]} )); then
    notify-send -i dialog-error "No se pudo escalar" "$(printf '%s\n' "${failed[@]}")"
fi

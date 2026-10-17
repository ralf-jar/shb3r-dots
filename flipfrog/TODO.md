# TODO

## Programas predeterminados por tipo de archivo
Mismo esquema que Loupe: `install.sh` (apps base) + `mimeapps.list` + `DEPENDENCIAS.md`.

- **PDF** -- hoy lo abre GIMP. Candidatos: `papers` (GNOME, misma línea que Loupe) o `zathura` + `zathura-pdf-mupdf`. Revisar: PDF con contraseña y miniaturas en Thunar.
- **Comprimidos** (zip/7z/rar) -- sin programa asignado, doble clic no hace nada. Candidato: `xarchiver` + `thunar-archive-plugin` (+ `7zip`, `unrar`). Revisar: archivos con contraseña y vista previa del contenido sin extraer.
- **Texto plano** (`text/plain`, `.lua`, `.css`, `.json`) -- apunta a Sublime Text, que `install.sh` no instala (AUR, de paga). Buscar más opciones de editor gráfico antes de decidir (`gnome-text-editor`, `mousepad`, ...); Sublime quedaría como extra.
- **Video** -- `.mp4` va a VLC (extra), `.mkv`/`.webm` a mpv (base). Unificar en mpv.
- **Entradas de Zen** -- `x-scheme-handler/chrome`, `.htm`, `xhtml`, ... apuntan a `zen.desktop`, que no se instala. Pasarlas a Brave.

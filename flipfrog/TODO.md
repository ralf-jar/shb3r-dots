# TODO

## Programas predeterminados por tipo de archivo
Mismo esquema que Loupe: `install.sh` (apps base) + `mimeapps.list` + `DEPENDENCIAS.md`.

- **PDF** -- hoy lo abre GIMP. Candidatos: `papers` (GNOME, misma línea que Loupe) o `zathura` + `zathura-pdf-mupdf`. Revisar: PDF con contraseña y miniaturas en Thunar.
- **Comprimidos** (zip/7z/rar) -- sin programa asignado, doble clic no hace nada. Candidato: `xarchiver` + `thunar-archive-plugin` (+ `7zip`, `unrar`). Revisar: archivos con contraseña y vista previa del contenido sin extraer.
- **Texto plano** (`text/plain`, `.lua`, `.css`, `.json`) -- apunta a Sublime Text, que `install.sh` no instala (AUR, de paga). Buscar más opciones de editor gráfico antes de decidir (`gnome-text-editor`, `mousepad`, ...); Sublime quedaría como extra.
- **Video** -- `.mp4` va a VLC (extra), `.mkv`/`.webm` a mpv (base). Unificar en mpv.
- **Entradas de Zen** -- `x-scheme-handler/chrome`, `.htm`, `xhtml`, ... apuntan a `zen.desktop`, que no se instala. Pasarlas a Brave.

## Bugs

- **Tooltips invisibles en el popup de Audiolibros** (2026-10-24) -- al pasar el mouse sobre los botones del lector (índice, ir a la narración, "Aa", etc.) no aparece ningún tooltip. En una prueba aparte con el mismo código del lector (`AudiobookPopup` con namespace `audiobook-popup`, hover real con un mouse virtual de uinput) los tooltips SÍ se veían; esa prueba no arrancaba el sondeo al daemon (`_start_daemon`). Sospecha sin confirmar: `_sync_reader` corre cada `POLL_MS` (250 ms) y llama `play_btn.set_tooltip_text(...)` en cada vuelta; en GTK3 cambiar un tooltip vuelve a consultar el tooltip bajo el puntero, lo que podría reiniciar la espera (~500 ms) antes de mostrarlo. Revisar: repetir la prueba con el sondeo activo y, si es eso, cambiar el tooltip solo cuando cambie el estado de pausa. Ver cómo probar hover en la memoria del agente (`reference_simulate_hover`).

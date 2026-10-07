# shb3r-dots

Escritorio completo sobre **Hyprland** para **CachyOS**: barra propia, panel de ajustes, lanzador de apps, temas con fondos animados, notificaciones, calendario, descargas, música y más. Todo en Python + GTK3.

## Instalación

1. Instala [CachyOS](https://cachyos.org) y elige **Hyprland** como escritorio.
2. Abre una terminal y pega:

   ```bash
   curl -fsSL shb3r.github.io/i | bash
   ```

   (Es lo mismo que `curl -fsSL https://raw.githubusercontent.com/ralf-jar/shb3r-dots/main/bootstrap.sh | bash`.)

3. Contesta dos preguntas (teclado y extras). Si pregunta algo más, presiona Enter.
4. Reinicia. Al entrar se abre una ventana con los atajos de teclado.

Lo que ya tenías en `~/.config` con el mismo nombre se mueve a `~/.config-respaldo-<fecha>/`; no se borra nada. Al terminar puedes ver el detalle de todo lo que se instaló y modificó (paquetes, servicios, archivos); queda guardado en `~/.cache/flipfrog-install-resumen.txt`.

Además del escritorio instala Brave Origin, gamescope y Thunar con acciones de clic derecho (rotar y escalar imágenes, poner de fondo, extraer archivos). Si contestas que sí a los extras, también Steam (con temas), Vesktop (Discord), ZapZap (WhatsApp) y la narración de audiolibros.

## Atajos básicos

| Atajo | Qué hace |
|---|---|
| `SUPER + H` | Muestra todos los atajos |
| `SUPER + Espacio` | Abre apps |
| `SUPER + T` | Terminal |
| `SUPER + +` | Panel de ajustes (wifi, bluetooth, sonido, temas, monitores) |
| `SUPER + P` | Galería de temas |
| `SUPER + Q` | Cierra la ventana |

## Calendario en tu celular

Las notas del calendario (clic en el reloj) se pueden sincronizar con Android: en el calendario toca **«Sincronizar con el celular»** y sigue los 4 pasos. Guía completa: [`calendar.md`](flipfrog/scripts/calendar/calendar.md).

## Más información

- Paquetes que instala y para qué: [`flipfrog/DEPENDENCIAS.md`](flipfrog/DEPENDENCIAS.md)
- Cómo está hecho por dentro: [`CLAUDE.md`](CLAUDE.md)

## Licencia

[GPLv3](LICENSE)

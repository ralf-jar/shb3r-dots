# shb3r-dots

Escritorio completo sobre **Hyprland** para **CachyOS**: barra propia, panel de ajustes, lanzador de apps, temas con fondos animados, notificaciones, calendario, descargas, música y más. Todo en Python + GTK3.

## Instalación

1. Instala [CachyOS](https://cachyos.org) y elige **Hyprland** como escritorio.
2. Abre una terminal y pega:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/ralf-jar/shb3r-dots/master/bootstrap.sh | bash
   ```

3. Contesta dos preguntas (teclado y extras). Si pregunta algo más, presiona Enter.
4. Reinicia. Al entrar se abre una ventana con los atajos de teclado.

Lo que ya tenías en `~/.config` con el mismo nombre se mueve a `~/.config-respaldo-<fecha>/`; no se borra nada.

Además del escritorio instala Brave Origin, Steam, Vesktop (Discord), ZapZap (WhatsApp) y Thunar con acciones de clic derecho (rotar y escalar imágenes, poner de fondo, extraer archivos).

## Atajos básicos

| Atajo | Qué hace |
|---|---|
| `SUPER + H` | Muestra todos los atajos |
| `SUPER + Espacio` | Abre apps |
| `SUPER + T` | Terminal |
| `SUPER + +` | Panel de ajustes (wifi, bluetooth, sonido, temas, monitores) |
| `SUPER + P` | Galería de temas |
| `SUPER + Q` | Cierra la ventana |

## Más información

- Paquetes que instala y para qué: [`flipfrog/DEPENDENCIAS.md`](flipfrog/DEPENDENCIAS.md)
- Cómo está hecho por dentro: [`CLAUDE.md`](CLAUDE.md)

---
name: documentar-dependencias
description: Registra en flipfrog/DEPENDENCIAS.md e install.sh cualquier paquete, módulo de Python, binario externo o archivo descargado que un cambio del repo empiece a usar. Úsala siempre que escribas o modifiques código que importe un módulo de Python, llame un comando con subprocess/exec_cmd/bash, o dependa de un servicio o modelo que antes no se usaba, incluidos popups, daemons, toggles, acciones de Thunar y scripts .sh. También cuando se quite una dependencia.
---

# Documentar dependencias nuevas

Antes de dar por terminado un cambio, revisa si agrega o quita una dependencia externa. Si lo hace, actualiza la documentación en el mismo cambio, sin esperar a que se pida.

## 1. Detectar

Revisa lo que agregó el cambio:
- `import` / `from ... import` de Python que no sean de la librería estándar ni módulos locales del repo. `gi.require_version("X", ...)` cuenta como el paquete dueño del typelib.
- Primer elemento de las listas de `subprocess.run`/`Popen`, `hl.dsp.exec_cmd(...)` de Lua, y comandos dentro de `.sh`.
- Servicios (`systemctl --user`), modelos y voces descargados, y CLIs instalados fuera de pacman.

## 2. Resolver el paquete y si viene por defecto

```bash
pacman -Qqo "$(command -v <binario>)"                     # binario
python3 -c "import <m>; print(<m>.__file__)"              # luego pacman -Qqo <ruta>
pacman -Qqo /usr/lib/girepository-1.0/<Nombre>-*.typelib  # gi
```

Qué cuenta como "por defecto": lo que instaló Calamares, que son las líneas `[ALPM] installed` de `/var/log/pacman.log` hasta antes del primer `pacman -Rns cachyos-hypr-noctalia` (línea ~1431):

```bash
head -1431 /var/log/pacman.log | grep -oP '\[ALPM\] installed \K\S+' | grep -qx <paquete> && echo default
```

Un paquete que ya viene por defecto no se documenta. Tampoco uno que ya está listado para ese mismo popup o acción.

## 3. Escribir

- **`flipfrog/DEPENDENCIAS.md`**: agrega el paquete a la fila del popup o acción que corresponda, con el uso en pocas palabras. Si es un popup o acción nueva, crea su fila. Si lo usan todos, va a "Comunes a todos". Si es de AUR o no viene de pacman, márcalo así, por ejemplo `(AUR)` o `(CLI, fuera de pacman)`. Los modelos y archivos descargados van en "Archivos que no son paquetes".
- **`install.sh`**:
  - Paquete oficial: va a la lista de `sudo pacman -S --needed`.
  - Paquete de AUR: va al `paru -S --needed`.
  - Si instalarlo pide algo interactivo (como el proveedor de onnxruntime de `piper-tts`), resuélvelo antes, con un comentario de por qué.
- **Si el cambio quita la última dependencia de un paquete**: quítalo de los dos lugares. Antes, confirma con `grep` que ningún otro script lo sigue usando.

## 4. Commit

`DEPENDENCIAS.md` se sube en el commit `doc: context and documentation agents`. Los cambios a `install.sh` van con el código, en el commit `feat:`/`fix:`. Así lo pide la regla de commits de `CLAUDE.md`.

## 5. Avisar

Al terminar, dile al usuario en una línea qué dependencia se agregó y si ya está instalada en esta máquina (`pacman -Q <paquete>`). Si no está, dale el comando para instalarla. No la instales tú sin preguntar.

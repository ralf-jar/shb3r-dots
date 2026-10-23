# Pantalla de arranque (Plymouth): qué cambia y cómo revertirlo

Selector de "Personalización" → **Pantalla de arranque** / **Obtener pantallas de arranque**
(2026-10-22). Código en `flipfrog/scripts/theme/boot_picker.py` + `plymouth_themes.py`;
detalle técnico en `CLAUDE.md`, sección "Selectores con vista previa".

## 1. Qué toca del sistema (fuera del repo)

Todo pasa por `pkexec` (pide contraseña). Nada más se modifica.

| Acción del selector | Qué cambia |
|---|---|
| Clic en un tema instalado | `plymouth-set-default-theme -R <tema>`: reescribe `Theme=` en `/etc/plymouth/plymouthd.conf` y reconstruye el initramfs (`/usr/lib/plymouth/plymouth-update-initrd` → `limine-mkinitcpio` en CachyOS) |
| "Instalar" (KDE Store) | Copia el tema a `/usr/share/plymouth/themes/<tema>/` con un archivo `.flipfrog-store` (id de la tienda). En la copia, `ImageDir=`/`ScriptFile=` del `.plymouth` apuntan a esa carpeta |
| "Quitar" | Borra `/usr/share/plymouth/themes/<tema>/`, solo si tiene `.flipfrog-store` |
| Abrir la tienda | Baja capturas y temas a `~/.cache/flipfrog-plymouth-store/` (sin root) |

Los temas que trae CachyOS (`cachyos-bootanimation`, el de fábrica; `cachyos`, `spinner`, `bgrt`, …)
nunca se borran ni se editan.

## 2. Volver a la pantalla de arranque de fábrica

```sh
plymouth-set-default-theme                          # tema actual
sudo plymouth-set-default-theme -R cachyos-bootanimation
```

Quitar todos los temas instalados desde la tienda:

```sh
for d in /usr/share/plymouth/themes/*/; do
    [ -f "$d/.flipfrog-store" ] && echo "$d"        # revisar la lista primero
done
# luego, por cada uno:  sudo rm -rf /usr/share/plymouth/themes/<tema>
rm -rf ~/.cache/flipfrog-plymouth-store
```

Si el tema activo era uno de esos, primero vuelve al de fábrica (arriba): si no, la
próxima reconstrucción del initramfs (cualquier actualización del kernel) buscaría un tema
que ya no existe.

## 3. Si la PC no arranca bien después de cambiar el tema

Un tema roto no debería impedir el arranque: Plymouth cae al modo texto y el sistema sigue.
Si aun así algo falla:

1. En el menú de Limine elige **Snapshots** (limine-snapper-sync) o la entrada de respaldo
   (`fallback`) del kernel.
2. Ya dentro: `sudo plymouth-set-default-theme -R cachyos-bootanimation`.
3. Sin poder entrar: USB de CachyOS → montar el sistema → `arch-chroot` →
   `plymouth-set-default-theme -R cachyos-bootanimation`.

## 4. Revertir el código

Commits en `main` (los de SDDM ya están reemplazados por los de Plymouth):

```
9e49b34 feat: login screen (SDDM) picker with KDE Store themes
0a14555 doc: context and documentation agents
aca5e08 feat: boot screen (Plymouth) picker instead of the SDDM one
68c1574 doc: context and documentation agents
4bb6df2 fix: boot picker shows progress while the initramfs rebuilds
ecc843c fix: boot picker rewrites Debian theme paths on install, store size in KB
b4e2ae8 fix: picker cards apply only on a click that started on the card
a6ce510 fix: boot picker progress notification expires on its own
978893c doc: context and documentation agents
```

Todo de una vez (deja un commit de reversión, no reescribe historia):

```sh
git checkout main
git revert --no-edit 9e49b34^..HEAD   # si HEAD sigue siendo el último de la lista
git checkout personal/themes-changes && git merge main
```

Archivos que tocan: `flipfrog/scripts/theme/{boot_picker,plymouth_themes,theme_picker,theme_kinds,
kde_store,theme_module}.py`, `flipfrog/scripts/waybar_lib.py` (lock `/tmp/boot-picker.pid`),
`hypr/config/windowrules.lua` (blur y `no_anim` del namespace `boot-picker`),
`flipfrog/i18n/{es-MX,en}.json` (códigos `arranque_*`), `CLAUDE.md` y este archivo.

Revertir a medias: quitar solo las dos filas de `theme_module.py` (bloque `boot_row`) esconde la
función sin tocar lo demás. Los cambios de `theme_picker.py` (pestañas configurables, `_run_hidden`,
clic que solo cuenta si empieza en la misma tarjeta, tamaño en KB) también los usan los selectores de
cursor e íconos.

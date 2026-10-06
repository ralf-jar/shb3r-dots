# Software instalado para el calendario (Radicale/CalDAV)

Todo lo que hubo que instalar en esta PC para que `calendar_popup.py`
sincronice notas con el celular vía CalDAV. Todos son paquetes oficiales de
Arch (repo `extra`), ninguno vino de AUR. Ya están agregados al
`pacman -S --needed` de `install.sh` para que una instalación nueva de este
repo los traiga solos.

| Paquete             | Repo    | Para qué |
|---------------------|---------|----------|
| `radicale`          | extra   | El servidor CalDAV/CardDAV en sí -- guarda los eventos y los expone por HTTP en `127.0.0.1:5232` / LAN. |
| `python-caldav`      | extra   | Librería que usa `calendar_popup.py` para hablar con Radicale (conectar, listar/crear/editar/borrar eventos) sin armar el protocolo CalDAV a mano. |
| `python-icalendar`   | extra   | Parseo/generación de eventos `VEVENT` (formato iCal) -- dependencia de `python-caldav`, y la usó también el script de migración de una sola vez. |
| `python-httpx`       | extra   | **No se usa directamente** -- hace falta solo porque un chequeo interno de `python-caldav` 3.2.1 (`is_async_client`) hace `import caldav.async_davclient` en cada llamada, y ese módulo revienta con `ImportError` si no hay ninguna librería HTTP async instalada, aunque el cliente sea 100% síncrono. Instalar `python-httpx` (sin usarlo) le da a esa comprobación algo que encontrar y deja de tronar. Confirmado a mano -- sin este paquete, `caldav.DAVClient(...)` falla antes de conectar nada. |

`python-requests` también lo usa `python-caldav` por debajo, pero ya venía
instalado de antes en esta PC (dependencia de algo más) -- no se agregó a
`install.sh` porque no se confirmó si haría falta agregarlo a mano en una
instalación nueva.

## Del lado del celular (GrapheneOS)

No es "software de la PC", pero es la otra mitad del mismo flujo -- sin
esto no hay con qué sincronizar.

### Apps instaladas
- **DAVx5** (F-Droid o Play Store) -- sincroniza la colección CalDAV de
  Radicale contra el `CalendarProvider` de Android.
- **Etar** (F-Droid o Play Store) -- app de calendario que sí lee ese
  `CalendarProvider` y muestra/edita los eventos de verdad. DAVx5 por sí
  solo no muestra los eventos, solo sincroniza -- hace falta una app de
  calendario aparte. **Proton Calendar no sirve para esto**: es un sistema
  cerrado con su propio almacenamiento, no lee del `CalendarProvider` de
  Android, así que aunque DAVx5 sincronice bien, Proton Calendar se queda
  sin ver nada.

### Configuración
1. En DAVx5: agregar cuenta → "Login with URL and username".
   - URL: `http://<IP-de-tu-PC>:5232/` (IP de la PC en la LAN).
   - Usuario: `waybar` -- Radicale corre con `auth type = none` (no valida
     contraseña), pero igual exige mandar *algún* usuario fijo para
     resolver `current-user-principal` (ver `CLAUDE.md`, sección
     "Calendario"). Contraseña: cualquier texto, se ignora.
2. Activar el switch de sincronización de la colección **"Notas"** dentro
   de la cuenta (que aparezca listada no alcanza, hay que prenderla) y
   forzar una sincronización manual la primera vez.
3. Abrir Etar, aceptar el permiso de acceso a los calendarios del
   dispositivo (así lee lo que sincronizó DAVx5) y confirmar que "Notas"
   esté marcado como visible en su lista de calendarios.

### Diagnóstico de red (celular no encontraba el servidor)
El síntoma inicial fue que DAVx5 no podía llegar a `<IP-de-tu-PC>:5232`
para nada. Se descartó paso a paso:
- **Confirmar misma subred**: Ajustes → Wi-Fi → detalles de la red →
  verificar que la IP del celular empiece igual que la de la PC
  (`192.168.X.x`) -- si no, es una red/VLAN distinta aunque el nombre de
  WiFi sea el mismo.
- **Probar con el navegador del celular** a `http://<IP-de-tu-PC>:5232/`
  (sin DAVx5 de por medio) -- si ni el navegador carga nada, el problema
  es de red/firewall, no de configuración de DAVx5.
- **Descartar VPN**: revisar Ajustes → Red e Internet → VPN -- una VPN con
  "bloquear conexiones sin VPN" activa corta el tráfico a IPs locales
  aunque el internet normal funcione bien.
- La causa real terminó siendo del lado de la PC (dos firewalls activos a
  la vez, `nftables` + `ufw`, ver `CLAUDE.md`) -- no hizo falta cambiar
  nada más en el celular una vez arreglado eso.

## No instalado, pero relevante

Nada de firewall (`ufw`, `nftables`) se instaló en esta tarea -- ya
estaban en el sistema de antes. Solo se les agregaron reglas nuevas para
el puerto 5232 (ver `CLAUDE.md`, sección "Calendario", incluye el gotcha
de tener nftables Y ufw activos a la vez).

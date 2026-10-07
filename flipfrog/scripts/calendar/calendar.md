# Calendario en tu celular (Android)

Las notas del calendario (clic en el reloj de la barra) viven en un servidor
CalDAV local, Radicale, que corre en tu PC. Con dos apps puedes verlas y
editarlas también en tu Android.

## La forma fácil: el asistente

Abre el calendario (clic en el reloj) y toca **«Sincronizar con el celular»**
(o busca **«Calendario en el celular»** con `SUPER + Espacio`). El asistente
tiene 4 pasos:

1. **Instala las apps.** Escanea los códigos QR con la cámara del celular:
   - **DAVx⁵**: sincroniza las notas. De pago en Google Play, gratis en F-Droid.
   - **Etar**: el calendario donde las ves. Gratis.
2. **Prende «Conexión desde el celular».** Hace los cambios de la PC por ti:
   - Radicale pasa de escuchar solo en esta PC (`127.0.0.1`) a escuchar en
     tu red (`0.0.0.0`), en `~/.config/radicale/config`, y se reinicia.
   - Si el firewall (UFW) está activo, agrega una regla que deja entrar al
     puerto 5232 **solo desde tu red de casa** (por ejemplo `192.168.1.0/24`).
     Para eso pide tu contraseña.
   - Apagarlo deshace las dos cosas.
3. **En DAVx⁵** toca **+** → **«Iniciar sesión con URL y nombre de
   usuario»** y escribe lo que muestra el asistente:
   - **URL**: `http://<IP-de-tu-PC>:5232/` (el asistente muestra la tuya y la
     puedes copiar).
   - **Usuario**: `waybar`. Tiene que ser exactamente ese; con otro, DAVx⁵ no
     encuentra el calendario.
   - **Contraseña**: cualquier texto. Radicale no la revisa.
4. **Activa «Notas».** En DAVx⁵, dentro de la cuenta nueva, prende el switch
   del calendario **Notas** y toca sincronizar. En Etar: menú → Calendarios a
   mostrar → marca **Notas**.

El celular y la PC tienen que estar en el **mismo Wi-Fi**.

## Si el celular no se conecta

- **Prueba la conexión sin DAVx⁵**: escanea el QR del paso 3 del asistente
  (abre la URL en el navegador del celular). Si no carga nada, el problema es
  de red o firewall, no de DAVx⁵.
- **Misma red**: en el celular, Ajustes → Wi-Fi → detalles de la red, la IP
  tiene que empezar igual que la de la PC (por ejemplo `192.168.1.x`). Si no,
  es otra red aunque el nombre del Wi-Fi sea el mismo (pasa con redes de
  invitados).
- **VPN en el celular**: una VPN con «bloquear conexiones sin VPN» corta el
  tráfico a la red local aunque internet funcione.
- **Otro firewall en la PC**: el asistente solo maneja UFW. Si además usas
  `nftables` directo (`/etc/nftables.conf`), ahí también hay que permitir el
  puerto 5232 desde tu red.
- **La IP de tu PC cambió** (el router asigna otra): vuelve a abrir el
  asistente, copia la URL nueva y edítala en la cuenta de DAVx⁵.

## Paquetes que usa (los instala `install.sh`)

| Paquete | Para qué |
|---|---|
| `radicale` | El servidor CalDAV: guarda las notas y las expone por HTTP en el puerto 5232. |
| `python-caldav` | Librería con la que `calendar_popup.py` habla con Radicale. |
| `python-icalendar` | Formato de los eventos (`VEVENT`), dependencia de `python-caldav`. |
| `python-httpx` | **No se usa directamente**: `python-caldav` 3.2.1 importa `caldav.async_davclient` en cada llamada y truena con `ImportError` si no hay ninguna librería HTTP async, aunque el cliente sea síncrono. |
| `qrencode` | Códigos QR del asistente (ya viene con CachyOS). |

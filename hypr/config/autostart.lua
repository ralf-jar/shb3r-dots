-- if you dont use UWSM add your auto start programs here, otherwise use XDG autostart https://wiki.archlinux.org/title/XDG_Autostart

-- Tags "-- name: <Nombre>" arriba de un hl.exec_cmd(...) lo hacen
-- aparecer en el configurador de Autostart del dashboard (pestaña
-- "Procesos" -> botón "Servicios" -> pestaña "Autostart",
-- flipfrog/scripts/services/autostart_state.py) -- ese script prende/
-- apaga la entrada comentando/descomentando la línea tageada, atómico,
-- sin pkexec (es un archivo de ~/.config). Las líneas de infraestructura
-- de sesión (dbus-update-activation-environment, systemctl --user
-- import-environment/start hyprland-session.target) quedan SIN tag a
-- propósito -- no son "apps", apagarlas rompería la sesión gráfica.
--
-- El cambio solo aplica en el PRÓXIMO login: hl.on("hyprland.start")
-- corre una sola vez al arrancar la sesión, ni un `hyprctl reload` ni
-- nada disparado en caliente vuelve a ejecutar este bloque.

hl.on("hyprland.start", function ()
    local home = os.getenv("HOME")
    hl.exec_cmd("dbus-update-activation-environment --systemd --all")

    -- Basicos
    -- name: Barra
    hl.exec_cmd("python3 " .. home .. "/.config/flipfrog/scripts/bar/bar.py")
    -- name: Restaurar wallpaper
    hl.exec_cmd("python3 " .. home .. "/.config/flipfrog/themer/wallpaper.py restore")
    -- Tareas periódicas como timers transitorios de systemd --user: entre
    -- corridas no queda ningún proceso vivo (systemctl --user list-timers).
    -- name: Rotación de temas
    hl.exec_cmd("python3 " .. home .. "/.config/flipfrog/scripts/theme/theme-rotator.py")
    -- name: Sincronizar íconos de Steam
    hl.exec_cmd("systemd-run --user --quiet --unit=ff-steam-sync --on-active=1min --on-unit-active=3h python3 " .. home .. "/.config/flipfrog/scripts/steam/steam-icons-sync.py")
    -- name: Notificador de actualizaciones
    hl.exec_cmd("systemd-run --user --quiet --unit=ff-updates-chk --on-active=30s --on-unit-active=2h python3 " .. home .. "/.config/flipfrog/scripts/updates/updates-checker.py")
    -- Alarmas, ruteo del ecualizador y registro del firewall: un solo
    -- proceso (ff-core), un hilo por servicio. Ver core/ff_core.py.
    -- name: Servicios de fondo (alarmas, ecualizador, firewall)
    hl.exec_cmd("python3 " .. home .. "/.config/flipfrog/scripts/core/ff_core.py")
    -- name: Restaurar No molestar
    hl.exec_cmd("python3 " .. home .. "/.config/flipfrog/scripts/notifications/dnd_state.py restore")
    -- name: WI-C100 forzar A2DP
    -- hl.exec_cmd("python3 " .. home .. "/.config/flipfrog/scripts/bluetooth/wic100_force_a2dp.py")
    -- name: Calendario (Radicale)
    hl.exec_cmd("radicale --config " .. home .. "/.config/radicale/config")
    -- name: Seguridad UI
    hl.exec_cmd("systemctl --user start hyprpolkitagent")
    -- En Wayland lo copiado se pierde al cerrar la app de donde salió;
    -- wl-clip-persist se queda con una copia.
    -- name: Portapapeles persistente
    hl.exec_cmd("wl-clip-persist --clipboard regular")
    -- Solo abre si flipfrog/scripts/keybinds/welcome.json lo pide (lo
    -- crea install.sh en una instalación nueva; el switch del popup lo apaga).
    -- name: Atajos al iniciar sesión
    hl.exec_cmd("sleep 3 && python3 " .. home .. "/.config/flipfrog/scripts/keybinds/keybinds_popup.py --welcome")

    -- Extras
    -- Las tres arrancan minimizadas a la bandeja: Vesktop con
    -- --start-minimized, Steam con -silent, ZapZap con su ajuste
    -- system/start_background (no tiene opción de línea de comandos;
    -- --setSettings lo guarda y sigue abriendo la app).
    -- name: Vesktop (Discord)
    hl.exec_cmd("sleep 2 && vesktop --enable-features=UseOzonePlatform --ozone-platform=wayland --start-minimized")
    -- name: Steam
    hl.exec_cmd("steam -silent")
    -- name: ZapZap (WhatsApp)
    hl.exec_cmd("zapzap --setSettings system/start_background true")
    -- name: OpenRGB (perfil)
    hl.exec_cmd("openrgb --startminimized --profile white")
    -- name: OpenRGB (tira LED)
    hl.exec_cmd("openrgb --device 6 --zone 0 --size 40 --mode direct --color 000000")
    -- name: GNOME Keyring
    hl.exec_cmd("gnome-keyring-daemon --start --components=secrets")
    -- name: Proton VPN (autoconectar)
    -- hl.exec_cmd("protonvpn connect")

    -- Sonido automatico: dispositivos y retraso configurables desde la
    -- pestaña "Bluetooth" del dashboard (flipfrog/scripts/bluetooth/), ya
    -- no un MAC fijo acá -- ver bluetooth_autoconnect.py
    -- name: Autoconexión Bluetooth
    hl.exec_cmd("python3 " .. home .. "/.config/flipfrog/scripts/bluetooth/bluetooth_autoconnect.py")

    -- Graficos para Wayland
    hl.exec_cmd("systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP")
    hl.exec_cmd("dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP")
    hl.exec_cmd("systemctl --user start hyprland-session.target")

end)

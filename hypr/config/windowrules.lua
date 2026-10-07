
-- Picture-in-Picture
hl.window_rule({
    match             = { title = "^([Pp]icture[-\\s]?[Ii]n[-\\s]?[Pp]icture)(.*)$" },
    float             = true,
    keep_aspect_ratio = true,
    move              = "73% 72%",
    size              = "25% 25%",
    pin               = true,
})

-- Gaming
local gamingApps = "^(steam_app.*|gamescope)$"
local gamingWorkspace = "name:gaming"

hl.window_rule({ match = { content = "game" }, workspace = gamingWorkspace })
hl.window_rule({ match = { class = gamingApps }, workspace = gamingWorkspace })
-- Juegos lanzados desde el launcher (flipfrog launcher.py): armado por
-- `hyprctl eval 'flipfrog_arm_game_launch()'`, manda a gaming la próxima
-- ventana nueva que no sea del cliente de Steam y se desarma solo. Por
-- evento y no con una regla habilitable: una regla armada también jala
-- ventanas ya abiertas cuando Hyprland reevalúa sus reglas (cambio de
-- título), confirmado con la terminal.
local gameArmId = 0
local gameArmed = false

function flipfrog_arm_game_launch(timeout_ms)
    gameArmId = gameArmId + 1
    local armId = gameArmId
    gameArmed = true
    hl.timer(function()
        if armId == gameArmId then
            gameArmed = false
        end
    end, { timeout = timeout_ms or 120000, type = "oneshot" })
end

hl.on("window.open", function(win)
    if not gameArmed or not win or (win.class or ""):match("^steam") then
        return
    end
    gameArmed = false
    hl.dispatch(hl.dsp.window.move({ workspace = gamingWorkspace, window = "address:" .. win.address }))
    hl.dispatch(hl.dsp.focus({ workspace = gamingWorkspace }))
end)

hl.window_rule({ match = { class = "^(steam)$", title = "^(Friends List)$" }, float = true })
-- Propiedades de un juego: su título es solo el nombre del juego, así que se
-- flota todo lo de Steam salvo la ventana principal, Big Picture y popups sin título.
hl.window_rule({
    match = {
        class = "^(steam)$",
        title = "negative:^(Steam|Steam Big Picture Mode|)$",
    },
    float  = true,
    center = true,
})
hl.window_rule({
    match = {
        class = "^(steam)$",
        title = "^(Launching\\.{3})$"
    },
    float     = true,
    center    = true,
    workspace = gamingWorkspace,
    confine_pointer = true
})
hl.window_rule({
    match = {
        class         = gamingApps,
        title         = "^(.+)$",
        initial_title = "negative:^(.*\\\\home\\\\.*)$",
    },
    size             = "monitor_w monitor_h",
    fullscreen_state = 2,
    content          = "game",
    confine_pointer = true
})
hl.window_rule({
    match = {
        class         = "^(steam_app.*)$",
        initial_title = "^$",
    },
    float            = true,
    center           = true,
    fullscreen       = true,
    fullscreen_state = 0,
})

-- Apps
local primaryWorkspace = 1

hl.window_rule({ match = { class = "^(.*\\.exe)$", float = true }, primaryWorkspace, center = true, fullscreen_state = 0 })
--hl.window_rule({ match = { class = "^(vesktop|discord)$" }, primaryWorkspace })
hl.window_rule({ match = { class = "^(.*[Cc]alculator.*)$" }, float = true, size = "380 616" })
hl.window_rule({ match = { class = "^(org.kde.keditfiletype)$" }, float = true })
hl.window_rule({ match = { class = "^(org.kde.ark)$" }, size = "(monitor_w*0.40) (monitor_h*0.40)" })
hl.window_rule({
    match = {
        class = "^(org.kde.dolphin)$",
        title = "Galculator|negative:^(Moviendo.*|Create New.*|Extract.*|Compress.*|Copying.*|Progress.*|Configure.*|Properties.*|Choose\\sApplication.*)$",
    },
    float = true,
    move = {
        "max(0, min(cursor_x - 650, monitor_w - 1320))",
        "max(0, min(cursor_y - 50, monitor_h - 820))"
    },
    size = "1300 800",
})

-- Float Utility Windows
local floatApps = {
    { class = "^(qt[56]ct)$" },
    { class = "^(org.pulseaudio.pavucontrol|blueman-manager|nm-applet|nm-connection-editor)$" },
    { title = "^(Winetricks.*|Protontricks.*)$" },
}
for _, m in ipairs(floatApps) do hl.window_rule({ match = m, float = true }) end

hl.window_rule({ 
    match = { float = true }, 
    move = "50% 50%" 
})

-- Float Common Modals
local modalMatches = {
    { title = "^([Gg]alculator|Moviendo|Open|Authentication Required|Add Folder to Workspace|Choose Files|Save As|Confirm to replace files|File Operation Progress)$" },
    { initial_title = "^(Open File)$" },
    { class = "^([Xx]dg-desktop-portal-gtk)$" },
    { title = "^(File Upload|Choose wallpaper|Library)(.*)$" },
    { class = "^(.*dialog.*)$" },
    { title = "^(.*dialog.*)$" },
    { title = "^Renombrar.*$" },
    { class = "^(hyprland-share-picker)$"},
}
for _, m in ipairs(modalMatches) 
do hl.window_rule({ match = m, float = true }) 
end

-- flipfrog/scripts/theme/theme_gallery.py (SUPER+SHIFT+T) -- ventana
-- normal en su workspace "🎨": sin blur aunque el tema lo tenga prendido,
-- sin borde de color ni sombra (decorate = false).
hl.window_rule({
    match    = { class = "^(flipfrog-theme-gallery)$" },
    -- Abre directo en su workspace (y cambia a él) -- hacerlo desde el
    -- script con un dispatch antes de crear la ventana era una carrera: a
    -- veces la ventana mapeaba en el workspace de antes.
    workspace = "name:🎨",
    no_blur  = true,
    decorate = false,
    no_shadow = true,
    border_size = 0,
})

-- flipfrog/scripts/windows/window_collage.py (SUPER+CTRL+TAB) -- mismo
-- criterio que la galería de temas, en su workspace "🪟": el fondo de la
-- ventana es transparente, sin blur se ve el wallpaper tal cual.
hl.window_rule({
    match    = { class = "^(flipfrog-window-collage)$" },
    workspace = "name:🪟",
    no_blur  = true,
    decorate = false,
    no_shadow = true,
    border_size = 0,
})

-- Ignore maximize requests from all apps. You'll probably like this.
local suppressMaximizeRule = hl.window_rule({
    name  = "suppress-maximize-events",
    match = { class = ".*" },

    suppress_event = "maximize",
})
-- suppressMaximizeRule:set_enabled(false)

-- Fix some dragging issues with XWayland
hl.window_rule({
    name  = "fix-xwayland-drags",
    match = {
        class      = "^$",
        title      = "^$",
        xwayland   = true,
        float      = true,
        fullscreen = false,
        pin        = false,
    },

    no_focus = true,
})

hl.window_rule({ 
    match = { class = WINDOWSTHEME }, 
    opacity = WINDOWSOPASITY
})

-- decoration.blur (enabled/size/passes/xray/vibrancy/noise) vive TODO
-- junto en config/decorations.lua ahora -- antes esta llamada de acá
-- solo repetía enabled/noise en un hl.config APARTE, sin size/passes: dos
-- llamadas a hl.config para la misma tabla anidada, en dos archivos,
-- cargados en orden (decorations.lua primero), son candidato real a que
-- ESTA, al no repetir size/passes/xray/vibrancy, los pise de vuelta a su
-- default -- sin confirmar en vivo todavía (el usuario reportó ver solo
-- el granulado, no el desenfoque real, después de este cambio; unificar
-- en una sola tabla saca la ambigüedad de encima sin importar cuál haya
-- sido la causa exacta). Ver decorations.lua para la tabla completa.

hl.layer_rule({
    match = { namespace = LAYOUTSTHEME },
    no_anim = true,
    blur = true
})

-- Dashboard (SUPER+plus, flipfrog/scripts/dashboard.py -- namespace real vía
-- GtkLayerShell.set_namespace, ver build_layer_window en waybar_lib.py):
-- blur real detrás del panel, pedido explícito del usuario (/design
-- referencia del launcher Android, "el propio background gana bastante
-- blur"). La intensidad real (size/passes) es GLOBAL, ver decorations.lua
-- -- este layer_rule solo marca que el dashboard participa.
--
-- xray=false (no true) -- pedido explícito del usuario: el blur tiene
-- que tomar lo que esté REALMENTE atrás del panel (una consola, otra
-- ventana), no saltar directo al escritorio/wallpaper. xray=true hacía
-- justo eso -- "ver a través" de cualquier ventana intermedia, ignorando
-- que hubiera una consola de por medio. Sin este campo, layer_rule ya
-- vale false por default -- se deja explícito para que quede documentado
-- el porqué, no por necesidad técnica.
--
-- ignore_alpha: la superficie layer-shell del dashboard es TODA la
-- pantalla y transparente (build_layer_window en waybar_lib.py -- el
-- "background" EventBox que cierra al click afuera cubre el monitor
-- entero; el panel visible es un hijo anclado adentro, mucho más chico).
-- Sin esto, Hyprland aplica blur a la GEOMETRÍA completa de la
-- superficie, no solo donde hay píxeles opacos -- pedido explícito del
-- usuario tras ver el blur cubriendo TODO el escritorio en vez de solo
-- detrás del panel. Con el umbral, blur ignora los píxeles con alpha
-- por debajo de eso -- el fondo transparente (medido en 0.0 exacto)
-- queda afuera, el panel real (#container en @mybackground directo, ver
-- dashboard.css) queda adentro. 0.02, no 0.1 -- confirmado en vivo
-- (2026-09-01) que 0.1 excluía del blur cualquier panel con alpha
-- configurado por debajo de eso (el editor de temas permite bajar
-- @mybackground hasta ahí sin restricción), dejando el panel sin blur
-- Y con un tinte casi imperceptible -- se veía completamente
-- transparente. 0.02 deja pasar cualquier alpha razonable configurado
-- por el usuario sin dejar de excluir el fondo real.
hl.layer_rule({
    match = { namespace = "dashboard" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- Mismo blur que el dashboard (ver bloque de arriba) para las dos
-- ventanas standalone rediseñadas con la misma línea minimalista
-- (services/services_popup.py -- "Servicios", abierta desde la pestaña
-- "Procesos"; theme/theme-editor.py -- editor de temas): antes de esto
-- las dos solo pintaban su #container/#services-container con
-- background-color a un alpha fijo -- un tinte plano sobre lo que
-- estuviera atrás, sin desenfoque real (mismo problema que tenía el
-- dashboard antes de este mecanismo). ignore_alpha=0.02 aplica igual acá
-- -- build_layer_window() arma la MISMA superficie pantalla-completa
-- transparente para cualquier popup que la use, no solo el dashboard
-- (ver waybar_lib.py) -- y xray=false por el mismo motivo (blurear lo
-- que esté REALMENTE atrás, no saltar al wallpaper).
hl.layer_rule({
    match = { namespace = "services-popup" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

hl.layer_rule({
    match = { namespace = "theme-editor" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- Resto de ventanas abiertas desde adentro del dashboard (subprocess.Popen
-- de un botón en una pestaña, ver theme_module.py/network_tab.py/
-- firewall_popup.py) -- mismo mecanismo, namespace real de cada una vía
-- build_layer_window(). firewall-log es un caso encadenado (se abre desde
-- un botón DENTRO de firewall-popup, no directo del dashboard) pero pasa
-- por el mismo build_layer_window() y merece el mismo tratamiento.
hl.layer_rule({
    match = { namespace = "google-fonts" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

hl.layer_rule({
    match = { namespace = "firewall-popup" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

hl.layer_rule({
    match = { namespace = "vpn-config" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

hl.layer_rule({
    match = { namespace = "firewall-log" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- calendar/calendar_popup.py (click en el reloj) -- faltaba en esta
-- lista, nunca tuvo blur real de Hyprland pese a construirse con el
-- mismo build_layer_window() del resto (namespace "calendar").
hl.layer_rule({
    match = { namespace = "calendar" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- calendar/phone_sync_popup.py (botón del calendario / lanzador)
hl.layer_rule({
    match = { namespace = "calendar-sync" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- La barra (flipfrog/scripts/bar/bar.py), pedido explícito del usuario.
-- Mismo motivo de ignore_alpha que el resto de arriba: en islas la barra
-- es UNA superficie transparente de ancho completo con píldoras pintadas
-- encima -- sin esto blurearía la tira completa, no solo detrás de cada
-- píldora. blur_popups: sin esto los tooltips y menús del tray (xdg_popup
-- hijos de esta superficie) no heredan el blur. El encendido real es
-- decoration:blur:enabled (toggles/blur_toggle.py).
hl.layer_rule({
    match = { namespace = "ff-bar" },
    blur = true,
    blur_popups = true,
    xray = false,
    ignore_alpha = 0.02
})

-- keybinds_popup.py (SUPER+SHIFT+K) -- primero del TO DO de ventanas que
-- todavía no tenían la línea de diseño (Bandcamp/
-- keybinds), mismo mecanismo que el resto de arriba.
hl.layer_rule({
    match = { namespace = "keybinds-popup" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})


-- bandcamp/bandcamp_popup.py (SUPER+M) -- mismo mecanismo,
-- build_layer_window() con namespace "bandcamp-radio".
hl.layer_rule({
    match = { namespace = "bandcamp-radio" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- power/power_menu.py (ícono de power de la barra) -- una superficie
-- pantalla-completa por monitor, toda en @mybackground (no
-- transparente-con-hijo-opaco como el resto), así que el blur cubre la
-- pantalla entera; ignore_alpha igual por consistencia con la lista.
hl.layer_rule({
    match = { namespace = "power-menu" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- launcher.py (SUPER+SPACE, reemplazo de Wofi) -- mismo mecanismo que
-- el resto de arriba, build_layer_window() con namespace "app-launcher".
hl.layer_rule({
    match = { namespace = "app-launcher" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- alarms/alarm_popup.py (click derecho en el reloj) -- mismo mecanismo
-- que el resto de arriba, build_layer_window() con namespace "alarms".
hl.layer_rule({
    match = { namespace = "alarms" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- downloads/download_popup.py (SUPER+D) -- mismo mecanismo, namespace
-- "download-popup" de build_layer_window().
hl.layer_rule({
    match = { namespace = "download-popup" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- audiobook/audiobook_popup.py (SUPER+A) -- mismo mecanismo, namespace
-- "audiobook-popup" de build_layer_window().
hl.layer_rule({
    match = { namespace = "audiobook-popup" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- notifications/notification_history.py (SUPER+N) -- mismo mecanismo,
-- namespace "notification-history" de build_layer_window().
hl.layer_rule({
    match = { namespace = "notification-history" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- disk/disk_usage_popup.py (click en la línea de disco de "Procesos") --
-- mismo mecanismo, namespace "disk-usage" de build_layer_window().
hl.layer_rule({
    match = { namespace = "disk-usage" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- audio/mixer.py, audio/volume-simple.py, audio/sink-selector.py --
-- mismo mecanismo, se habían quedado afuera de esta lista pese a usar
-- build_layer_window() como el resto.
hl.layer_rule({
    match = { namespace = "volume-mixer" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

hl.layer_rule({
    match = { namespace = "volume-simple" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

hl.layer_rule({
    match = { namespace = "sink-selector" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})

-- brightness/brightness.py -- mismo caso, faltaba en esta lista.
hl.layer_rule({
    match = { namespace = "brightness" },
    blur = true,
    xray = false,
    ignore_alpha = 0.02
})
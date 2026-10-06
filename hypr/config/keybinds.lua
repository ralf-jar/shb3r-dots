local mainMod        = "SUPER"
local launcherAmbxst = "ambxst run launcher"
local appLauncher    = "python3 ~/.config/flipfrog/scripts/launcher/launcher.py"
local explorer = "kitty -- yazi"
local musicPopup     = "python3 ~/.config/flipfrog/scripts/bandcamp/bandcamp_popup.py"
local musicCtl       = "python3 ~/.config/flipfrog/scripts/bandcamp/bandcamp_ctl.py "

-- SUPER+M abre el reproductor al SOLTAR la M, y solo si no se usó una
-- flecha mientras estaba presionada (SUPER+M+flecha controla la música).
local musicHeld = false
local musicUsed = false

local function musicPress()
    musicHeld = true
    musicUsed = false
end

local function musicRelease()
    if not musicHeld then
        return
    end
    musicHeld = false
    if not musicUsed then
        hl.exec_cmd(musicPopup)
    end
end

local function arrow(direction, musicArgs)
    return function()
        if musicHeld then
            musicUsed = true
            hl.exec_cmd(musicCtl .. musicArgs)
        else
            hl.dispatch(hl.dsp.focus({ direction = direction }))
        end
    end
end

-- section: Aplicaciones
hl.bind(mainMod .. " + plus",  hl.dsp.exec_cmd("python3 ~/.config/flipfrog/scripts/dashboard.py"))
-- description: Abrir dashboard del sistema
hl.bind(mainMod .. " + T",     hl.dsp.exec_cmd("kitty"))
-- description: Abrir terminal
hl.bind(mainMod .. " + E",     hl.dsp.exec_cmd("thunar"))
-- description: Abrir explorador de archivos
hl.bind(mainMod .. " + SPACE", hl.dsp.exec_cmd(appLauncher))
-- description: Abrir menú de aplicaciones
hl.bind(mainMod .. " + B",     hl.dsp.exec_cmd("brave-origin"))
-- description: Abrir navegador (Brave)
hl.bind(mainMod .. " + M", musicPress)
-- description: Abrir reproductor de musica (mantener M + flechas: canción/volumen)
hl.bind("M", musicRelease, { release = true, ignore_mods = true, non_consuming = true })
hl.bind(mainMod .. " + P", hl.dsp.exec_cmd("python3 ~/.config/flipfrog/scripts/theme/theme_gallery.py"))
-- description: Abrir galería de temas
hl.bind(mainMod .. " + D", hl.dsp.exec_cmd("python3 ~/.config/flipfrog/scripts/downloads/download_popup.py"))
-- description: Abrir gestor de descargas (MediaFire y YouTube)
hl.bind(mainMod .. " + A", hl.dsp.exec_cmd("python3 ~/.config/flipfrog/scripts/audiobook/audiobook_popup.py"))
-- description: Abrir audiolibros (EPUB a MP3)
hl.bind(mainMod .. " + N", hl.dsp.exec_cmd("python3 ~/.config/flipfrog/scripts/notifications/notification_history.py"))
-- description: Historial de notificaciones
hl.bind(mainMod .. " + S", hl.dsp.exec_cmd('bash ~/.config/hypr/scripts/screenshot.sh'))
-- description: Captura por región
hl.bind(mainMod .. " + CTRL + S", hl.dsp.exec_cmd('bash ~/.config/hypr/scripts/screenshot-full.sh'))
-- description: Captura de pantalla completa
hl.bind(mainMod .. " + W", hl.dsp.exec_cmd("python3 ~/.config/flipfrog/scripts/bar/bar_settings.py toggle visible"))
-- description: Muestra/oculta la barra
hl.bind(mainMod .. " + H", hl.dsp.exec_cmd("python3 ~/.config/flipfrog/scripts/keybinds/keybinds_popup.py"))
-- description: Muestra este popup con todos los atajos

-- section: Ventanas
hl.bind(mainMod .. " + CTRL + K", hl.dsp.exec_cmd("hyprctl kill"))
-- description: Modo matar ventana (clic para cerrar)
hl.bind(mainMod .. " + CTRL + F", hl.dsp.window.fullscreen())
-- description: Pantalla completa
hl.bind(mainMod .. " + ALT + F", hl.dsp.window.float({ action = "toggle" }))
-- description: Alternar ventana flotante
hl.bind(mainMod .. " + Q", hl.dsp.window.close())
-- description: Cerrar ventana
hl.bind(mainMod .. " + mouse:273", hl.dsp.window.drag(), { mouse = true })
-- description: Mover ventana (arrastrar)
hl.bind(mainMod .. " + left", arrow("left", "prev"))
-- description: Mover foco a la izquierda (con M: canción anterior)
hl.bind(mainMod .. " + right", arrow("right", "skip"))
-- description: Mover foco a la derecha (con M: siguiente canción)
hl.bind(mainMod .. " + up", arrow("up", "volume 5"), { repeating = true })
-- description: Mover foco hacia arriba (con M: subir volumen del reproductor)
hl.bind(mainMod .. " + down", arrow("down", "volume -5"), { repeating = true })
-- description: Mover foco hacia abajo (con M: bajar volumen del reproductor)
hl.bind(mainMod .. " + CTRL + up", hl.dsp.exec_cmd("python3 ~/.config/flipfrog/scripts/audio/focused_volume.py 5"), { repeating = true })
-- description: Subir volumen solo de la ventana con foco
hl.bind(mainMod .. " + CTRL + down", hl.dsp.exec_cmd("python3 ~/.config/flipfrog/scripts/audio/focused_volume.py -5"), { repeating = true })
-- description: Bajar volumen solo de la ventana con foco

-- section: Workspaces
hl.bind(mainMod .. " + SHIFT + G", hl.dsp.window.move({ workspace = "name:gaming" }))
-- description: Manda la ventana activa al modo "gaming"
hl.bind(mainMod .. " + SHIFT + C", hl.dsp.window.move({ workspace = "name:🎬" }))
-- description: Manda la ventana activa al modo "cine"
hl.bind(mainMod .. " + G", hl.dsp.focus({ workspace = "name:gaming" }))
-- description: Ir al workspace gaming
for i = 1, 9 do
    hl.bind(mainMod .. " + " .. i,            hl.dsp.focus({ workspace = i }))
    -- description: Ir al workspace 1-9
    hl.bind(mainMod .. " + SHIFT + " .. i,    hl.dsp.window.move({ workspace = i }))
    -- description: Mover ventana al workspace 1-9
end

hl.bind(mainMod .. " + 0",         hl.dsp.focus({ workspace = 10 }))
-- description: Ir al workspace 10
hl.bind(mainMod .. " + SHIFT + 0", hl.dsp.window.move({ workspace = 10 }))
-- description: Mover ventana al workspace 10

hl.bind(mainMod .. " + mouse_down", hl.dsp.focus({ workspace = "e+1" }))
-- description: Ir al workspace anterior
hl.bind(mainMod .. " + mouse_up",   hl.dsp.focus({ workspace = "e-1" }))
-- description: Ir al workspace siguiente
hl.bind(mainMod .. " + Tab",        hl.dsp.focus({ workspace = "e+1" }))
-- description: Ir al workspace siguiente
hl.bind(mainMod .. " + CTRL + Tab", hl.dsp.exec_cmd("python3 ~/.config/flipfrog/scripts/windows/window_collage.py"))
-- description: Collage de todas las ventanas abiertas

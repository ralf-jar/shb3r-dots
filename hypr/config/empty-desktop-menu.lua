-- Clic derecho sobre el escritorio vacío (el monitor bajo el cursor no
-- tiene ninguna ventana en su workspace activo) abre el launcher propio
-- -- mismo comando que SUPER+SPACE (keybinds.lua, appLauncher; duplicado
-- acá a propósito, es `local` en ese archivo). Ver CLAUDE.md, TODO.
--
-- Historial de intentos rotos / bugs confirmados a mano:
-- 1) Submap ("emptydesktop") para scopear mouse:273 -- suprimía TODOS
--    los demás binds mientras estaba activo (SUPER+1, SUPER+SPACE...),
--    no solo el clic derecho.
-- 2) hl.on("window.open"/"workspace.active"/"monitor.focused"/...) +
--    hl.get_active_workspace() (workspace con FOCO de teclado) -- mover
--    el mouse a un monitor vacío por hover (sin usar un keybind de
--    workspace) no dispara NINGUNO de esos eventos si no hay ventana que
--    tome el foco ahí, así que el estado quedaba pegado al último
--    monitor con una ventana real enfocada. Confirmado: clic derecho en
--    el monitor vacío no abría nada, y volviendo al monitor CON ventana
--    sí abría (estado invertido).
--
-- 3) Habilitar el bind con solo "workspace vacío" (sin más chequeos) --
--    La barra es una superficie layer-shell (`layer` level 2, "top"), NO
--    cuenta como ventana del workspace -- con el escritorio vacío, clic
--    derecho sobre un ícono del tray o de sonido/media disparaba Wofi en
--    vez de la acción propia del ícono (menú del tray, mixer, etc.),
--    porque nada distinguía "cursor sobre el fondo vacío" de "cursor
--    sobre la barra". Confirmado a mano (ver CLAUDE.md, TODO).
--
-- Solución real: nada de eventos -- un timer (hl.timer) que cada 150ms
-- pregunta qué monitor está bajo el CURSOR (hl.get_monitor_at_cursor(),
-- no el foco de teclado), si su workspace activo está vacío
-- (HL.Monitor.active_workspace.is_empty) Y si el cursor NO está encima
-- de ninguna superficie layer-shell de nivel "top" o superior
-- (hl.get_layers(), campo `layer`: 0/1 = background/bottom -- fondo de
-- pantalla, awww/mpvpaper; 2/3 = top/overlay -- la barra, el propio
-- dashboard, popups) -- habilitando/deshabilitando un único hl.bind()
-- con Keybind:set_enabled() -- nunca se sale del contexto de binds por
-- defecto, así que el resto de los atajos sigue vivo siempre. 150ms es
-- imperceptible para un clic humano y evita depender de qué eventos de
-- Hyprland sí o no se disparan con hover puro entre monitores.
local appLauncher = "python3 ~/.config/flipfrog/scripts/launcher/launcher.py"

local launcher_bind = hl.bind("mouse:273", hl.dsp.exec_cmd(appLauncher), { mouse = true })
launcher_bind:set_enabled(false)

local active = false

local function cursor_workspace_is_empty()
    local mon = hl.get_monitor_at_cursor()
    if mon == nil or mon.active_workspace == nil then
        return false
    end
    return mon.active_workspace.is_empty
end

-- `layer` sigue la convención de wlr-layer-shell: 0 background, 1
-- bottom, 2 top, 3 overlay. La barra (bar/bar.py) usa "top"; el fondo
-- de pantalla (awww-daemon/mpvpaper) es "background" -- por eso el
-- corte va en >= 2, no en > 0.
local UI_LAYER_MIN = 2

local function cursor_over_ui_layer()
    local pos = hl.get_cursor_pos()
    if pos == nil then
        return false
    end
    for _, layer in ipairs(hl.get_layers()) do
        if layer.layer >= UI_LAYER_MIN
            and pos.x >= layer.x and pos.x < layer.x + layer.w
            and pos.y >= layer.y and pos.y < layer.y + layer.h then
            return true
        end
    end
    return false
end

local function should_be_active()
    if cursor_over_ui_layer() then
        return false
    end
    return cursor_workspace_is_empty()
end

local function sync()
    local now = should_be_active()
    if now == active then
        return
    end
    active = now
    launcher_bind:set_enabled(active)
end

hl.timer(sync, { timeout = 150, type = "repeat" })

sync()

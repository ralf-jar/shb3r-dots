-- Dispara un `hyprctl reload` cuando cambia qué monitor debería estar
-- apagado por el workspace nombrado "🎬" (modo cine, SUPER+SHIFT+C, ver
-- keybinds.lua), y activa/desactiva "No molestar" (dunst) al entrar/salir
-- de ese workspace -- mandando un último aviso justo antes de pausar,
-- para que quede claro que a partir de ahí no van a saltar más. La
-- decisión de qué monitor apagar vive en
-- monitors.lua -- ver el comentario ahí: hl.monitor() llamado en caliente
-- NO reactiva un monitor ya disabled=true, confirmado a mano con
-- `hyprctl eval`, así que la única vía confiable es una reaplicación
-- completa de config. Este archivo solo detecta el cambio y pide esa
-- reaplicación.
--
-- dunstctl sí se puede llamar en caliente sin problema (a diferencia de
-- los monitores): el estado de pausa vive en el proceso de dunst, no en
-- algo que hyprctl reload pise o necesite reaplicar.
local monitors = require("config.monitors")

-- workspace.created/removed llegan antes de que hl.get_workspaces()
-- refleje el cambio (carrera confirmada a mano) -- se difiere un tick
-- con un timer oneshot para que la consulta ya vea el estado post-evento.
--
-- last_host arranca en el valor YA calculado por monitors.lua en esta
-- misma pasada de carga (no en nil): si arrancara en nil, la primera
-- comparación detectaría un "cambio" falso (nil -> "DP-3") cada vez que
-- este archivo se re-ejecuta con "🎬" ya existente, disparando un reload
-- redundante que a su vez volvería a cargar este archivo -- loop.
local last_host = monitors.cine_host()

local function dnd_is_paused()
    local p = io.popen("dunstctl is-paused")
    if not p then
        return false
    end
    local out = p:read("*a")
    p:close()
    return out:match("true") ~= nil
end

local function dnd_set_paused(paused)
    hl.exec_cmd("dunstctl set-paused " .. (paused and "true" or "false"))
end

-- Estado de dunst justo antes de entrar a "🎬", para devolverlo tal cual
-- estaba al salir (si el usuario ya tenía "No molestar" activado a mano
-- desde el dashboard, salir de cine no se lo debe desactivar). nil =
-- no estamos overrideando ahora mismo. No sobrevive a un reload disparado
-- por otra cosa mientras se está en "🎬" (p.ej. cambio de tema) -- caso
-- raro, se acepta la limitación en vez de sumarle un archivo de estado
-- persistido por esto.
local dnd_restore = nil

local function sync()
    local host = monitors.cine_host()
    if host == last_host then
        return
    end

    if not last_host and host then
        dnd_restore = dnd_is_paused()
        -- se manda ANTES de pausar dunst, o se la traga la propia pausa.
        hl.exec_cmd('notify-send -a "flipfrog" -i preferences-desktop-notifications "Modo cine activado" "No se recibirán notificaciones."')
        dnd_set_paused(true)
    elseif last_host and not host then
        dnd_set_paused(dnd_restore or false)
        dnd_restore = nil
    end

    last_host = host
    hl.exec_cmd("hyprctl reload")
end

local function schedule_sync()
    hl.timer(sync, { timeout = 50, type = "oneshot" })
end

hl.on("workspace.created", schedule_sync)
hl.on("workspace.removed", schedule_sync)
hl.on("workspace.move_to_monitor", schedule_sync)

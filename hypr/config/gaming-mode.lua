-- Fondo fijo mientras exista el workspace "gaming" (hay un juego
-- abierto) o "🎬" (modo cine): video -> un frame como imagen, gif ->
-- pausado. Al cerrarse los dos, se reanuda el fondo guardado. Cambiar de
-- tema en medio sí está permitido: wallpaper.py pone el fondo nuevo ya
-- congelado (ver flipfrog/themer/wallpaper.py, freeze/thaw/set).
--
-- "gaming" es un workspace nombrado normal (id -1337, ver
-- windowrules.lua: `workspace = "name:gaming"`), NO un escritorio
-- especial/overlay de Hyprland pese al nombre.
--
-- No se usa el payload de workspace.removed (confirmado en vivo que el
-- HL.Workspace que llega ya está invalidado, "HL.Workspace(expired)"):
-- se relee la lista viva un tick después del evento (misma carrera que
-- cine-mode.lua). El estado real es FROZEN_MARKER (lo escribe/borra
-- wallpaper.py), no una variable de este archivo: cada `hyprctl reload`
-- re-ejecuta esto (el modo cine recarga justo al entrar) y una variable
-- en memoria perdería un congelado pendiente.
local WALLPAPER = os.getenv("HOME") .. "/.config/flipfrog/themer/wallpaper.py"
local FREEZE_WORKSPACES = { ["gaming"] = true, ["🎬"] = true }
local FROZEN_MARKER = "/tmp/flipfrog-wallpaper-frozen"

local function freeze_wanted()
    for _, ws in ipairs(hl.get_workspaces()) do
        if FREEZE_WORKSPACES[ws.name] then
            return true
        end
    end
    return false
end

local function frozen()
    local f = io.open(FROZEN_MARKER, "r")
    if f then
        f:close()
        return true
    end
    return false
end

-- Evita mandar dos veces la misma acción mientras wallpaper.py todavía
-- no escribe/borra el marcador.
local pending = nil

local function sync()
    local want = freeze_wanted()
    if want == frozen() or want == pending then
        return
    end
    pending = want
    hl.exec_cmd("python3 " .. WALLPAPER .. (want and " freeze" or " thaw"))
end

local function schedule_sync()
    hl.timer(sync, { timeout = 50, type = "oneshot" })
end

hl.on("workspace.created", schedule_sync)
hl.on("workspace.removed", schedule_sync)
schedule_sync()

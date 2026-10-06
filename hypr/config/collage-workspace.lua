-- Workspace "🪟" exclusivo del collage de ventanas
-- (flipfrog/scripts/windows/window_collage.py, SUPER+CTRL+TAB): cualquier
-- otra ventana que abra ahí (una app lanzada con el collage en pantalla) o
-- que se mueva ahí (SUPER+SHIFT+N, arrastre) se regresa al último
-- workspace que se mostró en ese monitor antes de "🪟", se enfoca, y el
-- collage se cierra -- si no, la app quedaría tapada por él.
--
-- Mismo criterio que cine-mode.lua: el estado se relee un tick después del
-- evento (timer oneshot) en vez de confiar en el objeto que llega.
local COLLAGE_WS = "🪟"
local COLLAGE_CLASS = "flipfrog-window-collage"

-- { [nombre de monitor] = selector del último workspace que no es "🪟" }
local last_ws = {}

local function selector(ws)
    if ws.id > 0 then
        return ws.id
    end
    return "name:" .. ws.name
end

local function remember(ws)
    if ws and ws.name ~= COLLAGE_WS and not ws.special and ws.monitor then
        last_ws[ws.monitor.name] = selector(ws)
    end
end

for _, mon in ipairs(hl.get_monitors()) do
    remember(hl.get_active_workspace(mon.name))
end

hl.on("workspace.active", function()
    hl.timer(function()
        for _, mon in ipairs(hl.get_monitors()) do
            remember(hl.get_active_workspace(mon.name))
        end
    end, { timeout = 1, type = "oneshot" })
end)

local function evict(win)
    if not win or not win.address then
        return
    end
    local address = win.address
    hl.timer(function()
        local w = hl.get_window("address:" .. address)
        if not w or not w.workspace or w.workspace.name ~= COLLAGE_WS or w.class == COLLAGE_CLASS then
            return
        end
        local mon = w.workspace.monitor
        local target = (mon and last_ws[mon.name]) or 1
        hl.dispatch(hl.dsp.window.move({ workspace = target, window = "address:" .. address }))
        hl.dispatch(hl.dsp.focus({ window = "address:" .. address }))
        hl.dispatch(hl.dsp.window.close({ window = "class:^(" .. COLLAGE_CLASS .. ")$" }))
    end, { timeout = 1, type = "oneshot" })
end

hl.on("window.open", evict)
hl.on("window.move_to_workspace", evict)

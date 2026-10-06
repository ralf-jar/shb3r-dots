-- Aplica el layout de monitores guardado en monitor-config.lua
-- (editable desde el dashboard, SUPER+plus, tab "Monitores" --
-- flipfrog/scripts/monitors/monitor_module.py), pero apaga el que NO
-- aloja el workspace nombrado "🎬" (modo cine, SUPER+SHIFT+C, ver
-- keybinds.lua / cine-mode.lua) mientras ese workspace exista.
--
-- Esta decisión se recalcula ACÁ, cargando de cero en cada
-- `hyprctl reload`, y no vía un `hl.monitor()` suelto disparado desde
-- cine-mode.lua en respuesta a un evento -- confirmado a mano con
-- `hyprctl eval`: llamar hl.monitor({output=..., disabled=false, ...})
-- en caliente (fuera de la carga de config) NO reactiva un monitor que
-- ya está disabled=true. Solo la reaplicación completa de config (este
-- archivo re-ejecutándose en un reload) lo revierte. Por eso
-- cine-mode.lua no llama hl.monitor() directo: solo dispara
-- `hyprctl reload` cuando corresponde, y esta lógica de acá decide qué
-- aplicar en esa pasada.
--
-- Se llama hl.monitor() para TODOS los monitores en cada reload, sin
-- excepción (nada de "saltear si ya está en el estado correcto") -- se
-- intentó esa optimización para evitar el freeze de ventanas al
-- entrar/salir de "🎬", pero resultó al revés de confiable: confirmado a
-- mano que dos `hyprctl reload` seguidos, SIN tocar este archivo para
-- nada, terminaron con HDMI-A-2 en una escala/posición completamente
-- distinta cada vez (la propia hl.monitor() de Hyprland no siempre
-- reaplica el mismo spec de forma consistente cuando se la llama de
-- nuevo sobre un monitor que ya está en ese estado). Reaplicar siempre,
-- aunque cueste un poco de fluidez durante el toggle de cine, es la
-- versión que de verdad converge al mismo resultado cada vez.
--
-- monitor-config.lua (gitignored, estado/config personal como
-- colors.css) -- cada entrada: output, mode ("WxH@R", igual que antes),
-- position ("XxY"), scale, transform (0-3, ver monitor_config.py
-- TRANSFORM_LABELS), disabled, mirror_of (nombre de otro output o nil).
-- El PRIMERO de la lista es el "monitor principal" elegido desde el
-- dashboard -- acá no hace falta ninguna lógica extra para eso, ya que
-- hl.monitor() se sigue llamando en el mismo orden de siempre.
--
-- transform/mirror_of se pasan como campos extra en la MISMA llamada a
-- hl.monitor() (no hay un mecanismo separado documentado para esto en
-- el binding de Lua de Hyprland que usa este repo) -- monitor_module.py
-- deja constancia de que esto no está confirmado en vivo todavía: si
-- después de "Guardar y aplicar" un monitor no rota o no duplica pese a
-- que el resto de sus campos sí se aplicó, el nombre real de estos dos
-- campos en el binding puede ser otro. Sin `mode`/`position` reales
-- cuando hay mirror_of (Hyprland espera "auto"/"auto" para un monitor
-- espejado, no una resolución explícita).
local CONFIG_LUA = os.getenv("HOME") .. "/.config/hypr/monitor-config.lua"

local function load_monitors()
    local ok, result = pcall(dofile, CONFIG_LUA)
    if ok and type(result) == "table" and #result > 0 then
        return result
    end
    -- Sin archivo guardado todavía (primera vez que se usa el
    -- configurador) o archivo corrupto -- autodetectar todo en vez de
    -- dejar al usuario sin pantalla.
    return { { output = "", mode = "preferred", position = "auto", scale = 1, transform = 0, disabled = false, mirror_of = nil } }
end

local MONITORS = load_monitors()

local function cine_host()
    for _, ws in ipairs(hl.get_workspaces()) do
        if ws.name == "🎬" then
            return ws.monitor and ws.monitor.name or nil
        end
    end
    return nil
end

local host = cine_host()
local to_disable = nil
if host then
    for _, spec in ipairs(MONITORS) do
        if spec.output ~= host then
            to_disable = spec.output
            break
        end
    end
end

for _, spec in ipairs(MONITORS) do
    if spec.output == to_disable or spec.disabled then
        hl.monitor({ output = spec.output, disabled = true })
    elseif spec.mirror_of then
        -- "preferred" es el keyword de Hyprland para modo automático
        -- ("auto" es válido para `position`, NO para `mode` -- error real
        -- confirmado: "hl.monitor: error applying field 'mode'").
        hl.monitor({
            output = spec.output,
            mode = "preferred",
            position = "auto",
            scale = spec.scale,
            mirror = spec.mirror_of,
            disabled = false,
        })
    else
        hl.monitor({
            output = spec.output,
            mode = spec.mode,
            position = spec.position,
            scale = spec.scale,
            transform = spec.transform or 0,
            disabled = false,
        })
    end
end

-- Auto-asignación de los primeros workspaces a los primeros monitores
-- (pedido explícito del usuario, 2026-09-04): el principal (primero de
-- MONITORS) siempre toma el workspace "1", el siguiente monitor de la
-- lista el "2", y así -- vía `monitor:` de hl.workspace_rule(), que es
-- el mecanismo nativo de Hyprland para fijar en qué monitor vive un
-- workspace numerado (SUPER+N va ahí siempre, sin importar qué monitor
-- tiene el foco). Workspace 3+ nunca recibe regla acá -- sin ella,
-- Hyprland ya crea un workspace nuevo en el monitor con foco (el
-- comportamiento que el usuario pidió mantener intacto).
-- Índice por posición en MONITORS, no por estado disabled/cine --
-- mismo criterio que el loop de arriba: recalcular la asignación según
-- qué monitor está prendido en cada reload correría el número de
-- workspace de los demás monitores cada vez que entra/sale modo cine,
-- que es justo lo que este archivo evita en todo lo demás. Un monitor
-- espejado (mirror_of) no cuenta -- repite el contenido de otro
-- output, no tiene workspace propio que fijar.
local ws_index = 0
for _, spec in ipairs(MONITORS) do
    if not spec.mirror_of then
        ws_index = ws_index + 1
        hl.workspace_rule({ workspace = tostring(ws_index), monitor = spec.output, default = true })
    end
end

-- Touch: mapeo guardado en touch-config.lua (generado junto con
-- monitor-config.lua por monitor_module.py, save_touch() en
-- monitor_config.py -- tab "Monitores", fila con ícono/droplist por
-- touchscreen conectado). A diferencia de MONITORS, acá no hay estado
-- "en vivo" que leer como fallback: `hyprctl devices -j` no expone qué
-- output tiene asignado un touchscreen, así que sin este archivo
-- Hyprland decide solo -- comportamiento que motivó la feature (mapeaba
-- el touch físico de HDMI-A-2 al monitor equivocado por default).
-- transform sale de la MISMA pasada de MONITORS de arriba (spec.transform)
-- para que rotar un monitor desde el dashboard reoriente el touch con
-- él, sin un segundo lugar que desincronizar a mano.
local TOUCH_CONFIG_LUA = os.getenv("HOME") .. "/.config/hypr/touch-config.lua"

local function load_touch()
    local ok, result = pcall(dofile, TOUCH_CONFIG_LUA)
    if ok and type(result) == "table" then
        return result
    end
    return {}
end

for _, t in ipairs(load_touch()) do
    local transform = 0
    for _, spec in ipairs(MONITORS) do
        if spec.output == t.output then
            transform = spec.transform or 0
            break
        end
    end
    hl.device({ name = t.device, output = t.output, transform = transform })
end

return { specs = MONITORS, cine_host = cine_host }

-- Distribución de teclado: hypr/keyboard.lua (gitignored, lo escribe
-- install.sh), `return { layout = "es", variant = "" }`. Sin él, latam.
local kb = { layout = "latam", variant = "" }
local ok, custom = pcall(dofile, os.getenv("HOME") .. "/.config/hypr/keyboard.lua")
if ok and type(custom) == "table" then
    kb.layout = custom.layout or kb.layout
    kb.variant = custom.variant or kb.variant
end

hl.config({
    input = {
        accel_profile = "flat",
        kb_layout = kb.layout,
        kb_variant = kb.variant
    },
})

-- Mapeo touch->monitor: ver monitors.lua (touch-config.lua, generado
-- desde el dashboard, tab "Monitores") -- ya no fijo acá para que
-- rotar el monitor táctil desde ahí reoriente el touch con él.

hl.gesture({ fingers = 4, direction = "horizontal", action = "workspace" })
hl.gesture({ fingers = 3, direction = "down",       action = "close" })
hl.gesture({ fingers = 3, direction = "up",         action = "fullscreen" })
hl.gesture({ fingers = 3, direction = "left",       action = "float" })

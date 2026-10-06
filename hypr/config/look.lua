-- Ajustes de "Personalización" del dashboard (flipfrog/scripts/theme/
-- look_settings.py): opacidad de ventanas, gaps, borde, animaciones, blur y
-- cursor. Mismo patrón que corner-radius.json/blur-enabled.json en
-- decorations.lua -- un JSON que se relee en cada `hyprctl reload`, sin
-- librería de JSON (string.match). Defaults = valores de fábrica si el
-- archivo o una clave no existe.
local look = {
    opacity = 1,
    gaps_in = 5,
    gaps_out = 10,
    border_size = 2,
    animations = true,
    blur_size = 8,
    cursor_theme = nil,
    cursor_size = 24,
}

local f = io.open(os.getenv("HOME") .. "/.config/flipfrog/themer/hypr-look.json", "r")
if f then
    local content = f:read("*a")
    f:close()
    local function num(key)
        return tonumber(string.match(content, '"' .. key .. '"%s*:%s*([%d%.]+)'))
    end
    look.opacity = num("opacity") or look.opacity
    look.gaps_in = num("gaps_in") or look.gaps_in
    look.gaps_out = num("gaps_out") or look.gaps_out
    look.border_size = num("border_size") or look.border_size
    look.cursor_size = num("cursor_size") or look.cursor_size
    look.blur_size = num("blur_size") or look.blur_size
    if string.match(content, '"animations"%s*:%s*false') then
        look.animations = false
    end
    look.cursor_theme = string.match(content, '"cursor_theme"%s*:%s*"([^"]+)"')
end

return look

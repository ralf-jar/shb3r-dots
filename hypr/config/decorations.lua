local colors = require("config.color")
local look = require("config.look")

-- Toggle "Contraste alto" del dashboard (ver
-- flipfrog/scripts/toggles/oled_shader_toggle.py): decoration.screen_shader
-- no se setea con `hyprctl keyword` en caliente porque `hyprctl reload`
-- (p.ej. cada cambio de tema vía themer/reload.sh) vuelve a ejecutar este
-- archivo entero y lo pisaría -- se lee el mismo JSON que escribe el
-- toggle, mismo patrón que config.color leyendo colors.css.
local home = os.getenv("HOME")
local oled_enabled = false
local oled_state_file = io.open(home .. "/.config/flipfrog/themer/oled-shader.json", "r")
if oled_state_file then
    local content = oled_state_file:read("*a")
    oled_state_file:close()
    oled_enabled = string.match(content, '"enabled"%s*:%s*true') ~= nil
end
local oled_screen_shader = oled_enabled
    and (home .. "/.config/flipfrog/themer/shaders/oled-contrast.frag")
    or ""

-- Toggle "Blur" del dashboard (ver flipfrog/scripts/toggles/blur_toggle.py):
-- switch maestro de decoration:blur:enabled, mismo mecanismo de JSON +
-- reload que el de "Contraste alto" arriba. Default true si el archivo
-- no existe -- el blur viene prendido de fábrica, este toggle solo
-- permite apagarlo.
local blur_enabled = true
local blur_state_file = io.open(home .. "/.config/flipfrog/themer/blur-enabled.json", "r")
if blur_state_file then
    local content = blur_state_file:read("*a")
    blur_state_file:close()
    if string.match(content, '"enabled"%s*:%s*false') then
        blur_enabled = false
    end
end

-- Campo "Redondeo de bordes" del dashboard (flipfrog/themer/sync_radius.py):
-- mismo JSON que alimenta el border-radius de los .css, así las ventanas
-- siguen el mismo valor. 15 si el archivo no existe.
local rounding = 15
local radius_state_file = io.open(home .. "/.config/flipfrog/themer/corner-radius.json", "r")
if radius_state_file then
    local content = radius_state_file:read("*a")
    radius_state_file:close()
    rounding = tonumber(string.match(content, '"radius"%s*:%s*(%d+)')) or rounding
end

hl.config({
    general = {
        gaps_in = look.gaps_in,
        gaps_out = look.gaps_out,
        border_size = look.border_size,
        extend_border_grab_area = 10,
        resize_on_border = true,
        col = {
            active_border = { colors = { colors.myborders, colors.myborders2 }, angle = 45 },
            inactive_border = colors.myborderinactive,
        },
    },
    decoration = {
        dim_special = 0.3,
        rounding = rounding,
        active_opacity = 1,
        inactive_opacity = 1,
        fullscreen_opacity = 1,
        screen_shader = oled_screen_shader,
        blur = {
            enabled = blur_enabled,
            size = look.blur_size,
            passes = 3,
            xray = false,
            vibrancy = 0.0005,
            noise = WINDOWSNOISE,
            -- Tooltips de la barra (y cualquier menú contextual GTK) son
            -- xdg_popup hijos de la superficie layer-shell "ff-bar"
            -- -- decoration.blur NO se hereda a popups salvo que
            -- se active explícito acá (default false en Hyprland). El
            -- layer_rule de la barra (windowrules.lua) solo cubre la
            -- superficie base, no sus popups.
            popups = true,
        },
    },
})
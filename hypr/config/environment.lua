-- Environmental variables
-- if you don't use UWSM, define your variables here (e.g. hl.env("QT_QPA_PLATFORM", "wayland"))
-- Cursor elegido en Personalización (config/look.lua). Para apps que
-- leen XCURSOR_* al arrancar; en la sesión en curso lo aplica
-- `hyprctl setcursor` (look_settings.py).
local look = require("config.look")
if look.cursor_theme then
    hl.env("XCURSOR_THEME", look.cursor_theme)
    hl.env("XCURSOR_SIZE", tostring(look.cursor_size))
end

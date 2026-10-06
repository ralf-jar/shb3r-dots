WINDOWSTHEME = "^(sublime_text|steam|chrome|brave-origin|vesktop|[Tt]hunar|zapzap|com\\.rtosta\\.zapzap|steam|kitty|overskride|io\\.github\\.kaii_lb\\.Overskride|org\\.pulseaudio\\.pavucontrol)$"
LAYOUTSTHEME = "^(dunst|notifications)$"

-- Opacidad de ventanas: campo "Opacidad" de Personalización (config/look.lua).
local look = require("config.look")
WINDOWSOPASITY = look.opacity .. " " .. look.opacity
WINDOWSNOISE = 0.00
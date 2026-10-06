-- CachyOS Hyprland Configuration

require("config.animations")
require("config.autostart")
require("config.colors")
require("config.decorations")
require("config.empty-desktop-menu")
require("config.defaults")
require("config.environment")
require("config.gaming-mode")
require("config.input")
require("config.keybinds")
require("config.misc")
require("config.monitors")
require("config.cine-mode")
require("config.collage-workspace")
require("config.windowrules")
-- Config personal que no se sube al repo (.gitignore); opcional.
pcall(require, "config.personal")

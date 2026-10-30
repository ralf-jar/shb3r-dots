hl.config({
    dwindle = {
        preserve_split = true,
    },
    misc = {
        col = {
            splash = CACHYLGREEN,
        },
        -- Mientras arranca wallpaper.py se ve este color, no el fondo de
        -- fábrica de Hyprland (mismo negro que awww sin imagen).
        disable_hyprland_logo = true,
        force_default_wallpaper = 0,
        background_color = 0xff000000,
        middle_click_paste = false,
        enable_swallow = true,
        swallow_regex = "(kitty|ghostty|[Kk]onsole|Alacritty|gnome-terminal|xfce[0-9]?-terminal)",
        vrr = 3,
    },
    xwayland = {
        -- false = Hyprland escala las apps XWayland con la escala del monitor
        -- (GitHub Desktop/Electron, Steam/CEF). Con true se dibujaban a 1x y
        -- se veían diminutas en HDMI-A-2 (escala 1.5).
        force_zero_scaling = false
    },
    ecosystem = {
        no_update_news = true,
        no_donation_nag = true,
    },
})
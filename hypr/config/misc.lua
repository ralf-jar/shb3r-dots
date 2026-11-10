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
    -- Hyprland 0.57 (render:cm_auto_hdr = 1 por defecto) intenta pasar el
    -- monitor a HDR al entrar en fullscreen si la ventana declara HDR. En
    -- HDMI-A-2 eso dejaba el conector en Colorspace=BT2020_RGB (el EDID
    -- anuncia HDR pero el enlace va en 8 bits) y no lo regresaba a Default
    -- al salir: todo el monitor quedaba con tinte sepia y parpadeaba en
    -- cada entrada/salida de fullscreen. Los dos monitores son SDR de
    -- 8 bits, así que el auto-HDR no aporta nada acá.
    render = {
        cm_auto_hdr = 0,
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
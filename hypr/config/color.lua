local colors = {}
local css_path = os.getenv("HOME") .. "/.config/flipfrog/themer/colors.css"
local file = io.open(css_path, "r")
if file then
    for line in file:lines() do
        local name, value = string.match(line, "@define%-color%s+([%w_]+)%s+(rgba?%([^%)]+%))")
        
        if name and value then
            local lua_name = string.gsub(name, "-", "_")
            colors[lua_name] = value
            print("LOG: Detectado " .. lua_name .. " con valor " .. value)
        end
    end
    file:close()
else
    print("Error: No se pudo cargar el archivo CSS de colores.")
end
return colors
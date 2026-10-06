//
// oled-contrast.frag
// Filtro de contraste del toggle "Contraste alto" del dashboard (curva
// alrededor del punto medio) -- ver
// waybar/scripts/toggles/oled_shader_toggle.py.
//

#version 300 es

precision mediump float;
in vec2 v_texcoord;
layout(location = 0) out vec4 fragColor;
uniform sampler2D tex;

const float CONTRAST = 1.25;

void main() {
    vec4 pixColor = texture(tex, v_texcoord);

    vec3 color = (pixColor.rgb - 0.5) * CONTRAST + 0.5;
    color = clamp(color, 0.0, 1.0);

    fragColor = vec4(color, pixColor.a);
}

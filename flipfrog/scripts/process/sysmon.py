#!/usr/bin/env python3
"""
sysmon.py
Métricas de Uso de la barra -- isla compacta con %RAM, %CPU,
temperatura de CPU, %GPU y temperatura de GPU (en ese orden, pedido
explícito del usuario), coloreada por CATEGORÍA (no por umbral -- cada
categoría tiene un color fijo del tema, ver CATEGORY_COLORS). Vive
detrás de un toggle propio del dashboard ("sysmon" en
bar/bar-settings.json) -- ver CLAUDE.md.

Íconos Nerd Font por categoría en vez de prefijos de texto (R/C/G) --
pedido explícito del usuario, 2026-08-21: "R"/"C"/"G" no se entendían a
simple vista, y la temperatura sin ningún prefijo no aclaraba a qué
correspondía. Antes se había evitado esto a propósito por no poder
confirmar qué codepoints estaban mapeados en el font parcheado de esta
máquina sin arriesgar tofu-boxes -- reconfirmado esta vez de verdad,
no asumido: `fc-list` muestra que "Symbols Nerd Font" (el paquete
standalone solo-íconos) NO está instalado acá, pero los codepoints
elegidos sí renderizan bien igual, vía los Nerd Fonts de código que sí
están instalados (`ttf-jetbrains-mono-nerd`/`ttf-meslo-nerd`/
`ttf-fantasque-nerd`) y el fallback automático de fontconfig -- probado
renderizando offscreen con PangoCairo, mismo stack de fuentes que
`global.css` (`"Monocraft", "Symbols Nerd Font", sans-serif`), a 13px
(el tamaño real de la barra, no un tamaño más grande donde cualquier
glifo se ve más claro de lo que se vería en producción).

Waybar relanza este script entero en cada "interval" (no es un proceso
persistente) -- cpu_percent(interval=CPU_SAMPLE) bloquea a propósito un
rato corto: un Process/muestra global nuevo en cada corrida siempre da
0.0 en la primera lectura, a diferencia de process_module.py (ahí el
tab vive todo el tiempo que el dashboard está abierto, así que
cpu_percent(None) entre refrescos ya alcanza).
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import system_stats

sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
from i18n import t

CPU_SAMPLE = 0.35

# Colores del tema activo, no hex fijos -- pedido explícito del usuario,
# 2026-08-21. Waybar relanza este script entero en cada "interval" (ver
# docstring), así que releer colors.css en cada corrida ya alcanza para
# reflejar un cambio de tema sin reiniciar nada, mismo criterio que
# clock.py. Agrupado por CATEGORÍA (RAM/CPU/GPU), no por umbral -- pedido
# explícito del usuario, mismo día: antes el color cambiaba según el
# valor (ok/warn/bad), ahora cada categoría tiene un color fijo propio y
# su temperatura hereda el color de su categoría (temp. de CPU con el
# mismo color que %CPU, temp. de GPU con el mismo que %GPU).
CATEGORY_COLORS = {
    "ram": common.theme_color_hex("myforeground"),
    "cpu": common.theme_color_hex("myforegroundhover"),
    "gpu": common.theme_color_hex("myforegroundhover2"),
}

# Nerd Font, ver docstring -- verificados uno por uno a 13px, elegidos
# también por verse DISTINTOS entre sí (RAM/CPU quedaban casi idénticos
# con otros candidatos del mismo estilo "microchip"). Sin ícono propio
# para temperatura -- pedido explícito del usuario: el símbolo "°C" ya
# alcanza para identificarla, un glifo más ahí era redundante. RAM/GPU
# elegidos por el usuario 2026-08-21 de un lote de opciones mostradas
# (imagen con varios candidatos renderizados) -- no el primer intento.
ICON_RAM = chr(0xF07C7)  # md-monitor
ICON_CPU = chr(0xF2DB)   # fa-microchip
ICON_GPU = chr(0xEABE)   # cod-circuit-board


def span(text, category):
    return f"<span foreground='{CATEGORY_COLORS[category]}'>{text}</span>"


def status(cpu_sample=CPU_SAMPLE):
    ram = system_stats.ram_percent()
    cpu = system_stats.cpu_percent(cpu_sample)
    gpu = system_stats.gpu_stats()
    temp = system_stats.cpu_temp()

    # Orden pedido explícito del usuario: RAM% CPU% CPU-temp GPU% GPU-temp.
    # Espacio angosto (1) DENTRO de un componente (%, y su temperatura si
    # tiene) -- ancho de sobra (4) recién ENTRE componentes distintos,
    # pedido explícito del usuario: antes todo llevaba el mismo espacio
    # parejo, no se distinguía qué temperatura pertenecía a qué métrica.
    cpu_group = span(f"{ICON_CPU} {cpu:2.0f}%", "cpu")
    tooltip = [t("procesos", "tooltip_ram", ram=ram), t("procesos", "tooltip_cpu", cpu=cpu)]

    if temp is not None:
        cpu_group += " " + span(f"{temp:2.0f}°C", "cpu")
        tooltip.append(t("procesos", "tooltip_temp_cpu", temp=temp))

    groups = [span(f"{ICON_RAM} {ram:2.0f}%", "ram"), cpu_group]

    if gpu is not None:
        gpu_group = span(f"{ICON_GPU} {gpu['percent']:2.0f}%", "gpu") + " " + span(f"{gpu['temp_c']:2.0f}°C", "gpu")
        groups.append(gpu_group)
        tooltip.append(t(
            "procesos", "tooltip_gpu",
            percent=gpu["percent"], mem_percent=gpu["mem_percent"], temp_c=gpu["temp_c"],
        ))

    return {"text": "    ".join(groups), "tooltip": "\n".join(tooltip)}


def main():
    print(json.dumps(status()))


if __name__ == "__main__":
    main()

"""
system_stats.py
Helpers de hardware compartidos por process_module.py (pestaña "Procesos"
del dashboard) y sysmon.py (custom/sysmon, isla compacta de la barra) --
una sola fuente de datos para RAM/CPU/disco/GPU/temperatura, para que la
pestaña y el módulo de la barra no lean el hardware con heurísticas
distintas.
"""

import json
import subprocess

import psutil


def ram_percent():
    return psutil.virtual_memory().percent


def cpu_percent(interval=0.15):
    """interval bloqueante (no None) -- ver sysmon.py, cada corrida es un
    proceso nuevo sin muestra anterior con la que comparar."""
    return psutil.cpu_percent(interval=interval)


def disk_usage(path="/"):
    du = psutil.disk_usage(path)
    return {
        "percent": du.percent,
        "free_gb": du.free / (1024 ** 3),
        "total_gb": du.total / (1024 ** 3),
    }


def gpu_stats():
    """RTX 3070 vía nvidia-smi -- None si no responde (driver caído, sin
    GPU NVIDIA en otra máquina que comparta este repo, etc.), nunca
    excepciona."""
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        util, mem_used, mem_total, temp = (x.strip() for x in result.stdout.strip().split(","))
        return {
            "percent": float(util),
            "mem_percent": round(float(mem_used) / float(mem_total) * 100, 1),
            "temp_c": float(temp),
        }
    except ValueError:
        return None


def cpu_temp():
    """Tctl de k10temp (temp de control real de la CPU AMD de esta
    máquina, confirmado con `sensors -j`) -- si no aparece (otra CPU/
    máquina), cae al primer *_input numérico que encuentre en cualquier
    chip. None si `sensors` no está instalado o no devuelve nada usable."""
    try:
        result = subprocess.run(["sensors", "-j"], capture_output=True, text=True, timeout=2)
        data = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None

    tctl = data.get("k10temp-pci-00c3", {}).get("Tctl", {}).get("temp1_input")
    if isinstance(tctl, (int, float)):
        return tctl

    for chip in data.values():
        if not isinstance(chip, dict):
            continue
        for feature in chip.values():
            if not isinstance(feature, dict):
                continue
            for key, val in feature.items():
                if key.endswith("_input") and isinstance(val, (int, float)):
                    return val
    return None

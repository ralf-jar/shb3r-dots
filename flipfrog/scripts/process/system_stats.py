"""
system_stats.py
Helpers de hardware compartidos por process_module.py (pestaña "Procesos"
del dashboard) y sysmon.py (custom/sysmon, isla compacta de la barra) --
una sola fuente de datos para RAM/CPU/disco/GPU/temperatura, para que la
pestaña y el módulo de la barra no lean el hardware con heurísticas
distintas.
"""

import glob
import json
import os
import re
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


DRM_ROOT = "/sys/class/drm"
CARD_RE = re.compile(r"^card\d+$")


def _nvidia_gpu_stats():
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
        util, mem_used, mem_total, temp = (x.strip() for x in result.stdout.strip().split(",")[:4])
        return {
            "percent": float(util),
            "mem_percent": round(float(mem_used) / float(mem_total) * 100, 1),
            "temp_c": float(temp),
        }
    except ValueError:
        return None


def _read_number(path):
    try:
        with open(path) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None


def _amd_gpu_stats():
    """amdgpu expone uso, VRAM y temperatura en sysfs, sin herramientas
    extra. Con integrada + dedicada se queda con la de más VRAM."""
    best = None
    try:
        cards = [c for c in os.listdir(DRM_ROOT) if CARD_RE.match(c)]
    except OSError:
        return None
    for card in cards:
        dev = os.path.join(DRM_ROOT, card, "device")
        percent = _read_number(os.path.join(dev, "gpu_busy_percent"))
        if percent is None:
            continue
        used = _read_number(os.path.join(dev, "mem_info_vram_used"))
        total = _read_number(os.path.join(dev, "mem_info_vram_total")) or 0
        temps = glob.glob(os.path.join(dev, "hwmon", "hwmon*", "temp1_input"))
        temp = _read_number(temps[0]) if temps else None
        stats = {
            "percent": percent,
            "mem_percent": round(used / total * 100, 1) if used is not None and total else 0.0,
            "temp_c": temp / 1000 if temp is not None else 0.0,
            "_vram": total,
        }
        if best is None or stats["_vram"] > best["_vram"]:
            best = stats
    if best:
        best.pop("_vram")
    return best


def gpu_stats():
    """NVIDIA (nvidia-smi) o AMD (sysfs de amdgpu) -- None si no hay
    ninguna de las dos o no responde; nunca excepciona."""
    return _nvidia_gpu_stats() or _amd_gpu_stats()


# Sensor de la CPU por tipo de chip, no por dirección PCI (cambia entre
# máquinas): k10temp en AMD (Tctl), coretemp en Intel (paquete completo).
CPU_SENSORS = (
    ("k10temp-", ("Tctl", "Tdie")),
    ("zenpower-", ("Tdie", "Tctl")),
    ("coretemp-", ("Package id 0",)),
)


def cpu_temp():
    """None si `sensors` no está instalado o no hay un sensor de CPU
    conocido -- nunca cae a otro chip (el primero de la lista puede ser
    la RAM, `jc42`, o un SSD)."""
    try:
        result = subprocess.run(["sensors", "-j"], capture_output=True, text=True, timeout=2)
        data = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None

    for prefix, features in CPU_SENSORS:
        for chip, values in data.items():
            if not chip.startswith(prefix) or not isinstance(values, dict):
                continue
            for feature in features:
                for key, val in values.get(feature, {}).items():
                    if key.endswith("_input") and isinstance(val, (int, float)):
                        return val
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

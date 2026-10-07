"""Sincronizar el calendario con Android (DAVx⁵): abre o cierra Radicale a
la red local y su regla de UFW. Sin GTK, lo usa phone_sync_popup.py."""

import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "..", "firewall"))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import ufw_actions
import ufw_state
from i18n import t

RADICALE_CONFIG = os.path.expanduser("~/.config/radicale/config")
PORT = 5232
USER = "waybar"
TIMEOUT = 5

HOSTS_RE = re.compile(r"^hosts\s*=.*$", re.M)


def network():
    """(ip, subred) de la conexión con salida a internet, o (None, None)."""
    try:
        route = json.loads(subprocess.run(
            ["ip", "-j", "route", "get", "1.1.1.1"],
            capture_output=True, text=True, timeout=TIMEOUT).stdout)[0]
        ip, dev = route["prefsrc"], route["dev"]
        addrs = json.loads(subprocess.run(
            ["ip", "-j", "-4", "addr", "show", "dev", dev],
            capture_output=True, text=True, timeout=TIMEOUT).stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, KeyError):
        return None, None
    for iface in addrs:
        for info in iface.get("addr_info", []):
            if info.get("local") == ip:
                net = ipaddress.ip_interface(f"{ip}/{info['prefixlen']}").network
                return ip, str(net)
    return ip, None


def url(ip):
    return f"http://{ip}:{PORT}/"


def lan_enabled():
    try:
        with open(RADICALE_CONFIG) as f:
            m = HOSTS_RE.search(f.read())
    except OSError:
        return False
    return bool(m) and "0.0.0.0" in m.group(0)


def _firewall_rule(subnet):
    for rule in ufw_state.read_state()["rules"]:
        if rule["port"] == str(PORT) and rule["src"] == subnet and rule["action"] == "allow":
            return rule
    return None


def _write_hosts(lan):
    with open(RADICALE_CONFIG) as f:
        content = f.read()
    hosts = f"hosts = {'0.0.0.0' if lan else '127.0.0.1'}:{PORT}"
    content = HOSTS_RE.sub(hosts, content) if HOSTS_RE.search(content) \
        else content.replace("[server]", f"[server]\n{hosts}", 1)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(RADICALE_CONFIG))
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.replace(tmp, RADICALE_CONFIG)


def _restart_radicale():
    subprocess.run(["pkill", "-x", "radicale"], timeout=TIMEOUT)
    for _ in range(30):
        if subprocess.run(["pgrep", "-x", "radicale"], stdout=subprocess.DEVNULL).returncode != 0:
            break
        time.sleep(0.1)
    subprocess.Popen(["radicale", "--config", RADICALE_CONFIG], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def set_lan(enabled):
    """Devuelve (ok, mensaje). La regla de UFW solo deja entrar a la subred
    actual; si UFW está apagado no hace falta."""
    if not os.path.exists(RADICALE_CONFIG):
        return False, t("calendario", "sync_sin_radicale")
    _ip, subnet = network()
    ufw_on = ufw_state.read_state()["enabled"]

    if enabled:
        if ufw_on:
            if not subnet:
                return False, t("calendario", "sync_sin_red")
            if not _firewall_rule(subnet):
                ok, msg = ufw_actions.add_rule("allow", "tcp", str(PORT), subnet, "in")
                if not ok:
                    return False, msg
        _write_hosts(True)
    else:
        _write_hosts(False)
        rule = _firewall_rule(subnet) if (ufw_on and subnet) else None
        if rule:
            ok, msg = ufw_actions.delete_rule(rule["index"])
            if not ok:
                _restart_radicale()
                return False, msg
    _restart_radicale()
    return True, ""

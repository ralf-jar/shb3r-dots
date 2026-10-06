"""Lectura del estado de UFW sin pkexec. Ver CLAUDE.md "Administrador de
UFW"."""

import os
import re

UFW_CONF = "/etc/ufw/ufw.conf"
DEFAULT_UFW = "/etc/default/ufw"
RULES_FILES = ["/etc/ufw/user.rules", "/etc/ufw/user6.rules"]

TUPLE_RE = re.compile(
    r"^### tuple ### (?P<action>\S+) (?P<proto>\S+) (?P<dport>\S+) (?P<dst>\S+) "
    r"(?P<spt>\S+) (?P<src>\S+) (?P<direction>\S+)(?: comment=(?P<comment>[0-9a-fA-F]+))?\s*$"
)


def _read_kv_file(path):
    """Parsea archivos tipo shell KEY=value / KEY="value" (ufw.conf,
    /etc/default/ufw) sin ejecutarlos -- son config, no scripts de
    verdad a pesar de la extensión."""
    values = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return values


def _parse_rules_file(path, ip_version, start_index):
    rules = []
    index = start_index
    try:
        with open(path) as f:
            for line in f:
                m = TUPLE_RE.match(line)
                if not m:
                    continue
                comment = m.group("comment")
                if comment:
                    try:
                        comment = bytes.fromhex(comment).decode("utf-8", errors="replace")
                    except ValueError:
                        comment = None
                rules.append({
                    "index": index,
                    "action": m.group("action"),
                    "proto": m.group("proto"),
                    "port": m.group("dport"),
                    "src": m.group("src"),
                    "direction": m.group("direction"),
                    "comment": comment,
                    "ip_version": ip_version,
                })
                index += 1
    except FileNotFoundError:
        pass
    return rules, index


def read_state():
    conf = _read_kv_file(UFW_CONF)
    defaults = _read_kv_file(DEFAULT_UFW)

    rules_v4, next_index = _parse_rules_file(RULES_FILES[0], 4, 1)
    rules_v6, _ = _parse_rules_file(RULES_FILES[1], 6, next_index)

    return {
        "enabled": conf.get("ENABLED", "no").lower() == "yes",
        "loglevel": conf.get("LOGLEVEL", "low"),
        "default_input": defaults.get("DEFAULT_INPUT_POLICY", "?").lower(),
        "default_output": defaults.get("DEFAULT_OUTPUT_POLICY", "?").lower(),
        "default_forward": defaults.get("DEFAULT_FORWARD_POLICY", "?").lower(),
        "rules": rules_v4 + rules_v6,
    }


def rule_label(rule):
    port = "cualquiera" if rule["port"] == "any" else rule["port"]
    proto = "" if rule["proto"] in ("any", "") else f"/{rule['proto']}"
    src = "Cualquiera" if rule["src"] in ("0.0.0.0/0", "::/0", "any") else rule["src"]
    return f"{port}{proto}", rule["direction"].upper(), src

#!/usr/bin/env python3
"""custom/bandcamp-info -- ver CLAUDE.md, "Isla de la barra". Solo LEE
bandcamp_ipc.get_state(), nunca lanza el daemon."""
import html
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, ".."))
import common
from i18n import t
import bandcamp_ipc

MAX_LEN = 20


def _truncate(text):
    text = text or ""
    return text[:MAX_LEN] + "…" if len(text) > MAX_LEN else text


def status(state=None):
    state = state if state is not None else bandcamp_ipc.get_state()
    if not state or not state.get("title"):
        return {
            "text": t("bandcamp", "idle_text"),
            "tooltip": t("bandcamp", "idle_tooltip"),
        }

    title = html.escape(_truncate(state.get("title")))
    artist = html.escape(_truncate(state.get("artist")))
    artist_span = f"<span foreground='{common.theme_color_hex('myforegroundhover')}'>{artist}</span>"

    tooltip_parts = [state.get("title") or "", state.get("artist") or "", state.get("album_title") or ""]
    tooltip = " — ".join(p for p in tooltip_parts if p)

    return {"text": f"{title}\n{artist_span}", "tooltip": tooltip}


def main():
    print(json.dumps(status()))


if __name__ == "__main__":
    main()

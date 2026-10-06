#!/usr/bin/env python3
"""custom/bandcamp-playpause -- ver CLAUDE.md "Isla de la barra". Solo
da el ícono; el clic (bar/bar_modules.py) llama a bandcamp_ctl.py
toggle_pause por separado."""
import json

import bandcamp_ipc


def status(state=None):
    state = state if state is not None else bandcamp_ipc.get_state()
    paused = bool(state and state.get("paused"))
    return {"text": "⏵" if paused else "⏸"}


def main():
    print(json.dumps(status()))


if __name__ == "__main__":
    main()

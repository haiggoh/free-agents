#!/usr/bin/env python3
"""fa_gui/main.py — entry point of the optional native Free Agents GUI.

Arguments are parsed BEFORE NiceGUI is imported, so `--help` and argument errors work in any
interpreter (and an unknown flag never starts a server). Run through bin/free-agents-gui, which
selects the managed GUI venv.

Usage: main.py [--native | --browser [--port N]] [--selftest] [--help]
Environment:
  LA_GUI_SELFTEST_FILE  --selftest writes {"clicked": true, "version": ...} here on click
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

HOST = "127.0.0.1"   # loopback only, in every mode


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="free-agents-gui",
                                 description="Free Agents native GUI (NiceGUI).")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--native", action="store_true", help="native window (default)")
    mode.add_argument("--browser", action="store_true",
                      help="serve on 127.0.0.1 without a window (development/tests)")
    ap.add_argument("--port", type=int, default=0, help="port for --browser (default: free port)")
    ap.add_argument("--selftest", action="store_true",
                    help="minimal page: a button that round-trips through the shared core")
    args = ap.parse_args(argv)
    if args.port and not args.browser:
        ap.error("--port needs --browser")
    return args


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def build_selftest_page() -> None:
    from nicegui import ui
    import session_picker_model as m

    @ui.page("/")
    def index():
        ui.label("Free Agents — selftest").props('id="title"')
        out = ui.label("").props('id="result"')

        def click():
            version = m.plugin_version()
            path = os.environ.get("LA_GUI_SELFTEST_FILE")
            if path:
                Path(path).write_text(json.dumps({"clicked": True, "version": version}))
            out.set_text(f"core says free-agents v{version}")

        ui.button("Round trip", on_click=click).props('id="roundtrip"')


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    from nicegui import ui
    if args.selftest:
        build_selftest_page()
    else:
        from fa_gui import app as gui_app
        gui_app.build()
    port = args.port or _free_port()
    common = dict(title="Free Agents", reload=False, show=False, host=HOST, port=port,
                  show_welcome_message=False)
    if args.browser:
        print(f"free-agents-gui: serving http://{HOST}:{port}", flush=True)
        ui.run(**common)
    else:
        ui.run(native=True, window_size=(1100, 760), **common)
    return 0


if __name__ in {"__main__", "__mp_main__"}:
    sys.exit(main(sys.argv[1:]))

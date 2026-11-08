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

    path = os.environ.get("LA_GUI_SELFTEST_FILE")
    record: dict = {}

    def write(**kv):
        record.update(kv)
        if path:
            Path(path).write_text(json.dumps(record))

    @ui.page("/")
    async def index(client):
        ui.label("Free Agents — selftest").props('id="title"')
        out = ui.label("").props('id="result"')

        def click():
            version = m.plugin_version()
            write(clicked=True, version=version)
            out.set_text(f"core says free-agents v{version}")

        # "connected" = the page's websocket reached this backend: a loaded UI, not an open port.
        await client.connected()
        write(connected=True)

        ui.button("Round trip", on_click=click).props('id="roundtrip"')


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    from nicegui import app, ui
    ready = os.environ.get("LA_GUI_READY_FILE")
    if ready:
        # The launcher (install/gui-launcher/locate.sh) waits for this: a page has CONNECTED,
        # i.e. the window loaded the UI — "frontend ready", not merely "process started".
        app.on_connect(lambda: Path(ready).touch())
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
        ui.run(**common)   # NO window_args in browser mode — nothing here implies desktop blur
    else:
        # Phase 6: translucent/vibrancy come from the GUI appearance store, native mode only.
        # NiceGUI 3.19 takes window args on app.native.window_args — ui.run has NO window_args
        # kwarg (passing one crashes CustomServerConfig: measured real --native launch failure
        # 2026-11-08, "unexpected keyword argument 'window_args'").
        from fa_gui.appearance import native_window_args
        app.native.window_args.update(native_window_args())
        os.environ["LA_GUI_NATIVE"] = "1"   # app.py uses it to compute blur-active opacity
        ui.run(native=True, window_size=(1100, 950), **common)   # taller default: room for >=4 list rows (user 2026-11-05)
    return 0


# "__main__" ONLY: native mode opens the window in a multiprocessing SPAWN child, which re-imports
# this file as "__mp_main__". Running main() there exited the child before it opened the window,
# and the parent then shut down with exit 0 and no output (measured 2026-11-03).
if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

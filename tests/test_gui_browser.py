#!/usr/bin/env python3
"""GUI browser-mode tests (NiceGUI frontend). Run under the GUI venv:

    ~/.local/share/free-agents/gui-venv/bin/python -m pytest -q tests/test_gui_browser.py

The main suite (system pytest, no NiceGUI) SKIPS this file; .rtc/pre-flight.sh runs it as the
"gui" gate, where a missing venv FAILS (FA_ALLOW_MISSING_VENVS=1 to degrade). Servers bind
127.0.0.1 only and every run uses temp state — never the user's settings or keys.

Browser mode proves the rendered UI and its wiring; it does NOT qualify the native window,
Finder launch or Gatekeeper (docs/gui/QUALIFICATION.md covers those separately).
"""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("nicegui", reason="GUI venv only (install/setup-gui.sh --dev)")
pw = pytest.importorskip("playwright.sync_api", reason="GUI venv --dev only")

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "bin/fa_gui/main.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def children_of(pid: int) -> list[int]:
    r = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    return [int(x) for x in r.stdout.split()]


class Server:
    """Start main.py --browser in its own process group; stop it and prove nothing is left."""

    def __init__(self, *extra, env=None):
        self.port = free_port()
        self.tmp = tempfile.TemporaryDirectory()
        # PYTEST_CURRENT_TEST in a child makes NiceGUI enter ITS test mode (KeyError
        # NICEGUI_SCREEN_TEST_PORT): the server under test must see a production env.
        e = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
        e.update({"HOME": self.tmp.name, "LA_SESSION_MENU_CONFIG_DIR": self.tmp.name})
        e.update(env or {})
        self.proc = subprocess.Popen([sys.executable, str(MAIN), "--browser", "--port",
                                      str(self.port), *extra], env=e, cwd=self.tmp.name,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     start_new_session=True)
        self.url = f"http://127.0.0.1:{self.port}/"
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                if self.proc.poll() is not None:
                    log = self.proc.stdout.read() if self.proc.stdout else ""
                    raise RuntimeError(f"server died: {log[-2000:]}")
                time.sleep(0.1)
        raise RuntimeError("server did not start within 30 s")

    def stop(self) -> list[int]:
        kids = children_of(self.proc.pid)
        os.killpg(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        time.sleep(0.3)
        left = [k for k in kids if subprocess.run(["kill", "-0", str(k)],
                                                  capture_output=True).returncode == 0]
        self.tmp.cleanup()
        return left


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def test_app_signals_ready_and_renders_home_from_core(browser, tmp_path):
    # locate.sh waits for LA_GUI_READY_FILE: it must appear once a page has CONNECTED, and the
    # page must be the shared core's Home screen (its title + its action rows), not a stub.
    ready = tmp_path / "ready"
    srv = Server(env={"LA_GUI_READY_FILE": str(ready)})
    try:
        assert not ready.exists(), "ready before any page connected"
        page = browser.new_page()
        page.goto(srv.url)
        page.wait_for_selector("text=Session Launcher", timeout=15000)
        deadline = time.time() + 10
        while time.time() < deadline and not ready.exists():
            time.sleep(0.1)
        assert ready.exists()
        assert page.locator("text=Local sessions").count() >= 1
        assert page.locator("text=Remote free API sessions").count() >= 1
    finally:
        assert srv.stop() == []


def test_browser_selftest_round_trip(browser, tmp_path):
    out = tmp_path / "selftest.json"
    srv = Server("--selftest", env={"LA_GUI_SELFTEST_FILE": str(out)})
    try:
        page = browser.new_page()
        page.goto(srv.url)
        # The page's websocket reached the backend BEFORE any click (native qualification reads
        # the same marker to tell "window loaded the UI" from "a port was open").
        deadline = time.time() + 10
        while time.time() < deadline and not out.exists():
            time.sleep(0.1)
        assert json.loads(out.read_text()).get("connected") is True
        page.click("#roundtrip")
        page.wait_for_selector("#result:has-text('core says free-agents v')", timeout=10000)
        data = json.loads(out.read_text())
        manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        assert data == {"connected": True, "clicked": True, "version": manifest["version"]}
    finally:
        left = srv.stop()
    assert left == [], f"orphan child processes after stop: {left}"
    assert srv.proc.returncode is not None

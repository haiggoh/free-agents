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


def test_settings_row_click_writes_the_store(browser, tmp_path):
    # Task 2 proof (delivery-shaped): clicking a toggle row on /rate_limiter must flip the
    # value in the temp config dir's session-menu.local.json — the same store csl writes.
    srv = Server()
    try:
        store = Path(srv.tmp.name) / "session-menu.local.json"
        page = browser.new_page()
        page.goto(f"{srv.url}rate_limiter")
        row = page.locator('[data-action="RateLimiterScreen.z"]')  # "429 max retries" cycle row
        row.wait_for(timeout=15000)
        before = json.loads(store.read_text()) if store.exists() else {}
        before_retries = before.get("rate_limiter", {}).get("max_retries")
        row.locator(".cycle-right").click()  # the row's ▶ arrow runs the cycle (+1)
        deadline = time.time() + 10
        after = {}
        while time.time() < deadline:
            if store.exists():
                after = json.loads(store.read_text())
                if after.get("rate_limiter", {}).get("max_retries") != before_retries:
                    break
            time.sleep(0.1)
        assert after.get("rate_limiter", {}).get("max_retries") != before_retries, \
            f"store unchanged after click: {before.get('rate_limiter')} -> {after.get('rate_limiter')}"
    finally:
        assert srv.stop() == []


def test_download_queue_review_confirm_produces_cli_argv(browser, tmp_path):
    # Task 1/4 delivery-shaped: queue an entry on /download, Review, Yes in the dialog →
    # the launch stub file gets download-models.sh --select <alias>
    stub = tmp_path / "launch.json"
    srv = Server(env={"FA_GUI_LAUNCH_STUB": str(stub)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}download")
        entry = page.locator("[data-entry]").first
        entry.wait_for(timeout=20000)          # catalog prefetched by the catch-all
        alias = entry.get_attribute("data-entry")
        entry.click()                           # queue it
        page.wait_for_selector(f"[data-entry='{alias}'].queued", timeout=10000)
        page.get_by_text("Review queue and confirm", exact=False).first.click()  # the c action row
        page.wait_for_selector(".q-dialog", timeout=10000)         # the prompt dialog opened
        assert alias in page.locator(".q-dialog").inner_text()
        page.locator(".q-dialog button", has_text="Yes").click()
        deadline = time.time() + 10
        while time.time() < deadline and not stub.exists():
            time.sleep(0.1)
        assert stub.exists(), "confirm did not dispatch a launch"
        argv = json.loads(stub.read_text())["argv"]
        assert argv[0].endswith("install/download-models.sh")
        i = argv.index("--select")
        assert argv[i + 1] == alias
    finally:
        assert srv.stop() == []

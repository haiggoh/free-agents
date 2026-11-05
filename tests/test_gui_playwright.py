"""tests/test_gui_playwright.py — Task 9 playwright tests: the GUI lanes drive the real shared
core. Run under the GUI venv:  ~/.local/share/free-agents/gui-venv/bin/python -m pytest -q tests/test_gui_playwright.py

Spawns bin/fa_gui/main.py --browser on a free port with stub launchers (CSL_SELF /
CSL_REMOTE_LAUNCHER emitting synthetic inventory, FAKE_DELAY to simulate slow loads) and a tmp
HOME/config dir. NOTE: on this loaded box the NiceGUI import+bind takes minutes — the fixture
polls the port instead of sleeping a fixed time."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI_PY = Path(os.environ.get("FA_GUI_PY", str(Path.home() / ".local/share/free-agents/gui-venv/bin/python")))
sys.path.insert(0, str(ROOT / "bin"))
import session_picker_model as m2  # the test reads the SAME NOT_SET_LABEL constant csl uses

REMOTE_INV = ("nvidia-a\tnvidia\tNVIDIA A\tunknown\t0\t1\t0\tnvidia-a\n"
              "gemini-flash\tgemini\tGemini Flash\trenewing_free\t0\t1\t0\tgemini-flash\n")
LOCAL_INV = "qwen-3.8-operator\trapid\thigh\t\t\n"

STUB_HEAD = """#!/bin/sh
for a in "$@"; do [ "$a" = --inventory ] && { [ -n "$FAKE_DELAY" ] && sleep "$FAKE_DELAY"; cat <<'EOF'
"""
STUB_TAIL = """EOF
exit 0; }; done
echo ran "$@" >> "$STUB_LOG"
exit 0
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class GuiApp:
    """One spawned GUI app in browser mode with stub launchers."""

    def __init__(self, inventory: str, fake_delay: str = ""):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        stubs = t / "stubs"
        stubs.mkdir()
        for name, inv in (("csl", LOCAL_INV), ("remote-session.sh", inventory)):
            p = stubs / name
            p.write_text(STUB_HEAD + inv + STUB_TAIL)
            p.chmod(0o755)
        (t / "stub.log").touch()
        self.cfg = t / "config"
        self.cfg.mkdir()
        self.launch_stub = t / "launch.json"
        self.port = free_port()
        env = dict(os.environ)
        env.update({
            "CSL_SELF": str(stubs / "csl"),
            "CSL_REMOTE_LAUNCHER": str(stubs / "remote-session.sh"),
            "LA_SESSION_MENU_CONFIG_DIR": str(self.cfg),
            "HOME": str(t),
            "FAKE_DELAY": fake_delay,
            "STUB_LOG": str(t / "stub.log"),
            "FA_GUI_LAUNCH_STUB": str(self.launch_stub),
            "LA_HARDWARE": "mlx",
            # nicegui's pytest plugin redirects ui.run to NICEGUI_SCREEN_TEST_PORT when the
            # var is present — point it at our port so the child serves where we poll.
            "NICEGUI_SCREEN_TEST_PORT": str(self.port),
        })
        self.errlog = t / "app.err"
        self.proc = subprocess.Popen(
            [str(GUI_PY), str(ROOT / "bin/fa_gui/main.py"), "--browser", "--port", str(self.port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=open(self.errlog, "w"))
        self.base = f"http://127.0.0.1:{self.port}"
        deadline = time.time() + 420
        while time.time() < deadline:
            try:
                urllib.request.urlopen(self.base + "/", timeout=3)
                return
            except Exception:
                if self.proc.poll() is not None:
                    tail = self.errlog.read_text()[-1500:] if self.errlog.exists() else ""
                    raise RuntimeError(f"gui app exited early (rc={self.proc.returncode}): {tail}")
                time.sleep(10)
        raise RuntimeError("gui app did not come up in 420s")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.tmp.cleanup()


class GuiPlaywrightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.app = GuiApp(REMOTE_INV)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.app.stop()

    def page(self):
        p = self.browser.new_page(viewport={"width": 1280, "height": 800})
        p.goto(self.app.base + "/")
        try:
            p.wait_for_selector('.nav-row[data-target="remote"]', timeout=240000)
        except Exception:
            print("PAGE CONTENT ON TIMEOUT:", p.content()[:600], flush=True)
            raise
        return p

    def test_nav_home_remote_back(self):
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".gui-search input", timeout=180000)
        self.assertIn("Remote", p.locator("#title").inner_text())
        p.click('.nav-row[data-target="home"]')
        p.wait_for_selector('.nav-row[data-target="remote"]', timeout=180000)
        self.assertIn("Free Agents", p.locator("#title").inner_text())
        p.close()

    def test_search_filters_rows(self):
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".model-row", timeout=240000)
        self.assertEqual(p.locator(".model-row").count(), 2)
        p.fill(".gui-search input", "mistral")
        p.wait_for_timeout(1500)
        self.assertEqual(p.locator(".model-row").count(), 1)
        self.assertIn("NVIDIA A", p.locator(".model-row").first.inner_text())
        p.close()

    def test_effort_select_persists_to_state_file(self):
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".gui-search input", timeout=240000)
        rows = p.locator(".cycle-row")
        rows.first.wait_for(state="visible", timeout=240000)
        before = rows.first.locator(".cycle-value").inner_text()
        rows.first.locator(".cycle-right").click()
        p.wait_for_timeout(2000)
        doc = json.loads((Path(self.app.cfg) / "session-menu.local.json").read_text())
        self.assertIn("remote_api_session", doc.get("effort", {}))
        after = rows.first.locator(".cycle-value").inner_text()
        self.assertNotEqual(before, after, "cycle ▶ must change the shown value")
        p.close()

    def test_binary_switch_toggles_state_and_animates(self):
        # delivery-level: click the switch -> aria-checked flips, the value label updates,
        # and the COMPUTED styles move (track grey<->black, knob slides 2px<->18px) —
        # Quasar keeps state on the wrapper's aria-checked, never on the hidden native
        # input, so :checked CSS would silently never fire (measured 2026-11-05).
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        sw = p.locator(".switch").first
        sw.wait_for(state="visible", timeout=240000)
        row = sw.locator("xpath=ancestor::div[contains(@class,'cycle-row')]")
        aria_before = sw.get_attribute("aria-checked")
        val_before = row.locator(".cycle-value").inner_text()
        bg_before = sw.evaluate("e=>getComputedStyle(e).backgroundColor")
        knob_before = sw.evaluate("e=>getComputedStyle(e,'::after').left")
        sw.click()
        p.wait_for_timeout(2000)          # let the .18s CSS tween finish
        aria_after = sw.get_attribute("aria-checked")
        self.assertNotEqual(aria_before, aria_after, "click must flip aria-checked")
        self.assertNotEqual(val_before, row.locator(".cycle-value").inner_text(),
                            "value label must update after the toggle")
        bg_after = sw.evaluate("e=>getComputedStyle(e).backgroundColor")
        knob_after = sw.evaluate("e=>getComputedStyle(e,'::after').left")
        self.assertNotEqual(bg_before, bg_after, "track must fade grey<->black")
        self.assertNotEqual(knob_before, knob_after, "knob must slide left<->right")
        if aria_after == "true":
            self.assertEqual(bg_after, "rgb(0, 0, 0)")
            self.assertEqual(knob_after, "18px")
        else:
            self.assertEqual(bg_after, "rgb(187, 187, 187)")
            self.assertEqual(knob_after, "2px")
        # toggle back — the switch must be repeatable, not one-shot
        sw.click()
        p.wait_for_timeout(2000)
        self.assertEqual(sw.get_attribute("aria-checked"), aria_before, "second click must flip back")
        p.close()

    def test_binary_rows_on_off_sync_and_unset_label(self):
        # thinking row: switch and label must move together between ON and OFF — never None,
        # never a click that leaves the label unchanged (user 2026-11-05). None is the row's
        # ON/default state, so the switch starts ON.
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".switch", timeout=240000)
        think = p.locator(".cycle-row", has_text="Thinking").first
        sw = think.locator(".switch")
        chip = think.locator(".cycle-value")
        v0 = chip.inner_text().strip()
        self.assertIn(v0, ("ON", "OFF"), f"binary label must be ON/OFF, got {v0!r}")
        self.assertEqual(sw.get_attribute("aria-checked"), "true" if v0 == "ON" else "false")
        w0 = chip.evaluate("e=>e.getBoundingClientRect().width")
        sw.click()
        p.wait_for_timeout(1500)
        v1 = chip.inner_text().strip()
        self.assertEqual(v1, "OFF" if v0 == "ON" else "ON", "label must flip in sync with the switch")
        self.assertNotIn(v1, ("None", ""))
        self.assertEqual(sw.get_attribute("aria-checked"), "true" if v1 == "ON" else "false")
        w1 = chip.evaluate("e=>e.getBoundingClientRect().width")
        self.assertEqual(w0, w1, "ON vs OFF chip must keep the switch in place (fixed min-width)")
        # temperature row: unset value shows the model's shared NOT_SET_LABEL, not ""/"None"
        temp = p.locator(".cycle-row", has_text="Temperature").first
        self.assertEqual(temp.locator(".cycle-value").inner_text().strip(), m2.NOT_SET_LABEL)
        p.close()

    def test_keyboard_focus_reaches_launch(self):
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".model-row", timeout=240000)
        p.locator(".model-row").first.click()
        p.locator(".gui-launch").focus()
        p.keyboard.press("Enter")
        p.wait_for_timeout(3000)
        rec = json.loads(self.app.launch_stub.read_text())
        self.assertIn("--model", rec["argv"])
        p.close()


class GuiStaleTests(unittest.TestCase):
    def test_stale_inventory_after_navigation_not_applied(self):
        app = GuiApp(REMOTE_INV, fake_delay="3")
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                p = browser.new_page()
                p.goto(app.base + "/remote")
                p.wait_for_selector(".gui-search input", timeout=180000)
                p.wait_for_timeout(300)          # load in flight (FAKE_DELAY=3)
                p.click('.nav-row[data-target="home"]')
                p.wait_for_selector('.nav-row[data-target="remote"]', timeout=180000)
                p.click('.nav-row[data-target="remote"]')           # back while the first load is still running
                p.wait_for_selector(".model-row", timeout=240000)
                p.wait_for_timeout(4000)         # let the slow first load land — must be Stale
                self.assertEqual(p.locator(".model-row").count(), 2,
                                 "the late slow load must not overwrite the list")
                browser.close()
        finally:
            app.stop()


class GuiEmptyTests(unittest.TestCase):
    def test_empty_roster_shows_explanatory_state(self):
        app = GuiApp("")
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                p = browser.new_page()
                p.goto(app.base + "/remote")
                p.wait_for_selector("#empty-state", timeout=180000)
                browser.close()
        finally:
            app.stop()


if __name__ == "__main__":
    unittest.main()

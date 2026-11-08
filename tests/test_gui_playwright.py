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
            "LA_GUI_APPEARANCE_FILE": str(t / "gui-appearance.json"),
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

    def expand_provider_groups(self, p):
        # paint() rebuilds the list on every toggle, so re-query the headers after each
        # click — a stale element reference silently misses
        while True:
            hdrs = p.locator(".lane-list .group-header")
            clicked = False
            for i in range(hdrs.count()):
                h = hdrs.nth(i)
                if "▸" in h.inner_text():
                    h.click()
                    clicked = True
                    p.wait_for_timeout(500)
                    break
            if not clicked:
                return

    def open_groups(self, p):
        # lane screens start with "Model settings" / "Advanced" folded (csl parity) — wait for
        # the headers to render (SPA nav is async), expand any still showing ▸, then wait until
        # a cycle row is actually visible
        # only the lane fold headers — the model list's provider headers reuse .group-header
        # (inside the hidden list box) and clicking one of those times out as invisible
        p.wait_for_selector('.group-header[data-group="model"], .group-header[data-group="settings"]',
                            timeout=120000)
        for sel in ("model", "settings"):
            hdr = p.locator(f'.group-header[data-group="{sel}"]')
            if "▸" in hdr.inner_text():
                hdr.click()
        p.wait_for_selector(".cycle-row", state="visible", timeout=120000)

    def test_lane_groups_collapse_and_expand(self):
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".group-header", timeout=240000)
        hdrs = {h.get_attribute("data-group"): h for h in p.locator(".group-header").all()}
        self.assertIn("model", hdrs)
        self.assertIn("settings", hdrs)
        self.assertIn("Model settings", hdrs["model"].inner_text())
        self.assertIn("Advanced", hdrs["settings"].inner_text())
        hdr = hdrs["model"]
        box = hdr.locator("xpath=following-sibling::div[1]")
        arrow0 = "▸" in hdr.inner_text()
        vis0 = box.is_visible()
        hdr.click()
        p.wait_for_timeout(800)
        self.assertNotEqual("▸" in hdr.inner_text(), arrow0, "header click must flip the triangle")
        self.assertNotEqual(box.is_visible(), vis0, "header click must fold/expand the group box")
        if not box.is_visible():                       # make sure it ends expanded
            hdr.click()
            p.wait_for_timeout(800)
        self.assertGreaterEqual(box.locator(".cycle-row", has_text="Effort").count(), 1,
                                "Model settings group must hold the effort/temperature/thinking rows")
        p.close()

    def test_model_list_scrolls_internally_infobox_pinned(self):
        # user 2026-11-05: an expanded long model list must scroll the LIST, not the window —
        # the infobox stays pinned to the bottom of the window
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".gui-search input", timeout=240000)
        p.locator(".action-row", has_text="Choose model").first.click()
        p.wait_for_selector(".lane-list .group-header", timeout=240000)
        p.locator(".lane-list .group-header").first.click()     # expand the provider group
        p.wait_for_selector(".model-row", state="visible", timeout=120000)
        lst = p.locator(".lane-list")
        self.assertEqual(lst.evaluate("e=>getComputedStyle(e).overflowY"), "auto",
                         "the model list itself must be the scroller")
        doc_h = p.evaluate("()=>document.scrollingElement.scrollHeight")
        win_h = p.evaluate("()=>window.innerHeight")
        self.assertLessEqual(doc_h, win_h + 1, "the window must not scroll with the list open")
        info = p.locator("#infobox")
        self.assertTrue(info.is_visible())
        box = info.bounding_box()
        self.assertLess(abs((box["y"] + box["height"]) - win_h), 48,
                        "infobox must sit at the bottom of the window (shell p-6 + mt-2 ≈ 32-40px)")
        p.close()

    def test_list_min_height_and_emergency_window_scroll(self):
        # user 2026-11-05: the fixed shell is the NORM (window never scrolls, list scrolls
        # internally, infobox pinned); window-level scroll is the EMERGENCY exception, only
        # when the list area would shrink below ~4 rows (groups expanded + short window)
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".gui-search input", timeout=240000)
        shell = p.locator(".lane-shell")
        self.assertLessEqual(shell.evaluate("e=>e.scrollHeight"), shell.evaluate("e=>e.clientHeight") + 1,
                             "norm: the window must not scroll")
        p.locator(".action-row", has_text="Choose model").first.click()
        p.wait_for_selector(".lane-list .group-header", timeout=120000)
        p.locator(".lane-list .group-header").first.click()      # expand provider group
        p.wait_for_selector(".model-row", state="visible", timeout=120000)
        self.open_groups(p)                                        # expand Model settings + Advanced too
        p.set_viewport_size({"width": 1280, "height": 500})        # short window
        p.wait_for_timeout(800)
        lst = p.locator(".lane-list")
        self.assertGreaterEqual(lst.evaluate("e=>e.getBoundingClientRect().height"), 130,
                                "list keeps >= ~4 rows even in a short window (min-height)")
        self.assertEqual(lst.evaluate("e=>getComputedStyle(e).overflowY"), "auto")
        self.assertGreater(shell.evaluate("e=>e.scrollHeight"), shell.evaluate("e=>e.clientHeight"),
                           "emergency: shell scrolls only when the list area would be too small")
        p.close()

    def test_subscreens_render_via_generic_route(self):
        # continuation plan Phase 3: every buildable screen target renders its real rows
        # through the generic route (not the catch-all); unknown paths still get the
        # catch-all with a Back-to-Home link
        p = self.page()
        for target, needle in (("memguard", "RAM"), ("api_keys", "API"), ("theme_config", "Hue")):
            p.goto(f"{self.app.base}/{target}")
            p.wait_for_selector(".action-row, .cycle-row", timeout=240000)
            self.assertTrue(p.locator(".action-row, .cycle-row").count() > 0,
                            f"/{target} must render real rows")
            self.assertIn(needle, p.locator("body").inner_text(), f"/{target} content should mention {needle}")
        p.goto(f"{self.app.base}/definitely-not-a-screen")
        p.wait_for_selector(".menu-item", timeout=240000)
        self.assertIn("not built yet", p.content())
        p.close()

    def test_nav_home_remote_back(self):
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".gui-search input", timeout=180000)
        self.assertIn("Remote", p.locator("#title").inner_text())
        p.click('.nav-row[data-target="home"]')
        p.wait_for_selector('.nav-row[data-target="remote"]', timeout=180000)
        self.assertIn("Session Launcher", p.locator("#title").inner_text())
        p.close()

    def test_search_filters_rows(self):
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".gui-search input", timeout=240000)
        p.locator(".action-row", has_text="Choose model").first.click()   # open the list
        p.wait_for_selector(".lane-list .group-header", timeout=240000)
        self.expand_provider_groups(p)                                    # expand all provider groups
        p.wait_for_selector(".model-row", timeout=240000)
        self.assertEqual(p.locator(".model-row").count(), 2)
        p.fill(".gui-search input", "nvidia")
        p.wait_for_timeout(1500)
        self.assertEqual(p.locator(".model-row").count(), 1)
        self.assertIn("NVIDIA A", p.locator(".model-row").first.inner_text())
        p.close()

    def test_effort_select_persists_to_state_file(self):
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".gui-search input", timeout=240000)
        self.open_groups(p)
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
        self.open_groups(p)
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
        self.open_groups(p)
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

    def test_action_item_click_equals_control(self):
        # csl parity: the action item itself is the control — binary row click toggles like the
        # switch (label AND aria-checked move together), cycle row click steps +1 like ▶.
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        self.open_groups(p)
        think = p.locator(".cycle-row", has_text="Thinking").first
        v0 = think.locator(".cycle-value").inner_text().strip()
        think.locator(".cycle-name").click()          # click the item, not the switch
        p.wait_for_timeout(1500)
        v1 = think.locator(".cycle-value").inner_text().strip()
        self.assertEqual(v1, "OFF" if v0 == "ON" else "ON", "row click must toggle the binary row")
        self.assertEqual(think.locator(".switch").get_attribute("aria-checked"),
                         "true" if v1 == "ON" else "false", "switch visual must follow the row click")
        eff = p.locator(".cycle-row", has_text="Effort").first
        c0 = eff.locator(".cycle-value").inner_text().strip()
        eff.locator(".cycle-name").click()            # click the item, not the ▶ arrow
        p.wait_for_timeout(1500)
        c1 = eff.locator(".cycle-value").inner_text().strip()
        self.assertNotEqual(c0, c1, "row click on a cycle row must step +1 like the ▶ arrow")
        p.close()

    def test_model_row_click_launches(self):
        # csl parity: clicking a model row launches it directly (no Launch button) —
        # the LaunchRequest goes through the shared core's command_for into the stub
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".gui-search input", timeout=240000)
        p.locator(".action-row", has_text="Choose model").first.click()
        p.wait_for_selector(".lane-list .group-header", timeout=240000)
        self.expand_provider_groups(p)
        p.locator(".model-row").first.click()
        p.wait_for_timeout(3000)
        rec = json.loads(self.app.launch_stub.read_text())
        self.assertIn("--model", rec["argv"])          # remote lane: remote-session.sh --model <alias>
        self.assertIn("nvidia-a", rec["argv"])
        p.close()


    def test_two_clients_have_independent_state(self):
        # Continuation plan Phase 2: per-client state (toggle/launch hooks, fold state) must
        # not leak across browser clients. p2 navigates LAST, so under the old module-global
        # `shared` dict p2's toggle_list closure won and p1's Choose-model click toggled p2's
        # list instead of p1's — p1's .lane-list stayed display:none.
        p1 = self.page()
        p1.goto(self.app.base + "/local")
        p1.wait_for_selector('.action-row:has-text("Choose model")', timeout=180000)
        p2 = self.page()
        p2.goto(self.app.base + "/remote")
        p2.wait_for_selector('.action-row:has-text("Choose model")', timeout=180000)
        p1.locator('.action-row:has-text("Choose model")').first.click()
        p1.wait_for_timeout(700)
        style1 = (p1.locator(".lane-list").get_attribute("style") or "").replace(" ", "")
        self.assertNotIn("display:none", style1,
                         "client 1's own Choose-model click must reveal ITS list")
        style2 = (p2.locator(".lane-list").get_attribute("style") or "").replace(" ", "")
        self.assertIn("display:none", style2,
                      "client 2's list must stay hidden — untouched by client 1's click")
        p1.close()
        p2.close()

    def test_cycle_row_keyboard_focus_scoped(self):
        # Continuation plan Phase 3: ArrowRight/ArrowLeft step the FOCUSED cycle row,
        # Enter activates (+1, same as click); while the search input is focused, arrow
        # keys must not step any row.
        p = self.page()
        p.click('.nav-row[data-target="remote"]')
        p.wait_for_selector(".gui-search input", timeout=240000)
        self.open_groups(p)
        row = p.locator(".cycle-row").first
        row.wait_for(state="visible", timeout=240000)
        val = row.locator(".cycle-value")
        before = val.inner_text()
        row.focus()                                   # rows must be focusable (tabindex)
        p.keyboard.press("ArrowRight")
        p.wait_for_timeout(700)
        self.assertNotEqual(val.inner_text(), before, "ArrowRight on the focused row steps +1")
        p.keyboard.press("ArrowLeft")
        p.wait_for_timeout(700)
        self.assertEqual(val.inner_text(), before, "ArrowLeft steps back -1")
        p.keyboard.press("Enter")
        p.wait_for_timeout(700)
        v_after_enter = val.inner_text()
        self.assertNotEqual(v_after_enter, before, "Enter activates (+1 like click)")
        p.click(".gui-search input")                  # focus moves into the search input
        p.keyboard.press("ArrowRight")
        p.wait_for_timeout(700)
        self.assertEqual(val.inner_text(), v_after_enter,
                         "arrows while typing in the search input must not step the row")
        p.close()

    def test_theme_screen_gui_appearance_section(self):
        # Phase 4 UI: the theme screen carries a GUI-only appearance section bound to the
        # appearance store — presentation cycle, translucency switch, opacity cycle (50..100,
        # clamped), blur-request switch. Persists across reload; never touches the semantic
        # session-menu store. Self-contained: pin the store to defaults first (other tests
        # in this class flip presentation and share the one app).
        app_file = Path(self.app.tmp.name) / "gui-appearance.json"
        app_file.write_text(json.dumps({"schema_version": 1, "presentation": "compatibility"}))
        try:
            self._theme_appearance_checks(app_file)
        finally:
            app_file.write_text(json.dumps({"schema_version": 1, "presentation": "compatibility"}))

    def _theme_appearance_checks(self, app_file):
        p = self.page()
        p.goto(self.app.base + "/theme_config")
        p.wait_for_selector('.appearance-row[data-key="presentation"]', timeout=180000)
        app_file = Path(self.app.tmp.name) / "gui-appearance.json"

        pres = p.locator('.appearance-row[data-key="presentation"]')
        pval = pres.locator(".cycle-value")
        self.assertEqual(pval.inner_text(), "compatibility")
        pres.locator(".cycle-right").click()
        p.wait_for_timeout(500)
        self.assertEqual(pval.inner_text(), "modern")
        doc = json.loads(app_file.read_text())
        self.assertEqual(doc["presentation"], "modern", "cycle persists to the appearance store")

        sw = p.locator('.appearance-row[data-key="background_translucent"] .switch')
        sw.click()
        p.wait_for_timeout(500)
        doc = json.loads(app_file.read_text())
        self.assertIs(doc["background_translucent"], True)

        op = p.locator('.appearance-row[data-key="background_opacity_pct"]')
        for _ in range(12):                            # click left well past the 50 floor
            op.locator(".cycle-left").click()
            p.wait_for_timeout(120)
        doc = json.loads(app_file.read_text())
        self.assertEqual(doc["background_opacity_pct"], 50, "opacity clamps at the 50 floor")
        op.locator(".cycle-right").click()
        p.wait_for_timeout(300)
        self.assertEqual(json.loads(app_file.read_text())["background_opacity_pct"], 55)

        # semantic store untouched by all of the above
        self.assertFalse((Path(self.app.cfg) / "session-menu.local.json").exists()
                         and "presentation" in json.loads(
                             (Path(self.app.cfg) / "session-menu.local.json").read_text()),
                         "appearance must never land in the semantic store")
        # reload: values persist
        p.goto(self.app.base + "/theme_config")
        p.wait_for_selector('.appearance-row[data-key="presentation"]', timeout=180000)
        self.assertEqual(p.locator('.appearance-row[data-key="presentation"] .cycle-value').inner_text(),
                         "modern", "presentation persists across reload")
        p.close()

    def test_modern_presentation_renders_tiles_with_same_nav(self):
        # Phase 5: presentation=modern swaps menu rows for emoji tiles but keeps the SAME
        # targets/dispatch — a nav tile navigates exactly like the compat nav row.
        app_file = Path(self.app.tmp.name) / "gui-appearance.json"
        defaults = json.dumps({"schema_version": 1, "presentation": "compatibility"})
        try:
            app_file.write_text(json.dumps({"schema_version": 1, "presentation": "modern"}))
            p = self.page()                  # home in modern mode
            tiles = p.locator(".tile")
            tiles.first.wait_for(state="visible", timeout=180000)
            modern_targets = sorted(t.get_attribute("data-target") or "" for t in tiles.all())
            p.close()
            app_file.write_text(defaults)
            p2 = self.page()                 # home in compat mode
            rows = p2.locator(".nav-row")
            rows.first.wait_for(state="visible", timeout=180000)
            compat_targets = sorted(r.get_attribute("data-target") or "" for r in rows.all())
            p2.close()
            self.assertEqual(modern_targets, compat_targets,
                             "modern tiles must expose the same nav targets as compat rows")
            # and a tile click navigates
            app_file.write_text(json.dumps({"schema_version": 1, "presentation": "modern"}))
            p3 = self.page()
            p3.locator('.tile[data-target="remote"]').first.click()
            p3.wait_for_selector(".gui-search input", timeout=180000)
            self.assertIn("/remote", p3.url)
            p3.close()
        finally:
            app_file.write_text(defaults)   # never leak presentation into other tests

    def test_dependent_row_appears_after_typed_cycle(self):
        # Phase 3 remainder: the Classifier row exists in the model ONLY while auto_mode == 1,
        # so stepping Auto-mode to "classifier" must re-render the screen (csl parity) and
        # the row must appear — and disappear again when stepping back.
        p = self.page()
        p.goto(self.app.base + "/remote")
        p.wait_for_selector(".gui-search input", timeout=240000)
        self.open_groups(p)
        def auto_row():
            # rerender rebuilds the rows — re-query by the STABLE data-action id every time,
            # never by text (text matching can latch onto a different row after a rebuild)
            return p.locator('.cycle-row[data-action="RemoteScreen.a"]').first
        def classifier_count():
            return p.locator('.cycle-row', has_text="Classifier:").count()
        auto_row().wait_for(state="visible", timeout=240000)
        self.assertEqual(classifier_count(), 0,
                         "classifier row hidden while auto-mode is not 'classifier'")
        for _ in range(3):
            auto_row().locator(".cycle-right").click()
            p.wait_for_timeout(700)
            if classifier_count() > 0:
                break
        self.assertGreater(classifier_count(), 0,
                           "classifier row must appear after auto-mode steps to classifier")
        # step back: row disappears again
        for _ in range(3):
            auto_row().locator(".cycle-left").click()
            p.wait_for_timeout(700)
            if classifier_count() == 0:
                break
        self.assertEqual(classifier_count(), 0,
                         "classifier row must disappear when auto-mode leaves classifier")
        p.close()

    def test_dark_mode_tokens_differ_from_light(self):
        # Phase 8: dark_mode=true applies dark semantic tokens (body bg/text, menu-item,
        # hover, arrows, tint tinted dark). Computed styles must DIFFER from light mode —
        # matching values would mean the toggle did nothing (same-hash screenshot trap).
        app_file = Path(self.app.tmp.name) / "gui-appearance.json"
        defaults = json.dumps({"schema_version": 1, "presentation": "compatibility"})

        def probe(doc):
            app_file.write_text(json.dumps(doc))
            p = self.page()
            p.wait_for_selector(".nav-row", timeout=180000)
            body_bg = p.evaluate("getComputedStyle(document.body).backgroundColor")
            item_color = p.locator(".nav-row").first.evaluate(
                "e => getComputedStyle(e).color")
            p.close()
            return body_bg, item_color

        try:
            light = probe({"schema_version": 1, "presentation": "compatibility"})
            dark = probe({"schema_version": 1, "presentation": "compatibility", "dark_mode": True})
            self.assertNotEqual(light, dark, "dark mode must change computed styles")
            self.assertEqual(dark[0], "rgb(18, 18, 18)", "dark body background token")
            self.assertEqual(dark[1], "rgb(232, 232, 232)", "dark menu text token")
            # translucent + dark: the tint layer tints DARK, not white
            app_file.write_text(json.dumps({"schema_version": 1, "dark_mode": True,
                                            "background_translucent": True,
                                            "background_opacity_pct": 50}))
            p = self.page()
            p.wait_for_selector("#tint-layer", timeout=180000)
            bg = p.locator("#tint-layer").evaluate("e => getComputedStyle(e).backgroundColor")
            self.assertEqual(bg, "rgba(18, 18, 18, 0.5)", "dark tint layer")
            p.close()
        finally:
            app_file.write_text(defaults)

    def test_translucent_tint_layer_css_alpha(self):
        # Phase 6 (browser-mode part): translucent on -> exactly one tint layer (body::before)
        # at the stored opacity; root surfaces transparent; foreground untouched. Native
        # blur/vibrancy is NOT TESTED here — it needs real OS observation (see QUALIFICATION).
        app_file = Path(self.app.tmp.name) / "gui-appearance.json"
        defaults = json.dumps({"schema_version": 1, "presentation": "compatibility"})
        try:
            for pct, alpha in ((50, 0.5), (75, 0.75), (100, 1.0)):
                with self.subTest(pct=pct):
                    app_file.write_text(json.dumps({"schema_version": 1, "presentation": "compatibility",
                                                    "background_translucent": True,
                                                    "background_opacity_pct": pct}))
                    p = self.page()
                    p.wait_for_selector("#tint-layer", timeout=180000)
                    bg = p.locator("#tint-layer").evaluate(
                        "e => getComputedStyle(e).backgroundColor")
                    expected = f"rgba(255, 255, 255, {alpha})" if alpha < 1 else "rgb(255, 255, 255)"
                    self.assertEqual(bg, expected,
                                     f"one tint layer at opacity {pct}%, got {bg}")
                    html_bg = p.evaluate("getComputedStyle(document.documentElement).backgroundColor")
                    self.assertEqual(html_bg, "rgba(0, 0, 0, 0)", "root must be transparent")
                    p.close()
            # translucent off -> no tint layer at all
            app_file.write_text(defaults)
            p = self.page()
            p.wait_for_selector(".nav-row", timeout=180000)
            self.assertEqual(p.locator("#tint-layer").count(), 0,
                             "no tint layer when translucency is off")
            p.close()
        finally:
            app_file.write_text(defaults)


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
                p.wait_for_selector(".gui-search input", timeout=180000)
                p.locator(".action-row", has_text="Choose model").first.click()
                p.wait_for_selector(".lane-list .group-header", timeout=240000)
                while True:                                   # expand all provider groups (re-query: paint rebuilds)
                    hdrs = p.locator(".lane-list .group-header")
                    clicked = False
                    for i in range(hdrs.count()):
                        h = hdrs.nth(i)
                        if "▸" in h.inner_text():
                            h.click()
                            clicked = True
                            p.wait_for_timeout(500)
                            break
                    if not clicked:
                        break
                p.wait_for_selector(".model-row", timeout=240000)
                p.wait_for_timeout(4000)         # let the slow first load land — must be Stale
                self.assertEqual(p.locator(".model-row").count(), 2,
                                 "the late slow load must not overwrite the list")
                browser.close()
        finally:
            app.stop()


class GuiGroupDefaultTests(unittest.TestCase):
    def test_lane_groups_start_folded_like_csl(self):
        # fresh app: model_collapsed / settings_collapsed default True in csl — the GUI must
        # start with both group headers showing ▸ and their boxes hidden
        app = GuiApp(REMOTE_INV)
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                p = browser.new_page()
                p.goto(app.base + "/remote")
                hdr = p.locator('.group-header[data-group="model"]')
                hdr.wait_for(state="visible", timeout=240000)
                self.assertIn("▸", hdr.inner_text(), "Model settings starts folded (csl model_collapsed=True)")
                self.assertFalse(hdr.locator("xpath=following-sibling::div[1]").is_visible())
                adv = p.locator('.group-header[data-group="settings"]')
                self.assertIn("▸", adv.inner_text(), "Advanced starts folded (csl settings_collapsed=True)")
                self.assertFalse(adv.locator("xpath=following-sibling::div[1]").is_visible())
                browser.close()
        finally:
            app.stop()


    def test_go_launch_and_nav_stay_top_level(self):
        # user 2026-11-05: "Go launch" sits directly above "Choose model" (csl order), and
        # Back-to-Home / Switch-lane must NEVER be hidden inside a folded group
        app = GuiApp(REMOTE_INV)
        try:
            (app.cfg / "session-menu.local.json").write_text(
                '{"schema_version": 2, "last_launched": {"local_session": "qwen-3.8-operator"}}')
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                p = browser.new_page()
                p.goto(app.base + "/local")
                p.wait_for_selector(".group-header", timeout=240000)
                go = p.locator(".action-row", has_text="Go launch")
                self.assertEqual(go.count(), 1, "Go launch row must render on the lane screen")
                self.assertTrue(go.first.is_visible(), "Go launch must be top-level, not inside a group")
                choose = p.locator(".action-row", has_text="Choose model").first
                self.assertLess(go.first.bounding_box()["y"], choose.bounding_box()["y"],
                                "Go launch must sit above Choose model")
                # both groups folded, yet nav rows still visible at top level
                self.assertIn("▸", p.locator('.group-header[data-group="model"]').inner_text())
                self.assertTrue(p.locator('.nav-row[data-target="home"]').is_visible(),
                                "Back to Home must stay visible with Advanced folded")
                self.assertTrue(p.locator('.nav-row[data-target="remote"]').is_visible(),
                                "Switch-lane must stay visible with Advanced folded")
                # and clicking it actually launches (LaunchRequest -> stub), like a model-row click
                go.first.click()
                p.wait_for_timeout(2000)
                rec = json.loads(app.launch_stub.read_text())
                self.assertIn("--picker-launch", rec["argv"])   # command_for(req) builds this form
                self.assertIn("qwen-3.8-operator", rec["argv"])
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
                p.wait_for_selector(".gui-search input", timeout=180000)
                p.locator(".action-row", has_text="Choose model").first.click()   # open the (empty) list
                p.wait_for_selector(".empty-state", timeout=180000)             # class, not id
                browser.close()
        finally:
            app.stop()


if __name__ == "__main__":
    unittest.main()

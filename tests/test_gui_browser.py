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
        deadline = time.time() + 90   # 2026-11-20: cold GUI-venv import alone took 10.6 s; under load >30 s
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


def test_tool_action_row_dispatches_cli_argv(browser, tmp_path):
    # Task 3: a tool: row runs its command (csl parity) instead of navigating —
    # click "Show live limiter status" on /rate_limiter, assert the stub argv
    stub = tmp_path / "tool.json"
    srv = Server(env={"FA_GUI_LAUNCH_STUB": str(stub)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}rate_limiter")
        page.get_by_text("Show live limiter status", exact=False).first.click()
        deadline = time.time() + 10
        while time.time() < deadline and not stub.exists():
            time.sleep(0.1)
        assert stub.exists(), "tool row did not dispatch"
        argv = json.loads(stub.read_text())["argv"]
        assert argv[0] == "python3" and argv[1].endswith("bin/rate_limiter.py"), argv
        assert argv[2:] == ["status"], argv
    finally:
        assert srv.stop() == []


def test_launchd_status_tool_row_dispatches_backend_argv(browser, tmp_path):
    # Task 3: a tool:rt-* row on /runtime_launchd dispatches manage-backend.py argv
    stub = tmp_path / "tool2.json"
    srv = Server(env={"FA_GUI_LAUNCH_STUB": str(stub)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}runtime_launchd")
        page.get_by_text("Status", exact=False).first.click()
        deadline = time.time() + 10
        while time.time() < deadline and not stub.exists():
            time.sleep(0.1)
        assert stub.exists(), "launchd status row did not dispatch"
        argv = json.loads(stub.read_text())["argv"]
        assert argv[2:] == ["--backend", "rapid-mlx", "launchd", "status"], argv  # default backend
    finally:
        assert srv.stop() == []


def test_catalog_page_adds_and_removes_overlay_row(browser, tmp_path):
    # Task 4 delivery-shaped: the /catalog screen writes the overlay store through
    # catalog_overlay's validated path; the store file (fixture, not the real one)
    # gains then loses the row, and the download screen's catalog cache is invalidated.
    store = tmp_path / "model-catalog.local.psv"
    store.write_text("# existing\nexisting1|Existing|org/existing|main|ex1|1.0|default|||rapid\n")
    srv = Server(env={"LA_MODEL_CATALOG_LOCAL": str(store)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}catalog")
        page.wait_for_selector('[data-remove="existing1"]', timeout=15000)  # row + remove button render
        page.wait_for_selector("text=1.0 GB  [rapid]", timeout=15000)       # full row label, not just alias
        page.locator('[data-add="alias"]').fill("gui-added")
        page.locator('[data-add="name"]').fill("GUI Added")
        page.locator('[data-add="repo"]').fill("org/new")
        page.locator('[data-add="size"]').fill("2.5")
        page.locator('[data-add="groups"]').fill("default")
        page.locator('[data-add="serve"]').fill("rapid")
        page.get_by_text("Add", exact=True).click()
        page.wait_for_selector("text=gui-added", timeout=10000)
        content = store.read_text()
        assert content == ("# existing\n"
                           "existing1|Existing|org/existing|main|ex1|1.0|default|||rapid\n"
                           "gui-added|GUI Added|org/new|main|new|2.5|default|||rapid\n"), content
        page.locator('[data-remove="gui-added"]').click()
        page.wait_for_selector("text=gui-added", state="detached", timeout=10000)
        # full-file equality after remove too: comment line + untouched row byte-identical
        assert store.read_text() == ("# existing\n"
                                     "existing1|Existing|org/existing|main|ex1|1.0|default|||rapid\n")
    finally:
        assert srv.stop() == []


def test_catalog_page_rejects_invalid_row(browser, tmp_path):
    # validation is the point of the screen: a rejected row must notify AND leave the store byte-identical
    store = tmp_path / "model-catalog.local.psv"
    store.write_text("# existing\nexisting1|Existing|org/existing|main|ex1|1.0|default|||rapid\n")
    srv = Server(env={"LA_MODEL_CATALOG_LOCAL": str(store)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}catalog")
        page.wait_for_selector('[data-remove="existing1"]', timeout=15000)
        before = store.read_text()
        page.locator('[data-add="alias"]').fill("bad-size")
        page.locator('[data-add="name"]').fill("Bad")
        page.locator('[data-add="repo"]').fill("org/bad")
        page.locator('[data-add="size"]').fill("abc")          # not a number -> OverlayWriteError
        page.locator('[data-add="groups"]').fill("default")
        page.locator('[data-add="serve"]').fill("rapid")
        page.get_by_text("Add", exact=True).click()
        page.wait_for_selector("text=invalid row", timeout=10000)
        assert store.read_text() == before                     # no write on rejection
        page.locator('[data-add="alias"]').fill("existing1")   # duplicate alias also rejected
        page.locator('[data-add="size"]').fill("2.0")
        page.get_by_text("Add", exact=True).click()
        assert store.read_text() == before
        assert page.locator('[data-remove="existing1"]').count() == 1   # only the original row
    finally:
        assert srv.stop() == []


def test_catalog_edit_reaches_download_screen_and_invalidates_its_cache(browser, tmp_path):
    # /catalog must be reachable from the download screen's catalog list, and a write there must
    # invalidate the download screen's "catalog" cache (jobs.Loader.load returns cached kinds as-is)
    store = tmp_path / "model-catalog.local.psv"
    store.write_text("# existing\nexisting1|Existing|org/existing|main|ex1|1.0|default|||rapid\n")
    srv = Server(env={"LA_MODEL_CATALOG_LOCAL": str(store)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}download")
        page.wait_for_selector('[data-entry="existing1"]', timeout=15000)   # catalog cache populated
        page.locator('[data-nav="catalog"]').click()                         # nav reaches /catalog
        page.wait_for_selector('[data-add="alias"]', timeout=15000)
        page.locator('[data-add="alias"]').fill("gui-added")
        page.locator('[data-add="name"]').fill("GUI Added")
        page.locator('[data-add="repo"]').fill("org/new")
        page.locator('[data-add="size"]').fill("2.5")
        page.locator('[data-add="groups"]').fill("default")
        page.locator('[data-add="serve"]').fill("rapid")
        page.get_by_text("Add", exact=True).click()
        page.wait_for_selector('[data-remove="gui-added"]', timeout=10000)
        page.goto(f"{srv.url}download")                                      # cache was popped -> re-read
        page.wait_for_selector('[data-entry="gui-added"]', timeout=15000)    # fresh row visible
    finally:
        assert srv.stop() == []


def test_local_missing_row_offers_cleanup_and_unregisters(browser, tmp_path):
    # Task 4 (#missing cleanup): a registered-but-weights-missing alias renders as a red row
    # on /local; clicking it opens the confirm dialog; Yes dispatches csl --unregister-models
    # (the same subcommand csl's cleanup panel uses) and the row disappears after refresh
    stub = tmp_path / "launch.json"
    marker = tmp_path / "unregistered"
    csl_stub = tmp_path / "csl-stub.sh"
    csl_stub.write_text("#!/usr/bin/env bash\n"
                        "if [ \"$1\" = \"--inventory\" ]; then\n"
                        "  printf 'on-disk\\tvllm-mlx\\teager\\toperator\\n'\n"
                        "  [ -f \"$GHOST_MARKER\" ] || printf '#missing\\tghost-alias\\tghost-sub\\n'\n"
                        "elif [ \"$1\" = \"--unregister-models\" ]; then\n"
                        "  touch \"$GHOST_MARKER\"\n"
                        "fi\n")
    csl_stub.chmod(0o755)
    srv = Server(env={"FA_GUI_LAUNCH_STUB": str(stub), "CSL_SELF": str(csl_stub),
                      "GHOST_MARKER": str(marker)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}local")
        page.get_by_text("Choose model", exact=False).first.click()   # reveal the lane list (csl parity)
        page.wait_for_selector('[data-missing="ghost-alias"]', timeout=15000)
        page.locator('[data-missing="ghost-alias"]').click()
        page.wait_for_selector(".q-dialog", timeout=10000)
        assert "ghost-alias" in page.locator(".q-dialog").inner_text()
        page.locator(".q-dialog button", has_text="Yes").click()
        deadline = time.time() + 10
        while time.time() < deadline and not stub.exists():
            time.sleep(0.1)
        assert stub.exists(), "confirm did not dispatch"
        argv = json.loads(stub.read_text())["argv"]
        assert argv == [str(csl_stub), "--unregister-models", "ghost-alias"], argv
        page.wait_for_selector('[data-missing="ghost-alias"]', state="detached", timeout=15000)
    finally:
        assert srv.stop() == []


def test_modern_presentation_renders_tiles(browser, tmp_path):
    # 2026-11-19 regression: the rebase merged main's Action default choices=() and
    # render_plan misclassified nav rows as "select", so the tile grid rendered empty
    ap = tmp_path / "appearance.json"
    ap.write_text(json.dumps({"presentation": "modern"}))
    srv = Server(env={"LA_GUI_APPEARANCE_FILE": str(ap)})
    try:
        page = browser.new_page()
        page.goto(srv.url)                       # home
        page.wait_for_selector(".tile-grid", timeout=15000)
        n = page.locator(".tile-grid > *").count()   # delivery-shaped: tiles actually exist
        assert n > 0, "modern mode rendered an empty tile grid"
    finally:
        assert srv.stop() == []


def test_dark_mode_switch_flips_store_and_shell(browser, tmp_path):
    # feature wish 2026-11-19: light/dark mode switch in the appearance section; the
    # dark-shell class carries the flipped text colors
    ap = tmp_path / "appearance.json"
    ap.write_text(json.dumps({"dark_mode": False}))
    srv = Server(env={"LA_GUI_APPEARANCE_FILE": str(ap)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}theme_config")
        page.wait_for_selector('[data-key="dark_mode"]', timeout=15000)
        assert page.locator(".dark-shell").count() == 0
        page.locator('[data-key="dark_mode"] .q-checkbox').click()
        deadline = time.time() + 10
        while time.time() < deadline and json.loads(ap.read_text())["dark_mode"] is not True:
            time.sleep(0.1)
        assert json.loads(ap.read_text())["dark_mode"] is True   # store write is the outcome
        # user 2026-11-20: applies WITHOUT leaving the theme submenu — the toggle reloads the
        # page itself, so .dark-shell appears on the SAME url (no navigation in the test)
        page.wait_for_selector(".dark-shell", timeout=20000)
        assert page.url.rstrip("/").endswith("theme_config"), page.url
    finally:
        assert srv.stop() == []


def test_modern_tile_emoji_filter_follows_theme_mode(browser, tmp_path):
    # 2026-11-21/22 user pick: light mode = subdued filter on the bitmap; dark mode = the
    # bitmap untouched plus a solid-white ::after overlay at 0.6 (brightness>1 blew out the
    # highlights). Assert the COMPUTED contract in both modes.
    for dark, expect in ((False, "saturate(0.3) brightness(0.22)"), (True, "none")):
        ap = tmp_path / f"appearance-{dark}.json"
        ap.write_text(json.dumps({"presentation": "modern", "dark_mode": dark}))
        srv = Server(env={"LA_GUI_APPEARANCE_FILE": str(ap)})
        try:
            page = browser.new_page()
            page.goto(srv.url)
            page.wait_for_selector(".tile-grid .tile-emoji-subdued", timeout=20000)
            info = page.evaluate("""() => {
                const el = document.querySelector('.tile-grid .tile-emoji-subdued');
                return [getComputedStyle(el).filter,
                        getComputedStyle(el, '::after').opacity];
            }""")
            assert expect == info[0], (dark, info)
            if dark:   # the overlay is the bright-silhouette mechanism
                assert abs(float(info[1]) - 0.75) < 0.01, (dark, info)
        finally:
            assert srv.stop() == []


def test_tile_emoji_style_options_all_render(browser, tmp_path):
    # 2026-11-21 theme option: subdued (default) | noto (mono line-art font) | full (color)
    for style, expect in (("subdued", "saturate(0.3) brightness(0.22)"),
                          ("noto", "NotoEmojiMono"),
                          ("full", "none")):
        ap = tmp_path / f"appearance-{style}.json"
        ap.write_text(json.dumps({"presentation": "modern", "tile_emoji_style": style}))
        srv = Server(env={"LA_GUI_APPEARANCE_FILE": str(ap)})
        try:
            page = browser.new_page()
            page.goto(srv.url)
            page.wait_for_selector(".tile-grid .tile-emoji", timeout=20000)
            info = page.evaluate("""() => {
                const el = document.querySelector('.tile-grid .tile-emoji');
                const cs = getComputedStyle(el);
                return [cs.filter, cs.fontFamily];
            }""")
            if expect == "none":
                assert info[0] == "none", (style, info)
                # the font-face must be inside <style>, never stray visible text (2026-11-21
                # regression: the rule was prepended as raw head text and showed as a line)
                assert "@font-face" not in page.inner_text("body"), (style, "stray CSS text")
            elif expect == "NotoEmojiMono":
                assert expect in info[1], (style, info)
                loaded = page.evaluate(
                    "document.fonts.ready.then(() => document.fonts.check("
                    "'350 72px \"NotoEmojiMono\"', '\u0001F680'))")
                assert loaded, (style, "noto font did not actually load")
            else:
                assert expect in info[0], (style, info)
        finally:
            assert srv.stop() == []


def test_tile_emoji_cycle_keeps_theme_rows(browser, tmp_path):
    # 2026-11-21 user report: cycling a tile-emoji option wiped the appearance section
    # (the live apply re-rendered rows only) until re-entering the menu.
    ap = tmp_path / "appearance.json"
    ap.write_text(json.dumps({"presentation": "modern", "tile_emoji_style": "subdued"}))
    srv = Server(env={"LA_GUI_APPEARANCE_FILE": str(ap)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}theme_config")
        page.wait_for_selector('[data-key="tile_emoji_style"]', timeout=20000)
        page.locator('[data-key="tile_emoji_style"] .cycle-right').click()
        # live preview: the theme menu's REAL tile (Back-to-home) is the preview — its emoji
        # re-styles in place (the JS class swap lands a beat after the store write)
        deadline = time.time() + 10
        ff = ""
        while time.time() < deadline:
            ff = page.evaluate(
                "getComputedStyle(document.querySelector('.tile-grid .tile-emoji')).fontFamily")
            if "NotoEmojiMono" in ff:
                break
            time.sleep(0.2)
        assert "NotoEmojiMono" in ff, ff
        assert json.loads(ap.read_text())["tile_emoji_style"] == "noto"
        # the section must not vanish: the cycle rows stay, and no navigation happened
        assert page.locator('[data-key="tile_emoji_style"]').count() == 1
        assert page.locator('[data-key="presentation"]').count() == 1
        assert "theme_config" in page.url
    finally:
        assert srv.stop() == []


def test_theme_menu_labels_and_toggle_clicks(browser, tmp_path):
    # 2026-11-22: presentation reads "Tiles menu"; switches toggle on VALUE click (csl parity);
    # the appearance headline is a headline, not a hover row
    ap = tmp_path / "appearance.json"
    ap.write_text(json.dumps({"presentation": "modern", "dark_mode": False}))
    srv = Server(env={"LA_GUI_APPEARANCE_FILE": str(ap)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}theme_config")
        page.wait_for_selector('[data-key="dark_mode"]', timeout=20000)
        rowname = page.evaluate(
            "document.querySelector('[data-key=\"dark_mode\"] .cycle-name').className")
        assert "menu-item" not in rowname, rowname       # hover is action-row only
        pres = page.evaluate(
            "document.querySelector('[data-key=\"presentation\"] .cycle-value').innerText")
        assert "Tiles menu" in pres, pres
        page.locator('[data-key="dark_mode"] .cycle-value').click()   # value click == toggle
        deadline = time.time() + 10
        while time.time() < deadline and json.loads(ap.read_text())["dark_mode"] is not True:
            time.sleep(0.1)
        assert json.loads(ap.read_text())["dark_mode"] is True
        page.wait_for_selector(".dark-shell", timeout=5000)         # applied live
        page.locator('[data-key="dark_mode"] .cycle-name').click()  # name click == toggle too
        deadline = time.time() + 10
        while time.time() < deadline and json.loads(ap.read_text())["dark_mode"] is not False:
            time.sleep(0.1)
        assert json.loads(ap.read_text())["dark_mode"] is False
        page.wait_for_selector(".dark-shell", state="detached", timeout=5000)
        # follow-system is launch-only: the click stores the flag but never live-re-tints
        # the window (user 2026-11-22: it flipped the bg when dark mode was off)
        page.locator('[data-key="follow_system_appearance"] .cycle-value').click()
        deadline = time.time() + 10
        while time.time() < deadline and json.loads(ap.read_text())["follow_system_appearance"] is not True:
            time.sleep(0.1)
        assert json.loads(ap.read_text())["follow_system_appearance"] is True
        assert page.locator(".dark-shell").count() == 0
        assert page.locator(".launch-hint").count() >= 1
    finally:
        assert srv.stop() == []


def test_tile_emoji_row_only_in_tiles_menu(browser, tmp_path):
    # 2026-11-22: the Tile emoji treatment is irrelevant in compatibility mode — hidden
    ap = tmp_path / "appearance.json"
    ap.write_text(json.dumps({"presentation": "compatibility"}))
    srv = Server(env={"LA_GUI_APPEARANCE_FILE": str(ap)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}theme_config")
        page.wait_for_selector('[data-key="presentation"]', timeout=20000)
        assert page.locator('[data-key="tile_emoji_style"]').count() == 0
        assert page.locator(".tile-grid").count() == 0    # no tiles to treat
        page.locator('[data-key="presentation"] .cycle-value').click()   # step to Tiles menu
        deadline = time.time() + 10
        while time.time() < deadline and json.loads(ap.read_text())["presentation"] == "compatibility":
            time.sleep(0.1)
        assert json.loads(ap.read_text())["presentation"] == "modern"
        # live: the row appears without re-entering the menu
        page.wait_for_selector('[data-key="tile_emoji_style"]', timeout=20000)
    finally:
        assert srv.stop() == []


def test_tile_emoji_cycle_value_click_steps_like_the_arrow(browser, tmp_path):
    # 2026-11-21 csl parity: clicking the VALUE (or the name) of a cycle row steps like ▶
    ap = tmp_path / "appearance.json"
    ap.write_text(json.dumps({"presentation": "modern", "tile_emoji_style": "subdued"}))
    srv = Server(env={"LA_GUI_APPEARANCE_FILE": str(ap)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}theme_config")
        page.wait_for_selector('[data-key="tile_emoji_style"]', timeout=20000)
        page.locator('[data-key="tile_emoji_style"] .cycle-value').click()
        deadline = time.time() + 10
        while time.time() < deadline and json.loads(ap.read_text())["tile_emoji_style"] == "subdued":
            time.sleep(0.1)
        assert json.loads(ap.read_text())["tile_emoji_style"] == "noto"
        page.locator('[data-key="tile_emoji_style"] .cycle-name').click()   # name acts like ▶ too
        deadline = time.time() + 10
        while time.time() < deadline and json.loads(ap.read_text())["tile_emoji_style"] == "noto":
            time.sleep(0.1)
        assert json.loads(ap.read_text())["tile_emoji_style"] == "full"
    finally:
        assert srv.stop() == []


def test_theme_accent_rows_show_live_swatches(browser, tmp_path):
    # 2026-11-22 parity: the theme accent cycles show a swatch chip (csl's THEME_PRESETS
    # display) that follows the cycled value — objective mirror, no new dependencies
    srv = Server()
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}theme_config")
        row = page.locator('[data-action="ThemeConfigScreen.l"]')
        row.wait_for(timeout=20000)
        swatch = row.locator(".cycle-swatch")
        swatch.wait_for(timeout=5000)
        before = swatch.evaluate("el => getComputedStyle(el).backgroundColor")
        assert before.startswith("rgb"), before
        value_before = row.locator(".cycle-value").inner_text()
        assert "#" in value_before, value_before
        row.locator(".cycle-right").click()
        deadline = time.time() + 10
        after = before
        while time.time() < deadline:
            after = swatch.evaluate("el => getComputedStyle(el).backgroundColor")
            if after != before:
                break
            time.sleep(0.2)
        assert after != before, (before, after)
        # the value names the preset AND its hex — and BOTH follow the cycle
        value_after = row.locator(".cycle-value").inner_text()
        assert "#" in value_after and value_after != value_before, (value_before, value_after)
    finally:
        assert srv.stop() == []


def test_lowkey_chooser_selection_rides_the_launch(browser, tmp_path):
    # 2026-11-23 parity (QC R1): csl's lowkey chooser SELECTS a model (no launch on click);
    # the selection survives the session-name dialog round-trip and rides the convo launch
    # (--session + --provider); the one-shot dispatches --prompt without --session
    stub = tmp_path / "launch.json"
    csl_stub = tmp_path / "csl-stub.sh"
    csl_stub.write_text("#!/usr/bin/env bash\n"
                        "if [ \"$1\" = \"--inventory\" ]; then\n"
                        "  printf 'my-model\\tvllm-mlx\\teager\\toperator\\n'\n"
                        "fi\n")
    csl_stub.chmod(0o755)
    rs_stub = tmp_path / "remote-session.sh"
    rs_stub.write_text("#!/usr/bin/env bash\n"
                       "if [ \"$1\" = \"--inventory\" ]; then\n"
                       "  printf 'agent-ok\\topenrouter\\tAgent OK\\tstandard\\t0\\t1\\n'\n"
                       "fi\n")
    rs_stub.chmod(0o755)
    srv = Server(env={"FA_GUI_LAUNCH_STUB": str(stub), "CSL_SELF": str(csl_stub),
                      "CSL_REMOTE_LAUNCHER": str(rs_stub)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}lowkey")
        page.get_by_text("Choose model", exact=False).first.click()
        page.locator('[data-model="openrouter:agent-ok"]').click()      # SELECT (no launch)
        assert not stub.exists(), "choosing a model must not launch it"
        page.get_by_text("Set session name", exact=False).first.click()
        page.locator(".q-dialog input").fill("my-run")
        page.locator(".q-dialog button", has_text="Yes").click()
        page.wait_for_selector("text=Set session name: my-run", timeout=15000)
        # the selection survived the dialog round-trip
        page.get_by_text("Choose model", exact=False).first.click()
        assert "selected" in page.locator(
            '[data-model="openrouter:agent-ok"]').get_attribute("class")
        page.get_by_text("Start convo", exact=False).first.click()
        deadline = time.time() + 10
        while time.time() < deadline and not stub.exists():
            time.sleep(0.1)
        assert stub.exists(), "convo did not dispatch"
        argv = json.loads(stub.read_text())["argv"]
        assert argv[argv.index("--session") + 1] == "my-run", argv
        assert argv[argv.index("--provider") + 1] == "openrouter", argv
        # one-shot: the typed prompt rides, the session name does not (csl's contract)
        stub.unlink()
        page.get_by_text("Start one-shot", exact=False).first.click()
        page.wait_for_selector(".q-dialog", timeout=10000)
        page.locator(".q-dialog input").fill("summarize this repo")
        page.locator(".q-dialog button", has_text="Yes").click()
        deadline = time.time() + 10
        while time.time() < deadline and not stub.exists():
            time.sleep(0.1)
        argv = json.loads(stub.read_text())["argv"]
        assert argv[argv.index("--prompt") + 1] == "summarize this repo", argv
        assert "--session" not in argv, argv
    finally:
        assert srv.stop() == []


def test_cloud_route_switch_writes_settings_env(browser, tmp_path):
    # 2026-11-22 parity: the cloud-config route row cycles AND persists — the write lands in
    # the launch dir's .claude/settings.json env (FA_ROUTE_AGENT), never the real one
    srv = Server()
    try:
        env_file = Path(srv.tmp.name) / ".claude" / "settings.json"
        page = browser.new_page()
        page.goto(f"{srv.url}cloud_config")
        row = page.locator(".cycle-row", has_text="Agent dispatch")
        row.wait_for(timeout=20000)
        row.locator(".cycle-value").click()           # +1: free-first -> free-only
        deadline = time.time() + 10
        mode = None
        while time.time() < deadline:
            if env_file.exists():
                mode = json.loads(env_file.read_text()).get("env", {}).get("FA_ROUTE_AGENT")
                if mode:
                    break
            time.sleep(0.1)
        assert mode == "free-only", (mode, env_file.read_text() if env_file.exists() else "no file")
        # and the row label reflects the persisted value (the rerender pushes a beat later)
        page.wait_for_selector("text=Free only", timeout=5000)
    finally:
        assert srv.stop() == []


def test_remote_filter_applies_to_the_remote_list(browser, tmp_path):
    # 2026-11-22 parity: the RemoteFiltersScreen cycles must APPLY — a trial agent is
    # visible on /remote, hidden after "Limited trials: HIDDEN" is chosen, back on re-show
    stub = tmp_path / "remote-session.sh"
    stub.write_text("#!/usr/bin/env bash\n"
                    "if [ \"$1\" = \"--inventory\" ]; then\n"
                    "  printf 'agent-ok\\topenrouter\\tAgent OK\\tstandard\\t0\\t1\\n'\n"
                    "  printf 'agent-trial\\topenrouter\\tAgent Trial\\ttrial\\t0\\t1\\n'\n"
                    "fi\n")
    stub.chmod(0o755)
    srv = Server(env={"CSL_REMOTE_LAUNCHER": str(stub)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}remote")
        page.get_by_text("Choose model", exact=False).first.click()   # reveal the list (csl parity)
        page.locator('[data-group="openrouter"]').first.click()      # expand the provider group
        page.wait_for_selector("text=Agent OK", timeout=20000)
        models = page.locator("[data-model]")
        assert models.count() == 2 and "Agent Trial" in page.inner_text("body")
        page.goto(f"{srv.url}remote_filters")
        page.wait_for_selector(".cycle-row", timeout=20000)
        # the filter rows are BINARY switches (HIDDEN/SHOWN): the value click toggles
        trials = page.locator(".cycle-row", has_text="Limited trials")
        trials.locator(".cycle-value").click()
        page.goto(f"{srv.url}remote")
        page.get_by_text("Choose model", exact=False).first.click()
        page.locator('[data-group="openrouter"]').first.click()
        page.wait_for_selector("text=Agent OK", timeout=20000)
        models = page.locator("[data-model]")
        ids = [m.get_attribute("data-model") for m in models.all()]
        assert ids == ["agent-ok"], ids
        # and back on re-show
        page.goto(f"{srv.url}remote_filters")
        page.locator(".cycle-row", has_text="Limited trials").locator(".cycle-value").click()
        page.goto(f"{srv.url}remote")
        page.get_by_text("Choose model", exact=False).first.click()
        page.locator('[data-group="openrouter"]').first.click()
        page.wait_for_selector("text=Agent Trial", timeout=20000)
        models = page.locator("[data-model]")
        ids = [m.get_attribute("data-model") for m in models.all()]
        assert sorted(ids) == ["agent-ok", "agent-trial"], ids
    finally:
        assert srv.stop() == []


def test_warn_notice_page(browser, tmp_path):
    # 2026-11-22 parity: csl's warn: rows land a NOTICE (state_warnings); the GUI renders
    # the warn message page with a Back-to-Home instead of the "not built yet" catch-all
    srv = Server(env={})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}warn:1.2.3 is the only installed rapid-mlx version — not removed.")
        page.wait_for_selector("[data-warn]", timeout=20000)
        text = page.locator("[data-warn]").inner_text()
        assert "only installed rapid-mlx version" in text, text
        assert page.locator('[data-target="home"]').count() == 1
    finally:
        assert srv.stop() == []


def test_lowkey_session_name_prompt(browser, tmp_path):
    # 2026-11-22 parity: the lowkey "Set session name" row opens a TEXT dialog (csl's
    # typed prompt); a valid name lands in the row label AND the launch argv (--session)
    stub = tmp_path / "launch.json"
    srv = Server(env={"FA_GUI_LAUNCH_STUB": str(stub)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}lowkey")
        page.get_by_text("Set session name", exact=False).first.click()
        page.wait_for_selector(".q-dialog", timeout=10000)
        page.locator(".q-dialog input").fill("bad name!")
        page.locator(".q-dialog button", has_text="Yes").click()
        page.wait_for_selector("text=session name rejected", timeout=5000)
        assert json.loads(stub.read_text() if stub.exists() else "{}") == {}
        page.locator(".q-dialog input").fill("my-run")
        page.locator(".q-dialog button", has_text="Yes").click()
        page.wait_for_selector("text=Set session name: my-run", timeout=15000)
        page.get_by_text("Start one-shot", exact=False).first.click()
        page.wait_for_selector(".q-dialog", timeout=10000)      # csl opens the typed prompt
        page.locator(".q-dialog button", has_text="Yes").click()  # empty prompt = honest warning
        page.wait_for_selector("text=one-shot needs a prompt", timeout=5000)
        assert not stub.exists()
        # the argv contract (name riding a real launch) is unit-pinned in
        # tests/test_lowkey_session_name.py; selecting a model here would need a live
        # inventory, which this fixture does not provide
    finally:
        assert srv.stop() == []
    # 2026-11-20 spec: launch-only switch; the store write is the delivery-shaped outcome
    ap = tmp_path / "appearance.json"
    ap.write_text(json.dumps({"follow_system_appearance": False}))
    srv = Server(env={"LA_GUI_APPEARANCE_FILE": str(ap)})
    try:
        page = browser.new_page()
        page.goto(f"{srv.url}theme_config")
        page.wait_for_selector('[data-key="follow_system_appearance"]', timeout=15000)
        page.locator('[data-key="follow_system_appearance"] .q-checkbox').click()
        deadline = time.time() + 10
        while time.time() < deadline and json.loads(ap.read_text()).get("follow_system_appearance") is not True:
            time.sleep(0.1)
        assert json.loads(ap.read_text())["follow_system_appearance"] is True
    finally:
        assert srv.stop() == []

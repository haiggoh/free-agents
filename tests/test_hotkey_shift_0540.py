#!/usr/bin/env python3
"""0.54.0 Task 12 — shift+letter runs the same action as the letter, on every screen, while
uppercase still types into every text input.

Evidence: action labels render the key UPPERCASE (rendered_actions upper-cases a.key) but
on_key dispatched letter hotkeys only when key.islower() — so Shift+letter did nothing.
Fix: normalize a single-letter key with key.lower() at the ONE dispatch point, AFTER the
search-input branch so uppercase still types into the live filter and prompt inputs.
"""
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import session_picker_model as m  # noqa: E402
from picker_harness import PilotCase  # noqa: E402

try:
    import textual  # noqa: F401
except ImportError:
    textual = None
sp = None
if textual is not None:
    import session_picker as sp  # noqa: E402

pytestmark = pytest.mark.load_sensitive

_SCREEN_EXTRAS = {
    "AboutScreen": {}, "HomeScreen": {},
    "LocalScreen": {"models": [m.LocalModel(alias="qwen-3.8-operator", family="q")]},
    "RemoteScreen": {"agents": [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown", model_id="vendor/a")]},
    "RemoteFiltersScreen": {}, "CloudConfigScreen": {}, "ThemeConfigScreen": {},
    "RateLimiterScreen": {}, "MemoryGuardScreen": {},
    "VersionsScreen": {"op": "info", "entries": []},
    "RuntimeManagerScreen": {}, "APIKeysScreen": {}, "RuntimeScreen": {}, "LaunchdScreen": {},
    "LowkeyScreen": {"models": [m.LocalModel(alias="qwen-3.8-operator", family="q")],
                     "api_agents": [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown", model_id="vendor/a")]},
    "DownloadScreen": {"entries": [], "free_gb": 100.0, "headroom_gb": 10.0},
}


def _screen_subclasses():
    return sorted((getattr(m, n) for n in dir(m)
                   if isinstance(getattr(m, n), type) and issubclass(getattr(m, n), m.Screen)
                   and getattr(m, n) is not m.Screen and getattr(m, n).__module__ == m.__name__),
                  key=lambda c: c.__name__)


def _letter_actions(screen):
    return [a for a in screen.actions() if len(a.key) == 1 and a.key.isalpha()]


@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class ShiftLetterTests(PilotCase):
    def setUp(self):
        super().setUp()
        self._settings = m.Settings()
        import shutil
        for name in ("local-theme.json", "remote-theme.json"):
            shutil.copy(ROOT / "config" / name, Path(self.env["LA_THEME_CONFIG_DIR"]) / name)

    # 0.56.15 (perf): the every-screen sweep was ONE test (~11 min serial — it rebuilt the
    # picker per screen inside one method, so xdist could not spread it). One method per
    # screen class: xdist distributes them, and the gate's floor drops to the slowest screen.
    def test_shift_letter_runs_same_action_on_every_screen(self):
        subclasses = [c for c in _screen_subclasses() if c is not m.HomeScreen]
        self.assertGreater(len(subclasses), 5)
        for cls in subclasses:
            self._check_screen(cls)

    def _check_screen(self, cls):
        async def scenario(app, pilot, h):
            from session_picker import ConfirmPanel as _CP
            dispatched = []
            orig_dispatch = app.dispatch
            def spy(result, keep=None):
                dispatched.append(repr(result))
                return orig_dispatch(result, keep)
            app.dispatch = spy
            try:
                async def clean():
                    while isinstance(app.screen, _CP):
                        app.pop_screen()
                    if getattr(app, "pending_prompt", None):
                        app._close_prompt()
                    app.query_one("#prompt").display = False
                    app.pending_prompt = None
                    app.query_one("#actions").focus()
                    await pilot.pause()
                    await pilot.pause(0.3)

                for act in _letter_actions(cls(self._settings, owner=m.SUB,
                                               **_SCREEN_EXTRAS.get(cls.__name__, {}))):
                    app.screen_model = cls(self._settings, owner=m.SUB,
                                           **_SCREEN_EXTRAS.get(cls.__name__, {}))
                    app.render_model()
                    await clean()
                    before = len(dispatched)
                    await h.press(act.key)
                    lower = dispatched[before:]
                    app.screen_model = cls(self._settings, owner=m.SUB,
                                           **_SCREEN_EXTRAS.get(cls.__name__, {}))
                    app.render_model()
                    await clean()
                    before = len(dispatched)
                    await h.press(act.key.upper())
                    upper = dispatched[before:]
                    self.assertEqual(lower, upper,
                                     f"{cls.__name__} key {act.key!r}: lower={lower} upper={upper}")
            finally:
                app.dispatch = orig_dispatch
        self.run_picker(scenario, start="home")


def _make_per_screen_test(cls):
    def test(self):
        self._check_screen(cls)
    test.__name__ = f"test_shift_letter_on_{cls.__name__}"
    test.__doc__ = f"shift+letter on {cls.__name__} dispatches like the letter (0.56.15 per-screen split)"
    return test


for _cls in _screen_subclasses():
    if _cls is m.HomeScreen:
        continue          # root: Quit on 'q', no Back
    setattr(ShiftLetterTests, f"test_shift_letter_on_{_cls.__name__}", _make_per_screen_test(_cls))

    def test_uppercase_still_types_into_search(self):
        async def scenario(app, pilot, h):
            sm = app.screen_model
            self.assertIsInstance(sm, m.RemoteScreen)
            sm._toggle_choose_model()
            app.render_model()
            await h.wait_for(lambda: bool(sm.agents), "roster", secs=10)
            rows_w = app.query_one("#rows")
            rows_w.focus()
            await h.wait_for(lambda: app.focused is rows_w, "rows focused", secs=8)
            dispatched = []
            orig_dispatch = app.dispatch
            app.dispatch = lambda r, keep=None: dispatched.append(repr(r)) or orig_dispatch(r, keep)
            await h.press("J")                  # no action key 'j' on the remote screen
            search = app.query_one("#search")
            self.assertEqual(search.value, "J", f"uppercase must type into the filter: {search.value!r}")
            self.assertEqual(dispatched, [], f"no action may fire for an in-search key: {dispatched}")
            self.assertEqual(h.screen(), "RemoteScreen")

        self.run_picker(scenario, start="remote")

    def test_uppercase_in_prompt_inputs_types(self):
        async def scenario(app, pilot, h):
            sm = app.screen_model
            self.assertIsInstance(sm, m.LowkeyScreen)
            await h.press("s")                     # Set session name -> prompt opens
            await h.wait_for(lambda: app.query_one("#prompt").display, "prompt open", secs=8)
            for ch in ("A", "B", "C"):
                await h.press(ch)
            prompt = app.query_one("#prompt")
            self.assertEqual(prompt.value, "ABC", f"uppercase must type into the prompt: {prompt.value!r}")

        self.run_picker(scenario, start="lowkey")

    def test_shift_does_not_bypass_confirm_panel(self):
        async def scenario(app, pilot, h):
            # open a ConfirmPanel (missing-models cleanup offers one when the list has red rows);
            # simpler deterministic panel: the one-shot prompt is NOT a panel — use the register
            # offer via a missing model is complex, so drive ConfirmPanel directly:
            from session_picker import ConfirmPanel
            app.push_screen(ConfirmPanel("Sure?", "detail", [("yes", "Yes"), ("no", "No")]))
            await h.wait_for(lambda: isinstance(app.screen, ConfirmPanel), "panel open", secs=8)
            for ch in ("B", "Q", "Y"):
                await h.press(ch)                  # panel owns every key: no nav, no quit
            self.assertIsInstance(app.screen, ConfirmPanel, "panel must still own the keys")

        self.run_picker(scenario, start="home")


if __name__ == "__main__":
    unittest.main()

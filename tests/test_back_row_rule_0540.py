#!/usr/bin/env python3
"""0.54.0 Task 6 (D1) — Back / Quit is the LAST item of the ACTIONS PANE on every screen.

D1 (user, answered 2026-11-04): Back/Back to Home is always the last item of the actions pane —
never an accordion/list row, never a separate bottom pane. The pane's POSITION is per screen
type and unchanged: above the list on Remote/Local/Lowkey, below the accordion on Backend
manager and API keys. The 0.53.1 API-keys implementation (HOME_ROW_GROUP accordion row + nav
re-sectioned to "hidden") violated the rule and is removed.

These tests drive the RENDERED DOM (not the model's action list alone — 0.49.1/0.51.0/0.52.0/
0.53.1 each passed model-level checks while the rendered position was wrong): every Screen
subclass is built by introspection (count asserted == the enumerated subclasses), rendered at
100x24, and the Back/Quit option must be inside #actions and its last entry; no accordion row
may carry a Back label; on the model-list screens the pane is visible at first paint with a
200-row list; on backend-manager/api-keys the pane sits below the accordion.
"""
import os
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
    "AboutScreen": {},
    "HomeScreen": {},
    "LocalScreen": {"models": [m.LocalModel(alias=f"model-{i}", family="q") for i in range(200)]},
    "RemoteScreen": {"agents": [m.RemoteAgent(f"nvidia-{i}", "nvidia", f"Model {i}", "unknown",
                                               model_id=f"vendor/{i}") for i in range(200)]},
    "RemoteFiltersScreen": {},
    "CloudConfigScreen": {},
    "ThemeConfigScreen": {},
    "RateLimiterScreen": {},
    "MemoryGuardScreen": {},
    "VersionsScreen": {"op": "info", "entries": []},
    "RuntimeManagerScreen": {},
    "APIKeysScreen": {},
    "RuntimeScreen": {},
    "LaunchdScreen": {},
    "LowkeyScreen": {"models": [m.LocalModel(alias=f"model-{i}", family="q") for i in range(200)],
                     "api_agents": [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown",
                                                  model_id="vendor/a")]},
    "DownloadScreen": {"entries": [], "free_gb": 100.0, "headroom_gb": 10.0},
}


def _screen_subclasses():
    out = []
    for name in dir(m):
        obj = getattr(m, name)
        if isinstance(obj, type) and issubclass(obj, m.Screen) and obj is not m.Screen \
                and obj.__module__ == m.__name__:
            out.append(obj)
    return sorted(out, key=lambda c: c.__name__)


@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class BackRowRuleTests(PilotCase):
    def _all_screens(self, app):
        """Build every Screen subclass and render each on the live app; return (name, app-state)."""
        built = []
        for cls in _screen_subclasses():
            extras = dict(_SCREEN_EXTRAS.get(cls.__name__, {}))
            screen = cls(self._settings, **extras)     # construction failure FAILS the test
            built.append((cls.__name__, screen))
        return built

    def setUp(self):
        super().setUp()
        self._settings = m.Settings()
        import shutil
        for name in ("local-theme.json", "remote-theme.json"):
            shutil.copy(ROOT / "config" / name, Path(self.env["LA_THEME_CONFIG_DIR"]) / name)

    def test_back_is_last_item_of_actions_pane_on_every_screen(self):
        subclasses = _screen_subclasses()
        self.assertGreater(len(subclasses), 5)

        async def scenario(app, pilot, h):
            from textual.widgets import OptionList
            built = self._all_screens(app)
            self.assertEqual(len(built), len(subclasses))
            for name, screen in built:
                app.screen_model = screen
                if hasattr(screen, "_choose_model_visible"):
                    screen._choose_model_visible = True     # 200-row list open
                app.render_model()
                await pilot.pause()
                actions = app.query_one("#actions", OptionList)
                self.assertGreater(actions.option_count, 0, f"{name}: empty actions pane")
                last = actions.get_option_at_index(actions.option_count - 1)
                prompt = str(last.prompt)
                self.assertTrue("Back" in prompt or "Quit" in prompt,
                                f"{name}: last actions-pane item is {prompt!r}, not Back/Quit")
                # the whole nav section is the tail: every nav action after the last non-nav
                rendered = m.rendered_actions(screen)[0]
                nav = [a for a in rendered if a.section == "nav"]
                self.assertTrue(nav, f"{name}: no nav action")
                tail = rendered[-len(nav):]
                self.assertTrue(all(a.section == "nav" for a in tail),
                                f"{name}: nav is not the pane tail: {[a.section for a in rendered]}")
                # no accordion row carries a Back label
                acc = getattr(screen, "accordion", None)
                if acc is not None:
                    for g in acc.groups:
                        self.assertNotIn("Back", g.label, f"{name}: accordion row {g.label!r} is a Back row")
                        self.assertNotEqual(g.id, m.HOME_ROW_GROUP, f"{name}: HOME_ROW_GROUP still created")

        self.run_picker(scenario, start="home")

    def test_back_visible_without_scrolling_on_model_list_screens(self):
        async def scenario(app, pilot, h):
            from textual.widgets import OptionList
            for cls_name in ("RemoteScreen", "LocalScreen", "LowkeyScreen"):
                cls = getattr(m, cls_name)
                screen = cls(self._settings, **_SCREEN_EXTRAS[cls_name])
                screen._choose_model_visible = True
                app.screen_model = screen
                app.render_model()
                await pilot.pause()
                actions = app.query_one("#actions", OptionList)
                region = actions.region
                # first paint at 100x24: the pane (with Back as its last item) is in the viewport
                self.assertLess(region.y, 24, f"{cls_name}: actions pane below the fold at first paint")
                last = actions.get_option_at_index(actions.option_count - 1)
                self.assertTrue("Back" in str(last.prompt) or "Quit" in str(last.prompt))

        self.run_picker(scenario, start="home")

    def test_back_below_accordion_on_backend_manager(self):
        # 0.56.5 (user): APIKeysScreen no longer uses the actions pane — Back is the last
        # accordion row there with two blank rows above it (covered in test_0564_user_issues.py).
        async def scenario(app, pilot, h):
            from textual.widgets import OptionList
            for cls_name in ("RuntimeManagerScreen",):
                cls = getattr(m, cls_name)
                screen = cls(self._settings, **_SCREEN_EXTRAS[cls_name])
                app.screen_model = screen
                app.render_model()
                await pilot.pause()
                layout = app.query_one("#layout")
                actions = app.query_one("#actions", OptionList)
                rows = app.query_one("#rows", OptionList)
                order = [c.id for c in layout.children]
                self.assertGreater(order.index(actions.id), order.index(rows.id),
                                   f"{cls_name}: actions pane must be BELOW the accordion rows")
                last = actions.get_option_at_index(actions.option_count - 1)
                self.assertTrue("Back" in str(last.prompt) or "Quit" in str(last.prompt),
                                f"{cls_name}: last pane item {last.prompt!r}")

        self.run_picker(scenario, start="home")

    def test_b_key_still_navigates_back_on_every_screen(self):
        for cls in _screen_subclasses():
            if cls is m.HomeScreen:
                continue      # Home is the navigation root: it owns Quit ('q'), Back is meaningless there
            screen = cls(m.Settings(), owner=m.SUB, **_SCREEN_EXTRAS.get(cls.__name__, {}))
            result = screen.handle_key("b")
            self.assertIsNotNone(result, f"{cls.__name__}: 'b' did nothing")
            is_back = result == m.BACK or (isinstance(result, m.Nav) and result.target in ("home", "remote", "local", "runtime", "api_keys"))
            self.assertTrue(is_back, f"{cls.__name__}: 'b' returned {result!r}")


if __name__ == "__main__":
    unittest.main()

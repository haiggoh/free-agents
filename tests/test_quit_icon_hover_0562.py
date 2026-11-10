#!/usr/bin/env python3
"""tests/test_quit_icon_hover_0562.py — QC coverage for the quit-icon hover swap
(branch fix/csl-tui-quit-icon-apikeys-order). An out-of-family review (2026-11-10,
GLM 5.3 Flash via free-agent-tool validator) found the swap/restore had zero executable
coverage: stubbing _swap_quit_icon kept the whole suite green. These tests drive the
real mouse path over the #actions pane and assert on the RENDERED option prompts.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emoji_constants as ec  # noqa: E402
from picker_harness import PilotCase  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive


class QuitIconHoverPilot(PilotCase):
    def test_hover_on_actions_pane_swaps_quit_icon_then_restores(self):
        async def s(app, pilot, h):
            from textual.widgets import OptionList
            ol = app.query_one("#actions", OptionList)
            qidx = next(i for i, a in enumerate(app.action_ids) if a.key == "q")
            opt_i = next(j for j in range(ol.option_count)
                         if ol.get_option_at_index(j).id == f"a:{qidx}")
            await h.pilot.hover("#actions", offset=(2, opt_i))
            await h.pilot.pause(0.6)
            self.assertTrue(app.is_running, "app must survive hovering the #actions pane")
            prompt = str(ol.get_option_at_index(opt_i).prompt)
            self.assertIn(ec.EMOJI_QUIT_HOVER_STR, prompt,
                          "hovered Quit row must render the hover glyph")
            self.assertNotIn(ec.EMOJI_QUIT_STR, prompt)
            other_i = next(j for j in range(ol.option_count) if j != opt_i)
            await h.pilot.hover("#actions", offset=(2, other_i))
            await h.pilot.pause(0.6)
            self.assertTrue(app.is_running)
            restored = str(ol.get_option_at_index(opt_i).prompt)
            self.assertIn(ec.EMOJI_QUIT_STR, restored,
                          "Quit row must be restored when the hover moves away")
            self.assertNotIn(ec.EMOJI_QUIT_HOVER_STR, restored)
            await h.pilot.hover("#title")
            await h.pilot.pause(0.4)
            self.assertTrue(app.is_running, "leaving the list must not crash the app")
        self.run_picker(s, start="home")


class RemoteRenderedOrderPilot(PilotCase):
    def test_rendered_actions_pane_has_api_keys_before_filter_settings(self):
        async def s(app, pilot, h):
            await h.wait_for(lambda: not getattr(app, "_loading", set()), "startup load", secs=20)
            await h.press("r")
            await h.wait_screen("RemoteScreen")
            await h.expand_settings()   # k/n/y live inside the folded Model settings group
            labels = h.labels("actions")
            ki = next(i for i, lab in enumerate(labels) if "API keys" in lab)
            yi = next(i for i, lab in enumerate(labels) if "Filter settings" in lab)
            self.assertLess(ki, yi, "rendered menu must show API keys before Filter settings")
        self.run_picker(s, start="home")


if __name__ == "__main__":
    unittest.main()

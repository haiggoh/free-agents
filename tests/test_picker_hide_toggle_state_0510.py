"""tests/test_picker_hide_toggle_state_0510.py — 0.51.0 item A: the API-keys hide/show toggle
must reveal its CURRENT state in the row label and confirm visibly that the new state was
saved (user 2026-11-03: "the toggle doesn't reveal its state and doesn't confirm visibly that
the new state is saved")."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import PilotCase  # noqa: E402


class HideToggleState(PilotCase):
    async def _open_keys(self, h):
        await h.press("k")
        await h.wait_screen("APIKeysScreen")

    def test_toggle_label_reveals_state_and_save_is_confirmed(self):
        async def s(app, pilot, h):
            notes = []
            app.notify = lambda *a, **k: notes.append(str(a[0]) if a else "")
            await self._open_keys(h)
            rows = h.labels("rows")
            self.assertIn("Hide nvidia in the choose-model list (currently shown)", rows[3])
            await h.goto_row("item", "hide:nvidia")
            await h.press("enter")
            await h.pilot.pause(0.4)
            rows = h.labels("rows")
            # the row now states the CURRENT state, not just the action to perform
            self.assertIn("Show nvidia in the choose-model list (currently hidden)", rows[3])
            # persisted through the save callback into session-menu.local.json
            self.assertIn("nvidia", (self.saved_state().get("hidden_providers") or []),
                          f"hidden_providers not saved: {self.saved_state()}")
            # and the save got a VISIBLE confirmation, not a silent rebuild
            self.assertTrue(any("Saved" in n and "nvidia" in n and "hidden" in n for n in notes),
                            f"no save-confirmation notification, got {notes}")
        self.run_picker(s)

    def test_toggle_back_restores_shown_state(self):
        async def s(app, pilot, h):
            await self._open_keys(h)
            await h.goto_row("item", "hide:nvidia")
            await h.press("enter")
            await h.pilot.pause(0.3)
            await h.goto_row("item", "show:nvidia")
            await h.press("enter")
            await h.pilot.pause(0.3)
            rows = h.labels("rows")
            self.assertIn("Hide nvidia in the choose-model list (currently shown)", rows[3])
            self.assertNotIn("nvidia", (self.saved_state().get("hidden_providers") or []))
        self.run_picker(s)


class BackendChooserOrder(PilotCase):
    """0.51.0 item D: on the backend manager the "Choose backend" chooser renders AHEAD of all
    menu options, and the selection marker says "selected", never "current" (the menu is about
    which backend to modify, not which is in use)."""

    def test_chooser_first_and_marker_says_selected(self):
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            ids = [w.id for w in app.query_one("#layout").children]
            self.assertIn("rows", ids)
            self.assertIn("actions", ids)
            self.assertLess(ids.index("rows"), ids.index("actions"),
                            f"chooser rows must render ahead of the actions pane, got {ids}")
            await h.goto_row("group", "backends")
            await h.press("right")
            text = h.text()
            self.assertIn("← selected", text)
            self.assertNotIn("← current", text)
        self.run_picker(s)


if __name__ == "__main__":
    unittest.main()

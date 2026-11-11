#!/usr/bin/env python3
"""tests/test_cycle_ui_0554.py — 0.55.4 csl feedback round (user 2026-11-07):

- slim ❮ ❯ cycle glyphs (not ◀ ▶), and the value always CENTERED between them
- per-row value column: a row's cell is only as wide as its own widest value label
  (an ON/OFF toggle ≈ 3 chars + one space each side), not the global widest value
- the ❯ column must not move while a row cycles through different-width values
- mouse hover reaches the actions rows again (0.55.2 had painted the row-hover
  invisible, so most menus showed no hover reaction at all); glyph hover uses the
  TUI's native soft-blue family
- the copy toast renders ONE line (one border pipe) and sits clear of the info box
- the backend manager's upgrade toggles (0.55.3) render as ❮ ON/OFF ❯ rows and persist

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_cycle_ui_0554.py
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import PilotCase  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive


def _action_option(h, key):
    """(option_index_in_list, plain prompt text) for the action row with this shortcut key."""
    from textual.widgets import OptionList
    ol = h.app.query_one("#actions", OptionList)
    idx = next(i for i, a in enumerate(h.app.action_ids) if a.key == key)
    opt_i = next(i for i in range(ol.option_count)
                 if ol.get_option_at_index(i).id == f"a:{idx}")
    return opt_i, str(ol.get_option_at_index(opt_i).prompt)


def _between(text):
    """The value cell between the two cycle glyphs, as rendered (plain text)."""
    return text[text.rindex("❮") + 1:text.rindex("❯")]


def _cell_parts(text):
    seg = _between(text)
    left = len(seg) - len(seg.lstrip(" "))
    return left, seg.strip(), len(seg) - len(seg.rstrip(" "))


class CycleGlyphs(PilotCase):
    def _local_with_settings(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
        return s

    def test_slim_glyphs_flank_a_centered_value(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            for key in ("e", "o"):      # non-binary cycle rows keep ❮…❯ (j is a knob toggle)
                text = _action_option(h, key)[1]
                self.assertIn("❮", text, f"row {key} lost its left glyph: {text!r}")
                self.assertIn("❯", text, f"row {key} lost its right glyph: {text!r}")
                self.assertNotIn("◀", text, f"row {key} still uses the old glyph: {text!r}")
                self.assertNotIn("▶", text, f"row {key} still uses the old glyph: {text!r}")
                left, val, right = _cell_parts(text)
                self.assertGreaterEqual(left, 1, f"row {key}: no space left of the value: {text!r}")
                self.assertGreaterEqual(right, 1, f"row {key}: no space right of the value: {text!r}")
                self.assertLessEqual(right, left + 1,
                                     f"row {key}: value not centered between the glyphs: {text!r}")
            from picker_harness import action_by_key, assert_glyphs_for
            for key in ("j", "t", "p", "d", "w", "m"):   # binary toggles render the knob, no ❮❯
                assert_glyphs_for(self, _action_option(h, key)[1], action_by_key(h.app, key))
        self.run_picker(s, start="local")

    def test_toggle_cells_sized_to_their_own_values_not_the_global_max(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            # 0.56.x knob: the ❮❯ cell is gone — the value chip is followed by the
            # KNOB_CELLS-wide knob track (plus the knob glyph itself).
            from rich.cells import cell_len
            import emoji_constants as ec
            from picker_harness import action_by_key, assert_glyphs_for
            for key in ("t", "p", "d", "w", "m"):
                text = _action_option(h, key)[1]
                assert_glyphs_for(self, text, action_by_key(h.app, key))
                tail = text[-ec.KNOB_CELLS:]
                self.assertEqual(cell_len(tail), ec.KNOB_CELLS, f"knob track width wrong: {text!r}")
                self.assertTrue("●" in tail or "○" in tail, f"knob glyph missing: {text!r}")
        self.run_picker(s, start="local")

    def test_value_column_fixed_within_row_while_cycling(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            from textual.widgets import OptionList
            ol = h.app.query_one("#actions", OptionList)
            # Temperature: 'not set' (7) vs '0.0' (3) — the widest label in the row's own set.
            idx = next(i for i, a in enumerate(app.action_ids) if a.key == "o")
            ol.highlighted = next(j for j in range(ol.option_count)
                                  if ol.get_option_at_index(j).id == f"a:{idx}")
            t0 = _action_option(h, "o")[1]
            self.assertEqual(len(_between(t0)), len("not set") + 2, t0)
            await h.press("right")
            t1 = _action_option(h, "o")[1]
            self.assertNotEqual(t0, t1, "right must step the temperature row")
            self.assertEqual(t0.rindex("❯"), t1.rindex("❯"),
                             "❯ column must not move when the value width changes")
            left1, _, right1 = _cell_parts(t1)
            self.assertLessEqual(right1, left1 + 1, f"value not centered after stepping: {t1!r}")
            self.assertGreaterEqual(left1, 1, f"no space left of the value after stepping: {t1!r}")
            self.assertGreaterEqual(right1, 1, f"no space right of the value after stepping: {t1!r}")
        self.run_picker(s, start="local")


class HoverBehaviour(PilotCase):
    def test_row_hover_reaches_actions_rows_again(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            from textual.widgets import OptionList
            ol = h.app.query_one("#actions", OptionList)
            opt_i, text = _action_option(h, "e")
            # Hover the label, far from both glyphs: the row must actually respond.
            await h.pilot.hover("#actions", offset=(1 + text.index("Effort") + 2, opt_i))
            await h.pilot.pause(0.4)
            self.assertIsNotNone(getattr(ol, "_mouse_hovering_over", None),
                                 "the actions list must track the mouse over a row label")
            # 0.55.2 painted this hover invisible (color == background); the override is gone.
            self.assertNotIn("#actions > .option-list--option-hover", app.CSS,
                             "the invisible actions-row hover override must be gone")
            self.assertIn("OptionList > .option-list--option-hover { background: ansi_default; "
                          "color: ansi_bright_blue; }", app.CSS,
                          "the native soft-blue hover rule must cover the actions rows too")
        self.run_picker(s, start="local")

    def test_glyph_hover_on_emoji_rows_uses_display_columns(self):
        # The Watcher row's 👀 emoji is ONE character but TWO columns: glyph columns must be
        # measured with display widths (rich cell_len), not character indexes.
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            from rich.cells import cell_len
            from textual.widgets import OptionList
            ol = h.app.query_one("#actions", OptionList)
            idx = next(i for i, a in enumerate(app.action_ids) if a.key == "e")   # non-binary row (w is a knob toggle now)
            opt_i = next(j for j in range(ol.option_count)
                         if ol.get_option_at_index(j).id == f"a:{idx}")
            oid = ol.get_option_at_index(opt_i).id
            text = str(ol.get_option_at_index(opt_i).prompt)
            col = 1 + cell_len(text[:text.rindex("❯")])     # widget offset incl. 1-col padding
            await h.pilot.hover("#actions", offset=(col, opt_i))
            await h.pilot.pause(0.4)
            self.assertEqual(getattr(app, "_arrow_hover", None), (oid, +1),
                             "hovering the ❯ glyph at its DISPLAY column must mark it")
            await h.pilot.hover("#actions", offset=(1 + text.index("Effort") + 2, opt_i))
            await h.pilot.pause(0.4)
            self.assertIsNone(getattr(app, "_arrow_hover", None),
                              "hovering the row label must not highlight any glyph")
        self.run_picker(s, start="local")


class CopyToast(PilotCase):
    def test_toast_is_one_line_and_clear_of_the_bottom(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            app.copy_to_clipboard = lambda t: True   # geometry under test, not the clipboard
            app.copy_and_indicate("x" * 13)
            await h.pilot.pause(0.5)
            from textual.widgets._toast import Toast
            toast = app.query_one(Toast)
            self.assertEqual(toast.size.height, 1,
                             f"toast must be ONE line (one border pipe), got {toast.size.height}")
            await h.pilot.pause(0.3)                      # let the toast's layout settle
            bottom = toast.region.y + toast.region.height
            self.assertEqual(bottom, app.size.height - 3,
                             f"toast must sit 3 rows above the bottom edge (clear of the "
                             f"info box), got bottom={bottom} of {app.size.height}")
        self.run_picker(s, start="local", notifications=True)


class BackendUpgradeToggles(PilotCase):
    def setUp(self):
        super().setUp()
        # Never the user's real backend-upgrades.json (conftest tripwire): the model resolves
        # LA_BACKEND_UPGRADES_STATE first. Set it on os.environ DIRECTLY — PilotCase.setUp
        # already pushed self.env into os.environ, so adding to self.env now would be inert.
        self.bu_state = Path(self.cfg) / "backend-upgrades.json"
        self.env["LA_BACKEND_UPGRADES_STATE"] = str(self.bu_state)
        os.environ["LA_BACKEND_UPGRADES_STATE"] = str(self.bu_state)
        self.addCleanup(os.environ.pop, "LA_BACKEND_UPGRADES_STATE", None)

    def test_toggles_render_sized_and_persist(self):
        async def s(app, pilot, h):
            await h.press("v")
            await h.wait_screen("RuntimeManagerScreen")
            from picker_harness import action_by_key, assert_glyphs_for
            n_text = _action_option(h, "n")[1]
            self.assertIn("Notify about backend upgrades at launch", n_text)
            assert_glyphs_for(self, n_text, action_by_key(h.app, "n"))
            a_text = _action_option(h, "a")[1]
            self.assertIn("Install backend upgrades automatically in the background", a_text)
            assert_glyphs_for(self, a_text, action_by_key(h.app, "a"))
            # Pressing the row's shortcut steps it (Enter/click == +1 for setting rows).
            await h.press("n")
            await h.pilot.pause(0.3)
            state = json.loads(self.bu_state.read_text()) if self.bu_state.exists() else {}
            self.assertIs(state.get("notify"), False, f"toggle 'n' must persist notify=off: {state}")
            n_after = _action_option(h, "n")[1]
            self.assertIn("OFF", n_after, f"row must re-render OFF: {n_after!r}")
            assert_glyphs_for(self, n_after, action_by_key(h.app, "n"))
        self.run_picker(s)


if __name__ == "__main__":
    unittest.main()

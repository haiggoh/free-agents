#!/usr/bin/env python3
"""tests/test_cycle_indicators_0555.py — cycle-indicator plan (2026-11-04 user request):

Every cycleable action row in the csl Textual picker shows ❮ … ❯ indicators (0.55.4: slim
brackets, centered values, per-row columns); clicking ❮ behaves exactly like the Left key,
❯ like Right, and clicking the row label like Enter (for most rows Enter == step +1 ==
Right; the theme hue rows split it: Enter/click = ±20°, arrows/indicators = ∓5° — model
already carries that split, the TUI routing must honor it).

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_cycle_indicators_0555.py
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import ROOT, PilotCase  # noqa: E402
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


async def click_at_text(h, key, needle):
    """Real mouse click on the first occurrence of `needle` inside action row `key`'s label.
    The #actions list has padding 0 1 and no border, so widget x = 1 + DISPLAY column
    (0.55.4: display widths via rich.cell_len — emoji prefixes are one char, two cols)."""
    from rich.cells import cell_len
    opt_i, text = _action_option(h, key)
    col = 1 + cell_len(text[:text.index(needle)])
    await h.pilot.click("#actions", offset=(col, opt_i))
    await h.pilot.pause(0.3)


def row_label(h, key):
    return _action_option(h, key)[1]


class CycleIndicators(PilotCase):
    def seed(self):
        import shutil
        for name in ("local-theme.json", "remote-theme.json"):
            shutil.copy(ROOT / "config" / name, self.themes / name)

    def test_cycle_rows_show_indicators_plain_rows_do_not(self):
        async def s(app, pilot, h):
            await h.press("l")                       # local lane
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            from textual.widgets import OptionList
            ol = h.app.query_one("#actions", OptionList)
            for i, a in enumerate(app.action_ids):
                oid = f"a:{i}"
                opt_i = next(j for j in range(ol.option_count)
                             if ol.get_option_at_index(j).id == oid)
                text = str(ol.get_option_at_index(opt_i).prompt)
                if a.step is not None:
                    self.assertIn("❮", text, f"cycle row {a.key} missing ❮: {text!r}")
                    self.assertIn("❯", text, f"cycle row {a.key} missing ❯: {text!r}")
                    # arrows flank the VALUE, not the whole row (user 2026-11-06, nicegui parity):
                    # ❮ comes after the last ': ' and ❯ after the value
                    if ": " in text:
                        self.assertGreater(text.index("❮"), text.rindex(": "),
                                           f"❮ must sit before the value, not the row: {text!r}")
                    self.assertLess(text.index("❮"), text.rindex("❯"))
                else:
                    self.assertNotIn("❮", text, f"non-cycle row {a.key} gained ❮: {text!r}")
                    self.assertNotIn("❯", text, f"non-cycle row {a.key} gained ❯: {text!r}")
        self.run_picker(s, start="local")

    def test_indicator_clicks_step_like_arrow_keys(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            label0 = row_label(h, "e")               # Effort row
            await click_at_text(h, "e", "❯")
            label1 = row_label(h, "e")
            self.assertNotEqual(label0, label1, "clicking ❯ must step forward")
            await h.press("left")                    # left arrow == ❮
            self.assertEqual(row_label(h, "e"), label0, "❯ then Left must return to the start value")
            await click_at_text(h, "e", "❮")
            label2 = row_label(h, "e")
            self.assertNotEqual(label0, label2, "clicking ❮ must step back")
            await h.press("right")                   # right arrow == ❯
            self.assertEqual(row_label(h, "e"), label0, "❮ then Right must return to the start value")
            # the spaces around the glyph are part of the arrow (user 2026-11-06): clicking
            # the space just left of ❮ must step back too
            from rich.cells import cell_len
            from textual.widgets import OptionList
            ol = h.app.query_one("#actions", OptionList)
            idx = next(i for i, a in enumerate(h.app.action_ids) if a.key == "e")
            opt_i = next(j for j in range(ol.option_count)
                         if ol.get_option_at_index(j).id == f"a:{idx}")
            text = str(ol.get_option_at_index(opt_i).prompt)
            space_col = 1 + cell_len(text[:text.index("❮")]) - 1
            await h.pilot.click("#actions", offset=(space_col, opt_i))
            await h.pilot.pause(0.3)
            self.assertNotEqual(row_label(h, "e"), label0, "clicking the space before ❮ must step back")
            await h.press("right")
            self.assertEqual(row_label(h, "e"), label0)
            # ❯ stays at a fixed column while the value changes width (no jumping)
            await click_at_text(h, "e", "❯")
            text_after = row_label(h, "e")
            self.assertEqual(text.rindex("❯"), text_after.rindex("❯"),
                             "❯ column must not move when the value width changes")
        self.run_picker(s, start="local")

    def test_arrow_hover_highlights_only_the_glyph(self):
        async def s(app, pilot, h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
            from textual.widgets import OptionList
            ol = h.app.query_one("#actions", OptionList)
            idx = next(i for i, a in enumerate(app.action_ids) if a.key == "e")
            opt_i = next(j for j in range(ol.option_count)
                         if ol.get_option_at_index(j).id == f"a:{idx}")
            oid = ol.get_option_at_index(opt_i).id
            text = str(ol.get_option_at_index(opt_i).prompt)
            # hover the ❮ glyph (incl. its surrounding space): hover state tracks the glyph
            from rich.cells import cell_len
            await h.pilot.hover("#actions", offset=(1 + cell_len(text[:text.index("❮")]), opt_i))
            await h.pilot.pause(0.4)
            self.assertEqual(getattr(app, "_arrow_hover", None), (oid, -1),
                             "hovering ❮ must mark only that glyph")
            # hover the middle of the row label: no glyph hover
            await h.pilot.hover("#actions", offset=(1 + text.index("Effort") + 2, opt_i))
            await h.pilot.pause(0.4)
            self.assertIsNone(getattr(app, "_arrow_hover", None),
                              "hovering the row label must not highlight any arrow")
        self.run_picker(s, start="local")

    def test_row_click_is_enter_and_hue_split(self):
        # Theme screen: hue rows carry the split in the MODEL already (run=±20°, step=∓5°).
        # The TUI must route label click / Enter to run() (±20°) and ◀/▶ to step (∓5°).
        async def s(app, pilot, h):
            self.seed()
            await h.press("h")
            await h.wait_screen("ThemeConfigScreen")
            # highlight the hue row itself (the color preset row is highlighted first)
            from textual.widgets import OptionList
            ol = h.app.query_one("#actions", OptionList)
            idx = next(i for i, a in enumerate(app.action_ids) if a.key == "y")
            ol.highlighted = next(j for j in range(ol.option_count)
                                  if ol.get_option_at_index(j).id == f"a:{idx}")

            def deg():
                return int(re.search(r"(\d+)°", row_label(h, "y")).group(1))

            d0 = deg()
            # hue is re-read via an RGB round-trip, so allow ±1° quantization on every read
            def expect(target, what):
                self.assertAlmostEqual(deg(), target % 360, delta=1, msg=what)
            await h.press("right")                   # arrow key = +5
            expect(d0 + 5, "Right arrow must step +5°")
            await click_at_text(h, "y", "❯")         # ❯ indicator = Right = +5
            expect(d0 + 10, "clicking ❯ must step +5° like Right")
            await click_at_text(h, "y", "❮")         # ❮ indicator = Left = −5
            expect(d0 + 5, "clicking ❮ must step −5° like Left")
            await click_at_text(h, "y", "Hue")       # row label click = Enter = +20
            expect(d0 + 25, "clicking the row label must act like Enter (+20°)")
            await h.press("enter")                   # Enter on the hue row = +20 too
            expect(d0 + 45, "Enter on the hue row must be +20°, not +5°")
        self.run_picker(s)


if __name__ == "__main__":
    unittest.main()

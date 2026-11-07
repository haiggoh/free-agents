#!/usr/bin/env python3
"""tests/test_toggle_knob.py — binary toggles render as a sliding knob (nicegui switch parity).

- ON/OFF rows show "<name>: ON  ──●" / "<name>: OFF ○──" (thin track both ways): no ❮ ❯ glyphs, fixed-width value chip
- toggling animates: the knob passes through intermediate columns before resting at the new end
- multi-choice rows keep their ❮ value ❯ glyphs

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_toggle_knob.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import PilotCase  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive

TRACK = set("○●─━")


def _row(h, key):
    from textual.widgets import OptionList
    ol = h.app.query_one("#actions", OptionList)
    idx = next(i for i, a in enumerate(h.app.action_ids) if a.key == key)
    opt_i = next(i for i in range(ol.option_count) if ol.get_option_at_index(i).id == f"a:{idx}")
    return opt_i, str(ol.get_option_at_index(opt_i).prompt)


sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import emoji_constants as ec  # noqa: E402

N = ec.KNOB_CELLS
END = N - 1


def _knob(text):
    """The KNOB_CELLS-wide knob at the end of the row, and the knob's column inside it."""
    tail = text.rstrip()[-N:]
    assert set(tail) <= TRACK, f"no knob at the row end: {text!r}"
    pos = next(i for i, c in enumerate(tail) if c in "○●")
    return tail, pos


class ToggleKnob(PilotCase):
    def _open(self):
        async def go(h):
            await h.press("l")
            await h.wait_screen("LocalScreen")
            await h.expand_settings()
        return go

    def test_binary_rows_draw_a_knob_not_glyphs(self):
        async def s(app, pilot, h):
            await self._open()(h)
            for key in ("t", "p", "d", "w"):
                text = _row(h, key)[1]
                self.assertNotIn("❮", text, text)
                self.assertNotIn("❯", text, text)
                tail, pos = _knob(text)
                on = ": ON" in text
                self.assertEqual(pos, END if on else 0, f"row {key} knob at the wrong end: {text!r}")
                self.assertNotIn("━", tail, f"row {key}: the track must stay thin: {text!r}")
            # ENABLED/DISABLED rows: the knob follows the value word, not just "ON"
            mcp = _row(h, "m")[1]
            self.assertEqual(_knob(mcp)[1], END if ": ENABLED" in mcp else 0,
                             f"MCP row knob disagrees with its value: {mcp!r}")
            # a multi-choice row keeps the cycle glyphs
            self.assertIn("❮", _row(h, "e")[1])
        self.run_picker(s, start="local")

    def test_toggle_slides_through_intermediate_frames(self):
        async def s(app, pilot, h):
            await self._open()(h)
            _, before = _row(h, "t")
            start = _knob(before)[1]
            seen = []
            await pilot.press("t")
            end = time.time() + 0.6
            while time.time() < end:
                await pilot.pause(0.01)
                pos = _knob(_row(h, "t")[1])[1]
                if not seen or seen[-1] != pos:
                    seen.append(pos)
            final = _knob(_row(h, "t")[1])[1]
            self.assertEqual(final, END - start, f"knob did not land on the other end: {seen}")
            self.assertTrue(any(p not in (0, END) for p in seen),
                            f"no intermediate frame — it jumped instead of sliding: {seen}")
            self.assertNotEqual(before, _row(h, "t")[1], "value text must flip")
            # the thin-track rule must be checked on an ON row: OFF rows are thin either way,
            # so a check on OFF rows alone passed with a heavy ON track (mutation 2026-11-07)
            on_row = _row(h, "t")[1] if final == END else _row(h, "m")[1]
            self.assertEqual(_knob(on_row)[1], END, f"expected an ON row here: {on_row!r}")
            self.assertNotIn("━", _knob(on_row)[0], f"ON track must stay thin: {on_row!r}")
        self.run_picker(s, start="local")


if __name__ == "__main__":
    import unittest
    unittest.main()

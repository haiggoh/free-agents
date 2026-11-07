#!/usr/bin/env python3
"""tests/test_cycle_single_source_0556.py — 0.55.6 Task 1: one definition for the cycle
glyphs and their hover colour.

The glyphs live in bin/emoji_constants.py (CYCLE_LEFT/RIGHT); the hover colour must live
there too (ec.CYCLE_HOVER rich name, ec.CYCLE_HOVER_CSS derived Textual form) — the picker
source carries neither the colour nor the glyphs as typed literals, so a future change to
either updates every surface from one place (user 2026-11-07).

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_cycle_single_source_0556.py
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import ROOT  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive

PICKER = ROOT / "bin" / "session_picker.py"
MODEL = ROOT / "bin" / "session_picker_model.py"


def _code_lines(path: Path):
    """Source lines with comment tails (#...) stripped — glyph/colour literals may live in
    prose comments but never in code or CSS strings."""
    for line in path.read_text().splitlines():
        yield line.split("#", 1)[0]


class HoverColourSingleSource(unittest.TestCase):
    def test_hover_colour_has_one_definition(self):
        src = PICKER.read_text()
        self.assertNotIn("bright_blue", src,
                         "the hover colour must come from ec.CYCLE_HOVER, not a literal")
        self.assertNotIn("ansi_bright_blue", src,
                         "the CSS colour must come from ec.CYCLE_HOVER_CSS, not a literal")
        import emoji_constants as ec
        self.assertEqual(ec.CYCLE_HOVER_CSS, "ansi_" + ec.CYCLE_HOVER,
                         "the CSS form must be DERIVED from the rich name")
        import session_picker
        self.assertIn(f"color: {ec.CYCLE_HOVER_CSS};", session_picker.Picker.CSS,
                      "the hover rule must read the derived constant")

    def test_glyph_literals_only_in_emoji_constants(self):
        for path in (PICKER, MODEL):
            for n, code in enumerate(_code_lines(path), 1):
                for glyph in ("❮", "❯"):
                    self.assertNotIn(glyph, code,
                                     f"{path.name}:{n} carries a glyph literal: {code.strip()!r}")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""tests/test_local_action_emoji_0541.py — 0.55.8 Task 7 (0541 plan Item F):

Every Local-screen action row starts with an emoji (Unicode category So, or an emoji+VS16
pair) — the auto-sync row was the last one without one.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_local_action_emoji_0541.py
"""
import sys
import unicodedata
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import pytest  # noqa: E402
import session_picker_model as m  # noqa: E402

pytestmark = pytest.mark.load_sensitive


class LocalActionEmoji(unittest.TestCase):
    def test_every_local_action_row_starts_with_emoji(self):
        scr = m.LocalScreen(m.Settings(), [], owner=m.HOME_OWNED)
        acts = scr.actions()
        self.assertGreaterEqual(len(acts), 5, f"population guard: only {len(acts)} rows")

        def starts_with_emoji(label):
            first = label[0]
            if first == "️" or unicodedata.category(first) == "So":
                return True
            # an emoji base char + VS16 pair: the base may be category So already; accept
            # any char >= U+1F000 (emoji planes) with the next char VS16
            if len(label) > 1 and label[1] == "️" and ord(first) >= 0x1F000:
                return True
            return False

        # Choose model ('c') is EXEMPT by design (user 2026-11-09): the one bare row among
        # emoji rows stands out, and it is the most important option in each lane.
        bare = [(a.key, a.label) for a in acts
                if a.key != "c" and not starts_with_emoji(a.label)]
        self.assertEqual(bare, [],
                         "rows without a leading emoji:\n" +
                         "\n".join(f"  {k}: {l!r}" for k, l in bare))


if __name__ == "__main__":
    unittest.main()

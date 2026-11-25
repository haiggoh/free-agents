#!/usr/bin/env python3
"""tests/test_salvaged_docs.py — pins the R1 batch-D salvages (M2 dispatch findings).

The salvaged docs are content, not code: nothing else in the suite read them, so a deletion
or a wrong port stayed green. These tests pin exactly what the salvage delivered.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SalvagedDocsTests(unittest.TestCase):
    def test_fairness_plan_status_flip_is_present(self):
        # salvaged from branch docs/plan-rl-fairness-complete: 0.61.0 shipped the fairness
        # work, so the plan on main must say completed (one-line flip, nothing else changed)
        text = (ROOT / "docs" / "superpowers" / "plans"
                / "2026-11-20-rate-limiter-fairness.md").read_text(encoding="utf-8")
        self.assertIn("**Status:** completed", text)
        self.assertNotIn("**Status:** in-progress", text)

    def test_0556_handoff_carries_exactly_the_two_salvaged_receipts(self):
        # salvaged from branch docs/handoff-0556-receipts: exactly two receipt lines appended
        text = (ROOT / "docs" / "plans"
                / "2026-11-07-handoff-la-evict-orphans-and-dispatch-out.md").read_text(encoding="utf-8")
        shipped = [line for line in text.splitlines() if line.startswith("- 2026-11-07 ~22:14")]
        staged = [line for line in text.splitlines() if line.startswith("- 2026-11-07 ~22:10")]
        self.assertEqual(len(shipped), 1)
        self.assertEqual(len(staged), 1)
        self.assertIn("Phase 3 SHIPPED", shipped[0])
        self.assertIn("v0.55.6", shipped[0])
        self.assertIn("Phase 4 (read-only, staged)", staged[0])
        # the pre-existing receipts must still be there (port by content, not rewrite)
        self.assertIn("- 2026-11-07 ~21:45 — Phase 2:", text)


if __name__ == "__main__":
    unittest.main()

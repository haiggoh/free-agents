#!/usr/bin/env python3
"""bin/merge-settings.py must never let an overlay REMOVE a base denial (blind-trust's DESTRUCTIVE_DENY).
Before 0.27.2 every permissions key except `allow` was replaced by the overlay's, so any overlay carrying
its own `deny` list silently dropped `Bash(sudo:*)`, `git push --force`, `rm -rf ~` etc."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("ms", Path(__file__).resolve().parent.parent / "bin" / "merge-settings.py")
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)


class Deny(unittest.TestCase):
    BASE = {"permissions": {"defaultMode": "bypassPermissions", "allow": ["Read"], "deny": ["Bash(sudo:*)", "Bash(rm -rf ~:*)"]}}

    def test_overlay_deny_is_added_not_substituted(self):
        out = ms.merge_settings(self.BASE, {"permissions": {"deny": ["Bash(dangerous:*)"]}})["permissions"]
        self.assertEqual(out["deny"], ["Bash(sudo:*)", "Bash(rm -rf ~:*)", "Bash(dangerous:*)"])

    def test_overlay_without_deny_keeps_base(self):
        out = ms.merge_settings(self.BASE, {"permissions": {"allow": ["Edit"]}})["permissions"]
        self.assertEqual(out["deny"], self.BASE["permissions"]["deny"])
        self.assertEqual(out["allow"], ["Read", "Edit"])

    def test_empty_overlay_deny_cannot_clear(self):
        out = ms.merge_settings(self.BASE, {"permissions": {"deny": []}})["permissions"]
        self.assertIn("Bash(sudo:*)", out["deny"])

    def test_other_keys_still_overlay_wins(self):
        out = ms.merge_settings(self.BASE, {"permissions": {"defaultMode": "acceptEdits"}})["permissions"]
        self.assertEqual(out["defaultMode"], "acceptEdits")


if __name__ == "__main__":
    unittest.main()

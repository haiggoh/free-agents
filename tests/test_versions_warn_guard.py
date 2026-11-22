#!/usr/bin/env python3
"""tests/test_versions_warn_guard.py — the versions screen's keep-one-core guard must
return a warn: nav (never a removal prompt) when the only installed core version is
picked for removal; the GUI renders warn:* as a notice page (2026-11-22).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import session_picker_model as m  # noqa: E402


class VersionsWarnGuardTests(unittest.TestCase):
    def _screen(self) -> "m.VersionsScreen":
        s = m.Settings()
        s.runtime_backend = "rapid-mlx"
        scr = m.VersionsScreen(s, op="remove", entries=[])
        scr.accordion.selected_id = "1.2.3"
        return scr

    def test_only_core_version_refuses_with_warn_nav(self):
        res = self._screen().activate_selected()
        self.assertIsInstance(res, m.Nav)
        self.assertTrue(res.target.startswith("warn:"), res.target)
        self.assertIn("only installed", res.target)
        self.assertIn("rapid-mlx", res.target)

    def test_second_installed_version_still_prompts(self):
        # keep-one-core only guards the LAST core version: a second complete entry
        # takes the normal confirmation path
        s = m.Settings()
        s.runtime_backend = "rapid-mlx"
        other = m.VersionEntry(version="0.99.0", status="complete")
        scr = m.VersionsScreen(s, op="remove", entries=[other])
        scr.accordion.selected_id = "1.2.3"
        res = scr.activate_selected()
        self.assertIsInstance(res, m.Nav)
        self.assertEqual(res.target, "prompt:rt-remove")


if __name__ == "__main__":
    unittest.main()
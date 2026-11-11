#!/usr/bin/env python3
"""tests/test_0566_cyan_timeout.py — 0.56.6: subheadline cyan single source + L1 gate timeout.

1. The subheadline cyan (ansi_bright_cyan / bright_cyan) is defined ONCE in
   emoji_constants (SUBHEADLINE_CYAN + SUBHEADLINE_CYAN_CSS) and never typed as a literal
   in session_picker.py or the knob table.
2. L1: both gates run with pytest-timeout (--timeout=900 --timeout-method=thread) and the
   picker venv pins pytest-timeout in install/session-picker-requirements.txt.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import emoji_constants as ec  # noqa: E402


class CyanSingleSourceTests(unittest.TestCase):
    def test_constants_are_the_one_definition(self):
        self.assertEqual(ec.SUBHEADLINE_CYAN, "bright_cyan")
        self.assertEqual(ec.SUBHEADLINE_CYAN_CSS, "ansi_" + ec.SUBHEADLINE_CYAN)

    def test_no_literal_cyan_left_in_picker_sources(self):
        for name in ("bin/session_picker.py", "bin/session_picker_model.py", "bin/emoji_constants.py"):
            src = (ROOT / name).read_text()
            self.assertNotIn("ansi_bright_cyan", src, f"{name}: literal CSS token")
            if name != "bin/emoji_constants.py":   # the constant itself lives there
                self.assertNotIn('"bright_cyan"', src, f"{name}: literal rich style")
        # the CSS rule goes through the sentinel, replaced from the constant
        src = (ROOT / "bin/session_picker.py").read_text()
        self.assertIn("@SUBHEADLINE_CYAN@", src)
        self.assertIn('.replace("@SUBHEADLINE_CYAN@", ec.SUBHEADLINE_CYAN_CSS)', src)
        # the knob flash row uses the constant
        flash = [r for r in ec.KNOB_SEQUENCE if r[2] == "new"]
        self.assertTrue(any(r[3] == ec.SUBHEADLINE_CYAN for r in flash))


class GateTimeoutTests(unittest.TestCase):
    def test_main_gate_test_cmd_has_timeout(self):
        cfg = json.loads((ROOT / "rtc-ship.json").read_text())
        self.assertIn("--timeout=900", cfg["test_cmd"])
        self.assertIn("--timeout-method=thread", cfg["test_cmd"])

    def test_preflight_gates_have_timeout(self):
        src = (ROOT / ".rtc/pre-flight.sh").read_text()
        self.assertIn("--timeout=900", src)
        self.assertIn("--timeout-method=thread", src)

    def test_picker_venv_requirements_pin_pytest_timeout(self):
        req = (ROOT / "install/session-picker-requirements.txt").read_text()
        self.assertIn("pytest-timeout==", req)


if __name__ == "__main__":
    unittest.main()

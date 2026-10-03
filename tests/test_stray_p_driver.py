#!/usr/bin/env python3
"""tests/test_stray_p_driver.py — the Apple Terminal driver must reach the App INSTANCE.

Regression: 786d487 set `driver_class` as a class attribute, but Textual 3.7.1's App.__init__
does `self.driver_class = driver_class or self.get_driver_class()`, so every instance got the
stock LinuxDriver and the stray `p` stayed. These tests check the instance, and the bytes the
driver writes, under the picker venv. Real Apple Terminal confirmation is the user's.
"""
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = Path(os.environ.get("LA_PICKER_VENV", Path.home() / ".local/share/free-agents/picker-venv")) / "bin/python"

PROBE = r'''
import argparse, io, os, sys
sys.path.insert(0, sys.argv[1])
import session_picker as sp
app = sp.Picker("home", argparse.Namespace(exclude_trials=False, local_capable_shown=False))
drv = app.driver_class
print("DRIVER", drv.__name__)
# Bytes the driver writes for the two DECRQM queries (no terminal needed).
out = io.StringIO()
d = drv.__new__(drv)
d.write = out.write
d.input_tty = True          # the sync-mode query is only sent to a real tty
d._query_in_band_window_resize()
d._request_terminal_sync_mode_support()
print("BYTES", repr(out.getvalue()))
'''


@unittest.skipUnless(VENV_PY.exists(), "picker venv missing: run install/setup-session-picker.sh")
class StrayPDriver(unittest.TestCase):
    def probe(self, term_program):
        env = dict(os.environ, LA_SESSION_MENU_CONFIG_DIR=str(ROOT / "tests"))
        env.pop("TERM_PROGRAM", None)
        if term_program:
            env["TERM_PROGRAM"] = term_program
        r = subprocess.run([str(VENV_PY), "-c", PROBE, str(ROOT / "bin")], env=env,
                           text=True, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = dict(l.split(" ", 1) for l in r.stdout.splitlines() if " " in l)
        return lines["DRIVER"], lines["BYTES"]

    def test_apple_terminal_instance_gets_the_safe_driver_and_emits_no_decrqm(self):
        driver, data = self.probe("Apple_Terminal")
        self.assertEqual(driver, "AppleTerminalSafeDriver")
        self.assertNotIn("2048$p", data)
        self.assertNotIn("2026$p", data)

    def test_other_terminals_keep_the_stock_driver_and_both_queries(self):
        driver, data = self.probe("iTerm.app")
        self.assertEqual(driver, "LinuxDriver")
        self.assertIn("2048$p", data)
        self.assertIn("2026$p", data)


if __name__ == "__main__":
    unittest.main()

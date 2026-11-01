#!/usr/bin/env python3
"""tests/test_loadscale.py — the load->timeout mapping used by the PTY/litellm tests.

Usage: python3 tests/test_loadscale.py [-v]
"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loadscale  # noqa: E402

# Modules whose tests spawn real processes / drive the picker PTY harness and therefore
# flake under extreme shared-box load (the 2026-11-01 pty-litellm-venv flake). They must
# carry pytest.mark.load_sensitive so `-m "not load_sensitive"` can deselect them.
LOAD_SENSITIVE_MODULES = [
    "test_session_picker_pty.py",
    "test_picker_load_race.py",
    "test_new_features.py",
    "test_litellm_contract.py",
    "test_litellm_pipx_report.py",
    "test_litellm_trust_shim.py",
    "test_venv_durability.py",
    "test_venv_health.py",
    "test_venv_claims_bottle_backends.py",
]


class LoadScaleTests(unittest.TestCase):
    def test_mapping_clamps(self):
        self.assertEqual(loadscale.scale_for(0), 1.0)
        self.assertEqual(loadscale.scale_for(1), 1.0)
        self.assertEqual(loadscale.scale_for(2), 1.0)     # idle-machine numbers stay x1
        self.assertEqual(loadscale.scale_for(8), 4.0)
        self.assertEqual(loadscale.scale_for(16), 8.0)
        self.assertEqual(loadscale.scale_for(100), 8.0)   # clamped

    def test_load_avg1_is_a_float(self):
        la = loadscale.load_avg1()
        self.assertIsInstance(la, float)
        self.assertGreaterEqual(la, 0.0)

    def test_current_scale_sampled_per_call_and_in_range(self):
        s = loadscale.current_scale()
        self.assertGreaterEqual(s, 1.0)
        self.assertLessEqual(s, 8.0)
        self.assertEqual(s, loadscale.scale_for(loadscale.load_avg1()))


class LoadSensitiveMarkerTests(unittest.TestCase):
    """The load-sensitive modules must be marked, so a loaded gate can deselect them.

    RED-first contract (2026-11-01 flake waypoint): `pytest --markers` lists load_sensitive,
    every module in LOAD_SENSITIVE_MODULES collects items under -m load_sensitive and zero
    under -m "not load_sensitive". The "not" run also includes this (unmarked) file as a
    planted positive, proving the expression itself works.
    """

    HERE = Path(__file__).resolve().parent

    def _collect(self, *args):
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
            capture_output=True, text=True, cwd=self.HERE, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return r.stdout

    def test_marker_is_registered(self):
        r = subprocess.run([sys.executable, "-m", "pytest", "--markers"],
                           capture_output=True, text=True, cwd=self.HERE, timeout=120)
        self.assertIn("load_sensitive", r.stdout)

    def test_every_load_sensitive_module_carries_the_mark(self):
        out = self._collect("-m", "load_sensitive", *LOAD_SENSITIVE_MODULES)
        collected = {line.split("::")[0].split("/")[-1] for line in out.splitlines() if "::" in line}
        self.assertEqual(collected, set(LOAD_SENSITIVE_MODULES),
                         f"marked-collect mismatch; raw head: {out[:400]}")

    def test_not_load_sensitive_deselects_all_of_them(self):
        out = self._collect("-m", "not load_sensitive", *LOAD_SENSITIVE_MODULES, "test_loadscale.py")
        collected = {line.split("::")[0].split("/")[-1] for line in out.splitlines() if "::" in line}
        self.assertNotIn(set(LOAD_SENSITIVE_MODULES) & collected, collected)
        # planted positive: this unmarked module must still collect — otherwise the
        # "not" expression deselected everything and the assertion above is vacuous
        self.assertIn("test_loadscale.py", collected, out[:400])


if __name__ == "__main__":
    unittest.main()

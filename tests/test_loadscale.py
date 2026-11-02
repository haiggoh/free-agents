#!/usr/bin/env python3
"""tests/test_loadscale.py — the load->timeout mapping used by the PTY/litellm tests.

Usage: python3 tests/test_loadscale.py [-v]
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loadscale  # noqa: E402

# Modules whose tests spawn real processes / drive the picker PTY harness and therefore
# flake under extreme shared-box load (the 2026-11-01 pty-litellm-venv flake). They must
# carry pytest.mark.load_sensitive so `-m "not load_sensitive"` can deselect them.
# Marked tests ALSO get the limiter priority knobs by default (conftest
# _limiter_knobs_for_load_sensitive): LA_LIMITER_RESERVE=0.2 + LA_LIMITER_PRIORITY=1, so
# they draw from the reserved 20% of the rate-limiter bucket instead of flaking when a
# concurrent session fills it. See LimiterKnobDefaultTests below and rtc-ship.json
# "_comment_limiter_knobs" for manual/live use.
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


class LimiterKnobDefaultTests(unittest.TestCase):
    """0.50.4: tests marked `load_sensitive` get the rate-limiter priority knobs BY DEFAULT,
    so they don't flake when a concurrent session hammers the shared limiter.

    The knobs (bin/rate_limiter.py, shipped 0.50.1):
      LA_LIMITER_RESERVE=0.2  reserve 20% of the bucket for PRIORITY acquisitions; normal
                              traffic stops at 80% (limit*(1-reserve)). Default 0.0 = off.
      LA_LIMITER_PRIORITY=1   this process acquires with priority=True and draws from the reserve.
    Manual / live-session use (NOT needed for marked tests — conftest sets it):
      LA_LIMITER_RESERVE=0.2 in the env of the priority process, or
      python3 bin/rate_limiter.py set-owner <session-id> --priority [--rpm-scale 0.2]
      python3 bin/rate_limiter.py owners    # who is eating the bucket
      python3 bin/rate_limiter.py probe     # my bucket's external occupancy
    Also documented in rtc-ship.json "_comment_limiter_knobs".
    """

    @pytest.mark.load_sensitive
    def test_marked_test_gets_reserve_and_priority_by_default(self):
        self.assertEqual(os.environ.get("LA_LIMITER_RESERVE"), "0.2")
        self.assertEqual(os.environ.get("LA_LIMITER_PRIORITY"), "1")

    def test_unmarked_test_gets_neither_knob(self):
        self.assertNotIn("LA_LIMITER_RESERVE", os.environ)
        self.assertNotIn("LA_LIMITER_PRIORITY", os.environ)


if __name__ == "__main__":
    unittest.main()

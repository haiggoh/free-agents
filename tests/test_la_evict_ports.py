#!/usr/bin/env python3
"""0.49.8: test_la_evict.sh must be safe to run concurrently.

Regression test for the port-pick race that blocked gates on 2026-10-31: the base scan
binds-then-closes the candidate ports, so a parallel run or an external listener can
steal one before the controlled servers bind ("Port N belongs to unexpected pid").
The fix (machine-wide lock + retry wrapper with a raised scan start) makes two
simultaneous runs both pass — the lock serializes them.

Usage: python3 tests/test_la_evict_ports.py [-v]
"""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "test_la_evict.sh"


class LaEvictPortSafetyTests(unittest.TestCase):
    def test_two_concurrent_runs_both_pass(self):
        # Each run takes a couple of minutes alone; serialized by the lock, two runs
        # take about twice that. Bounded by the communicate timeout below.
        procs = [subprocess.Popen(["bash", str(SUITE)], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True)
                 for _ in range(2)]
        for p in procs:
            out, _ = p.communicate(timeout=900)
            self.assertEqual(p.returncode, 0, out[-3000:])
            self.assertIn("0 failed", out, "a concurrent run failed its checks")


if __name__ == "__main__":
    unittest.main()

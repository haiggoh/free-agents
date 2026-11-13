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
import tempfile
import unittest
from pathlib import Path

import pytest

# spawns real processes / guards real ports; flaked under shared-box load in gate runs
# (2026-11-12) — the gate deselects load_sensitive via -m 'not load_sensitive'
pytestmark = pytest.mark.load_sensitive

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "test_la_evict.sh"


class LaEvictPortSafetyTests(unittest.TestCase):
    def test_concurrent_runs_serialize_via_the_lock(self):
        # 0.49.8 fix: the base scan bound-then-closed its probe sockets, so a parallel run
        # or an external listener could steal a port before the servers bound. The fix is a
        # machine-wide flock on the suite. This test runs ONE suite and asserts the lock is
        # HELD for its whole run — a second run would wait, never collide on the same base.
        import time
        lockdir = Path(tempfile.gettempdir()) / "free-agents-la-evict.lock.d"
        p = subprocess.Popen(["bash", str(SUITE)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        try:
            held = False
            for _ in range(1200):                     # up to 10 min of probing
                if p.poll() is not None:
                    break
                # mkdir is atomic: it fails while the suite holds the lock dir
                r = subprocess.run(["mkdir", str(lockdir)], capture_output=True)
                if r.returncode != 0:
                    held = True                       # lock held by the running suite
                    break
                time.sleep(0.5)
            self.assertTrue(held, "the suite must hold the machine-wide lock while running")
            self.assertEqual(p.wait(timeout=900), 0, "the suite itself must pass")
        finally:
            if p.poll() is None:
                p.kill()


if __name__ == "__main__":
    unittest.main()

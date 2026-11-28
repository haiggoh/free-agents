#!/usr/bin/env python3
"""0.67.0 Task 3 — the alloc lock's dead-holder break is atomic (audit must-fix #2).

Two waiters, released by a barrier, race a planted lock whose holder is dead. The OLD
break (rm -f holder; rmdir; continue) lets the second waiter delete the first waiter's
FRESH lock, so both end up inside the critical section. The fix breaks the stale lock by
ATOMIC RENAME (mv "$lock" "$lock.stale.$$.$RANDOM") — only one rename can win.

Outcome asserted from the workers' HOLD/REL log: never two holders inside the critical
section at once, over 50 trials. Drives the REAL _alloc_lock/_alloc_unlock from
bin/remote-session.sh (sourced like the swap tests source library.sh), in a sandbox RUNDIR.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _library():
    """Extract just the lock functions from bin/remote-session.sh — sourcing the whole
    preamble would run its top-level config sourcing against a fake ROOT; the four
    functions are self-contained (RUNDIR is exported by the worker)."""
    src = (ROOT / "bin/remote-session.sh").read_text()
    parts = []
    for name in ("_proc_start", "_owner_alive", "_alloc_lock", "_alloc_unlock"):
        i = src.index(f"\n{name}() {{")
        j = src.index("\n}\n", i)
        parts.append(src[i + 1 : j + 2])
    lib = "\n".join(parts)
    # TEST-ONLY injection: model the loaded-box ps latency the shipped code itself documents
    # ("one liveness check can take over a second on a loaded box", 0.3-0.6 s measured
    # 2026-11-27). It widens the window between a waiter READING the stale holder and its
    # break attempt — exactly the window the dead-holder race lives in. The invariant under
    # test (never two holders inside at once) is unchanged.
    assert lib.count("_owner_alive() {") == 1
    return lib.replace("_owner_alive() {",
                       "_owner_alive() { sleep 0.2;   # test-only loaded-box ps latency", 1)

WORKER = r'''
source "$1"
export RUNDIR="$2"
while [ ! -e "$3" ]; do sleep 0.01; done   # barrier: both waiters released together
if _alloc_lock; then
    echo "HOLD $$" >> "$RUNDIR/holds.log"
    sleep 0.15
    echo "REL $$" >> "$RUNDIR/holds.log"
    _alloc_unlock
    echo WON
else
    echo LOST
fi
'''


class AllocLockAtomicTests(unittest.TestCase):
    def test_dead_holder_break_admits_exactly_one_waiter_at_a_time(self):
        library = _library()
        trials_with_double_hold = 0
        for _ in range(50):
            rundir = tempfile.mkdtemp(prefix="fa-alloc-")
            try:
                lib = Path(rundir) / "lib.sh"
                lib.write_text(library)
                lock = Path(rundir) / ".port-alloc.lock"
                lock.mkdir()
                dead = subprocess.Popen(["sleep", "0"])
                dead.wait()
                (lock / "holder").write_text(f"{dead.pid} whatever\n")  # dead holder
                go = Path(rundir) / "go"
                env = dict(os.environ, LA_PORT_LOCK_WAIT_S="5")
                procs = [subprocess.Popen(["bash", "-c", WORKER, "w", str(lib), rundir, str(go)],
                                          env=env, text=True, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE) for _ in range(2)]
                go.touch()  # release the barrier
                wons = 0
                for p in procs:
                    out, err = p.communicate(timeout=30)
                    self.assertEqual(p.returncode, 0, err)
                    wons += out.count("WON")
                log = (Path(rundir) / "holds.log").read_text().splitlines()
                active = 0
                double = False
                for line in log:
                    if line.startswith("HOLD"):
                        active += 1
                        if active > 1:
                            double = True
                    elif line.startswith("REL"):
                        active -= 1
                if double:
                    trials_with_double_hold += 1
                self.assertGreaterEqual(wons, 1, "at least one waiter must win the lock")
            finally:
                shutil.rmtree(rundir, ignore_errors=True)
        self.assertEqual(trials_with_double_hold, 0,
                         f"{trials_with_double_hold}/50 trials had two holders inside the "
                         "critical section — the dead-holder break is not atomic")


if __name__ == "__main__":
    unittest.main(verbosity=2)

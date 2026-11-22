#!/usr/bin/env python3
"""Task 5 Step 1 — mixed-writer v2 (C2 hard constraint): a FROZEN 0.61.0 writer and the
new code share the NVIDIA main file; all NEW dimensions live in the sidecar <state>.quota.

Multiprocess, like test_rate_limiter_mixed.py: 200 interleaved acquisitions must satisfy
  (a) grants in any 60 s <= 40 (never overspend, across both writers),
  (b) the sidecar counters equal the NEW process's bookings,
  (c) the main file still parses with EXACTLY the 0.61.0 key set (old writers drop unknown
      keys on their rewrites — nothing of ours may leak into the main file).
"""
import importlib.util
import json
import multiprocessing as mp
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
import rate_limiter as rl  # noqa: E402

OLD = REPO / "tests" / "fixtures" / "rate_limiter_0_61_0.py"

_spec = importlib.util.spec_from_file_location("old_rate_limiter", OLD)
old = importlib.util.module_from_spec(_spec)
sys.modules["old_rate_limiter"] = old
_spec.loader.exec_module(old)

FROZEN = 1_000_000.0
MAIN_KEYS_0610 = {"stamps", "cooldown_until", "limit", "consecutive_429s",
                  "last_429_time", "models", "owners", "waiters", "deficit"}


def _old_worker(path, attempts, out):
    b = old.SlidingWindowLimiter(path, rpm=40, clock=lambda: FROZEN)
    got = sum(1 for _ in range(attempts) if b.try_acquire() == 0.0)
    out.put(("old", got))


def _new_worker(path, attempts, out):
    # the new code with a sidecar attached (tpm dimension): every grant books 1 token there
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("provider_limits", REPO / "bin" / "provider_limits.py")
    pl = ilu.module_from_spec(spec)
    sys.modules["provider_limits"] = pl
    spec.loader.exec_module(pl)
    from quota_ledger import QuotaLedger
    lim = pl.ProviderLimits(rpm=40, tpm=40, rpd=None, rpw=None, tpd=None,
                            dispatch_only=False, reserves_on_request=False,
                            measured_at="test", source="test")
    b = rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: FROZEN,
                                quota=QuotaLedger(str(path) + ".quota", lim,
                                                  clock=lambda: FROZEN))
    got = 0
    for _i in range(attempts):
        if b.try_acquire(tokens=1) == 0.0:
            got += 1
    out.put(("new", got))


class MixedWriterV2Tests(unittest.TestCase):
    def test_old_and_new_share_one_file_without_losing_state(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "state")
            rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: FROZEN).status()  # create
            q = mp.Queue()
            procs = [mp.Process(target=_old_worker, args=(path, 150, q)) for _ in range(2)]
            procs += [mp.Process(target=_new_worker, args=(path, 500, q)) for _ in range(3)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(300)
            for p in procs:
                self.assertFalse(p.is_alive(), "worker did not finish within 300s")
                self.assertEqual(p.exitcode, 0, "worker exited non-zero")
            results = [q.get() for _ in range(5)]
            total = sum(g for _k, g in results)
            self.assertLessEqual(total, 40, "(a) grants in the frozen window never overspend")
            with open(path) as f:
                st = json.load(f)
            self.assertEqual(set(st.keys()) - {"grants"}, MAIN_KEYS_0610,
                             "(c) main file keeps EXACTLY the 0.61.0 key set "
                             "(grants is append-only and old code ignores it)")
            side = Path(str(path) + ".quota")
            self.assertTrue(side.exists(), "new dimensions live in the sidecar")
            sq = json.loads(side.read_text())
            new_booked = sum(g for k, g in results if k == "new")
            sidecar_tokens = sum(int(s.get("n", 0)) for s in sq.get("tpm", {}).get("stamps", []))
            # without the sidecar lock, concurrent read-modify-write loses updates
            self.assertEqual(sidecar_tokens, new_booked,
                             f"(b) sidecar counters equal the new processes' bookings "
                             f"({sidecar_tokens} != {new_booked})")


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for bin/rate_limiter.py -- the machine-wide NVIDIA token bucket.

The property that matters is INTER-PROCESS: N separate processes sharing one state file must
never spend more tokens than the bucket holds. The race test freezes the clock (no refill) so
the expected total is exact, and widens the read->write window so a missing lock is caught
reliably rather than by luck. The mutation check proves the test can fail.
"""
import importlib.util
import multiprocessing as mp
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rate_limiter", REPO / "bin" / "rate_limiter.py")
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)

FROZEN = 1_000_000.0


def _worker(path, attempts, disable_lock, out):
    if disable_lock:
        rl.fcntl.flock = lambda fd, op: None          # the mutation under test
    bucket = rl.FileTokenBucket(path, rpm=40, clock=lambda: FROZEN)
    orig_read = bucket._read

    def slow_read(fd, now):                            # widen the race window
        result = orig_read(fd, now)
        time.sleep(0.002)
        return result

    bucket._read = slow_read
    got = sum(1 for _ in range(attempts) if bucket.try_acquire() == 0.0)
    out.put(got)


def _run(disable_lock):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "state")
        rl.FileTokenBucket(path, rpm=40, clock=lambda: FROZEN).status()  # create, full
        q = mp.Queue()
        procs = [mp.Process(target=_worker, args=(path, 30, disable_lock, q)) for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        return sum(q.get() for _ in procs)


class TokenBucketTests(unittest.TestCase):
    def test_single_process_capacity_then_wait(self):
        with tempfile.TemporaryDirectory() as d:
            b = rl.FileTokenBucket(os.path.join(d, "s"), rpm=40, clock=lambda: FROZEN)
            self.assertEqual([b.try_acquire() for _ in range(40)], [0.0] * 40)
            self.assertAlmostEqual(b.try_acquire(), 1.5, places=6)

    def test_refill_is_one_token_per_1_5_seconds(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = rl.FileTokenBucket(os.path.join(d, "s"), rpm=40, clock=lambda: now[0])
            for _ in range(40):
                b.try_acquire()
            now[0] += 1.5
            self.assertEqual(b.try_acquire(), 0.0)
            self.assertGreater(b.try_acquire(), 0.0)

    def test_corrupt_state_starts_full_not_wedged(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s")
            Path(p).write_text("{not json")
            b = rl.FileTokenBucket(p, rpm=40, clock=lambda: FROZEN)
            self.assertEqual(b.try_acquire(), 0.0)

    def test_timeout_raises(self):
        with tempfile.TemporaryDirectory() as d:
            b = rl.FileTokenBucket(os.path.join(d, "s"), rpm=40, clock=lambda: FROZEN)
            for _ in range(40):
                b.try_acquire()
            with self.assertRaises(rl.RateLimitTimeout):
                b.acquire(max_wait=0.5)

    def test_state_file_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s")
            rl.FileTokenBucket(p).status()
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)

    def test_inter_process_never_overspends(self):
        self.assertEqual(_run(disable_lock=False), 40)

    def test_mutation_without_lock_overspends(self):
        # If this ever passes with the lock removed, the race test above proves nothing.
        self.assertGreater(_run(disable_lock=True), 40)


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    unittest.main(verbosity=2)

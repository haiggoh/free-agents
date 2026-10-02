#!/usr/bin/env python3
"""Tests for bin/rate_limiter.py -- the machine-wide NVIDIA sliding-window limiter.

The property that matters is INTER-PROCESS: N separate processes sharing one state file must
never take more slots than the window allows. The race test freezes the clock (no refill) so
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
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rate_limiter", REPO / "bin" / "rate_limiter.py")
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)

FROZEN = 1_000_000.0


def _worker(path, attempts, disable_lock, out):
    if disable_lock:
        rl.fcntl.flock = lambda fd, op: None          # the mutation under test
    bucket = rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: FROZEN)
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
        rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: FROZEN).status()  # create, empty
        q = mp.Queue()
        procs = [mp.Process(target=_worker, args=(path, 30, disable_lock, q)) for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        return sum(q.get() for _ in procs)


def _admitted_in_first_minute(limiter, now):
    """Simulate greedy callers against a fake clock; count admissions in the first 60 s."""
    sent = 0
    while now[0] < 60.0:
        if limiter.try_acquire() == 0.0:
            sent += 1
        else:
            now[0] += 0.05
    return sent


class SlidingWindowTests(unittest.TestCase):
    def test_single_process_limit_then_wait_for_oldest(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = rl.SlidingWindowLimiter(os.path.join(d, "s"), rpm=40, clock=lambda: now[0])
            self.assertEqual([b.try_acquire() for _ in range(40)], [0.0] * 40)
            self.assertAlmostEqual(b.try_acquire(), 60.0, places=6)   # oldest leaves at +60 s
            now[0] += 60.0
            self.assertEqual(b.try_acquire(), 0.0)

    def test_never_more_than_limit_in_any_rolling_minute(self):
        # The 0.19.10 token bucket admitted 79 in the first minute (full start + refill).
        now = [0.0]
        with tempfile.TemporaryDirectory() as d:
            b = rl.SlidingWindowLimiter(os.path.join(d, "s"), rpm=40, clock=lambda: now[0])
            self.assertEqual(_admitted_in_first_minute(b, now), 40)

    def test_limit_is_env_tunable(self):
        now = [0.0]
        with tempfile.TemporaryDirectory() as d:
            os.environ["LA_NVIDIA_RPM"] = "30"
            try:
                b = rl.SlidingWindowLimiter(os.path.join(d, "s"), clock=lambda: now[0])
            finally:
                del os.environ["LA_NVIDIA_RPM"]
            self.assertEqual(_admitted_in_first_minute(b, now), 30)

    def test_429_pauses_every_process_sharing_the_file(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s")
            a = rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: now[0], cooldown=10)
            other = rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: now[0], cooldown=10)
            for _ in range(5):
                a.try_acquire()
            info = a.note_429()
            self.assertEqual(info["in_window"], 5)          # locates the real limit
            self.assertAlmostEqual(other.try_acquire(), 10.0, places=6)
            now[0] += 10.0
            self.assertEqual(other.try_acquire(), 0.0)

    def test_429_books_hidden_sdk_attempts(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = rl.SlidingWindowLimiter(os.path.join(d, "s"), rpm=40, clock=lambda: now[0])
            b.try_acquire()
            self.assertEqual(b.note_429(hidden_attempts=2)["in_window"], 3)
            now[0] += 61.0                                  # all three age out together
            self.assertEqual(b.status()["in_window"], 0)

    def test_429_honours_retry_after(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = rl.SlidingWindowLimiter(os.path.join(d, "s"), rpm=40, clock=lambda: now[0])
            self.assertEqual(b.note_429(retry_after=25)["pause"], 25.0)
            self.assertAlmostEqual(b.try_acquire(), 25.0, places=6)

    def test_old_token_bucket_state_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s")
            Path(p).write_text('{"tokens": 38.0, "stamp": 1.0, "capacity": 40.0}')
            b = rl.SlidingWindowLimiter(p, rpm=40, clock=lambda: FROZEN)
            self.assertEqual(b.try_acquire(), 0.0)

    def test_corrupt_state_starts_empty_not_wedged(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s")
            Path(p).write_text("{not json")
            b = rl.SlidingWindowLimiter(p, rpm=40, clock=lambda: FROZEN)
            self.assertEqual(b.try_acquire(), 0.0)

    def test_timeout_raises(self):
        with tempfile.TemporaryDirectory() as d:
            b = rl.SlidingWindowLimiter(os.path.join(d, "s"), rpm=40, clock=lambda: FROZEN)
            for _ in range(40):
                b.try_acquire()
            with self.assertRaises(rl.RateLimitTimeout):
                b.acquire(max_wait=0.5)

    def test_state_file_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s")
            rl.SlidingWindowLimiter(p).status()
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)

    def test_inter_process_never_overspends(self):
        self.assertEqual(_run(disable_lock=False), 40)

    def test_mutation_without_lock_overspends(self):
        # If this ever passes with the lock removed, the race test above proves nothing.
        self.assertGreater(_run(disable_lock=True), 40)


class SavedSettingsTests(unittest.TestCase):
    """The picker's rate-limiter screen persists settings; proxies must honour them.

    Precedence: explicit constructor arg > LA_NVIDIA_* env > saved picker setting > default.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("LA_NVIDIA_")}
        self.env["LA_SESSION_MENU_CONFIG_DIR"] = str(self.cfg)
        self.state = os.path.join(self.tmp.name, "state")

    def save(self, doc):
        (self.cfg / "session-menu.local.json").write_text(
            '{"schema_version": 1, "rate_limiter": %s}' % doc)

    def limiter(self, **env):
        with unittest.mock.patch.dict(os.environ, {**self.env, **env}, clear=True):
            return rl.get_limiter(self.state, clock=lambda: FROZEN)

    def test_saved_mode_and_rpm_are_used(self):
        # Fails if get_limiter only reads the environment (the old menu's settings vanished).
        self.save('{"mode": "smooth_bucket", "rpm": 30, "bucket_capacity": 4, "cooldown": 5}')
        lim = self.limiter()
        self.assertIsInstance(lim, rl.SmoothTokenBucket)
        self.assertAlmostEqual(lim.rate, 0.5)
        # The saved key "cooldown" is the BASE cooldown of main's 429 backoff.
        self.assertEqual((lim.capacity, lim.base_cooldown), (4, 5.0))

    def test_backoff_defaults_follow_main(self):
        lim = self.limiter()
        self.assertEqual((lim.base_cooldown, lim.max_cooldown, lim.backoff_multiplier,
                          lim.max_retries), (30.0, 300.0, 2.0, 5))

    def test_environment_still_wins(self):
        self.save('{"mode": "smooth_bucket", "rpm": 30}')
        lim = self.limiter(LA_NVIDIA_MODE="sliding_window", LA_NVIDIA_RPM="20")
        self.assertIsInstance(lim, rl.SlidingWindowLimiter)
        self.assertEqual(lim.limit, 20)

    def test_absent_or_broken_file_falls_back_to_defaults(self):
        self.assertEqual(self.limiter().limit, 40)
        (self.cfg / "session-menu.local.json").write_text("{broken")
        self.assertIsInstance(self.limiter(), rl.SlidingWindowLimiter)

    def test_max_wait_uses_saved_value(self):
        self.save('{"max_wait": 30}')
        with unittest.mock.patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(rl._max_wait(None), 30.0)


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Task 3 Step 1 — state I/O: read to EOF (L2), fail SAFE on corruption (L2), and the
dispatch tool's torn-read peek (L6). Main suite; no LiteLLM needed."""
import contextlib
import importlib.util as ilu
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
import rate_limiter as rl  # noqa: E402

_spec = ilu.spec_from_file_location("free_agent_tool", REPO / "bin" / "free-agent-tool.py")
fat = ilu.module_from_spec(_spec)
_spec.loader.exec_module(fat)

CLASSES = (rl.SlidingWindowLimiter, rl.SmoothTokenBucket)


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class StateIoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()

    def _path(self, name):
        return os.path.join(self.tmp.name, name)

    def _smooth_state(self, waiters):
        now = self.clock.now
        return {"tokens": 0.0, "last_refill": now, "capacity": 6, "rate": 40 / 60,
                "cooldown_until": 0.0, "consecutive_429s": 0, "last_429_time": 0.0,
                "models": {}, "owners": {}, "waiters": waiters, "deficit": {},
                "grants": []}

    def _sliding_state(self, waiters):
        return {"stamps": [], "cooldown_until": 0.0, "limit": 40, "consecutive_429s": 0,
                "last_429_time": 0.0, "models": {}, "owners": {}, "waiters": waiters,
                "deficit": {}}

    def test_state_larger_than_64k_is_read_whole(self):
        # L2: the old single os.read(fd, 65536) truncated a big file -> parse error.
        # 200 KB of valid waiters must parse whole and survive a try_acquire round-trip.
        waiters = {f"w{i}": {"owner": "o", "arrived": self.clock.now,
                             "last_seen": self.clock.now, "class": "normal",
                             "kind": "interactive", "model": ""} for i in range(3000)}
        for cls in CLASSES:
            with self.subTest(cls=cls.__name__):
                path = self._path(cls.__name__)
                state = self._smooth_state(waiters) if cls is rl.SmoothTokenBucket \
                    else self._sliding_state(waiters)
                Path(path).write_text(json.dumps(state))
                assert Path(path).stat().st_size > 65536
                b = cls(path, rpm=40, clock=self.clock)
                b.try_acquire(token=rl._waiter_token("new"))
                st = json.loads(Path(path).read_text())
                self.assertEqual(len(st["waiters"]), 3001, "big state survived whole")
                self.assertEqual(st.get("cooldown_until", 0.0), 0.0, "no fail-safe on a valid file")

    def test_corrupt_state_fails_safe(self):
        # L2: garbage bytes must FAIL SAFE (cooldown active, nothing granted), be written
        # back, and logged loudly — never reset to permissive.
        for cls in CLASSES:
            with self.subTest(cls=cls.__name__):
                path = self._path(cls.__name__)
                Path(path).write_bytes(b'{"tokens": 5, "last_refill": "corrupt')
                b = cls(path, rpm=40, clock=self.clock, cooldown=30)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    wait = b.try_acquire(token=rl._waiter_token("t"))
                self.assertGreater(wait, 0.0, "corrupt state must not grant")
                self.assertIn("failing SAFE", err.getvalue(), err.getvalue())
                st = json.loads(Path(path).read_text())
                self.assertEqual(st["cooldown_until"], self.clock.now + 30,
                                 "fail-safe cooldown written back")
                if cls is rl.SmoothTokenBucket:
                    self.assertEqual(st["tokens"], 0.0, "fail-safe: no tokens")
                else:
                    self.assertEqual(st["stamps"], [], "fail-safe: empty window")


class DispatchPeekTests(unittest.TestCase):
    """L6 + C3: the load peek goes through the limiter (whole-file read under flock, fail
    SAFE on garbage) — never raises, never creates state. A torn file is treated as
    loaded (conservative: the patient tier), a missing file as not loaded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state")
        Path(self.path).write_text("{}")
        self.env = {"LA_NVIDIA_THROTTLE_STATE": self.path,
                    "FA_LIMITER_DIR": os.path.join(self.tmp.name, "limiter")}

    def test_quiet_state_is_not_loaded(self):
        self.assertFalse(fat.nvidia_load_detected(self.env))

    def test_active_cooldown_is_loaded(self):
        import time
        Path(self.path).write_text(json.dumps(
            {"stamps": [], "cooldown_until": time.time() + 3600, "limit": 40,
             "consecutive_429s": 0, "last_429_time": 0.0, "models": {}, "owners": {},
             "waiters": {}, "deficit": {}}))
        self.assertTrue(fat.nvidia_load_detected(self.env))

    def test_garbage_state_fails_safe_towards_loaded(self):
        Path(self.path).write_bytes(b'{"stamps": [trunc')
        self.assertTrue(fat.nvidia_load_detected(self.env), "fail-safe: corrupt = loaded")

    def test_torn_then_whole_read_sees_the_whole_file(self):
        import time
        Path(self.path).write_bytes(b'{"stamps": [trunc')      # torn
        whole = json.dumps({"stamps": [], "cooldown_until": time.time() + 3600, "limit": 40,
                            "consecutive_429s": 0, "last_429_time": 0.0, "models": {},
                            "owners": {}, "waiters": {}, "deficit": {}})
        Path(self.path).write_text(whole)                       # the writer finished
        self.assertTrue(fat.nvidia_load_detected(self.env), "whole file read -> real answer")

    def test_missing_file_returns_false_and_is_not_created(self):
        p = os.path.join(self.tmp.name, "nope")
        self.assertFalse(fat.nvidia_load_detected({"LA_NVIDIA_THROTTLE_STATE": p}))
        self.assertFalse(Path(p).exists())


if __name__ == "__main__":
    unittest.main()

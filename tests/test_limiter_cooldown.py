#!/usr/bin/env python3
"""Task 3 Step 1 — per-model cooldown in the GRANT path (L1), the real 60 s classifier
(L3/L7), and own-owner age preservation (L5). Main suite; no LiteLLM needed.

Each behaviour is asserted on both limiter classes (SmoothTokenBucket, SlidingWindowLimiter)
and on the STATE FILE, never on internal counters alone.
"""
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

CLASSES = (rl.SlidingWindowLimiter, rl.SmoothTokenBucket)


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class LimiterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()

    def path(self, cls):
        # one state file PER CLASS: the two formats share keys but not meanings
        return os.path.join(self.tmp.name, cls.__name__)

    def bucket(self, cls, cooldown=30, name=None):
        return cls(os.path.join(self.tmp.name, name or cls.__name__), rpm=40,
                   clock=self.clock, cooldown=cooldown)

    def state(self, cls):
        return json.loads(Path(self.path(cls)).read_text())


class CoolingModelTests(LimiterCase):
    """L1: try_acquire(model=...) returns the model's remaining cooldown and never
    consumes a token while models[model].until > now; the waiter is registered so the
    queue heartbeats continue (a chain-less proxy waits out the cooldown inside aacquire)."""

    def test_cooling_model_gets_no_token(self):
        for cls in CLASSES:
            with self.subTest(cls=cls.__name__):
                b = self.bucket(cls)
                b.note_429(model="nvidia_nim/m")          # cools m for the base cooldown
                t = rl._waiter_token("s1")
                wait = b.try_acquire(token=t, model="nvidia_nim/m")
                self.assertGreater(wait, 0.0, "cooling model must not get a token")
                st = self.state(cls)
                self.assertIn(t, st["waiters"], "waiter registered (heartbeat continues)")
                if cls is rl.SmoothTokenBucket:
                    self.assertEqual(st["tokens"], float(b.capacity),
                                     "no token consumed while cooling")
                else:
                    self.assertEqual(len(st["stamps"]), 0, "no stamp consumed while cooling")
                # a DIFFERENT model still gets a token right away (fresh bucket, same
                # cooling entry: only the cooled model is blocked)
                b2 = self.bucket(cls, name=cls.__name__ + "-b")
                b2.note_429(model="nvidia_nim/m")
                self.assertEqual(
                    b2.try_acquire(token=rl._waiter_token("s2"), model="nvidia_nim/other"), 0.0)

    def test_chainless_proxy_waits_out_cooldown(self):
        for cls in CLASSES:
            with self.subTest(cls=cls.__name__):
                b = self.bucket(cls)
                b.note_429(model="m")                     # 30 s cooldown
                self.clock.now += 31
                self.assertEqual(b.try_acquire(token=rl._waiter_token("s1"), model="m"), 0.0)


class ClassifierTests(LimiterCase):
    """L3: the 429 classifier counts REAL grants in the last 60 s, not burst depletion.
    Under steady load (tokens ~ 0, smooth) a single model's 429 must still be a
    model_throttle when few grants are in the window, and rpm_overrun when the window
    is genuinely half full."""

    def _seed_grants(self, cls, n):
        path = self.path(cls)
        now = self.clock.now
        if cls is rl.SmoothTokenBucket:
            state = {"tokens": 0.0, "last_refill": now, "capacity": 6, "rate": 40 / 60,
                     "cooldown_until": 0.0, "consecutive_429s": 0, "last_429_time": 0.0,
                     "models": {}, "owners": {}, "waiters": {}, "deficit": {},
                     "grants": [{"t": now - i * 1.4, "o": "legacy", "c": "normal"}
                                for i in range(n)]}
        else:
            state = {"stamps": [now - i * 1.4 for i in range(n)], "cooldown_until": 0.0,
                     "limit": 40, "consecutive_429s": 0, "last_429_time": 0.0,
                     "models": {}, "owners": {}, "waiters": {}, "deficit": {}}
        Path(path).write_text(json.dumps(state))
        return self.bucket(cls)

    def test_steady_load_model_429_is_model_throttle(self):
        for cls in CLASSES:
            with self.subTest(cls=cls.__name__, grants=5):
                b = self._seed_grants(cls, 5)             # few grants in the window
                info = b.note_429(model="m")
                self.assertEqual(info["kind"], "model_throttle", info)
                self.assertEqual(info["in_window"], 5, info)

    def test_half_full_window_429_is_rpm_overrun(self):
        for cls in CLASSES:
            with self.subTest(cls=cls.__name__, grants=40):
                b = self._seed_grants(cls, 40)            # window genuinely full
                info = b.note_429(model="m")
                self.assertEqual(info["kind"], "rpm_overrun", info)
                self.assertEqual(info["in_window"], 40, info)

    def test_legacy_peek_appends_to_grants(self):
        # L7: the token=None peek path must feed the window count the classifier reads
        for cls in CLASSES:
            with self.subTest(cls=cls.__name__):
                b = self.bucket(cls)
                self.assertEqual(b.try_acquire(), 0.0)    # legacy peek, consumes
                st = self.state(cls)
                if cls is rl.SmoothTokenBucket:
                    self.assertEqual(len(st.get("grants", [])), 1, st.get("grants"))
                else:
                    self.assertEqual(len(st["stamps"]), 1)


class OwnOwnerAgeTests(LimiterCase):
    """L5: when the queue winner belongs to our own owner, the winner is served (booked
    as granted) and the CALLER keeps its queue age — popping the winner stole the age."""

    def test_own_owner_serves_winner_not_steals_age(self):
        now = self.clock.now
        for cls in CLASSES:
            with self.subTest(cls=cls.__name__):
                t1, t2 = "w-t1", "w-t2"
                a2 = now + 10
                w1 = {t1: {"owner": "me", "arrived": now, "last_seen": now,
                           "class": "normal", "kind": "interactive", "model": ""}}
                # seed: window has room for one more grant, and one waiter (t1) is ours
                if cls is rl.SlidingWindowLimiter:
                    state = {"stamps": [{"t": now - i, "o": "other"} for i in range(39)],
                             "cooldown_until": 0.0, "limit": 40, "consecutive_429s": 0,
                             "last_429_time": 0.0, "models": {}, "owners": {},
                             "waiters": w1, "deficit": {}}
                else:
                    state = {"tokens": 1.0, "last_refill": now, "capacity": 6,
                             "rate": 40 / 60, "cooldown_until": 0.0, "consecutive_429s": 0,
                             "last_429_time": 0.0, "models": {}, "owners": {},
                             "waiters": w1, "deficit": {}, "grants": []}
                Path(self.path(cls)).write_text(json.dumps(state))
                b = self.bucket(cls)
                with mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": "me"}):
                    self.clock.now = a2                     # t2 arrives later
                    wait = b.try_acquire(token=t2)          # caller t2; winner t1 (same owner)
                    self.assertGreater(wait, 0.0)
                    st = self.state(cls)
                    self.assertIn("granted", st["waiters"].get(t1, {}),
                                  f"winner served, not popped: {st['waiters']}")
                    self.assertEqual(st["waiters"].get(t2, {}).get("arrived"), a2,
                                     "caller's queue age preserved")
                    # the winner's ack consumes nothing further
                    self.assertEqual(b.try_acquire(token=t1), 0.0)


if __name__ == "__main__":
    unittest.main()

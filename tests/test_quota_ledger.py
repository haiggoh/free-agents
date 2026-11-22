#!/usr/bin/env python3
"""Task 5 Step 1 — the quota sidecar (C2): tokens/min, per-UTC-day and per-ISO-week
counters, header adoption, refusal marks, and the two invariants that keep a laptop
honest across sleep (long sleep refills to capacity, never beyond; a request larger
than tpm is ineligible immediately, not queued forever).
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
_spec = importlib.util.spec_from_file_location("provider_limits", REPO / "bin" / "provider_limits.py")
_pl = importlib.util.module_from_spec(_spec)
sys.modules["provider_limits"] = _pl
_spec.loader.exec_module(_pl)
import quota_ledger as ql  # noqa: E402


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def limits(**kw):
    base = {"rpm": 1000, "tpm": 8000, "rpd": 100, "rpw": None, "tpd": 100000,
            "dispatch_only": False, "reserves_on_request": False,
            "measured_at": "2026-11-20", "source": "test"}
    base.update(kw)
    return _pl.ProviderLimits(**base)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()

    def led(self, **kw):
        return ql.QuotaLedger(os.path.join(self.tmp.name, "sidecar"),
                              limits(**kw), self.clock)

    def test_tpm_window_rolls(self):
        led = self.led(tpm=8000)
        self.assertEqual(led.book(6000, self.clock.now), 0.0 or None
                         if False else None)  # book() returns None; room() is the waiter
        wait = led.room(3000, self.clock.now)
        self.assertGreater(wait, 0.0, "second 3000 tokens must wait for tpm room")
        self.clock.now += 61.0
        self.assertEqual(led.room(3000, self.clock.now), 0.0, "after the window it fits")

    def test_day_and_week_counters_roll_at_utc_boundaries(self):
        led = self.led(rpd=2, tpd=None)
        led.book(1, self.clock.now)
        led.book(1, self.clock.now)
        self.assertGreater(led.room(1, self.clock.now), 0.0, "third request left the day budget")
        # advance 25 h: a new UTC day -> the daily counter resets, the week counter does not
        self.clock.now += 25 * 3600
        self.assertEqual(led.room(1, self.clock.now), 0.0, "new day: daily room again")

    def test_week_counter_rolls_at_week_boundary(self):
        led = self.led(rpd=None, rpw=1)
        led.book(1, self.clock.now)
        self.assertGreater(led.room(1, self.clock.now), 0.0, "weekly budget spent")
        self.clock.now += 8 * 24 * 3600
        self.assertEqual(led.room(1, self.clock.now), 0.0, "new ISO week: weekly room again")

    def test_headers_override_local_remaining(self):
        led = self.led(rpm=None, tpm=None, rpd=None, rpw=None, tpd=None)
        led.adopt_headers({"x-ratelimit-remaining-requests": "3"}, self.clock.now)
        self.assertEqual(led.room(1, self.clock.now), 0.0, "3 remaining: fits")
        led.book(1, self.clock.now)
        led.book(1, self.clock.now)
        led.book(1, self.clock.now)
        self.assertGreater(led.room(1, self.clock.now), 0.0, "header budget exhausted")

    def test_refusal_marks_exhausted_until_reset(self):
        led = self.led(rpm=None)
        led.mark_exhausted("day", self.clock.now + 3600)
        self.assertGreater(led.room(1, self.clock.now), 0.0, "exhausted: waits")
        self.clock.now += 3601
        self.assertEqual(led.room(1, self.clock.now), 0.0, "past the reset it fits")

    def test_long_sleep_refills_to_capacity_not_beyond(self):
        # invariant 3 / Review Focus 3: an 9-hour gap must not grant a burst beyond capacity
        led = self.led(rpd=100)
        for _ in range(100):
            led.book(1, self.clock.now)
        self.clock.now += 9 * 3600
        snap = led.snapshot(self.clock.now)
        self.assertEqual(snap["day"]["tokens"], 100, "counters do not decay mid-day")
        self.assertEqual(snap["day"]["remaining_rpd"], 0, "no burst credit after a sleep")
        self.clock.now += 24 * 3600 - 9 * 3600 - 60
        self.assertEqual(led.room(1, self.clock.now), 0.0, "next day resets")

    def test_request_larger_than_tpm_is_ineligible(self):
        led = self.led(tpm=8000)
        self.assertEqual(led.room(9000, self.clock.now), float("inf"),
                         "a request over tpm can never fit — ineligible at once")

    def test_booking_is_refundable(self):
        led = self.led(tpm=8000)
        led.book(6000, self.clock.now)
        led.refund(5000, self.clock.now)          # only 1000 were really used
        self.assertEqual(led.room(2000, self.clock.now), 0.0, "refund makes room")

    def test_sidecar_takes_its_own_exclusive_lock(self):
        # lock order main -> sidecar: while a ledger op holds the sidecar lock, another
        # process must NOT be able to take it (deterministic pin for "the sidecar locks")
        import fcntl
        led = self.led()
        fd = led._locked()                    # holds LOCK_EX on the sidecar file
        try:
            other = os.open(str(led.path), os.O_RDWR)
            try:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                raised = False
            except OSError:
                raised = True                 # BlockingIOError: the lock is really held
            finally:
                os.close(other)
            self.assertTrue(raised, "sidecar lock not held during an op")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


if __name__ == "__main__":
    unittest.main()
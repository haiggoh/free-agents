#!/usr/bin/env python3
"""Task 1 (quota reservation receipts): QuotaLedger.book returns a Receipt and
settle(receipt, used) reconciles EXACTLY that reservation — in its own TPM stamp,
its own UTC day and its own ISO week — idempotently, with Policy U for receipts
whose stamp an old sidecar writer dropped.
"""
import datetime
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


def ts(y, mo, d, h=0, mi=0, s=0):
    return datetime.datetime(y, mo, d, h, mi, s,
                             tzinfo=datetime.timezone.utc).timestamp()


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()

    def led(self, **kw):
        return ql.QuotaLedger(os.path.join(self.tmp.name, "sidecar"),
                              limits(**kw), self.clock)

    def _tpm_used(self, led, now):
        return led.snapshot(now)["tpm"]["used"]

    def test_book_returns_round_trippable_receipt(self):
        led = self.led()
        self.clock.now = ts(2026, 1, 2, 3, 4, 5)
        r = led.book(1000, self.clock.now)
        self.assertEqual(r.n, 1000)
        self.assertEqual(r.at, self.clock.now)
        self.assertEqual(r.day, "2026-01-02")
        self.assertTrue(r.week.startswith("2026-W"), r.week)
        self.assertEqual(len(r.id), 32)
        r2 = ql.Receipt.from_json(json.loads(json.dumps(r.to_json())))
        self.assertEqual(r, r2)

    def test_refund_targets_original_reservation(self):
        led = self.led()
        a = led.book(1000, 100.0)
        led.book(2000, 120.0)
        self.assertEqual(led.settle(a, 125, 140.0), "settled")
        # t+50: A's own stamp (booked @100, age 50) carries the refund: 1000 -> 125
        self.assertEqual(self._tpm_used(led, 150.0), 125 + 2000)
        self.assertEqual(led.snapshot(150.0)["day"]["tokens"], 3000 - 875)
        # t+61: A's stamp aged out of the 60 s window; B must still hold 2000
        # (old code refunds the NEWEST stamp: B would wrongly read 1125 here)
        self.assertEqual(self._tpm_used(led, 161.0), 2000)

    def test_settle_is_idempotent(self):
        led = self.led()
        a = led.book(1000, 100.0)
        self.assertEqual(led.settle(a, 125, 110.0), "settled")
        st0 = led.snapshot(110.0)
        self.assertEqual(led.settle(a, 125, 110.0), "already")
        self.assertEqual(led.snapshot(110.0), st0)

    def test_expired_receipt_never_touches_live_stamp(self):
        led = self.led()
        a = led.book(1000, 0.0)
        led.book(2000, 70.0)
        # A's stamp is 71 s old: outside the 60 s TPM window -> the STAMP part is
        # unknown (never touches B's stamp), but the ledger knows the receipt, and
        # the UTC day/week still match -> those counters are refunded ("settled").
        self.assertEqual(led.settle(a, 125, 71.0), "settled")
        self.assertEqual(self._tpm_used(led, 71.0), 2000)   # B's stamp untouched
        snap = led.snapshot(71.0)
        self.assertEqual(snap["day"]["tokens"], 3000 - 875)
        self.assertEqual(snap["week"]["tokens"], 3000 - 875)
        # idempotent: a second call changes nothing
        st0 = snap
        self.assertEqual(led.settle(a, 125, 71.0), "already")
        self.assertEqual(led.snapshot(71.0), st0)

    def test_settle_after_stamp_expiry_still_settles(self):
        # The long-stream case at ledger level: the receipt record outlives the
        # 60 s stamp window, so a settle 2000 s later still reads "settled" and
        # refunds the period counters (the stamp part is simply gone).
        led = self.led()
        a = led.book(1000, 0.0)
        self.assertEqual(led.settle(a, 125, 2000.0), "settled")
        snap = led.snapshot(2000.0)
        self.assertEqual(snap["day"]["tokens"], 125)        # 1000 - 875, no clamp needed
        self.assertEqual(self._tpm_used(led, 2000.0), 0)

    def test_settle_keeps_original_utc_period(self):
        led = self.led()
        d1 = ts(2026, 1, 1, 23, 59, 50)
        d2 = ts(2026, 1, 2, 0, 0, 10)
        d3 = ts(2026, 1, 2, 0, 0, 20)
        a = led.book(1000, d1)
        led.book(2000, d2)
        self.assertEqual(led.settle(a, 125, d3), "settled")
        snap = led.snapshot(d3)
        self.assertEqual(snap["day"]["key"], "2026-01-02")
        # receipt.day is the PREVIOUS UTC day: the day-2 counter must be untouched
        # (old code rollovers first, then subtracts -> day-2 would read 1125)
        self.assertEqual(snap["day"]["tokens"], 2000)
        # same ISO week -> the week counter still gets the refund
        self.assertEqual(snap["week"]["tokens"], 3000 - 875)
        # A's stamp (30 s old) is still inside the TPM window -> refunded in place
        self.assertEqual(snap["tpm"]["used"], 125 + 2000)

    def test_settle_keeps_original_utc_week(self):
        led = self.led()
        w1 = ts(2026, 1, 4, 23, 59, 50)   # Sunday   -> ISO week 2026-W01
        w2 = ts(2026, 1, 5, 0, 0, 10)     # Monday   -> ISO week 2026-W02
        a = led.book(1000, w1)
        led.book(2000, w2)
        self.assertEqual(led.settle(a, 125, w2 + 10), "settled")
        snap = led.snapshot(w2 + 10)
        # receipt.week is the PREVIOUS ISO week: the week-2 counter must be untouched
        self.assertEqual(snap["week"]["tokens"], 2000)
        self.assertEqual(snap["day"]["tokens"], 2000)       # day also rolled
        self.assertEqual(snap["tpm"]["used"], 125 + 2000)   # stamp still in window

    def test_unknown_receipt_policy_u(self):
        led = self.led()
        a = led.book(1000, 100.0)
        led.book(2000, 120.0)
        # simulate an OLD sidecar writer: wholesale rewrite that drops the receipt
        # ids and the settled-id set (0.62.0-0.63.2 quota_ledger._load does exactly this)
        path = os.path.join(self.tmp.name, "sidecar")
        with open(path) as f:
            st = json.load(f)
        for s in st["tpm"]["stamps"]:
            s.pop("id", None)
        st.pop("settled", None)
        st.pop("receipts", None)          # 0.62.0-0.63.2 writers drop BOTH new keys
        with open(path, "w") as f:
            json.dump(st, f)
        self.assertEqual(led.settle(a, 125, 130.0), "unknown")
        # no stamp is touched (both still in window): never refund "a" random stamp
        self.assertEqual(self._tpm_used(led, 130.0), 3000)
        # the period counters still matched the receipt -> refunded there
        snap = led.snapshot(130.0)
        self.assertEqual(snap["day"]["tokens"], 3000 - 875)
        self.assertEqual(snap["week"]["tokens"], 3000 - 875)

    def test_receipts_survive_reload_and_interleaved_writers(self):
        path = os.path.join(self.tmp.name, "sidecar")
        clock = FakeClock(100.0)
        l1 = ql.QuotaLedger(path, limits(), clock)
        l2 = ql.QuotaLedger(path, limits(), clock)
        a = l1.book(1000, clock.now)
        clock.now = 120.0
        b = l2.book(2000, clock.now)
        clock.now = 140.0
        self.assertEqual(l2.settle(a, 125, clock.now), "settled")   # L2 settles L1's receipt
        clock.now = 150.0
        self.assertEqual(l1.settle(b, 1800, clock.now), "settled")  # L1 settles L2's receipt
        clock.now = 155.0
        snap = l1.snapshot(clock.now)
        # A settled used=125 -> its stamp holds 125; B settled used=1800 -> 1800
        self.assertEqual(snap["tpm"]["used"], 125 + 1800)
        self.assertEqual(snap["day"]["tokens"], 3000 - 875 - 200)
        self.assertEqual(snap["week"]["tokens"], 3000 - 875 - 200)

    def test_no_usage_refunds_nothing(self):
        led = self.led()
        a = led.book(1000, 100.0)
        st0 = led.snapshot(100.0)
        self.assertEqual(led.settle(a, None, 100.0), "no-usage")
        self.assertEqual(led.snapshot(100.0), st0)          # absent usage is NEVER zero
        # a no-usage settle does not mark the receipt settled: real usage settles later
        self.assertEqual(led.settle(a, 125, 110.0), "settled")

    def test_usage_above_estimate_books_extra(self):
        led = self.led()
        a = led.book(1000, 100.0)
        self.assertEqual(led.settle(a, 1250, 110.0), "over")
        snap = led.snapshot(110.0)
        self.assertEqual(snap["day"]["tokens"], 1000 + 250)   # real spend above the estimate
        self.assertEqual(snap["week"]["tokens"], 1000 + 250)
        self.assertEqual(snap["tpm"]["used"], 1000 + 250)     # extra stamp on the current window
        st0 = snap
        self.assertEqual(led.settle(a, 1250, 110.0), "already")
        self.assertEqual(led.snapshot(110.0), st0)

    def test_no_tpm_provider_settles_from_period_counters(self):
        # NVIDIA default lane: tpm=None writes NO stamp; the receipt still settles
        # from day/week (a missing stamp is Policy U only when tpm is ACTIVE).
        led = self.led(tpm=None, tpd=None)
        a = led.book(1000, 100.0)
        self.assertEqual(led.settle(a, 125, 110.0), "settled")
        snap = led.snapshot(110.0)
        self.assertEqual(snap["day"]["tokens"], 1000 - 875)
        self.assertEqual(snap["week"]["tokens"], 1000 - 875)


if __name__ == "__main__":
    unittest.main()

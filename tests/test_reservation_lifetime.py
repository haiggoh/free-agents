#!/usr/bin/env python3
"""Task 2 (quota reservation receipts — wiring): the booking is created only AFTER
the limiter grants, lives exactly as long as its request, the granting poll books
the WINNER's own estimate, and the failure callback drops only its own entry.
Run under the LiteLLM interpreter (the hook module is loaded the same way the
existing hook tests load it).
"""
import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

try:
    import litellm  # noqa: F401
    HAVE_LITELLM = True
except Exception:
    HAVE_LITELLM = False

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
_spec = importlib.util.spec_from_file_location("provider_limits", REPO / "bin" / "provider_limits.py")
_pl = importlib.util.module_from_spec(_spec)
sys.modules["provider_limits"] = _pl
_spec.loader.exec_module(_pl)
import quota_ledger as ql          # noqa: E402
import rate_limiter as rl          # noqa: E402


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def limits(**kw):
    base = {"rpm": 40, "tpm": 8000, "rpd": None, "rpw": None, "tpd": None,
            "dispatch_only": False, "reserves_on_request": False,
            "measured_at": "2026-11-20", "source": "test"}
    base.update(kw)
    return _pl.ProviderLimits(**base)


def load_hooks():
    spec = importlib.util.spec_from_file_location(
        "la_proxy_hooks", REPO / "bin" / "la_proxy_hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _receipt(rid, n, at, day="1970-01-01", week="1970-W01"):
    return ql.Receipt(id=rid, n=n, at=at, day=day, week=week)


def _assert_no_la_keys(kwargs):
    leaked = [k for k in list(kwargs) + list(kwargs.get("messages", []))
              if isinstance(k, str) and k.startswith("_la_")]
    assert leaked == [], f"internal keys forwarded upstream: {leaked}"


class GrantTimingTests(unittest.TestCase):
    """The hook records the receipt only AFTER aacquire_grant returns a grant."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mod = load_hooks()
        os.environ.pop("LA_PROXY_FALLBACK_NAMES", None)
        self.addCleanup(os.environ.pop, "LA_PROXY_FALLBACK_NAMES", None)
        self.mod._CALL_BOOKED.clear()

    def _hooks_with(self, bucket):
        hooks = self.mod.LAProxyHooks()
        hooks._bucket = bucket
        return hooks

    def _kwargs(self, call_id, max_tokens=700):
        return {"model": "nvidia_nim/acme/x", "max_tokens": max_tokens,
                "litellm_call_id": call_id,
                "messages": [{"role": "user", "content": "hi " * 100}]}

    def test_booking_created_only_after_grant(self):
        mod = self.mod
        release = asyncio.Event()
        rec = _receipt("a" * 32, 700, 1_000_000.0)

        class StubBucket:
            def model_cooling(self, model):
                return 0.0

            async def aacquire(self, **kw):     # booby trap: the OLD pre-grant API
                raise AssertionError("hook must book via aacquire_grant, after the grant")

            async def aacquire_grant(self, **kw):
                await release.wait()            # the queue wait
                return 3.0, rec

            def note_success(self, model=None, tokens_used=None, receipt=None):
                self.success = (model, tokens_used, receipt)

        bucket = StubBucket()
        hooks = self._hooks_with(bucket)
        kwargs = self._kwargs("c-A")

        async def scenario():
            task = asyncio.create_task(hooks.async_pre_call_deployment_hook(kwargs, None))
            await asyncio.sleep(0)              # let the hook reach the queue wait
            await asyncio.sleep(0)
            self.assertEqual(mod._CALL_BOOKED, {},
                             "no receipt may exist while the grant is pending")
            release.set()
            await task

        asyncio.run(scenario())
        got = mod._CALL_BOOKED.get("c-A")
        self.assertIsNotNone(got, "the receipt is recorded only after the grant")
        self.assertEqual(got.n, 700)
        self.assertEqual(got.id, rec.id)
        _assert_no_la_keys(kwargs)

    @unittest.skipUnless(HAVE_LITELLM, "litellm absent: run under the LiteLLM pipx venv")
    def test_timeout_denial_leaves_no_booking(self):
        mod = self.mod
        import litellm
        from rate_limiter import RateLimitTimeout

        class StubBucket:
            def model_cooling(self, model):
                return 0.0

            async def aacquire(self, **kw):
                raise AssertionError("hook must book via aacquire_grant, after the grant")

            async def aacquire_grant(self, **kw):
                raise RateLimitTimeout("no NVIDIA slot within 300s")

            def note_success(self, model=None, tokens_used=None, receipt=None):
                raise AssertionError("a denial never succeeds")

        hooks = self._hooks_with(StubBucket())
        kwargs = self._kwargs("c-B")
        with self.assertRaises(litellm.RateLimitError):
            asyncio.run(hooks.async_pre_call_deployment_hook(kwargs, None))
        self.assertEqual(mod._CALL_BOOKED, {},
                         "a request that never got a slot books nothing")

    def test_ineligible_estimate_books_zero_receipt(self):
        # The estimate alone can never fit tpm: the first acquire raises ineligible,
        # the retry grants with tokens=0 and the receipt records n=0. Settling that
        # receipt books the REAL usage ("over") — absent usage refunds nothing.
        mod = self.mod
        from rate_limiter import RateLimitTimeout
        clock = FakeClock(1000.0)
        ledger = ql.QuotaLedger(os.path.join(self.tmp.name, "sidecar"),
                                limits(tpm=100), clock)
        zero_rec = _receipt("e" * 32, 0, 1000.0)

        class StubBucket:
            def model_cooling(self, model):
                return 0.0

            async def aacquire_grant(self, **kw):
                if kw.get("tokens", 0) > 0:
                    raise RateLimitTimeout(
                        f"ineligible: {kw['tokens']} tokens can never fit this provider's limits")
                return 0.0, zero_rec

            def note_success(self, model=None, tokens_used=None, receipt=None):
                ledger.settle(receipt, tokens_used, clock.now)

        hooks = self._hooks_with(StubBucket())
        kwargs = self._kwargs("c-C", max_tokens=5000)
        asyncio.run(hooks.async_pre_call_deployment_hook(kwargs, None))
        got = mod._CALL_BOOKED.get("c-C")
        self.assertIsNotNone(got, "the tokens=0 grant still mints a Receipt")
        self.assertEqual(got.n, 0)
        # the real usage (250 > estimate 0) is booked, never refunded
        asyncio.run(hooks.async_log_success_event(
            {"model": "nvidia_nim/acme/x", "litellm_call_id": "c-C"},
            {"usage": {"prompt_tokens": 200, "completion_tokens": 50}}, 0, 1))
        snap = ledger.snapshot(clock.now)
        self.assertEqual(snap["day"]["tokens"], 250, "real spend above a 0 estimate is booked")
        self.assertEqual(snap["tpm"]["used"], 250)
        _assert_no_la_keys(kwargs)

    def test_booking_survives_long_queue_with_interleaved_request(self):
        # A waits 1900 s in the queue; its receipt is born AT the grant (t+1900), so the
        # later settle refunds A's own estimate. B books and completes meanwhile.
        mod = self.mod
        clock = FakeClock(1_000_000.0)
        ledger = ql.QuotaLedger(os.path.join(self.tmp.name, "sidecar"), limits(), clock)
        real = rl.SlidingWindowLimiter(os.path.join(self.tmp.name, "s"), rpm=40,
                                       clock=clock, quota=ledger)
        granted = {}

        class StubBucket:
            model_cooling = real.model_cooling

            async def aacquire_grant(self, **kw):
                rec = ledger.book(kw["tokens"], clock.now)   # the grant instant
                granted["rec"] = rec
                return 1900.0, rec

            note_success = real.note_success                 # the REAL settle path

        hooks = self._hooks_with(StubBucket())
        # B books and settles meanwhile (interleaved, different receipt)
        b_rec = ledger.book(2000, 1_000_010.0)
        ledger.settle(b_rec, 1800, 1_000_020.0)              # B refunds 200
        # A finally gets its grant 1900 s after it queued
        clock.now = 1_001_900.0
        kwargs = self._kwargs("c-D")
        asyncio.run(hooks.async_pre_call_deployment_hook(kwargs, None))
        a_rec = mod._CALL_BOOKED.get("c-D")
        self.assertIsNotNone(a_rec)
        self.assertEqual(a_rec.at, 1_001_900.0, "the booking is stamped at the GRANT")
        clock.now = 1_001_905.0
        asyncio.run(hooks.async_log_success_event(
            {"model": "nvidia_nim/acme/x", "litellm_call_id": "c-D"},
            {"usage": {"prompt_tokens": 100, "completion_tokens": 25}}, 0, 1))
        snap = ledger.snapshot(clock.now)
        # A booked 700 at the grant, settled used=125 -> refund 575; B net 1800.
        # B's stamp (t+10) has aged out of the 60 s window by t+1905: only A's
        # stamp (125) still shows in tpm; B's tokens live in the day/week counters.
        self.assertEqual(snap["day"]["tokens"], 1800 + 125)
        self.assertEqual(snap["tpm"]["used"], 125)
        self.assertNotIn("c-D", mod._CALL_BOOKED, "the success hook pops its entry")

    def test_booking_survives_long_stream(self):
        # A granted at t0, the stream runs 2000 s: the settle finds the stamp expired
        # (outside the 60 s TPM window) and refunds the day/week counters only.
        mod = self.mod
        clock = FakeClock(1_000_000.0)
        ledger = ql.QuotaLedger(os.path.join(self.tmp.name, "sidecar"), limits(), clock)
        real = rl.SlidingWindowLimiter(os.path.join(self.tmp.name, "s"), rpm=40,
                                       clock=clock, quota=ledger)
        rec = ledger.book(700, 1_000_000.0)                  # the grant-time booking
        mod._CALL_BOOKED["c-E"] = rec                        # as the pre-call hook would

        class StubBucket:
            model_cooling = real.model_cooling

            async def aacquire_grant(self, **kw):
                return 0.0, rec

            note_success = real.note_success

        hooks = self._hooks_with(StubBucket())
        clock.now = 1_002_000.0                              # the stream ran 2000 s
        asyncio.run(hooks.async_log_success_event(
            {"model": "nvidia_nim/acme/x", "litellm_call_id": "c-E"},
            {"usage": {"prompt_tokens": 100, "completion_tokens": 25}}, 0, 1))
        snap = ledger.snapshot(clock.now)
        # refund = n - used = 700 - 125 = 575; the day counter holds it, the expired
        # stamp was never touched (tpm window shows 0)
        self.assertEqual(snap["day"]["tokens"], 125, "day refunded by the receipt's amount")
        self.assertEqual(snap["tpm"]["used"], 0, "the expired stamp was never touched")

    def test_failure_callback_removes_only_owned_entry(self):
        mod = self.mod
        hooks = self.mod.LAProxyHooks()
        hooks._bucket = None
        mod._CALL_BOOKED["f-A"] = _receipt("a" * 32, 500, 1.0)
        mod._CALL_BOOKED["f-B"] = _receipt("b" * 32, 900, 2.0)
        asyncio.run(hooks.async_log_failure_event(
            {"model": "nvidia_nim/x", "litellm_call_id": "f-A",
             "exception": RuntimeError("upstream died")}, None, None, None))
        self.assertNotIn("f-A", mod._CALL_BOOKED, "the failed call's entry is dropped")
        self.assertIn("f-B", mod._CALL_BOOKED, "an in-flight call's entry is intact")
        # no settle happened: the failed call's reservation STAYS booked in the
        # sidecar (the provider may have counted it) — the registry pop is the
        # only effect, so there is nothing further to assert here by design.

    def test_bound_prunes_only_dead_entries(self):
        mod = self.mod
        # the bound is DERIVED, not a literal: 2x (stretched-wait cap + stream timeout)
        self.assertEqual(mod._RECORD_BOUND,
                         2.0 * (float(rl.STRETCHED_WAIT_CAP) + mod.STREAM_TIMEOUT_S))
        self.assertEqual(mod.STREAM_TIMEOUT_S, 300.0,
                         "LA_STREAM_TIMEOUT's documented default")
        base = 10_000.0
        old = _receipt("c" * 32, 1, base - mod._RECORD_BOUND - 1.0)
        edge = _receipt("d" * 32, 1, base - (mod._RECORD_BOUND - 1.0))
        new = _receipt("e" * 32, 1, base)
        mod._CALL_BOOKED.clear()
        mod._CALL_BOOKED["old"] = old
        mod._CALL_BOOKED["edge"] = edge

        class StubBucket:
            def model_cooling(self, model):
                return 0.0

            async def aacquire_grant(self, **kw):
                return 0.0, new

            def note_success(self, model=None, tokens_used=None, receipt=None):
                pass

        hooks = self._hooks_with(StubBucket())
        with unittest.mock.patch.object(mod, "_now", lambda: base):
            asyncio.run(hooks.async_pre_call_deployment_hook(self._kwargs("new"), None))
        self.assertNotIn("old", mod._CALL_BOOKED,
                         "an entry past the request-lifetime bound is pruned")
        self.assertIn("edge", mod._CALL_BOOKED,
                      "an entry one second inside the bound survives")
        self.assertIn("new", mod._CALL_BOOKED)

    def test_success_hook_resolves_the_booking_provider(self):
        # MEASURED (contract probe 2026-11-25): the success event reports the RESOLVED
        # model ('openai/gpt-oss-120b' for a groq/openai/... deployment), so the old
        # _bucket_for() lookup found no limits entry and the settle never ran. The
        # pre-call must hand the success hook the provider it booked under.
        mod = self.mod
        import litellm  # noqa: F401  (the hook import path needs it registered)

        class GroqBucket:
            def __init__(self):
                self.success = None

            def model_cooling(self, model):
                return 0.0

            async def aacquire_grant(self, **kw):
                return 0.0, _receipt("b" * 32, 2000, 1000.0)

            def note_success(self, model=None, tokens_used=None, receipt=None):
                self.success = (model, tokens_used, receipt)

        bucket = GroqBucket()
        hooks = mod.LAProxyHooks()
        hooks._bucket = None
        hooks._buckets = {"groq": bucket}
        pre = {"model": "groq/openai/gpt-oss-120b", "max_tokens": 2000,
               "litellm_call_id": "c-P", "messages": [{"role": "user", "content": "hi"}]}
        asyncio.run(hooks.async_pre_call_deployment_hook(pre, None))
        # the success event sees the RESOLVED model, without the vendor prefix
        post = {"model": "openai/gpt-oss-120b", "litellm_call_id": "c-P"}
        asyncio.run(hooks.async_log_success_event(
            post, {"usage": {"prompt_tokens": 1000, "completion_tokens": 250}}, 0, 1))
        self.assertIsNotNone(bucket.success,
                             "the settle must run even though the resolved model "
                             "names a provider with no limits entry")
        self.assertEqual(bucket.success[2].n, 2000)
        self.assertEqual(bucket.success[1], 1250)


class WinnerReceiptTests(unittest.TestCase):
    """The granting poll books the WINNER's own estimate and hands the winner its
    receipt through the shared state (both limiter classes)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _rig(self, cls, **lim_kw):
        tmp = tempfile.TemporaryDirectory()          # fresh per subTest: no shared sidecar
        self.addCleanup(tmp.cleanup)
        clock = FakeClock(1000.0)
        ledger = ql.QuotaLedger(os.path.join(tmp.name, "sidecar"), limits(), clock)
        lim = cls(os.path.join(tmp.name, "s"), rpm=2, clock=clock,
                  quota=ledger, **lim_kw)
        return clock, ledger, lim

    def test_winner_gets_its_own_receipt(self):
        for cls, lim_kw in ((rl.SlidingWindowLimiter, {}),
                            (rl.SmoothTokenBucket, {"capacity": 2})):
            # a long waiter TTL so the handoff needs no heartbeat dance in a sync test
            with self.subTest(cls.__name__), \
                 unittest.mock.patch.dict(os.environ, {"LA_NVIDIA_WAITER_TTL": "120"}):
                clock, ledger, lim = self._rig(cls, **lim_kw)
                self.assertEqual(lim.try_acquire(token=None), 0.0)   # fill the window
                self.assertEqual(lim.try_acquire(token=None), 0.0)
                with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": "sess-A"}):
                    self.assertGreater(lim.try_acquire(token="tkA", model="nvidia_nim/m",
                                                       tokens=500), 0.0,
                                       "A queues while the window is full")
                clock.now += 61.0       # t+61: the window drains / the bucket refills
                with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": "sess-B"}):
                    self.assertGreater(lim.try_acquire(token="tkB", model="nvidia_nim/m",
                                                       tokens=100), 0.0,
                                       "B polls and hands A the grant")
                with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": "sess-A"}):
                    self.assertEqual(lim.try_acquire(token="tkA", model="nvidia_nim/m",
                                                     tokens=500), 0.0, "A acks its grant")
                rec = lim._take_grant_receipt("tkA")
                self.assertIsNotNone(rec, "the winner's receipt is delivered on ack")
                self.assertEqual(rec.n, 500, "the WINNER's estimate, not the poller's")
                self.assertEqual(ledger.snapshot(clock.now)["day"]["tokens"], 500,
                                 "A's 500 booked at the grant; B booked nothing")

    def test_note_success_settles_the_receipt_both_classes(self):
        for cls, lim_kw in ((rl.SlidingWindowLimiter, {}),
                            (rl.SmoothTokenBucket, {"capacity": 4})):
            with self.subTest(cls.__name__):
                clock, ledger, lim = self._rig(cls, **lim_kw)
                rec = ledger.book(1000, clock.now)
                lim.note_success("nvidia_nim/m", tokens_used=125, receipt=rec)
                snap = ledger.snapshot(clock.now)
                self.assertEqual(snap["day"]["tokens"], 125, "refund 875 from the day counter")
                self.assertEqual(snap["tpm"]["used"], 125, "refund from the receipt's OWN stamp")
                day0 = snap["day"]["tokens"]
                lim.note_success("nvidia_nim/m", tokens_used=100, receipt=None)
                self.assertEqual(ledger.snapshot(clock.now)["day"]["tokens"], day0,
                                 "receipt=None writes nothing to the ledger")


class FallbackBoundaryTests(unittest.TestCase):
    """LiteLLM may reuse litellm_call_id across a fallback chain. The registry keys
    by call id, so the second attempt's receipt overwrites the first: the first
    booking stays booked (its refund is LOST), and no refund ever reaches the wrong
    provider. This is the documented boundary of the deferred waypoint (contract
    test 8b stays xfail)."""

    def test_second_attempt_owns_the_call_id_and_no_wrong_refund_happens(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clock = FakeClock(1000.0)
        ledger = ql.QuotaLedger(os.path.join(tmp.name, "sidecar"), limits(), clock)
        recA = ledger.book(700, 1000.0)                    # attempt 1 booked HERE
        mod = load_hooks()
        mod._CALL_BOOKED.clear()
        mod._CALL_BOOKED["same-call-id"] = recA
        recB = _receipt("f" * 32, 900, 1001.0, day=recA.day, week=recA.week)
        mod._book_call({"litellm_call_id": "same-call-id"}, recB)   # attempt 2 overwrites
        taken = mod._take_booked({"litellm_call_id": "same-call-id"})
        self.assertEqual(taken.id, recB.id, "the LAST attempt owns the call id")
        self.assertEqual(ledger.snapshot(1000.0)["day"]["tokens"], 700,
                         "attempt 1's booking is untouched — never a wrong-provider refund")


if __name__ == "__main__":
    unittest.main(verbosity=2)

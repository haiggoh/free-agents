"""tests/test_gui_jobs.py — Task 9: Loader generation/stale/cache semantics (no browser)."""
import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from fa_gui.jobs import STALE, Loader  # noqa: E402


class LoaderTests(unittest.TestCase):
    def test_caches_per_kind_no_reload(self):
        async def run():
            calls = []
            ldr = Loader()
            a = await ldr.load("remote", lambda: calls.append(1) or "roster")
            b = await ldr.load("remote", lambda: calls.append(2) or "other")
            return a, b, calls
        a, b, calls = asyncio.run(run())
        self.assertEqual(a, "roster")
        self.assertIs(b, a, "second load of the same kind returns the cache (no reload on Back)")
        self.assertEqual(len(calls), 1)

    def test_concurrent_same_kind_loads_coalesce(self):
        # Continuation plan Phase 2: identical concurrent loads are COALESCED — one fn run,
        # both callers get the same result. No sleep between the two calls: on asyncio's
        # single-threaded loop the cache-check -> _inflight write is one synchronous block,
        # so the second caller MUST join the first's task (a race here would mean threads).
        async def run():
            ldr = Loader()
            calls = []
            def slow():
                calls.append(1)
                time.sleep(0.2)
                return "slow"
            r1, r2 = await asyncio.gather(ldr.load("remote", slow, client="A"),
                                          ldr.load("remote", slow, client="B"))
            return r1, r2, calls
        r1, r2, calls = asyncio.run(run())
        self.assertEqual(len(calls), 1, "truly concurrent same-kind loads must coalesce into one run")
        self.assertEqual(r1, "slow")
        self.assertEqual(r2, "slow", "the coalesced caller gets the same result, not Stale")

    def test_leave_page_makes_inflight_result_stale(self):
        async def run():
            ldr = Loader()
            def slow():
                time.sleep(0.2)
                return "late"
            t = asyncio.ensure_future(ldr.load("local", slow))
            await asyncio.sleep(0.02)
            ldr.leave_page()          # user navigated away mid-load
            return await t
        self.assertIs(asyncio.run(run()), STALE)

    def test_leave_page_is_per_client(self):
        # Phase 2: one browser client navigating away must NOT stale another client's load
        async def run():
            ldr = Loader()
            def slow():
                time.sleep(0.2)
                return "late"
            ta = asyncio.ensure_future(ldr.load("local", slow, client="A"))
            tb = asyncio.ensure_future(ldr.load("remote", slow, client="B"))
            await asyncio.sleep(0.02)
            ldr.leave_page(client="A")      # client A navigated away mid-load
            return await asyncio.gather(ta, tb)
        ra, rb = asyncio.run(run())
        self.assertIs(ra, STALE, "client A left the page: its result is stale")
        self.assertEqual(rb, "late", "client B is unaffected by client A's navigation")

    def test_client_load_survives_legacy_global_leave_page(self):
        # legacy no-arg leave_page() bumps only the client-less generation, keeping the
        # old single-client contract intact
        async def run():
            ldr = Loader()
            def slow():
                time.sleep(0.2)
                return "late"
            t = asyncio.ensure_future(ldr.load("local", slow, client="A"))
            await asyncio.sleep(0.02)
            ldr.leave_page()                # legacy global bump — must NOT touch client A
            return await t
        self.assertEqual(asyncio.run(run()), "late")


if __name__ == "__main__":
    unittest.main()

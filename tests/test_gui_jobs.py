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

    def test_slower_first_load_loses_to_newer_generation(self):
        async def run():
            ldr = Loader()
            def slow():
                time.sleep(0.2)
                return "slow"
            def fast():
                return "fast"
            t1 = asyncio.ensure_future(ldr.load("remote", slow))
            await asyncio.sleep(0.02)
            t2 = asyncio.ensure_future(ldr.load("remote", fast))
            r1, r2 = await asyncio.gather(t1, t2)
            return r1, r2
        r1, r2 = asyncio.run(run())
        self.assertIs(r1, STALE, "the slower first load must come back Stale")
        self.assertEqual(r2, "fast")

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


if __name__ == "__main__":
    unittest.main()

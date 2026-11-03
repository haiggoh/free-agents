#!/usr/bin/env python3
"""tests/test_picker_startup_perf_0507.py — 0.50.8 Task 2: startup perf vs CHANGELOG claims.

CHANGELOG claims the first paint never waits on detection or inventory. Measured on the
0.50.8 branch (real binary, PTY, this machine, 2026-11-03): first byte 0.04 s, startup
loading label until ~6.4 s, settled ~13.5 s — and baseline main measured 0.09 s / 6.0 s /
10.2 s, so no regression. The >30 s reports were the pre-0.50.8 serial HF warm-up delaying
ROWS (fixed in Task 1, tests/test_picker_async_load_0507.py).

Pinned here (load-tolerant, generous ceiling): with the remote roster stubbed to land after
8 s, the home MENU (title painted) must appear in under 6 s — i.e. first paint does NOT wait
for the roster. Mutation anchor: making the first render wait for the "remote" inventory
pushes t_paint past the bound → red.

Usage: python3 tests/test_picker_startup_perf_0507.py -v   (or pytest)
"""
import os
import sys
import time
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import session_picker_model as m  # noqa: E402
from picker_harness import ANSI, RECORDER, REMOTE_INV, PTYCase  # noqa: E402

pytestmark = pytest.mark.load_sensitive  # PTY timing under shared load

TITLE_MARK = "Session Launcher"      # HomeScreen title: "Claude Code Free-Agents: Session Launcher"
ROSTER_DELAY_S = 8.0                 # the stubbed roster lands this late
PAINT_CEILING_S = 6.0                # generous, load-tolerant: menu must paint well before the roster
FULL_CEILING_S = 25.0                # roster + startup prefetch done by then


def slow_inventory_stub(text, secs):
    return ("#!/bin/sh\n"
            f"sleep {secs}\n"
            'for a in "$@"; do [ "$a" = --inventory ] && { cat <<\'EOF\'\n' + text +
            "EOF\nexit 0; }; done\n" + RECORDER.split("\n", 1)[1])


@pytest.fixture(autouse=True)
def _sandbox_catalog_cache():
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="fa-cat-cache-")
    old = os.environ.get(m.CATALOG_CACHE_DIR_ENV)
    os.environ[m.CATALOG_CACHE_DIR_ENV] = tmp
    yield
    if old is None:
        os.environ.pop(m.CATALOG_CACHE_DIR_ENV, None)
    else:
        os.environ[m.CATALOG_CACHE_DIR_ENV] = old
    shutil.rmtree(tmp, ignore_errors=True)


class StartupPerf(PTYCase):
    def test_first_paint_does_not_wait_for_the_roster(self):
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(slow_inventory_stub(REMOTE_INV, ROSTER_DELAY_S))
        stub.chmod(0o755)

        t0 = time.monotonic()
        self.spawn("home")
        # Pump until the home menu title is painted (or the paint ceiling).
        t_paint = None
        while time.monotonic() - t0 < PAINT_CEILING_S:
            self.pump(0.2)
            if TITLE_MARK in self.screen():
                t_paint = time.monotonic() - t0
                break
        self.assertIsNotNone(t_paint,
                             f"home menu not painted within {PAINT_CEILING_S}s — first paint waits on data")
        assert t_paint is not None          # narrow for the comparison below
        self.assertLess(t_paint, ROSTER_DELAY_S - 1.0,
                        f"menu painted at {t_paint:.2f}s but the roster lands at {ROSTER_DELAY_S}s — "
                        "the paint must not wait for it")

        # The full startup (roster landed, loading label gone) still completes under a
        # generous ceiling even on a loaded box. NOTE: self.buf accumulates every byte ever
        # printed, so "loading" stays in it forever — settle must be detected on NEW bytes,
        # not on the buffer.
        import select
        t_full = None
        last_loading = time.monotonic()
        while time.monotonic() - t0 < FULL_CEILING_S:
            r, _, _ = select.select([self.fd], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(self.fd, 65536).decode("utf-8", "replace")
                except OSError:
                    break
                if not chunk:
                    break
                self.buf += chunk
                if "loading" in ANSI.sub("", chunk):
                    last_loading = time.monotonic()
            elif time.monotonic() - last_loading > 2.0:
                t_full = time.monotonic() - t0
                break
        self.assertIsNotNone(t_full, f"startup never settled within {FULL_CEILING_S}s")
        print(f"\n  0.50.8 startup: t_paint={t_paint:.2f}s t_full={t_full:.2f}s "
              f"(roster stub delay {ROSTER_DELAY_S}s)")


if __name__ == "__main__":
    unittest.main()

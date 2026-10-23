#!/usr/bin/env python3
"""The repo's bash test suites, wired into the pytest gates (0.41.3).

The ~50 tests/*.sh suites predate the pytest gates and were never collected by them, so a
fixture could rot for weeks while every gate stayed green (measured 2026-10-23: six suites
red — one since the memguard phase-1 commit — and the promote flow that runs one of them as
its pin validator had never once executed). This module runs each suite as one pytest test,
so the ship gates exercise the whole fleet on every release.

Each suite is self-contained (its own sandbox, env pins, port guards); failures print the
suite's own output tail.
"""

import subprocess
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SUITES = sorted(str(p) for p in TESTS.glob("test_*.sh"))


@pytest.mark.parametrize("suite", SUITES, ids=lambda s: Path(s).name)
def test_shell_suite(suite: str):
    r = subprocess.run(["bash", suite], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (
        f"{Path(suite).name} failed:\n"
        f"{r.stdout[-4000:]}\n{r.stderr[-2000:]}"
    )

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
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
SUITES = sorted(str(p) for p in TESTS.glob("test_*.sh"))

# Shell suites and modules that drive real processes / ports / PTY / pbcopy and flaked
# repeatedly in full gate runs on a loaded shared box (2026-11-12: la_evict, la_reboot,
# rapid_backend, la_evict_ports lock, picker right-click-pbcopy — at loadavg 4-7). The gate
# runs -m 'not load_sensitive' (rtc-ship.json test_cmd), so these MUST carry the mark to be
# deselected there; test_heavy_suites_and_modules_are_deselected_by_the_gate_marker pins it.
HEAVY_SUITES = ("test_la_evict.sh", "test_la_reboot.sh", "test_rapid_backend.sh")
HEAVY_MODULES = ("test_la_evict_ports.py", "test_picker_hide_toggle_state_0510.py",
                 "test_no_real_notifications_0541.py")  # spawns nested xdist + osascript
# single test inside an otherwise light module (module-level function, so a mark works):
HEAVY_TESTS = ("test_moespresso_manager.py::test_a_4x_release_never_appears_as_installable",)


_PARAMS = [
    pytest.param(s, id=Path(s).name,
                 marks=pytest.mark.load_sensitive if Path(s).name in HEAVY_SUITES else ())
    for s in SUITES
]


@pytest.mark.parametrize("suite", _PARAMS)
def test_shell_suite(suite: str):
    r = subprocess.run(["bash", suite], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (
        f"{Path(suite).name} failed:\n"
        f"{r.stdout[-4000:]}\n{r.stderr[-2000:]}"
    )


def _collect(args, *paths):
    r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                        "-p", "no:cacheprovider", *args, *paths],
                       capture_output=True, text=True, cwd=TESTS, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


def test_heavy_suites_and_modules_are_deselected_by_the_gate_marker():
    heavy_ids = ([f"test_shell_suite[{s}]" for s in HEAVY_SUITES] + list(HEAVY_MODULES)
                 + list(HEAVY_TESTS))
    paths = ("test_shell_suites.py", *HEAVY_MODULES, "test_moespresso_manager.py")
    deselected = _collect(["-m", "not load_sensitive"], *paths)
    for hid in heavy_ids:
        assert hid not in deselected, \
            f"{hid} is still collected under -m 'not load_sensitive' — the ship gate would run it"
    everything = _collect([], *paths)
    for hid in heavy_ids:
        assert hid in everything, \
            f"control: {hid} must collect WITHOUT the mark, else the deselect assertion is vacuous"

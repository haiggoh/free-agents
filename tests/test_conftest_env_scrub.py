#!/usr/bin/env python3
"""conftest.py scrubs inherited LA_* variables before any test runs (test-shell-suites-red-live).

A plain "no LA_* in os.environ" assertion would pass vacuously from a clean shell — exactly the
shell in which the leak never shows. So the outer test PLANTS launcher-style variables and re-runs
pytest on this file; the inner probe (enabled by an LA_TEST_* knob, which the scrub keeps) asserts
they did not arrive.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = "LA_TEST_SCRUB_PROBE"
PLANTED = {"LA_LIVE_CONFIG_DIR": "/nonexistent/poisoned-live-config",
           "LA_MOESPRESSO_BIN": "/bin/sh",
           "LA_CUR_SERVE": "rapid"}


@pytest.mark.skipif(os.environ.get(PROBE) != "1", reason="inner probe; run by the outer test")
def test_inner_probe_sees_no_inherited_launcher_vars():
    leaked = sorted(k for k in PLANTED if k in os.environ)
    assert not leaked, f"conftest let launcher vars through: {leaked}"
    # The scrub must reach CHILD processes too — the bash suites are children.
    out = subprocess.run(["bash", "-c", "env"], capture_output=True, text=True).stdout
    assert "LA_LIVE_CONFIG_DIR=" not in out, "a spawned suite still inherits LA_LIVE_CONFIG_DIR"


def test_conftest_scrubs_launcher_env_from_the_run_and_its_children():
    env = {**os.environ, **PLANTED, PROBE: "1"}
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(Path(__file__)), "-k", "inner_probe"],
        capture_output=True, text=True, env=env, cwd=Path(__file__).parent.parent, timeout=120)
    assert r.returncode == 0 and "1 passed" in r.stdout, (
        f"inner probe did not pass under planted launcher env:\n{r.stdout[-3000:]}\n{r.stderr[-1000:]}")

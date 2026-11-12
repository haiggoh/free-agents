#!/usr/bin/env python3
"""Gate self-recovery (rtc 1.4.0): every per-test --timeout must sit BELOW the stall window.

rtc-ship's watchdog kills a gate that produces neither output nor CPU for the stall window
(default 600 s). A pytest-timeout dump can only print if the per-test timeout fires FIRST, so
with --timeout=900 against a 600 s window the dump never prints and the .stall report cannot
name the wedged test. This test pins the invariant: every --timeout=N in test_cmd and in
.rtc/pre-flight.sh satisfies N + 120 <= stall_seconds (120 s margin for the dump to print and
the watchdog's sample interval). RED on the old 900/600 config.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARGIN = 120


def _timeouts(text: str):
    # --timeout=900 and '--timeout 900' both match; '--timeout-method=thread' cannot
    # (the char after --timeout is '-', not '=' or space).
    return [int(m) for m in re.findall(r"--timeout[= ](\d+)", text)]


def _sources():
    cfg = json.loads((ROOT / "rtc-ship.json").read_text())
    srcs = {"rtc-ship.json test_cmd": cfg.get("test_cmd", "")}
    pf = ROOT / ".rtc" / "pre-flight.sh"
    if pf.exists():
        srcs[".rtc/pre-flight.sh"] = pf.read_text()
    return cfg, srcs


def test_every_per_test_timeout_is_below_the_stall_window():
    cfg, srcs = _sources()
    stall = int(cfg.get("stall_seconds", 600))
    for name, text in srcs.items():
        for t in _timeouts(text):
            assert t + MARGIN <= stall, (
                f"{name}: --timeout={t}s leaves {stall - t}s under the {stall}s stall "
                f"window — below the {MARGIN}s margin, so the timeout dump can never "
                f"print before the watchdog kills the gate"
            )


def test_stall_seconds_is_declared_when_a_timeout_is_set():
    cfg, srcs = _sources()
    any_timeout = any(_timeouts(t) for t in srcs.values())
    if any_timeout:
        assert "stall_seconds" in cfg, (
            "a per-test --timeout is set but rtc-ship.json declares no stall_seconds: "
            "the window falls back to the 600 s default and the invariant above is "
            "only pinned against that default"
        )

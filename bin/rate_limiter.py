#!/usr/bin/env python3
"""rate_limiter.py - a sliding-window request limiter shared by EVERY process on this machine.

WHY FILE-BACKED. NVIDIA's free tier allows 40 requests per minute per key, and every remote
session runs its own LiteLLM proxy in its own process. A limiter held in memory throttles one
proxy and lets three proxies send 120 RPM between them -- which is how one proxy log alone
collected 180 x 429 out of 864 requests. So the state lives in a small JSON file, and every
read-modify-write happens under fcntl.flock on that file.

WHY A SLIDING WINDOW, NOT A TOKEN BUCKET (0.19.13). The 0.19.10 bucket started FULL (40) and
refilled at 40/min, so any rolling 60 s could admit up to 79 requests -- roughly double the
published limit if NVIDIA counts per rolling minute. Two concurrent sessions then logged
1241 x 429 against 1139 x 200 while the bucket had queued almost nothing. The window below
admits at most LIMIT requests in ANY 60 s span, which is the literal reading of "40 per minute".

WHY IT WAITS INSTEAD OF FAILING. A 429 surfaced to Claude Code is retried by Claude Code, which
adds load to the very window that is full. Queueing here (bounded by max_wait) converts a burst
into a short delay that the user never sees as an error.

WHY A 429 PAUSES EVERYONE. If NVIDIA still answers 429, the window's idea of the limit is wrong
(or NVIDIA counts differently). note_429() records a machine-wide cooldown, so the OTHER
proxies stop too instead of spending their remaining allowance into the same wall, and returns
the window count at that moment -- the number that locates the real limit. It also books the
upstream attempts the caller could not see (see la_proxy_hooks.SDK_HIDDEN_RETRIES).

Usage:
  rate_limiter.py acquire [--max-wait S]  take one slot, waiting if needed; prints the wait
  rate_limiter.py status                  print the current window state, take nothing
  rate_limiter.py --help

Environment:
  LA_NVIDIA_THROTTLE_STATE  state file (default ~/.claude/local-agents/.nvidia_throttle_state)
  LA_NVIDIA_RPM             requests allowed in any rolling 60 s window (default 40)
  LA_NVIDIA_MAX_WAIT        seconds a caller may queue before giving up (default 120)
  LA_NVIDIA_429_COOLDOWN    seconds every proxy pauses after a 429 with no Retry-After (default 10)

Exit codes: 0 ok; 2 usage error; 3 timed out waiting for a slot.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import sys
import time
from pathlib import Path

DEFAULT_STATE = Path.home() / ".claude" / "local-agents" / ".nvidia_throttle_state"
WINDOW_SECONDS = 60.0


class RateLimitTimeout(Exception):
    """No slot became available within max_wait."""


class SlidingWindowLimiter:
    def __init__(self, path: Path | str | None = None, rpm: float | None = None,
                 clock=time.time, cooldown: float | None = None):
        self.path = Path(path or os.environ.get("LA_NVIDIA_THROTTLE_STATE") or DEFAULT_STATE)
        self.limit = int(float(rpm or os.environ.get("LA_NVIDIA_RPM") or 40))
        self.cooldown = float(cooldown if cooldown is not None
                              else os.environ.get("LA_NVIDIA_429_COOLDOWN") or 10)
        self._clock = clock

    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _read(self, fd: int, now: float) -> tuple[list[float], float]:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 65536)
        try:
            state = json.loads(raw) if raw else {}
            stamps = [float(s) for s in state["stamps"]]
            cooldown_until = float(state.get("cooldown_until", 0.0))
        except Exception:
            # A missing, corrupt or old-format (token bucket) file starts EMPTY: the safe
            # failure is one extra window, never a permanently full one that wedges every session.
            return [], 0.0
        # A stamp in the future (clock step) is treated as now, so a skewed clock can hold a
        # slot for at most one window, never forever.
        stamps = [min(s, now) for s in stamps if s > now - WINDOW_SECONDS]
        stamps.sort()
        return stamps, cooldown_until

    def _write(self, fd: int, stamps: list[float], cooldown_until: float) -> None:
        data = json.dumps({"stamps": stamps, "cooldown_until": cooldown_until,
                           "limit": self.limit}).encode()
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data)

    def try_acquire(self) -> float:
        """Take a slot if one is free. Returns 0.0 on success, else the seconds to wait.

        The whole read-prune-append-write runs under one exclusive lock, so two processes can
        never both take the last slot.
        """
        fd = self._locked()
        try:
            now = self._clock()
            stamps, cooldown_until = self._read(fd, now)
            if now < cooldown_until:
                self._write(fd, stamps, cooldown_until)
                return cooldown_until - now
            if len(stamps) < self.limit:
                stamps.append(now)
                self._write(fd, stamps, cooldown_until)
                return 0.0
            self._write(fd, stamps, cooldown_until)
            # The oldest request leaves the window first; wait for exactly that.
            return max(1e-3, stamps[-self.limit] + WINDOW_SECONDS - now)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def note_429(self, retry_after: float | None = None, hidden_attempts: int = 0) -> dict:
        """Record an upstream 429: pause every proxy. Returns the window state at that moment.

        hidden_attempts: upstream requests that were sent but never passed through acquire()
        (the OpenAI SDK's own retries inside one LiteLLM call). NVIDIA counted them, so the
        window counts them too, or it keeps under-reporting what was really sent.
        """
        fd = self._locked()
        try:
            now = self._clock()
            stamps, cooldown_until = self._read(fd, now)
            stamps.extend([now] * max(0, int(hidden_attempts)))
            pause = float(retry_after) if retry_after and retry_after > 0 else self.cooldown
            cooldown_until = max(cooldown_until, now + pause)
            self._write(fd, stamps, cooldown_until)
            return {"in_window": len(stamps), "limit": self.limit, "pause": pause}
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def acquire(self, max_wait: float | None = None) -> float:
        """Block until a slot is taken; returns the total seconds waited."""
        limit = _max_wait(max_wait)
        waited = 0.0
        while True:
            need = self.try_acquire()
            if need == 0.0:
                return waited
            if waited + need > limit:
                raise RateLimitTimeout(f"no NVIDIA slot within {limit:.0f}s")
            time.sleep(need)
            waited += need

    async def aacquire(self, max_wait: float | None = None) -> float:
        """Async twin of acquire() -- sleeps without blocking the proxy's event loop."""
        limit = _max_wait(max_wait)
        waited = 0.0
        while True:
            need = self.try_acquire()
            if need == 0.0:
                return waited
            if waited + need > limit:
                raise RateLimitTimeout(f"no NVIDIA slot within {limit:.0f}s")
            await asyncio.sleep(need)
            waited += need

    def status(self) -> dict:
        fd = self._locked()
        try:
            now = self._clock()
            stamps, cooldown_until = self._read(fd, now)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return {"path": str(self.path), "in_window": len(stamps), "limit": self.limit,
                "window_seconds": WINDOW_SECONDS,
                "cooldown_remaining": round(max(0.0, cooldown_until - now), 3)}


def _max_wait(value: float | None) -> float:
    return float(value if value is not None else os.environ.get("LA_NVIDIA_MAX_WAIT") or 120)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="rate_limiter.py",
        description="Machine-wide NVIDIA sliding-window limiter (file-backed, fcntl-locked).",
        epilog="Env: LA_NVIDIA_THROTTLE_STATE, LA_NVIDIA_RPM (default 40 per rolling 60 s), "
               "LA_NVIDIA_MAX_WAIT (default 120), LA_NVIDIA_429_COOLDOWN (default 10).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    acq = sub.add_parser("acquire", help="take one slot, waiting if needed")
    acq.add_argument("--max-wait", type=float, default=None)
    sub.add_parser("status", help="print window state without taking a slot")
    args = parser.parse_args(argv)

    limiter = SlidingWindowLimiter()
    if args.cmd == "status":
        print(json.dumps(limiter.status()))
        return 0
    try:
        waited = limiter.acquire(args.max_wait)
    except RateLimitTimeout as exc:
        print(f"rate_limiter: {exc}", file=sys.stderr)
        return 3
    print(f"{time.time():.3f} acquired waited={waited:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

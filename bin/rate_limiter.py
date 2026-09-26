#!/usr/bin/env python3
"""rate_limiter.py - a token bucket shared by EVERY process on this machine.

WHY FILE-BACKED. NVIDIA's free tier allows 40 requests per minute per key, and every remote
session runs its own LiteLLM proxy in its own process. A limiter held in memory throttles one
proxy and lets three proxies send 120 RPM between them -- which is how one proxy log alone
collected 180 x 429 out of 864 requests. So the bucket lives in a small JSON file, and every
read-modify-write happens under fcntl.flock on that file.

WHY IT WAITS INSTEAD OF FAILING. A 429 surfaced to Claude Code is retried by Claude Code, which
adds load to the very bucket that is empty. Queueing here (bounded by max_wait) converts a burst
into a short delay that the user never sees as an error.

Usage:
  rate_limiter.py acquire [--max-wait S]  take one token, waiting if needed; prints the wait
  rate_limiter.py status                  print the current bucket state, take nothing
  rate_limiter.py --help

Environment:
  LA_NVIDIA_THROTTLE_STATE  state file (default ~/.claude/local-agents/.nvidia_throttle_state)
  LA_NVIDIA_RPM             bucket capacity AND refill per minute (default 40)
  LA_NVIDIA_MAX_WAIT        seconds a caller may queue before giving up (default 120)

Exit codes: 0 ok; 2 usage error; 3 timed out waiting for a token.
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


class RateLimitTimeout(Exception):
    """No token became available within max_wait."""


class FileTokenBucket:
    def __init__(self, path: Path | str | None = None, rpm: float | None = None,
                 clock=time.time):
        self.path = Path(path or os.environ.get("LA_NVIDIA_THROTTLE_STATE") or DEFAULT_STATE)
        self.capacity = float(rpm or os.environ.get("LA_NVIDIA_RPM") or 40)
        self.refill_seconds = 60.0 / self.capacity    # 40 RPM -> one token every 1.5 s
        self._clock = clock

    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _read(self, fd: int, now: float) -> tuple[float, float]:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 4096)
        try:
            state = json.loads(raw) if raw else {}
            tokens, stamp = float(state["tokens"]), float(state["stamp"])
        except Exception:
            # A missing or corrupt file starts full: the safe failure is one extra burst,
            # never a permanently empty bucket that wedges every session.
            return self.capacity, now
        # Refill for the time that passed, capped at capacity. A stamp in the future (clock
        # step) is treated as now, so the bucket cannot be starved by a skewed clock.
        elapsed = max(0.0, now - stamp)
        return min(self.capacity, tokens + elapsed / self.refill_seconds), now

    def _write(self, fd: int, tokens: float, stamp: float) -> None:
        data = json.dumps({"tokens": tokens, "stamp": stamp, "capacity": self.capacity}).encode()
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data)

    def try_acquire(self) -> float:
        """Take a token if one is available. Returns 0.0 on success, else the seconds to wait.

        The whole read-refill-decrement-write runs under one exclusive lock, so two processes
        can never both spend the last token.
        """
        fd = self._locked()
        try:
            tokens, now = self._read(fd, self._clock())
            if tokens >= 1.0:
                self._write(fd, tokens - 1.0, now)
                return 0.0
            self._write(fd, tokens, now)
            return (1.0 - tokens) * self.refill_seconds
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def acquire(self, max_wait: float | None = None) -> float:
        """Block until a token is taken; returns the total seconds waited."""
        limit = _max_wait(max_wait)
        waited = 0.0
        while True:
            need = self.try_acquire()
            if need == 0.0:
                return waited
            if waited + need > limit:
                raise RateLimitTimeout(f"no NVIDIA token within {limit:.0f}s")
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
                raise RateLimitTimeout(f"no NVIDIA token within {limit:.0f}s")
            await asyncio.sleep(need)
            waited += need

    def status(self) -> dict:
        fd = self._locked()
        try:
            tokens, now = self._read(fd, self._clock())
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return {"path": str(self.path), "tokens": round(tokens, 3), "capacity": self.capacity,
                "refill_seconds": self.refill_seconds}


def _max_wait(value: float | None) -> float:
    return float(value if value is not None else os.environ.get("LA_NVIDIA_MAX_WAIT") or 120)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="rate_limiter.py",
        description="Machine-wide NVIDIA token bucket (file-backed, fcntl-locked).",
        epilog="Env: LA_NVIDIA_THROTTLE_STATE, LA_NVIDIA_RPM (default 40), "
               "LA_NVIDIA_MAX_WAIT (default 120).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    acq = sub.add_parser("acquire", help="take one token, waiting if needed")
    acq.add_argument("--max-wait", type=float, default=None)
    sub.add_parser("status", help="print bucket state without taking a token")
    args = parser.parse_args(argv)

    bucket = FileTokenBucket()
    if args.cmd == "status":
        print(json.dumps(bucket.status()))
        return 0
    try:
        waited = bucket.acquire(args.max_wait)
    except RateLimitTimeout as exc:
        print(f"rate_limiter: {exc}", file=sys.stderr)
        return 3
    print(f"{time.time():.3f} acquired waited={waited:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

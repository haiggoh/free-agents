#!/usr/bin/env python3
"""rate_limiter.py - a request limiter shared by EVERY process on this machine.

Two modes for NVIDIA's 40 RPM free tier:

1. SLIDING WINDOW (default, strict): At most LIMIT requests in ANY rolling 60s window.
   Guarantees never exceeding 40 RPM but can feel bursty (all 40 used, then 60s wait).

2. SMOOTH TOKEN BUCKET (opt-in): Small capacity (default 6) with continuous refill
   at rpm/60 tokens per second. Same 40 RPM average but smoother: small bursts allowed,
   then steady ~1.5s between requests. Prevents "all 40 then wait 60s" experience.

WHY FILE-BACKED. NVIDIA's free tier allows 40 requests per minute per key, and every remote
session runs its own LiteLLM proxy in its own process. A limiter held in memory throttles one
proxy and lets three proxies send 120 RPM between them -- which is how one proxy log alone
collected 180 x 429 out of 864 requests. So the state lives in a small JSON file, and every
read-modify-write happens under fcntl.flock on that file.

WHY SLIDING WINDOW OVER CLASSIC TOKEN BUCKET (0.19.13). The 0.19.10 bucket started FULL (40)
and refilled at 40/min, so any rolling 60 s could admit up to 79 requests -- roughly double
the published limit if NVIDIA counts per rolling minute. Two concurrent sessions then logged
1241 x 429 against 1139 x 200 while the bucket had queued almost nothing. The sliding window
admits at most LIMIT requests in ANY 60 s span, which is the literal reading of "40 per minute".

WHY SMOOTH BUCKET IS SAFE. It uses a SMALL capacity (default 6, not 40) with the SAME refill
rate (40/60 = 0.667/sec). In ANY 60s window it can admit at most capacity + 60*rate = 6 + 40 = 46
requests -- close to 40 with a small headroom. The 429 cooldown (below) handles any overshoot.

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
  LA_NVIDIA_RPM             requests allowed per minute (default 40)
  LA_NVIDIA_MODE            "sliding_window" (default) or "smooth_bucket"
  LA_NVIDIA_BUCKET_CAPACITY burst capacity for smooth_bucket mode (default 6)
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
DEFAULT_BUCKET_CAPACITY = 6  # Small capacity for smooth limiting


class RateLimitTimeout(Exception):
    """No slot became available within max_wait."""


class SmoothTokenBucket:
    """Token bucket with small capacity and continuous refill for smooth 40 RPM.

    Same average rate as sliding window (40 RPM = 0.667 tokens/sec) but with
    small capacity (default 6) allowing small bursts without the "all 40 then wait 60s"
    experience of a strict sliding window.
    """
    def __init__(self, path: Path | str | None = None, rpm: float | None = None,
                 capacity: int | None = None, clock=time.time, cooldown: float | None = None):
        self.path = Path(path or os.environ.get("LA_NVIDIA_THROTTLE_STATE") or DEFAULT_STATE)
        self.rate = float(rpm or os.environ.get("LA_NVIDIA_RPM") or 40) / 60.0  # tokens/sec
        self.capacity = int(capacity if capacity is not None
                           else os.environ.get("LA_NVIDIA_BUCKET_CAPACITY") or DEFAULT_BUCKET_CAPACITY)
        self.cooldown = float(cooldown if cooldown is not None
                              else os.environ.get("LA_NVIDIA_429_COOLDOWN") or 10)
        self._clock = clock
        self._tokens = float(self.capacity)
        self._last_refill = clock()
        self._cooldown_until = 0.0
        self._load()

    def _load(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                with open(self.path, 'r') as f:
                    data = json.load(f)
                # Handle migration from sliding window format
                if 'stamps' in data:
                    self._tokens = float(self.capacity)
                    self._last_refill = self._clock()
                    self._cooldown_until = 0.0
                else:
                    self._tokens = float(data.get('tokens', self.capacity))
                    self._last_refill = float(data.get('last_refill', self._clock()))
                    self._cooldown_until = float(data.get('cooldown_until', 0.0))
            except Exception:
                self._tokens = float(self.capacity)
                self._last_refill = self._clock()
                self._cooldown_until = 0.0

    def _save(self):
        data = {
            'tokens': self._tokens,
            'last_refill': self._last_refill,
            'capacity': self.capacity,
            'rate': self.rate,
            'cooldown_until': self._cooldown_until
        }
        # Atomic write
        tmp = self.path.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def _refill(self, now: float):
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now

    def try_acquire(self) -> float:
        """Take a token if available. Returns 0.0 on success, else seconds to wait.

        Reads state from file on EVERY call (like SlidingWindowLimiter) so concurrent
        processes see each other's token consumption immediately.
        """
        fd = self._locked()
        try:
            now = self._clock()
            # Read current state from file (handles concurrent updates)
            self._load_state(fd, now)

            if now < self._cooldown_until:
                self._save_state(fd)
                return self._cooldown_until - now

            self._refill(now)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                self._save_state(fd)
                return 0.0
            # Time until next token
            wait = (1.0 - self._tokens) / self.rate
            self._save_state(fd)
            return max(1e-3, wait)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _load_state(self, fd: int, now: float):
        """Load state from file and refill based on elapsed time."""
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 65536)
        try:
            state = json.loads(raw) if raw else {}
            self._tokens = float(state.get('tokens', self.capacity))
            self._last_refill = float(state.get('last_refill', now))
            self._cooldown_until = float(state.get('cooldown_until', 0.0))
        except Exception:
            self._tokens = float(self.capacity)
            self._last_refill = now
            self._cooldown_until = 0.0
        # Refill based on elapsed since last_refill
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now

    def _save_state(self, fd: int):
        data = json.dumps({
            'tokens': self._tokens,
            'last_refill': self._last_refill,
            'capacity': self.capacity,
            'rate': self.rate,
            'cooldown_until': self._cooldown_until
        }).encode()
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data)

    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def note_429(self, retry_after: float | None = None, hidden_attempts: int = 0) -> dict:
        """Record an upstream 429: pause every proxy."""
        fd = self._locked()
        try:
            now = self._clock()
            self._load_state(fd, now)
            # Book hidden attempts
            self._tokens = max(0.0, self._tokens - float(hidden_attempts))
            pause = float(retry_after) if retry_after and retry_after > 0 else self.cooldown
            self._cooldown_until = max(self._cooldown_until, now + pause)
            self._save_state(fd)
            # Estimate requests in window: capacity - current tokens + hidden attempts booked
            in_window = int(self.capacity - self._tokens + hidden_attempts)
            return {"in_window": in_window, "limit": int(self.rate * 60), "pause": pause}
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def acquire(self, max_wait: float | None = None) -> float:
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
            self._load_state(fd, now)
            return {
                "path": str(self.path),
                "tokens": round(self._tokens, 2),
                "capacity": self.capacity,
                "rate_per_sec": round(self.rate, 3),
                "rpm_equiv": round(self.rate * 60, 1),
                "cooldown_remaining": round(max(0.0, self._cooldown_until - now), 3),
                "mode": "smooth_bucket"
            }
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


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


def get_limiter(path: Path | str | None = None, rpm: float | None = None,
                clock=time.time, cooldown: float | None = None) -> SlidingWindowLimiter | SmoothTokenBucket:
    """Factory: returns the configured limiter mode."""
    mode = os.environ.get("LA_NVIDIA_MODE", "sliding_window").lower()
    if mode == "smooth_bucket":
        return SmoothTokenBucket(path=path, rpm=rpm, clock=clock, cooldown=cooldown)
    return SlidingWindowLimiter(path=path, rpm=rpm, clock=clock, cooldown=cooldown)


def _print_menu():
    """Print interactive menu for rate limiter configuration."""
    print("\n" + "=" * 60)
    print("NVIDIA Rate Limiter Configuration")
    print("=" * 60)
    print("Current mode: {}".format(os.environ.get("LA_NVIDIA_MODE", "sliding_window")))
    print("RPM limit:    {}".format(os.environ.get("LA_NVIDIA_RPM", "40")))
    if os.environ.get("LA_NVIDIA_MODE", "sliding_window").lower() == "smooth_bucket":
        print("Bucket cap:   {}".format(os.environ.get("LA_NVIDIA_BUCKET_CAPACITY", "6")))
    print("Max wait:     {}s".format(os.environ.get("LA_NVIDIA_MAX_WAIT", "120")))
    print("429 cooldown: {}s".format(os.environ.get("LA_NVIDIA_429_COOLDOWN", "10")))
    print("State file:   {}".format(os.environ.get("LA_NVIDIA_THROTTLE_STATE", DEFAULT_STATE)))
    print("-" * 60)
    print("Options:")
    print("  1) Switch to sliding_window (strict 40 RPM rolling window)")
    print("  2) Switch to smooth_bucket (smoother, small bursts, 40 RPM avg)")
    print("  3) Set RPM limit")
    print("  4) Set bucket capacity (smooth_bucket only)")
    print("  5) Set max wait time")
    print("  6) Set 429 cooldown")
    print("  7) Show current status")
    print("  8) Reset state file")
    print("  9) Export config for shell (copy-paste)")
    print("  q) Quit")
    print("=" * 60)


def _interactive_menu():
    """Interactive menu for configuring the rate limiter."""
    while True:
        _print_menu()
        try:
            choice = input("Select option: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if choice == 'q':
            break
        elif choice == '1':
            os.environ["LA_NVIDIA_MODE"] = "sliding_window"
            print("✓ Mode set to sliding_window")
        elif choice == '2':
            os.environ["LA_NVIDIA_MODE"] = "smooth_bucket"
            print("✓ Mode set to smooth_bucket")
        elif choice == '3':
            try:
                rpm = input("RPM limit (default 40): ").strip()
                if rpm:
                    os.environ["LA_NVIDIA_RPM"] = rpm
                    print(f"✓ RPM set to {rpm}")
            except (EOFError, KeyboardInterrupt):
                print()
        elif choice == '4':
            try:
                cap = input("Bucket capacity (default 6): ").strip()
                if cap:
                    os.environ["LA_NVIDIA_BUCKET_CAPACITY"] = cap
                    print(f"✓ Capacity set to {cap}")
            except (EOFError, KeyboardInterrupt):
                print()
        elif choice == '5':
            try:
                wait = input("Max wait seconds (default 120): ").strip()
                if wait:
                    os.environ["LA_NVIDIA_MAX_WAIT"] = wait
                    print(f"✓ Max wait set to {wait}s")
            except (EOFError, KeyboardInterrupt):
                print()
        elif choice == '6':
            try:
                cd = input("429 cooldown seconds (default 10): ").strip()
                if cd:
                    os.environ["LA_NVIDIA_429_COOLDOWN"] = cd
                    print(f"✓ Cooldown set to {cd}s")
            except (EOFError, KeyboardInterrupt):
                print()
        elif choice == '7':
            limiter = get_limiter()
            print(json.dumps(limiter.status(), indent=2))
        elif choice == '8':
            confirm = input("Really reset state file? (y/N): ").strip().lower()
            if confirm == 'y':
                path = Path(os.environ.get("LA_NVIDIA_THROTTLE_STATE", DEFAULT_STATE))
                if path.exists():
                    path.unlink()
                print("✓ State file reset")
        elif choice == '9':
            print("\n# Copy-paste into your shell:")
            print(f'export LA_NVIDIA_MODE="{os.environ.get("LA_NVIDIA_MODE", "sliding_window")}"')
            print(f'export LA_NVIDIA_RPM="{os.environ.get("LA_NVIDIA_RPM", "40")}"')
            if os.environ.get("LA_NVIDIA_MODE", "sliding_window").lower() == "smooth_bucket":
                print(f'export LA_NVIDIA_BUCKET_CAPACITY="{os.environ.get("LA_NVIDIA_BUCKET_CAPACITY", "6")}"')
            print(f'export LA_NVIDIA_MAX_WAIT="{os.environ.get("LA_NVIDIA_MAX_WAIT", "120")}"')
            print(f'export LA_NVIDIA_429_COOLDOWN="{os.environ.get("LA_NVIDIA_429_COOLDOWN", "10")}"')
            print(f'export LA_NVIDIA_THROTTLE_STATE="{os.environ.get("LA_NVIDIA_THROTTLE_STATE", str(DEFAULT_STATE))}"')
        else:
            print("Invalid option")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="rate_limiter.py",
        description="Machine-wide NVIDIA rate limiter (file-backed, fcntl-locked).",
        epilog="Env: LA_NVIDIA_THROTTLE_STATE, LA_NVIDIA_RPM (default 40), "
               "LA_NVIDIA_MODE (sliding_window|smooth_bucket), "
               "LA_NVIDIA_BUCKET_CAPACITY (default 6), "
               "LA_NVIDIA_MAX_WAIT (default 120), LA_NVIDIA_429_COOLDOWN (default 10).")
    sub = parser.add_subparsers(dest="cmd", required=False)
    acq = sub.add_parser("acquire", help="take one slot, waiting if needed")
    acq.add_argument("--max-wait", type=float, default=None)
    sub.add_parser("status", help="print window state without taking a slot")
    sub.add_parser("menu", help="interactive configuration menu")
    args = parser.parse_args(argv)

    if args.cmd == "menu" or (not argv and sys.stdin.isatty()):
        _interactive_menu()
        return 0

    if not args.cmd:
        parser.print_help()
        return 2

    limiter = get_limiter()
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

#!/usr/bin/env python3
"""quota_ledger.py — the quota SIDECAR for a provider bucket (spec C2, provider-buckets).

The NVIDIA main state file's key set is FROZEN (live 0.61.x proxies rewrite it wholesale and
drop unknown keys), so every NEW dimension — tokens/min, requests+tokens per UTC day and per
ISO week — lives in a sidecar `<state>.quota` file that old code never opens. Lock order is
always main → sidecar: the caller holds the main lock and takes the sidecar lock inside it.

Counters are keyed by UTC day (`YYYY-MM-DD`) and ISO week (`YYYY-Www`) from time.time(), never
by a monotonic clock, so a laptop waking from sleep cannot manufacture a burst (a 9-hour gap
refills to capacity, never beyond).

Usage:
  quota_ledger.py --help
  quota_ledger.py demo [PATH]   print a snapshot of a sidecar (read-only)

Environment: none (path is a constructor arg).
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import sys
from pathlib import Path

WINDOW_SECONDS = 60.0


def _utc_day(now: float) -> str:
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%d")


def _utc_week(now: float) -> str:
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%G-W%V")


def _next_day(now: float) -> float:
    d = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    nxt = (d + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.timestamp()


def _next_week(now: float) -> float:
    d = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    days_to_monday = (7 - d.weekday()) % 7 or 7
    nxt = (d + datetime.timedelta(days=days_to_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return nxt.timestamp()


def _empty() -> dict:
    return {"tpm": {"stamps": [], "limit": None},
            "day": {"key": None, "requests": 0, "tokens": 0,
                    "limit_rpd": None, "limit_tpd": None, "remaining_requests": None},
            "week": {"key": None, "requests": 0, "tokens": 0,
                     "limit_rpw": None, "limit_tpdw": None},
            "exhausted": {"tpm": 0.0, "day": 0.0, "week": 0.0}}


class QuotaLedger:
    """File-backed sidecar counters for one provider. Callers hold the MAIN limiter lock;
    every method takes the sidecar lock itself (lock order main -> sidecar)."""

    def __init__(self, path: Path | str, limits, clock=__import__("time").time):
        self.path = Path(path)
        self.limits = limits          # provider_limits.ProviderLimits
        self._clock = clock

    # -- io -------------------------------------------------------------------
    def _locked(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)   # lock order: caller holds main, ledger takes sidecar
        return fd

    def _load(self, fd) -> dict:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = b""
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            raw += b
        try:
            st = json.loads(raw) if raw else {}
        except ValueError:
            st = {}
        base = _empty()
        for k in base:
            if isinstance(st.get(k), dict):
                base[k].update(st[k])
        return base

    def _save(self, fd, st: dict) -> None:
        data = json.dumps(st).encode()
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data)

    def _rollover(self, st: dict, now: float) -> None:
        """Reset the day/week buckets when their UTC key changed (sleep-safe)."""
        day, week = _utc_day(now), _utc_week(now)
        if st["day"]["key"] != day:
            st["day"].update(key=day, requests=0, tokens=0, remaining_requests=None)
        if st["week"]["key"] != week:
            st["week"].update(key=week, requests=0, tokens=0)
        # NOTE: exhausted[] marks are absolute timestamps and survive rollovers — a quota
        # refusal's reset_at can legitimately sit in the next day.

    # -- public API (all take the sidecar lock) --------------------------------
    def room(self, tokens: int, now: float) -> float:
        """Seconds until a request of `tokens` fits (0.0 = fits now; inf = never, e.g.
        tokens > tpm)."""
        fd = self._locked()
        try:
            st = self._load(fd)
            self._rollover(st, now)
            L = self.limits
            st["tpm"]["limit"] = L.tpm
            st["day"].update(limit_rpd=L.rpd, limit_tpd=L.tpd)
            st["week"].update(limit_rpw=L.rpw)
            worst = 0.0
            # tpm window
            if L.tpm:
                stamps = [s for s in st["tpm"]["stamps"]
                          if now - float(s.get("t", 0.0)) < WINDOW_SECONDS]
                st["tpm"]["stamps"] = stamps
                used = sum(int(s.get("n", 0)) for s in stamps)
                if tokens > L.tpm:
                    return float("inf")
                if used + tokens > L.tpm and stamps:
                    worst = max(worst, stamps[0]["t"] + WINDOW_SECONDS - now)
            if st["exhausted"]["tpm"] > now:
                worst = max(worst, st["exhausted"]["tpm"] - now)
            # day (a header override is authoritative even without a local rpd)
            rem = st["day"]["remaining_requests"]
            if rem is None and L.rpd is not None:
                rem = L.rpd - st["day"]["requests"]
            if rem is not None and rem < 1:
                worst = max(worst, _next_day(now) - now)
            if L.tpd is not None and st["day"]["tokens"] + tokens > L.tpd:
                worst = max(worst, _next_day(now) - now)
            if st["exhausted"]["day"] > now:
                worst = max(worst, st["exhausted"]["day"] - now)
            # week
            if L.rpw is not None and st["week"]["requests"] + 1 > L.rpw:
                worst = max(worst, _next_week(now) - now)
            if st["exhausted"]["week"] > now:
                worst = max(worst, st["exhausted"]["week"] - now)
            return worst
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def book(self, tokens: int, now: float) -> None:
        fd = self._locked()
        try:
            st = self._load(fd)
            self._rollover(st, now)
            if self.limits.tpm:
                st["tpm"]["stamps"].append({"t": now, "n": int(tokens)})
            st["day"]["requests"] += 1
            st["day"]["tokens"] += int(tokens)
            if st["day"]["remaining_requests"] is not None:
                st["day"]["remaining_requests"] -= 1   # the adopted figure counts down locally
            st["week"]["requests"] += 1
            st["week"]["tokens"] += int(tokens)
            self._save(fd, st)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def refund(self, n: int, now: float) -> None:
        """Give back `n` tokens (booked estimate was too high); day/week counters, and the
        newest tpm stamp."""
        fd = self._locked()
        try:
            st = self._load(fd)
            self._rollover(st, now)
            st["day"]["tokens"] = max(0, st["day"]["tokens"] - int(n))
            st["week"]["tokens"] = max(0, st["week"]["tokens"] - int(n))
            stamps = st["tpm"]["stamps"]
            if stamps:
                stamps[-1]["n"] = max(0, int(stamps[-1].get("n", 0)) - int(n))
            self._save(fd, st)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def adopt_headers(self, headers, now: float) -> None:
        """x-ratelimit-* headers (when the provider sends them) override the local remaining
        figures for the current day."""
        h = {str(k).lower(): v for k, v in (headers or {}).items()}
        rem_req = h.get("x-ratelimit-remaining-requests")
        fd = self._locked()
        try:
            st = self._load(fd)
            self._rollover(st, now)
            if rem_req is not None:
                try:
                    st["day"]["remaining_requests"] = int(float(rem_req))
                except (TypeError, ValueError):
                    pass
            self._save(fd, st)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def mark_exhausted(self, dimension: str, until: float) -> None:
        fd = self._locked()
        try:
            st = self._load(fd)
            st["exhausted"][dimension] = float(until)
            self._save(fd, st)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def snapshot(self, now: float) -> dict:
        fd = self._locked()
        try:
            st = self._load(fd)
            self._rollover(st, now)
            L = self.limits
            stamps = [s for s in st["tpm"]["stamps"]
                      if now - float(s.get("t", 0.0)) < WINDOW_SECONDS]
            tpm_used = sum(int(s.get("n", 0)) for s in stamps)
            return {
                "tpm": {"used": tpm_used, "limit": L.tpm},
                "day": {"key": st["day"]["key"], "requests": st["day"]["requests"],
                        "tokens": st["day"]["tokens"], "limit_rpd": L.rpd,
                        "limit_tpd": L.tpd,
                        "remaining_rpd": (st["day"]["remaining_requests"]
                                          if st["day"]["remaining_requests"] is not None
                                          else (L.rpd - st["day"]["requests"]
                                                if L.rpd is not None else None))},
                "week": {"key": st["week"]["key"], "requests": st["week"]["requests"],
                         "tokens": st["week"]["tokens"], "limit_rpw": L.rpw},
                "exhausted": dict(st["exhausted"]),
            }
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quota_ledger.py",
                                 description="Quota sidecar for a provider bucket "
                                             "(<state>.quota). 'demo PATH' prints a snapshot read-only.")
    sub = ap.add_subparsers(dest="cmd")
    d = sub.add_parser("demo", help="print a sidecar snapshot (read-only)")
    d.add_argument("path", nargs="?", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "demo" and args.path:
        import time
        from provider_limits import ProviderLimits
        dummy = ProviderLimits(rpm=None, tpm=None, rpd=None, rpw=None, tpd=None,
                               dispatch_only=False, reserves_on_request=False,
                               measured_at="", source="demo")
        led = QuotaLedger(args.path, dummy)
        print(json.dumps(led.snapshot(time.time()), indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

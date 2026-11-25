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
import dataclasses
import datetime
import fcntl
import json
import os
import sys
import uuid
from pathlib import Path

WINDOW_SECONDS = 60.0
SETTLED_TTL = WINDOW_SECONDS + 86400.0   # a settled id is remembered a TPM window + 1 day


@dataclasses.dataclass(frozen=True)
class Receipt:
    """Identity of ONE reservation: book() mints it, settle() reconciles only it."""

    id: str          # uuid4 hex
    n: int           # tokens booked (the estimate)
    at: float        # booking time (epoch seconds)
    day: str         # "YYYY-MM-DD" UTC of the booking
    week: str        # ISO "YYYY-Www" of the booking

    def to_json(self) -> dict:
        return {"id": self.id, "n": self.n, "at": self.at,
                "day": self.day, "week": self.week}

    @staticmethod
    def from_json(d: dict) -> "Receipt":
        return Receipt(id=str(d["id"]), n=int(d["n"]), at=float(d["at"]),
                       day=str(d["day"]), week=str(d["week"]))


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
            "exhausted": {"tpm": 0.0, "day": 0.0, "week": 0.0},
            "settled": {},
            "receipts": {}}


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
        for k, v in st.items():          # keys a FUTURE writer added: we are its old reader
            if k not in base and isinstance(v, dict):
                base[k] = v
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

    def book(self, tokens: int, now: float) -> Receipt:
        """Reserve `tokens` for one request; returns the request's Receipt — the
        ONLY identity settle() reconciles against (0.63.3)."""
        fd = self._locked()
        try:
            st = self._load(fd)
            self._rollover(st, now)
            self._prune_settled(st, now)
            receipt = Receipt(id=uuid.uuid4().hex, n=int(tokens), at=float(now),
                              day=_utc_day(now), week=_utc_week(now))
            # The receipt RECORD is what distinguishes "our booking, stamp expired"
            # (settle -> settled, day/week only) from a receipt this file never knew
            # (an old sidecar writer dropped it — Policy U -> unknown). Old writers
            # drop the whole receipts key; the stamps' ids survive inside their list.
            st["receipts"][receipt.id] = {"n": receipt.n, "at": receipt.at,
                                          "day": receipt.day, "week": receipt.week}
            if self.limits.tpm:
                st["tpm"]["stamps"].append({"t": now, "n": int(tokens),
                                            "id": receipt.id})
            st["day"]["requests"] += 1
            st["day"]["tokens"] += int(tokens)
            if st["day"]["remaining_requests"] is not None:
                st["day"]["remaining_requests"] -= 1   # the adopted figure counts down locally
            st["week"]["requests"] += 1
            st["week"]["tokens"] += int(tokens)
            self._save(fd, st)
            return receipt
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _prune_settled(st: dict, now: float) -> None:
        for key in ("settled", "receipts"):
            d = st.get(key)
            if isinstance(d, dict):
                if key == "settled":
                    stale = [k for k, v in d.items()
                             if now - float(v) > SETTLED_TTL]
                else:
                    stale = [k for k, v in d.items()
                             if now - float(v.get("at", 0.0)) > SETTLED_TTL]
                for k in stale:
                    d.pop(k, None)

    def settle(self, receipt: Receipt, used: int | None, now: float) -> str:
        """Reconcile EXACTLY the reservation `receipt` was booked as (idempotent).

        used None -> "no-usage" (absent usage is never treated as zero). used above
        the estimate books the difference as extra usage ("over"). Otherwise the
        over-estimate is refunded from the receipt's OWN stamp (while inside the
        TPM window) and from the day/week counters ONLY while they are still the
        booking's period. A receipt whose stamp is gone (pruned by an old sidecar
        writer, or aged out of the window) refunds only the matching period
        counters and reads "unknown" — never another stamp (Policy U). Returns
        "settled" | "already" | "unknown" | "over" | "no-usage".
        """
        fd = self._locked()
        try:
            st = self._load(fd)
            self._rollover(st, now)
            self._prune_settled(st, now)
            if used is None:
                return "no-usage"
            settled = st.setdefault("settled", {})
            if str(receipt.id) in settled:
                return "already"
            used = int(used)
            tpm_active = bool(self.limits.tpm)
            known = str(receipt.id) in st.get("receipts", {})
            target = None
            if tpm_active:
                for s in st["tpm"]["stamps"]:
                    if (s.get("id") == receipt.id
                            and now - float(s.get("t", 0.0)) < WINDOW_SECONDS):
                        target = s
                        break
            diff = int(receipt.n) - used
            if diff < 0:
                extra = -diff                 # usage above the estimate is real spend
                st["day"]["tokens"] += extra
                st["week"]["tokens"] += extra
                if tpm_active:
                    st["tpm"]["stamps"].append({"t": now, "n": extra})
                outcome = "over"
            else:
                if target is not None:
                    target["n"] = max(0, int(target.get("n", 0)) - diff)
                if st["day"]["key"] == receipt.day:
                    st["day"]["tokens"] = max(0, st["day"]["tokens"] - diff)
                if st["week"]["key"] == receipt.week:
                    st["week"]["tokens"] = max(0, st["week"]["tokens"] - diff)
                # Policy U: this file never knew the receipt (an old sidecar writer
                # dropped the receipts record). An expired stamp alone is NOT unknown —
                # the receipt record survives it (long streams settle this way).
                outcome = "unknown" if not known else "settled"
            settled[str(receipt.id)] = float(now)
            self._save(fd, st)
            return outcome
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def refund(self, n: int, now: float) -> None:
        """DEPRECATED (0.63.3): refunds the NEWEST stamp — the exact defect receipts
        replace. Kept for the existing ledger tests and external callers; a call
        from rate_limiter is a regression and raises."""
        import inspect
        caller = inspect.stack()[1].frame.f_globals.get("__name__", "")
        if caller.split(".")[-1] == "rate_limiter":
            raise RuntimeError(
                "quota_ledger.refund() is deprecated: settle(receipt, used) instead")
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

#!/usr/bin/env python3
"""rate_limiter.py - a request limiter shared by EVERY process on this machine.

Two modes for NVIDIA's 40 RPM free tier:

1. SLIDING WINDOW (default, strict): At most LIMIT requests in ANY rolling 60s window.
   Guarantees never exceeding 40 RPM but can feel bursty (all 40 used, then 60s wait).

2. SMOOTH TOKEN BUCKET (opt-in): Small capacity (default 6) with continuous refill
   at rpm/60 tokens per second. Same 40 RPM average but smoother: small bursts allowed,
   then steady ~1.5s between requests. Prevents "all 40 then wait 60s" experience.

EXPONENTIAL BACKOFF FOR 429s (new in 0.20.x). When NVIDIA returns 429:
- 1st 429: base_cooldown (30s)
- 2nd 429: base_cooldown * multiplier (60s)
- 3rd 429: base_cooldown * multiplier^2 (120s)
- etc., capped at max_cooldown (300s)
Resets on successful request. This prevents hammering an already-overloaded endpoint.

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

FAIRNESS QUEUE (2026-11-20, design docs/superpowers/specs/2026-11-19-nvidia-rate-limiter-
fairness-design.md). Waiting is visible: acquire()/aacquire() register each call in the shared
"waiters" key, and the next free slot goes to the policy-ranked winner under the same lock —
never by polling luck, and no capacity is ever reserved idle. The new keys (waiters, grants,
deficit) are append-only, so older running proxies keep parsing the file and their window
accounting stays exact; they drop the new keys on their wholesale rewrites and new code
re-writes them on every poll (self-heal within waiter_poll_seconds). `policy: legacy`
hot-reloads to the pre-queue semantics as the kill-switch.

WHY A 429 PAUSES EVERYONE. If NVIDIA still answers 429, the window's idea of the limit is wrong
(or NVIDIA counts differently). note_429() records a machine-wide cooldown, so the OTHER
proxies stop too instead of spending their remaining allowance into the same wall, and returns
the window count at that moment -- the number that locates the real limit. It also books the
upstream attempts the caller could not see (see la_proxy_hooks.SDK_HIDDEN_RETRIES).

Usage:
  rate_limiter.py acquire [--max-wait S]  take one slot, waiting if needed; prints the wait
  rate_limiter.py status                  print the current window state, take nothing
  rate_limiter.py lane-toggle             flip THIS owner's priority lane (user action)
  rate_limiter.py menu                    open the NVIDIA rate limiter screen of the session picker
  rate_limiter.py --help

Settings chosen in the picker (csl → n) are saved in config/session-menu.local.json and read
here, so they now outlive the menu. Precedence: explicit argument > LA_NVIDIA_* environment
variable > saved picker setting > built-in default.

Environment:
  LA_NVIDIA_THROTTLE_STATE    state file (default ~/.claude/free-agents/.nvidia_throttle_state)
  LA_NVIDIA_RPM               requests allowed per minute (default 40)
  LA_NVIDIA_MODE              "sliding_window" (default) or "smooth_bucket"
  LA_NVIDIA_BUCKET_CAPACITY   burst capacity for smooth_bucket mode (default 6)
  LA_NVIDIA_MAX_WAIT          seconds a caller may queue before giving up (default 300)
  LA_NVIDIA_429_COOLDOWN      base cooldown seconds after a 429 (default 30)
  LA_NVIDIA_429_MAX_COOLDOWN  max cooldown seconds (cap for exponential backoff, default 300)
  LA_NVIDIA_429_BACKOFF_MULTIPLIER  exponential backoff multiplier (default 2.0)
  LA_NVIDIA_429_MAX_RETRIES   max retry attempts before giving up (default 5)
  FREE_AGENTS_CONFIG_DIR  where the saved picker settings live (alias: LA_SESSION_MENU_CONFIG_DIR; both set must agree; default: ~/.claude/free-agents/session-menu/)
  LA_NVIDIA_POLICY            grant policy: aging (default) | deficit | legacy (kill-switch)
  LA_NVIDIA_TIER1_SECONDS     aging tier 1 threshold seconds (default 45)
  LA_NVIDIA_TIER2_SECONDS     aging tier 2 threshold seconds (default 150)
  LA_NVIDIA_WAITER_TTL        stale-waiter prune seconds (default 15)
  LA_NVIDIA_WAITER_POLL       wait-loop sleep clamp / heartbeat interval seconds (default 2)
  LA_NVIDIA_DEFICIT_RATE      deficit accrual multiplier (default 1.0)
  LA_NVIDIA_DEFICIT_CAP_SECONDS  deficit ceiling seconds (default 600)
  LA_LIMITER_RESERVE          lane share cap fraction (default 0.2; env name kept from 0.50.1)
  LA_NVIDIA_DISPATCH_BOOST    "on" (default): dispatch/background rank ahead within tier 0 only
  LA_LIMITER_KIND             this caller's kind: interactive (default) | dispatch | background
  LA_LIMITER_LANE_APPROVAL    agent-initiated lane gate: ask (default) | agent
  LA_LIMITER_PRIORITY         "1" = register this process in the priority lane

Exit codes: 0 ok; 2 usage error; 3 timed out waiting for a slot.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import itertools
import json
import os
import sys
import time
from pathlib import Path

from quota_ledger import Receipt

DEFAULT_STATE = Path.home() / ".claude" / "free-agents" / ".nvidia_throttle_state"
WINDOW_SECONDS = 60.0
DEFAULT_BUCKET_CAPACITY = 6  # Small capacity for smooth limiting

# Exponential backoff configuration for 429 handling
DEFAULT_429_BASE_COOLDOWN = 30.0      # Base cooldown seconds (was 10)
DEFAULT_429_MAX_COOLDOWN = 300.0      # Max cooldown seconds (was 120 max_wait)
DEFAULT_429_BACKOFF_MULTIPLIER = 2.0  # Exponential backoff multiplier
DEFAULT_429_MAX_RETRIES = 5           # Max retry attempts before giving up
DEFAULT_MAX_WAIT = 300.0              # Queue bound (0.20.11 documented 300; code kept 120 until 0.22.0)
# The patient-tier ceiling on a load-stretched wait (stretched_wait's cap). la_proxy_hooks
# derives its booking-record bound from this — ONE source of truth for the longest a live
# request can sit in the queue.
STRETCHED_WAIT_CAP = 1800.0

# The saved picker key "cooldown" is the BASE cooldown (kept under its old name so saved
# files from before the backoff stay valid).
_ENV_FOR = {"rpm": "LA_NVIDIA_RPM", "mode": "LA_NVIDIA_MODE",
            "bucket_capacity": "LA_NVIDIA_BUCKET_CAPACITY", "max_wait": "LA_NVIDIA_MAX_WAIT",
            "cooldown": "LA_NVIDIA_429_COOLDOWN", "max_cooldown": "LA_NVIDIA_429_MAX_COOLDOWN",
            "backoff_multiplier": "LA_NVIDIA_429_BACKOFF_MULTIPLIER",
            "max_retries": "LA_NVIDIA_429_MAX_RETRIES",
            # fairness knobs (design docs/superpowers/specs/2026-11-19-nvidia-rate-limiter-
            # fairness-design.md). lane_share_cap keeps the env name LA_LIMITER_RESERVE so
            # existing env setups keep working under the new share-cap meaning.
            "policy": "LA_NVIDIA_POLICY", "tier1_seconds": "LA_NVIDIA_TIER1_SECONDS",
            "tier2_seconds": "LA_NVIDIA_TIER2_SECONDS", "waiter_ttl_seconds": "LA_NVIDIA_WAITER_TTL",
            "waiter_poll_seconds": "LA_NVIDIA_WAITER_POLL", "deficit_rate": "LA_NVIDIA_DEFICIT_RATE",
            "deficit_cap_seconds": "LA_NVIDIA_DEFICIT_CAP_SECONDS",
            "lane_share_cap": "LA_LIMITER_RESERVE", "dispatch_boost": "LA_NVIDIA_DISPATCH_BOOST",
            "lane_approval": "LA_LIMITER_LANE_APPROVAL", "fast_lane": "LA_LIMITER_FAST_LANE"}


def _saved_settings() -> dict:
    """The picker's saved rate_limiter section, or {} (absent, invalid, or unreadable).

    Read-only and fail-open to defaults: a proxy must never stop because a preference
    file is broken. session_menu_state validates types and ranges before returning."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import session_menu_state as sms
    cfg = sms.config_dir()   # loud abort on a conflicting env pair — OUTSIDE the fail-open try
    try:
        return sms.load(cfg).rate_limiter
    except Exception:
        return {}


def _setting(key: str, default):
    """Environment variable > saved picker setting > default (explicit args handled by callers)."""
    env = os.environ.get(_ENV_FOR[key])
    if env:
        return env
    saved = _saved_settings().get(key)
    return default if saved is None else saved


# --- fairness queue (design docs/superpowers/specs/2026-11-19-nvidia-rate-limiter-fairness-design.md)
POLICIES = ("aging", "deficit", "legacy")
KINDS = ("interactive", "dispatch", "background", "agent")  # agent: 0.63.0 fast-lane kind
DEFAULT_POLICY = "aging"
DEFAULT_TIER1_SECONDS = 45.0
DEFAULT_TIER2_SECONDS = 150.0
DEFAULT_WAITER_TTL_SECONDS = 15.0
DEFAULT_WAITER_POLL_SECONDS = 2.0
DEFAULT_DEFICIT_RATE = 1.0
DEFAULT_DEFICIT_CAP_SECONDS = 600.0
DEFAULT_LANE_SHARE_CAP = 0.2
DEFAULT_DISPATCH_BOOST = True
DEFAULT_FAST_LANE = "off"   # 0.63.0: off = 0.62.3 behavior (the A/B control arm)
DEFAULT_LANE_APPROVAL = "ask"

_TOKEN_COUNTER = itertools.count()
_SETTINGS_CACHE = {}


def _saved_settings_cached() -> dict:
    """_saved_settings() behind an mtime-keyed cache: one stat() per call instead of a
    read+parse, so the hot grant path can re-read saved settings (hot reload) cheaply."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import session_menu_state as sms
    cfg = sms.config_dir()   # loud abort on a conflicting env pair — OUTSIDE the fail-open try
    try:
        p = Path(cfg) / "session-menu.local.json"
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
        hit = _SETTINGS_CACHE.get("entry")
        if hit is not None and hit[0] == key:
            return hit[1]
        val = _saved_settings()
        _SETTINGS_CACHE["entry"] = (key, val)
        return val
    except Exception:
        return {}


def _hot(key: str, default):
    """Hot resolution for the NEW fairness knobs: env > saved picker setting (mtime-cached
    read, so a submenu change hot-applies to running proxies) > default. The eight
    pre-fairness settings keep their __init__-time read via _setting()."""
    env = os.environ.get(_ENV_FOR[key])
    if env not in (None, ""):
        return env
    saved = _saved_settings_cached().get(key)
    return default if saved is None else saved


def _cfg() -> dict:
    """Snapshot of the hot fairness knobs, coerced and clamped to their validated ranges."""
    def num(key, default, lo, hi):
        try:
            v = float(_hot(key, default))
        except (TypeError, ValueError):
            return default
        return min(hi, max(lo, v))

    def choice(key, default, choices):
        v = str(_hot(key, default)).strip().lower()
        return v if v in choices else default

    def boolean(key, default):
        v = _hot(key, default)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)

    return {"policy": choice("policy", DEFAULT_POLICY, POLICIES),
            "tier1_seconds": num("tier1_seconds", DEFAULT_TIER1_SECONDS, 5, 600),
            "tier2_seconds": num("tier2_seconds", DEFAULT_TIER2_SECONDS, 10, 3600),
            "waiter_ttl_seconds": num("waiter_ttl_seconds", DEFAULT_WAITER_TTL_SECONDS, 5, 120),
            "waiter_poll_seconds": num("waiter_poll_seconds", DEFAULT_WAITER_POLL_SECONDS, 0.5, 10),
            "deficit_rate": num("deficit_rate", DEFAULT_DEFICIT_RATE, 0.1, 10),
            "deficit_cap_seconds": num("deficit_cap_seconds", DEFAULT_DEFICIT_CAP_SECONDS, 30, 7200),
            "lane_share_cap": num("lane_share_cap", DEFAULT_LANE_SHARE_CAP, 0.05, 0.9),
            "dispatch_boost": boolean("dispatch_boost", DEFAULT_DISPATCH_BOOST),
            "lane_approval": choice("lane_approval", DEFAULT_LANE_APPROVAL, ("ask", "agent"))}


def _waiter_token(owner: str) -> str:
    """Unique per acquire() call: one owner with several in-flight requests registers each."""
    return f"{owner}:{os.getpid()}:{next(_TOKEN_COUNTER)}"


def _kind(owner_opts: dict | None = None) -> str:
    k = (owner_opts or {}).get("kind") or os.environ.get("LA_LIMITER_KIND") or "interactive"
    k = str(k).strip().lower()
    return k if k in KINDS else "interactive"


def _prune_waiters(waiters: dict, now: float, ttl: float) -> int:
    """Drop waiters not heard from within ttl (granted entries get 2*ttl to ack), so a
    killed proxy cannot hold the queue head. Returns the pruned count."""
    stale = [tok for tok, w in waiters.items()
             if now - float(w.get("last_seen", 0.0)) > (2.0 * ttl if "granted" in w else ttl)]
    for tok in stale:
        waiters.pop(tok, None)
    return len(stale)


def _register_waiter(waiters: dict, token: str, owner: str, cls: str, kind: str,
                     model, now: float, tokens: int = 0) -> None:
    """Register or heartbeat one waiter. `arrived` is set once and never resets; every
    call refreshes last_seen (the heartbeat that survives long cooldowns). `tokens` is
    the caller's estimate, recorded ONCE ("tok") so the granting poll can book the
    WINNER's quota and mint its receipt (0.63.3); a heartbeat never overwrites it."""
    w = waiters.get(token)
    if w is None:
        waiters[token] = {"owner": owner, "arrived": now, "last_seen": now,
                          "class": cls, "kind": kind, "model": model or "",
                          "tok": int(tokens)}
    else:
        w["last_seen"] = now
        w["class"] = cls
        w["kind"] = kind


def _tier(arrived: float, now: float, t1: float, t2: float) -> int:
    """Aging tier of a waiter: 0 fresh, 1 past tier1_seconds, 2 past tier2_seconds."""
    waited = now - arrived
    if waited >= t2:
        return 2
    if waited >= t1:
        return 1
    return 0


def _rank(w: dict, now: float, cfg: dict) -> tuple:
    """Higher wins. (class, fast, tier, boost, -arrived): a manual lane beats everything;
    0.63.0 fast lane — a fast-lane kind (fast_lane setting) beats any non-priority request
    regardless of age tier, but still loses to the manual lane (class sorts first); an aged
    request beats a fresh one within the same fast class; within tier 0 only,
    dispatch/background rank ahead of interactive when dispatch_boost is on; then FIFO."""
    cls = 1 if w.get("class") == "priority" else 0
    fast = 1 if w.get("kind") in _fast_lane_kinds(cfg) else 0
    arrived = float(w.get("arrived", now))
    tier = _tier(arrived, now, cfg["tier1_seconds"], cfg["tier2_seconds"])
    boost = 1 if (tier == 0 and cfg["dispatch_boost"]
                  and w.get("kind") in ("dispatch", "background")) else 0
    return (cls, fast, tier, boost, -arrived)


def _lane_cap(limit: int, share_cap: float) -> int:
    """Max priority-owned grants in the rolling window while normal waiters are queued."""
    return max(1, round(share_cap * limit))


def _fast_lane_kinds(cfg: dict) -> frozenset:
    """0.63.0: kinds the fast lane ranks above interactive. off/empty = none (0.62.3 behavior);
    agents = dispatch + agent; all-noninteractive also background; unknown set value -> agents."""
    v = str(cfg.get("fast_lane", DEFAULT_FAST_LANE)).strip().lower()
    if v in ("", "off", "0", "false", "none"):
        return frozenset()
    if v == "all-noninteractive":
        return frozenset({"dispatch", "agent", "background"})
    return frozenset({"dispatch", "agent"})


def _prio_grants_sliding(stamps: list, owners: dict, fast_kinds=frozenset()) -> int:
    """Lane grants (priority-owned, plus fast-lane-kind-owned when the lane is on) currently
    in the window. Sliding mode derives the lane cap from the existing stamps + owners map —
    no new state keys (grants carry the kind as "k"; stamps via the owner's kind)."""
    n = 0
    for s in stamps:
        _t, o = _stamp_parts(s)
        if o == "legacy":
            continue
        opts = _owner_opts(owners, o)
        if opts["priority"] or opts["kind"] in fast_kinds:
            n += 1
    return n


def _owner_debt(deficit: dict, waiters: dict, owner: str, now: float, cfg: dict) -> float:
    """An owner's deficit: stored residual + sum over its waiting requests of
    (now - arrived) * deficit_rate, capped at deficit_cap_seconds."""
    total = float(deficit.get(owner, 0.0))
    for w in waiters.values():
        if "granted" not in w and w.get("owner") == owner:
            total += (now - float(w.get("arrived", now))) * cfg["deficit_rate"]
    return min(cfg["deficit_cap_seconds"], total)


def _pick_winner(waiters: dict, now: float, cfg: dict, owners: dict,
                 own_counts: dict, limit: int, prio_grants: int | None = None,
                 deficit: dict | None = None) -> str | None:
    """Rank the live waiters (stale already pruned, granted entries excluded) and return
    the winning token. The lane class is refreshed from the LIVE owners map at every
    evaluation, so a lane flip applies at the next poll. An owner at its rpm_scale cap is
    skipped; while >=1 normal waiter is registered, a priority grant additionally needs
    room under the lane share cap (with no normal waiters the cap does not bind — zero
    idle capacity)."""
    cap = _lane_cap(limit, cfg["lane_share_cap"])
    fast_kinds = _fast_lane_kinds(cfg)
    normal_waiting = False
    for tok, w in waiters.items():
        if "granted" in w:
            continue
        owner = w.get("owner", "")
        w["class"] = "priority" if _owner_opts(owners, owner)["priority"] else "normal"
        # 0.63.0: a fast-lane waiter is NOT a "normal" waiter — the share cap exists to protect
        # interactive sessions, and binds only while one is queued.
        if (w["class"] != "priority"
                and not (fast_kinds and w.get("kind") in fast_kinds)):
            normal_waiting = True
    eligible = []
    for tok, w in waiters.items():
        if "granted" in w:
            continue
        owner = w.get("owner", "")
        opts = _owner_opts(owners, owner)
        own_limit = max(1, int(limit * opts["rpm_scale"]))
        if own_counts.get(owner, 0) >= own_limit:
            continue
        # 0.63.0: while normal waiters queue, the lane share cap covers fast-lane grants too
        # (priority + fast kinds), so interactive sessions are never starved by the fast lane.
        is_lane = w["class"] == "priority" or (bool(fast_kinds) and w.get("kind") in fast_kinds)
        if (is_lane and normal_waiting and prio_grants is not None
                and prio_grants >= cap):
            continue
        eligible.append(tok)
    if not eligible:
        return None
    if cfg["policy"] == "deficit" and deficit is not None:
        def rank(tok):
            w = waiters[tok]
            cls = 1 if w.get("class") == "priority" else 0
            debt = _owner_debt(deficit, waiters, w.get("owner", ""), now, cfg)
            return (cls, debt, -float(w.get("arrived", now)))
        return max(eligible, key=rank)
    return max(eligible, key=lambda tok: _rank(waiters[tok], now, cfg))


def _owner() -> str:
    """0.50.1: who books a slot — LA_LIMITER_OWNER, else the session id, else the pid.
    Stamps are attributed to this owner, so the shared bucket is no longer anonymous."""
    o = os.environ.get("LA_LIMITER_OWNER")
    if o:
        return o
    sid = os.environ.get("LA_SESSION_IDENTITY") or ""
    if sid:
        try:
            return json.loads(sid).get("sessionId") or sid
        except Exception:
            return sid
    return f"pid-{os.getpid()}"


def _reserve_frac() -> float:
    """DEPRECATED (kept for compatibility): LA_LIMITER_RESERVE is now the lane SHARE CAP (hot
    key lane_share_cap, default 0.2), enforced at grant time only while normal waiters are
    queued. The old capacity partition (an idle reserve) is retired — no capacity is withheld."""
    try:
        return min(0.9, max(0.0, float(os.environ.get("LA_LIMITER_RESERVE", "0.0"))))
    except ValueError:
        return 0.0


def _owner_opts(owners: dict, owner: str) -> dict:
    """Per-owner options from the state map, with the process env as fallback."""
    o = owners.get(owner) if isinstance(owners, dict) else None
    o = o if isinstance(o, dict) else {}
    try:
        scale = float(o.get("rpm_scale", 1.0))
    except (TypeError, ValueError):
        scale = 1.0
    return {"priority": bool(o.get("priority", False)) or os.environ.get("LA_LIMITER_PRIORITY") == "1",
            "rpm_scale": min(8.0, max(0.05, scale)),
            "kind": o.get("kind")}


def _stamp(t: float, owner: str) -> dict:
    return {"t": float(t), "o": owner}


def _stamp_parts(s) -> tuple:
    """A stamp is {"t": ts, "o": owner}; a bare float is a pre-0.50.1 legacy stamp (owner "legacy")."""
    if isinstance(s, dict):
        try:
            return float(s.get("t", 0.0)), str(s.get("o", "legacy"))
        except (TypeError, ValueError):
            return 0.0, "legacy"
    try:
        return float(s), "legacy"
    except (TypeError, ValueError):
        return 0.0, "legacy"


class RateLimitTimeout(Exception):
    """No slot became available within max_wait."""


def _read_all(fd) -> bytes:
    """Read a state file to EOF (loop os.read until b""). One 64 KiB read TRUNCATES a file
    grown under heavy queueing, and the truncated JSON then failed the parse (L2)."""
    chunks = []
    while True:
        b = os.read(fd, 65536)
        if not b:
            return b"".join(chunks)
        chunks.append(b)


def _fail_safe_state(now: float, base_cooldown: float) -> dict:
    """The state a CORRUPT file must yield (L2, 0.62.0): nothing grants — tokens 0 / empty
    window and an ACTIVE cooldown — never the old reset to full capacity, which let a burst
    straight through right after a 429. Callers write it back and log 'failing SAFE'."""
    return {"tokens": 0.0, "last_refill": now, "stamps": [], "limit": 0,
            "cooldown_until": now + base_cooldown, "consecutive_429s": 0,
            "last_429_time": 0.0, "models": {}, "owners": {}, "waiters": {},
            "deficit": {}, "grants": []}


def _model_wait(models: dict, model: str | None, now: float) -> float:
    """Seconds the model still cools (0.0 = free to try). Shared by both limiter classes."""
    if not model:
        return 0.0
    entry = models.get(model)
    if not entry:
        return 0.0
    return max(0.0, float(entry.get("until", 0.0)) - now)


def _quota_wait(quota, now: float, tokens: int) -> float:
    """Sidecar wait for a request of `tokens` (0.0 = fits now; inf = never fits)."""
    if quota is None:
        return 0.0
    return quota.room(int(tokens), now)


def _quota_book(quota, now: float, tokens: int):
    """Book tokens in the sidecar at the grant instant; returns the reservation's
    Receipt (None when this provider has no sidecar). A 0-token grant still books:
    the request counts, and its receipt settles the real usage later (0.63.3)."""
    if quota is None:
        return None
    return quota.book(int(tokens), now)


def _book_winner(lim, w: dict, now: float) -> None:
    """Grant-time quota booking for a queue handoff: the WINNER's estimate (recorded
    at registration as "tok") is booked and its receipt rides in the waiter entry
    ("rcp") until the winner acks its next poll (0.63.3). Without a sidecar this
    records nothing, exactly as before receipts existed."""
    r = _quota_book(lim.quota, now, int(w.get("tok") or 0))
    if r is not None:
        w["rcp"] = r.to_json()


def _cool_model(models: dict, model: str, now: float, retry_after: float | None,
                base: float, mult: float, cap: float) -> tuple[float, float, int]:
    """Book one per-model 429 into `models` (mutated): backoff base * mult^(n-1), capped,
    n reset only after a quiet period longer than `cap`. Shared by both limiter modes so
    their per-model behaviour cannot drift. Returns (pause, backoff_cooldown, n)."""
    entry = models.get(model, {"until": 0.0, "n": 0, "last": 0.0})
    entry["n"] = int(entry.get("n", 0)) + 1
    if now - float(entry.get("last", 0.0)) > cap:
        entry["n"] = 1                    # quiet period: a new situation, not a run
    backoff_cooldown = min(base * (mult ** (entry["n"] - 1)), cap)
    pause = float(retry_after) if retry_after and retry_after > 0 else backoff_cooldown
    entry["until"] = max(float(entry.get("until", 0.0)), now + pause)
    entry["last"] = now
    models[model] = entry
    return pause, backoff_cooldown, entry["n"]


def _cooling(models: dict, now: float) -> dict:
    return {m: round(max(0.0, float(e.get("until", 0.0)) - now), 1)
            for m, e in models.items() if float(e.get("until", 0.0)) > now}


class SmoothTokenBucket:
    """Token bucket with small capacity and continuous refill for smooth 40 RPM.

    Same average rate as sliding window (40 RPM = 0.667 tokens/sec) but with
    small capacity (default 6) allowing small bursts without the "all 40 then wait 60s"
    experience of a strict sliding window.

    Implements exponential backoff for 429 errors:
    - Tracks consecutive 429s and increases cooldown exponentially
    - Resets backoff on successful requests
    """
    def __init__(self, path: Path | str | None = None, rpm: float | None = None,
                 capacity: int | None = None, clock=time.time, cooldown: float | None = None,
                 quota=None):
        self.path = Path(path or os.environ.get("LA_NVIDIA_THROTTLE_STATE") or DEFAULT_STATE)
        self.quota = quota                # 0.62.0 C2: QuotaLedger sidecar (<state>.quota) or None
        self.rate = float(rpm or _setting("rpm", 40)) / 60.0  # tokens/sec
        self.capacity = int(capacity if capacity is not None
                           else _setting("bucket_capacity", DEFAULT_BUCKET_CAPACITY))
        # Exponential backoff config
        self.base_cooldown = float(cooldown if cooldown is not None
                                   else _setting("cooldown", DEFAULT_429_BASE_COOLDOWN))
        self.max_cooldown = float(_setting("max_cooldown", DEFAULT_429_MAX_COOLDOWN))
        self.backoff_multiplier = float(_setting("backoff_multiplier", DEFAULT_429_BACKOFF_MULTIPLIER))
        self.max_retries = int(_setting("max_retries", DEFAULT_429_MAX_RETRIES))
        self._clock = clock
        self._tokens = float(self.capacity)
        self._last_refill = clock()
        self._cooldown_until = 0.0
        # Exponential backoff state
        self._consecutive_429s = 0
        self._last_429_time = 0.0
        self._models: dict = {}           # per-model cooldowns, same shape as the window's
        self._owners: dict = {}           # 0.50.1: per-owner {priority, rpm_scale}, live-editable
        self._waiters: dict = {}          # fairness queue (same schema as the window's)
        self._deficit: dict = {}          # deficit policy residuals
        self._grant_receipts: dict = {}   # 0.63.3: token -> Receipt, born at the grant
        self._grants: list = []           # rolling 60s grant log; feeds the lane share cap
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
                    self._consecutive_429s = 0
                    self._waiters = {}
                    self._deficit = {}
                    self._grants = []
                else:
                    self._tokens = float(data.get('tokens', self.capacity))
                    self._last_refill = float(data.get('last_refill', self._clock()))
                    self._cooldown_until = float(data.get('cooldown_until', 0.0))
                    self._consecutive_429s = int(data.get('consecutive_429s', 0))
                    self._last_429_time = float(data.get('last_429_time', 0.0))
                    owners = data.get('owners', {})
                    self._owners = owners if isinstance(owners, dict) else {}
                    waiters = data.get('waiters', {})
                    self._waiters = waiters if isinstance(waiters, dict) else {}
                    deficit = data.get('deficit', {})
                    self._deficit = deficit if isinstance(deficit, dict) else {}
                    grants = data.get('grants', [])
                    self._grants = [g for g in grants if isinstance(g, dict)] if isinstance(grants, list) else []
            except Exception:
                self._tokens = float(self.capacity)
                self._last_refill = self._clock()
                self._cooldown_until = 0.0
                self._consecutive_429s = 0
                self._last_429_time = 0.0
                self._owners = {}
                self._waiters = {}
                self._deficit = {}
                self._grants = []

    def _save(self):
        data = {
            'tokens': self._tokens,
            'last_refill': self._last_refill,
            'capacity': self.capacity,
            'rate': self.rate,
            'cooldown_until': self._cooldown_until,
            'consecutive_429s': self._consecutive_429s,
            'last_429_time': self._last_429_time,
            'owners': self._owners,
            'waiters': self._waiters,
            'deficit': self._deficit,
            'grants': self._grants
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

    def try_acquire(self, priority: bool | None = None, token: str | None = None,
                    kind: str | None = None, model: str | None = None,
                    tokens: int = 0) -> float:
        """Take a token if available. Returns 0.0 on success, else seconds to wait.
        `tokens` (0.62.0 C2) also books that many tokens in the quota sidecar; a request
        that can never fit (tokens > tpm) returns inf; while any sidecar dimension lacks
        room the request waits in the queue without consuming a main token.

        Reads state from file on EVERY call (like SlidingWindowLimiter) so concurrent
        processes see each other's token consumption immediately.
        Resets exponential backoff on successful acquisition only -- an EXPIRED pause no
        longer resets it (0.40.0, same fix as the sliding window).

        Per-owner rpm_scale s makes this owner's acquisitions cost 1/s tokens (s<1 =
        throttle: half rate costs 2 tokens).

        Fairness queue — the SAME protocol as SlidingWindowLimiter (shared _pick_winner):
        acquire()/aacquire() always pass a `token`; a failed call registers in `waiters` and
        the next affordable token is booked for the policy-ranked winner, logged in the
        rolling `grants` list (which also feeds the lane share cap). The reserve
        token-partition is retired: LA_LIMITER_RESERVE is the lane share cap only, and it
        binds solely while normal waiters are queued. token=None is the legacy peek; an
        empty registry behaves identically to it."""
        owner = _owner()
        opts = _owner_opts(self._owners, owner)
        prio = opts["priority"] if priority is None else bool(priority)
        cost = 1.0 / opts["rpm_scale"]
        cfg = _cfg()
        cls = "priority" if prio else "normal"
        wkind = kind or _kind(opts)
        limit = max(1, int(self.rate * 60))
        fd = self._locked()
        try:
            now = self._clock()
            # Read current state from file (handles concurrent updates)
            self._load_state(fd, now)
            _prune_waiters(self._waiters, now, cfg["waiter_ttl_seconds"])
            self._grants = [g for g in self._grants
                            if now - float(g.get("t", 0.0)) < WINDOW_SECONDS]

            def need_seconds() -> float:
                need = cost - self._tokens
                return need / self.rate if need > 0 else 1e-3

            if now < self._cooldown_until:
                if token is not None:
                    _register_waiter(self._waiters, token, owner, cls, wkind, model, now,
                                     tokens=tokens)
                self._save_state(fd)
                return self._cooldown_until - now

            model_wait = _model_wait(self._models, model, now)
            if model_wait > 0:
                # L1 (0.62.0): a cooling model never gets a token — the request waits in
                # the queue (registered waiter, heartbeats continue) instead of firing.
                if token is not None:
                    _register_waiter(self._waiters, token, owner, cls, wkind, model, now,
                                     tokens=tokens)
                self._save_state(fd)
                return model_wait

            qwait = _quota_wait(self.quota, now, tokens)
            if qwait == float("inf"):
                return float("inf")          # ineligible: can never fit (e.g. tokens > tpm)
            if qwait > 0:
                # C2: a sidecar dimension lacks room — wait WITHOUT consuming a main token
                if token is not None:
                    _register_waiter(self._waiters, token, owner, cls, wkind, model, now,
                                     tokens=tokens)
                self._save_state(fd)
                return qwait

            self._refill(now)
            if token is None:
                # legacy peek: today's exact semantics (minus the retired reserve partition)
                if self._tokens >= cost:
                    self._tokens -= cost
                    self._grants.append({"t": now, "o": owner, "c": cls, "k": _kind()})
                    _quota_book(self.quota, now, tokens)
                    # SUCCESS: Reset exponential backoff counter
                    self._consecutive_429s = 0
                    self._last_429_time = 0.0
                    self._save_state(fd)
                    return 0.0
                self._save_state(fd)
                return max(1e-3, need_seconds())

            # a granted entry means our token was already booked for us by the granting poll
            own_w = self._waiters.get(token)
            if own_w is not None and "granted" in own_w:
                rcp = own_w.get("rcp")     # the granting poll's receipt for OUR estimate
                self._waiters.pop(token, None)
                if rcp:
                    self._grant_receipts[token] = Receipt.from_json(rcp)
                self._save_state(fd)
                return 0.0

            registry = {t: w for t, w in self._waiters.items()
                        if "granted" not in w and t != token}
            if cfg["policy"] == "legacy" or not registry:
                # empty queue (or kill-switch): uncontended behavior, identical to token=None
                if self._tokens >= cost:
                    self._waiters.pop(token, None)
                    self._tokens -= cost
                    self._grants.append({"t": now, "o": owner, "c": cls, "k": _kind()})
                    self._grant_receipts[token] = _quota_book(self.quota, now, tokens)
                    self._consecutive_429s = 0
                    self._last_429_time = 0.0
                    self._save_state(fd)
                    return 0.0
                _register_waiter(self._waiters, token, owner, cls, wkind, model, now,
                                 tokens=tokens)
                self._save_state(fd)
                return max(1e-3, need_seconds())

            fast_kinds = _fast_lane_kinds(cfg)
            prio_grants = sum(1 for g in self._grants
                              if g.get("c") == "priority" or g.get("k") in fast_kinds)
            winner = _pick_winner(self._waiters, now, cfg, self._owners, {}, limit,
                                  prio_grants, deficit=self._deficit)
            if winner is not None:
                w = self._waiters[winner]
                w_opts = _owner_opts(self._owners, w.get("owner", ""))
                w_cost = 1.0 / w_opts["rpm_scale"]
                if self._tokens >= w_cost:
                    if cfg["policy"] == "deficit":
                        w_owner = w.get("owner", "")
                        debt = _owner_debt(self._deficit, self._waiters, w_owner, now, cfg)
                        self._deficit[w_owner] = max(0.0, debt - WINDOW_SECONDS / limit)
                    if w.get("owner") == owner:
                        # L5 (0.62.0): serve the WINNER (book it as granted) and keep the
                        # caller's queue age — popping the winner stole its `arrived`.
                        self._tokens -= w_cost
                        self._grants.append({"t": now, "o": w["owner"], "c": w.get("class", cls),
                                 "k": w.get("kind", "interactive")})
                        # the WINNER's estimate is booked; its own ack delivers the receipt
                        _book_winner(self, w, now)
                        w["granted"] = now
                        _register_waiter(self._waiters, token, owner, cls, wkind, model, now,
                                         tokens=tokens)
                        self._consecutive_429s = 0
                        self._last_429_time = 0.0
                        self._save_state(fd)
                        return max(1e-3, need_seconds())
                    # book the token for the WINNER; the winner acks on its next poll
                    self._tokens -= w_cost
                    self._grants.append({"t": now, "o": w["owner"], "c": w.get("class", "normal"),
                                 "k": w.get("kind", "interactive")})
                    _book_winner(self, w, now)
                    w["granted"] = now
                    _register_waiter(self._waiters, token, owner, cls, wkind, model, now,
                                     tokens=tokens)
                    self._save_state(fd)
                    return max(1e-3, need_seconds())

            _register_waiter(self._waiters, token, owner, cls, wkind, model, now,
                             tokens=tokens)
            self._save_state(fd)
            return max(1e-3, need_seconds())
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _load_state(self, fd: int, now: float):
        """Load state from file and refill based on elapsed time."""
        os.lseek(fd, 0, os.SEEK_SET)
        raw = _read_all(fd)
        try:
            state = json.loads(raw) if raw else {}
            self._tokens = float(state.get('tokens', self.capacity))
            self._last_refill = float(state.get('last_refill', now))
            self._cooldown_until = float(state.get('cooldown_until', 0.0))
            self._consecutive_429s = int(state.get('consecutive_429s', 0))
            self._last_429_time = float(state.get('last_429_time', 0.0))
            models = state.get('models', {})
            self._models = models if isinstance(models, dict) else {}
            owners = state.get('owners', {})
            self._owners = owners if isinstance(owners, dict) else {}
            waiters = state.get('waiters', {})
            self._waiters = waiters if isinstance(waiters, dict) else {}
            deficit = state.get('deficit', {})
            self._deficit = deficit if isinstance(deficit, dict) else {}
            grants = state.get('grants', [])
            self._grants = [g for g in grants if isinstance(g, dict)] if isinstance(grants, list) else []
        except Exception as exc:
            # L2 (0.62.0): a corrupt/torn file FAILS SAFE — no tokens, active cooldown —
            # written back and logged, never reset to permissive capacity.
            state = _fail_safe_state(now, self.base_cooldown)
            print(f"rate_limiter: state unreadable ({exc}) -> failing SAFE", file=sys.stderr)
            self._tokens = float(state['tokens'])
            self._last_refill = float(state['last_refill'])
            self._cooldown_until = float(state['cooldown_until'])
            self._consecutive_429s = 0
            self._last_429_time = 0.0
            self._models = {}
            self._owners = {}
            self._waiters = {}
            self._deficit = {}
            self._grants = []
            self._save_state(fd)
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
            'cooldown_until': self._cooldown_until,
            'consecutive_429s': self._consecutive_429s,
            'last_429_time': self._last_429_time,
            'models': self._models,
            'owners': self._owners,
            # append-only schema: old code ignores unknown keys but drops them on its
            # wholesale rewrites; new code re-writes them on every poll (self-heal)
            'waiters': self._waiters,
            'deficit': self._deficit,
            'grants': self._grants
        }).encode()
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data)

    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def note_429(self, retry_after: float | None = None, hidden_attempts: int = 0,
                 model: str | None = None) -> dict:
        """Record an upstream 429, CLASSIFIED like SlidingWindowLimiter.note_429 (0.40.0):
        bucket more than half full (few recent requests) and a model given -> NVIDIA's
        per-MODEL throttle, cool only that model; otherwise an RPM overrun that pauses
        every proxy with exponential backoff.

        Tracks consecutive 429s and increases cooldown exponentially:
        - 1st 429: base_cooldown (30s)
        - 2nd 429: base_cooldown * multiplier (60s)
        - 3rd 429: base_cooldown * multiplier^2 (120s)
        - etc., capped at max_cooldown (300s)
        Resets on successful request acquisition.
        """
        fd = self._locked()
        try:
            now = self._clock()
            self._load_state(fd, now)
            # Book hidden attempts (they count in the window too, like the sliding mode's stamps)
            self._tokens = max(0.0, self._tokens - float(hidden_attempts))
            self._grants.extend([{"t": now, "o": _owner(), "c": "normal"}] * max(0, int(hidden_attempts)))
            self._grants = [g for g in self._grants if now - float(g.get("t", 0.0)) < WINDOW_SECONDS]
            # L3 (0.62.0): the classifier counts REAL grants in the last 60 s — burst
            # depletion (capacity - tokens) said "40/40" under steady load and misclassified
            # a single model's 429 as an RPM overrun.
            limit = max(1, int(self.rate * 60))
            in_window = len(self._grants)

            if model and in_window < limit / 2:
                pause, backoff_cooldown, n = _cool_model(
                    self._models, model, now, retry_after,
                    self.base_cooldown, self.backoff_multiplier, self.max_cooldown)
                self._save_state(fd)
                return {"in_window": in_window, "limit": int(self.rate * 60), "pause": pause,
                        "consecutive_429s": n, "backoff_cooldown": backoff_cooldown,
                        "kind": "model_throttle", "model": model}

            # Exponential backoff: increase consecutive_429s, calculate cooldown
            self._consecutive_429s += 1
            # If it's been a while since last 429, reset backoff (session recovered)
            if now - self._last_429_time > self.max_cooldown:
                self._consecutive_429s = 1

            # Calculate exponential backoff
            backoff_cooldown = self.base_cooldown * (self.backoff_multiplier ** (self._consecutive_429s - 1))
            backoff_cooldown = min(backoff_cooldown, self.max_cooldown)

            # Use Retry-After if provided, otherwise use exponential backoff
            pause = float(retry_after) if retry_after and retry_after > 0 else backoff_cooldown
            self._cooldown_until = max(self._cooldown_until, now + pause)
            self._last_429_time = now

            self._save_state(fd)
            return {"in_window": in_window, "limit": int(self.rate * 60), "pause": pause,
                    "consecutive_429s": self._consecutive_429s, "backoff_cooldown": backoff_cooldown,
                    "kind": "rpm_overrun", "model": model or ""}
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def note_success(self, model: str | None = None, tokens_used: int | None = None,
                     receipt=None) -> None:
        """A completed request: clear that model's entry and reset the global counter.
        0.63.3: reconcile the request's OWN reservation — settle(receipt, tokens_used)
        refunds its over-estimate in the receipt's own stamp/day/week (idempotently);
        receipt=None (or no sidecar) writes nothing to the ledger."""
        fd = self._locked()
        try:
            now = self._clock()
            self._load_state(fd, now)
            if model:
                self._models.pop(model, None)
            self._consecutive_429s = 0
            self._last_429_time = 0.0
            self._save_state(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        if self.quota is not None and receipt is not None:
            self.quota.settle(receipt, tokens_used, self._clock())

    def _take_grant_receipt(self, token: str):
        """Pop the Receipt born at THIS token's grant (None when the grant carried no
        sidecar booking). One-shot: the acquire loop takes it on success."""
        return self._grant_receipts.pop(token, None)

    def note_quota_headers(self, headers) -> None:
        """0.62.0 C2: adopt x-ratelimit-* headers into the sidecar (they override local)."""
        if self.quota is not None:
            self.quota.adopt_headers(headers, self._clock())

    def note_quota_refusal(self, dimension: str, reset_at: float) -> None:
        """0.62.0 C2: a quota refusal marks the dimension exhausted until its reset."""
        if self.quota is not None:
            self.quota.mark_exhausted(dimension, reset_at)

    def model_cooling(self, model: str) -> float:
        """Seconds left in that model's cooldown (0.0 = free to try). Read under the lock."""
        fd = self._locked()
        try:
            now = self._clock()
            self._load_state(fd, now)
            entry = self._models.get(model)
            return max(0.0, float(entry.get("until", 0.0)) - now) if entry else 0.0
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _release(self, token: str) -> None:
        """Deregister one waiter (the timeout/exception path); a no-op when absent."""
        self._grant_receipts.pop(token, None)   # a receipt never taken dies with the wait
        fd = self._locked()
        try:
            now = self._clock()
            self._load_state(fd, now)
            if self._waiters.pop(token, None) is not None:
                self._save_state(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def acquire(self, max_wait: float | None = None, priority: bool | None = None,
                kind: str | None = None, model: str | None = None,
                tokens: int = 0) -> float:
        """Block until a token is taken; every sleep is clamped to waiter_poll_seconds so a
        long machine-wide cooldown never stops the queue heartbeats (same as the window)."""
        waited, _rc = self.acquire_grant(max_wait=max_wait, priority=priority, kind=kind,
                                         model=model, tokens=tokens)
        return waited

    def acquire_grant(self, max_wait: float | None = None, priority: bool | None = None,
                      kind: str | None = None, model: str | None = None,
                      tokens: int = 0):
        """acquire(), returning (seconds waited, the reservation's Receipt | None) —
        the receipt a success settles against (0.63.3)."""
        limit = _max_wait(max_wait)
        waited = 0.0
        token = _waiter_token(_owner())
        poll = _cfg()["waiter_poll_seconds"]
        try:
            while True:
                need = self.try_acquire(priority=priority, token=token, kind=kind,
                                        model=model, tokens=tokens)
                if need == 0.0:
                    return waited, self._take_grant_receipt(token)
                if need == float("inf"):
                    raise RateLimitTimeout(
                        f"ineligible: {tokens} tokens can never fit this provider's limits")
                if waited + need > limit:
                    raise RateLimitTimeout(f"no NVIDIA slot within {limit:.0f}s")
                s = min(need, poll)
                time.sleep(s)
                waited += s
        finally:
            self._release(token)

    async def aacquire(self, max_wait: float | None = None, priority: bool | None = None,
                       kind: str | None = None, model: str | None = None,
                       tokens: int = 0) -> float:
        waited, _rc = await self.aacquire_grant(max_wait=max_wait, priority=priority,
                                                kind=kind, model=model, tokens=tokens)
        return waited

    async def aacquire_grant(self, max_wait: float | None = None,
                             priority: bool | None = None, kind: str | None = None,
                             model: str | None = None, tokens: int = 0):
        """aacquire(), returning (seconds waited, the reservation's Receipt | None) —
        the receipt a success settles against (0.63.3)."""
        limit = _max_wait(max_wait)
        waited = 0.0
        token = _waiter_token(_owner())
        poll = _cfg()["waiter_poll_seconds"]
        try:
            while True:
                need = self.try_acquire(priority=priority, token=token, kind=kind,
                                        model=model, tokens=tokens)
                if need == 0.0:
                    return waited, self._take_grant_receipt(token)
                if need == float("inf"):
                    raise RateLimitTimeout(
                        f"ineligible: {tokens} tokens can never fit this provider's limits")
                if waited + need > limit:
                    raise RateLimitTimeout(f"no NVIDIA slot within {limit:.0f}s")
                s = min(need, poll)
                await asyncio.sleep(s)
                waited += s
        finally:
            self._release(token)

    def status(self) -> dict:
        fd = self._locked()
        try:
            now = self._clock()
            self._load_state(fd, now)
            recent = [g for g in self._grants
                      if now - float(g.get("t", now)) < WINDOW_SECONDS]
            return {
                "path": str(self.path),
                # 0.62.3 review fix: the rpm headroom term needs these in smooth mode too
                # (0.62.0 omitted them, so provider_headroom's rpm part read grants=[]
                # and always reported 1.0).
                "in_window": len(recent),
                "grants": recent,
                "tokens": round(self._tokens, 2),
                "capacity": self.capacity,
                "rate_per_sec": round(self.rate, 3),
                "rpm_equiv": round(self.rate * 60, 1),
                "cooldown_remaining": round(max(0.0, self._cooldown_until - now), 3),
                "mode": "smooth_bucket",
                "consecutive_429s": self._consecutive_429s,
                "cooling_models": _cooling(self._models, now),
                "base_cooldown": self.base_cooldown,
                "max_cooldown": self.max_cooldown,
                "backoff_multiplier": self.backoff_multiplier,
                "waiters": [{"token": t, "owner": w.get("owner", ""),
                             "waited_s": round(now - float(w.get("arrived", now)), 1),
                             "class": w.get("class", "normal"), "kind": w.get("kind", "interactive"),
                             "granted": "granted" in w} for t, w in self._waiters.items()],
                "lane_grants_in_window": sum(1 for g in self._grants if g.get("c") == "priority")
            }
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


class SlidingWindowLimiter:
    def __init__(self, path: Path | str | None = None, rpm: float | None = None,
                 clock=time.time, cooldown: float | None = None, quota=None):
        self.path = Path(path or os.environ.get("LA_NVIDIA_THROTTLE_STATE") or DEFAULT_STATE)
        self.quota = quota                # 0.62.0 C2: QuotaLedger sidecar (<state>.quota) or None
        self.limit = int(float(rpm or _setting("rpm", 40)))
        # Exponential backoff config
        self.base_cooldown = float(cooldown if cooldown is not None
                                   else _setting("cooldown", DEFAULT_429_BASE_COOLDOWN))
        self.max_cooldown = float(_setting("max_cooldown", DEFAULT_429_MAX_COOLDOWN))
        self.backoff_multiplier = float(_setting("backoff_multiplier", DEFAULT_429_BACKOFF_MULTIPLIER))
        self.max_retries = int(_setting("max_retries", DEFAULT_429_MAX_RETRIES))
        self._clock = clock
        # Exponential backoff state
        self._consecutive_429s = 0
        self._last_429_time = 0.0
        self._owners: dict = {}           # 0.50.1: per-owner {priority, rpm_scale}
        self._waiters: dict = {}          # fairness queue: token -> waiter entry
        self._deficit: dict = {}          # deficit policy: owner -> residual seconds
        self._grant_receipts: dict = {}   # 0.63.3: token -> Receipt, born at the grant

    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _read(self, fd: int, now: float) -> tuple[list, float, int, float, dict, dict]:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = _read_all(fd)
        try:
            state = json.loads(raw) if raw else {}
            raw_stamps = state.get("stamps", [])
            # 0.50.1: stamps keep their owner — dicts stay dicts, bare floats are legacy.
            stamps = list(raw_stamps)
            cooldown_until = float(state.get("cooldown_until", 0.0))
            consecutive_429s = int(state.get("consecutive_429s", 0))
            last_429_time = float(state.get("last_429_time", 0.0))
            models = state.get("models", {}) if isinstance(state.get("models", {}), dict) else {}
            owners = state.get("owners", {})
            self._owners = owners if isinstance(owners, dict) else {}
            waiters = state.get("waiters", {})
            self._waiters = waiters if isinstance(waiters, dict) else {}
            deficit = state.get("deficit", {})
            self._deficit = deficit if isinstance(deficit, dict) else {}
        except Exception as exc:
            # L2 (0.62.0): a corrupt/torn file FAILS SAFE — empty window, ACTIVE cooldown —
            # written back and logged, never reset to permissive.
            print(f"rate_limiter: state unreadable ({exc}) -> failing SAFE", file=sys.stderr)
            self._waiters = {}
            self._deficit = {}
            self._owners = {}
            cooldown_until = now + self.base_cooldown
            self._write(fd, [], cooldown_until, 0, 0.0, {})
            return [], cooldown_until, 0, 0.0, {}, {}
        # A stamp in the future (clock step) is treated as now, so a skewed clock can hold a
        # slot for at most one window, never forever.
        stamps = [s for s in stamps if _stamp_parts(s)[0] > now - WINDOW_SECONDS]
        stamps.sort(key=lambda s: _stamp_parts(s)[0])
        return stamps, cooldown_until, consecutive_429s, last_429_time, models, self._waiters

    def _write(self, fd: int, stamps: list, cooldown_until: float,
               consecutive_429s: int = 0, last_429_time: float = 0.0,
               models: dict | None = None, owners: dict | None = None,
               waiters: dict | None = None, grants: list | None = None,
               deficit: dict | None = None) -> None:
        owner = _owner()
        payload = {"stamps": [s if isinstance(s, dict) else _stamp(s, owner) for s in stamps],
                   "cooldown_until": cooldown_until,
                   "limit": self.limit,
                   "consecutive_429s": consecutive_429s,
                   "last_429_time": last_429_time,
                   "models": models or {},
                   "owners": owners if owners is not None else self._owners,
                   # append-only schema: old code ignores unknown keys but drops them on
                   # its wholesale rewrites; new code re-writes them on every poll, so the
                   # registry self-heals within one poll interval (mixed-writer contract)
                   "waiters": waiters if waiters is not None else getattr(self, "_waiters", {}),
                   "deficit": deficit if deficit is not None else getattr(self, "_deficit", {})}
        if grants is not None:
            payload["grants"] = grants
        data = json.dumps(payload).encode()
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, data)

    def try_acquire(self, priority: bool | None = None, token: str | None = None,
                    kind: str | None = None, model: str | None = None,
                    tokens: int = 0) -> float:
        """Take a slot if one is free. Returns 0.0 on success, else the seconds to wait.
        `tokens` (0.62.0 C2) also books that many tokens in the quota sidecar; inf when the
        request can never fit (tokens > tpm).

        The whole read-prune-append-write runs under one exclusive lock, so two processes can
        never both take the last slot.
        Resets exponential backoff on successful acquisition. An EXPIRED cooldown no longer
        resets the backoff counter (0.40.0): the counter used to be cleared the moment a pause
        lapsed, before the next request had even failed, so four 429s in a row each read
        "pause 30s, consecutive=1" and a model throttled for hours was retried ~2x/min forever.
        Only a success (an acquisition, or note_success from a completed request) resets it.

        0.50.1 priority lane + per-owner throttle: stamps are attributed to the caller's
        owner id. With LA_LIMITER_RESERVE>0 (default 0.0 = off), that fraction of the window limit is
        reserved for priority acquisitions (owner "priority" flag or LA_LIMITER_PRIORITY=1);
        normal traffic stops at limit*(1-reserve). An owner's rpm_scale s caps ITS OWN
        window count at limit*s (s<1 throttles that session live, at any time).

        Fairness queue (design docs/superpowers/specs/2026-11-19-nvidia-rate-limiter-fairness-
        design.md): acquire()/aacquire() always pass a `token`; a failed try_acquire registers
        the caller in the shared `waiters` registry and the next free slot goes to the
        policy-ranked winner, booked under the WINNER's owner — a fresh arrival never barges
        an occupied queue. token=None is the legacy peek: exactly the pre-queue semantics,
        registry untouched. An empty registry behaves identically to token=None, and
        `policy: legacy` (hot setting) restores barging even for queued callers.
        """
        owner = _owner()
        opts = _owner_opts(self._owners, owner)
        prio = opts["priority"] if priority is None else bool(priority)
        own_limit = max(1, int(self.limit * opts["rpm_scale"]))
        # The reserve PARTITION is retired: LA_LIMITER_RESERVE now means the lane share cap
        # (hot key lane_share_cap), enforced at grant time only while normal waiters exist —
        # the window is open to everyone otherwise, so no capacity ever idles.
        normal_limit = self.limit
        cfg = _cfg()
        cls = "priority" if prio else "normal"
        wkind = kind or _kind(opts)
        fd = self._locked()
        try:
            now = self._clock()
            stamps, cooldown_until, consecutive_429s, last_429_time, models, waiters = self._read(fd, now)
            self._consecutive_429s = consecutive_429s
            self._last_429_time = last_429_time
            _prune_waiters(waiters, now, cfg["waiter_ttl_seconds"])

            def need_seconds() -> float:
                # The oldest request leaves the window first; wait for exactly that. When the
                # block came from the owner's OWN scale (fewer stamps than limit), wait on those.
                if not stamps:
                    return 1e-3
                idx = min(self.limit, len(stamps))
                return max(1e-3, _stamp_parts(stamps[-idx])[0] + WINDOW_SECONDS - now)

            if now < cooldown_until:
                if token is not None:
                    # no grants during a machine-wide pause, but the heartbeat continues
                    _register_waiter(waiters, token, owner, cls, wkind, model, now,
                                     tokens=tokens)
                self._write(fd, stamps, cooldown_until, consecutive_429s, last_429_time, models)
                return cooldown_until - now

            model_wait = _model_wait(models, model, now)
            if model_wait > 0:
                # L1 (0.62.0): a cooling model never gets a stamp — the request waits in
                # the queue (registered waiter, heartbeats continue) instead of firing.
                if token is not None:
                    _register_waiter(waiters, token, owner, cls, wkind, model, now,
                                     tokens=tokens)
                self._write(fd, stamps, cooldown_until, consecutive_429s, last_429_time, models)
                return model_wait

            qwait = _quota_wait(self.quota, now, tokens)
            if qwait == float("inf"):
                return float("inf")          # ineligible: can never fit (e.g. tokens > tpm)
            if qwait > 0:
                # C2: a sidecar dimension lacks room — wait WITHOUT consuming a stamp
                if token is not None:
                    _register_waiter(waiters, token, owner, cls, wkind, model, now,
                                     tokens=tokens)
                self._write(fd, stamps, cooldown_until, consecutive_429s, last_429_time, models)
                return qwait

            own_count = sum(1 for s in stamps if _stamp_parts(s)[1] == owner)
            capacity_free = len(stamps) < normal_limit and own_count < own_limit

            if token is None:
                # legacy peek: today's exact semantics, the registry is neither read nor written
                if capacity_free:
                    stamps.append(now)
                    _quota_book(self.quota, now, tokens)
                    # SUCCESS: Reset exponential backoff counter
                    self._consecutive_429s = 0
                    self._last_429_time = 0.0
                    self._write(fd, stamps, cooldown_until, 0, 0.0, models)
                    return 0.0
                self._write(fd, stamps, cooldown_until, consecutive_429s, last_429_time, models)
                return need_seconds()

            # a granted entry means our slot was already booked for us by the granting poll
            own_w = waiters.get(token)
            if own_w is not None and "granted" in own_w:
                rcp = own_w.get("rcp")     # the granting poll's receipt for OUR estimate
                waiters.pop(token, None)
                if rcp:
                    self._grant_receipts[token] = Receipt.from_json(rcp)
                self._write(fd, stamps, cooldown_until, consecutive_429s, last_429_time, models)
                return 0.0

            registry = {t: w for t, w in waiters.items() if "granted" not in w and t != token}
            if cfg["policy"] == "legacy" or not registry:
                # empty queue (or kill-switch): uncontended behavior, identical to token=None
                if capacity_free:
                    waiters.pop(token, None)
                    stamps.append(_stamp(now, owner))
                    self._grant_receipts[token] = _quota_book(self.quota, now, tokens)
                    self._consecutive_429s = 0
                    self._last_429_time = 0.0
                    self._write(fd, stamps, cooldown_until, 0, 0.0, models)
                    return 0.0
                _register_waiter(waiters, token, owner, cls, wkind, model, now,
                                 tokens=tokens)
                self._write(fd, stamps, cooldown_until, consecutive_429s, last_429_time, models)
                return need_seconds()

            window_free = len(stamps) < normal_limit
            if window_free:
                own_counts = {}
                for s in stamps:
                    _t, o = _stamp_parts(s)
                    own_counts[o] = own_counts.get(o, 0) + 1
                prio_grants = _prio_grants_sliding(stamps, self._owners, _fast_lane_kinds(cfg))
                winner = _pick_winner(waiters, now, cfg, self._owners, own_counts, self.limit,
                                      prio_grants, deficit=self._deficit)
                if winner is not None:
                    w = waiters[winner]
                    if cfg["policy"] == "deficit":
                        # a grant spends the inter-slot credit; the remainder persists as residual
                        w_owner = w.get("owner", "")
                        debt = _owner_debt(self._deficit, waiters, w_owner, now, cfg)
                        self._deficit[w_owner] = max(0.0, debt - WINDOW_SECONDS / self.limit)
                    if w.get("owner") == owner:
                        # L5 (0.62.0): serve the WINNER (book it as granted) and keep the
                        # caller's queue age — popping the winner stole its `arrived`.
                        stamps.append(_stamp(now, w["owner"]))
                        # the WINNER's estimate is booked; its own ack delivers the receipt
                        _book_winner(self, w, now)
                        w["granted"] = now
                        _register_waiter(waiters, token, owner, cls, wkind, model, now,
                                         tokens=tokens)
                        self._consecutive_429s = 0
                        self._last_429_time = 0.0
                        self._write(fd, stamps, cooldown_until, 0, 0.0, models)
                        return need_seconds()
                    # book the slot under the WINNER's owner; the winner acks on its next poll
                    stamps.append(_stamp(now, w["owner"]))
                    _book_winner(self, w, now)
                    w["granted"] = now
                    _register_waiter(waiters, token, owner, cls, wkind, model, now,
                                     tokens=tokens)
                    self._write(fd, stamps, cooldown_until, consecutive_429s, last_429_time, models)
                    return need_seconds()

            _register_waiter(waiters, token, owner, cls, wkind, model, now,
                             tokens=tokens)
            self._write(fd, stamps, cooldown_until, consecutive_429s, last_429_time, models)
            return need_seconds()
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def note_429(self, retry_after: float | None = None, hidden_attempts: int = 0,
                 model: str | None = None) -> dict:
        """Record an upstream 429, CLASSIFIED (0.40.0):

        - window at least half full, or model unknown -> RPM overrun: pause EVERY proxy
          sharing the file with exponential backoff (today's behaviour).
        - window near-empty and a model given -> NVIDIA's per-MODEL throttle (measured
          2026-10-20: GLM 5.3 Flash answered 429 in 0.2 s for 6+ h while DeepSeek on the
          same key served 46/46): cool ONLY that model, in the same locked state file.
          Other models' proxies never notice.

        Per-model backoff grows exactly like the global one (base * mult^(n-1), capped),
        and n resets only on a real success of THAT model (note_success), never on expiry.
        Returns the usual dict plus "kind" ("model_throttle" | "rpm_overrun") and "model".

        hidden_attempts: upstream requests that were sent but never passed through acquire()
        (the OpenAI SDK's own retries inside one LiteLLM call). NVIDIA counted them, so the
        window counts them too, or it keeps under-reporting what was really sent.
        """
        fd = self._locked()
        try:
            now = self._clock()
            stamps, cooldown_until, consecutive_429s, last_429_time, models, waiters = self._read(fd, now)

            # Book hidden attempts
            stamps.extend([now] * max(0, int(hidden_attempts)))

            if model and len(stamps) < self.limit / 2:
                # Per-model throttle: no global pause, one entry per upstream model id.
                pause, backoff_cooldown, n = _cool_model(
                    models, model, now, retry_after,
                    self.base_cooldown, self.backoff_multiplier, self.max_cooldown)
                self._write(fd, stamps, cooldown_until, consecutive_429s, last_429_time, models)
                return {"in_window": len(stamps), "limit": self.limit, "pause": pause,
                        "consecutive_429s": n, "backoff_cooldown": backoff_cooldown,
                        "kind": "model_throttle", "model": model}

            # RPM overrun (or model unknown): today's machine-wide exponential backoff.
            consecutive_429s += 1
            # If it's been a while since last 429, reset backoff (session recovered)
            if now - last_429_time > self.max_cooldown:
                consecutive_429s = 1

            # Calculate exponential backoff
            backoff_cooldown = self.base_cooldown * (self.backoff_multiplier ** (consecutive_429s - 1))
            backoff_cooldown = min(backoff_cooldown, self.max_cooldown)

            # Use Retry-After if provided, otherwise use exponential backoff
            pause = float(retry_after) if retry_after and retry_after > 0 else backoff_cooldown
            cooldown_until = max(cooldown_until, now + pause)
            last_429_time = now

            self._write(fd, stamps, cooldown_until, consecutive_429s, last_429_time, models)
            return {"in_window": len(stamps), "limit": self.limit, "pause": pause,
                    "consecutive_429s": consecutive_429s, "backoff_cooldown": backoff_cooldown,
                    "kind": "rpm_overrun", "model": model or ""}
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def note_success(self, model: str | None = None, tokens_used: int | None = None,
                     receipt=None) -> None:
        """A completed request: clear that model's cooldown entry, and reset the global
        backoff counter. The ONLY resets (0.40.0) — an expired pause no longer clears
        anything, or a model throttled for hours retried at base cooldown forever.
        0.63.3: reconcile the request's OWN reservation — settle(receipt, tokens_used)
        refunds its over-estimate in the receipt's own stamp/day/week (idempotently);
        receipt=None (or no sidecar) writes nothing to the ledger."""
        fd = self._locked()
        try:
            now = self._clock()
            stamps, cooldown_until, consecutive_429s, last_429_time, models, waiters = self._read(fd, now)
            if model and model in models:
                models.pop(model)
            self._write(fd, stamps, cooldown_until, 0, 0.0, models)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        if self.quota is not None and receipt is not None:
            self.quota.settle(receipt, tokens_used, self._clock())

    def _take_grant_receipt(self, token: str):
        """Pop the Receipt born at THIS token's grant (None when the grant carried no
        sidecar booking). One-shot: the acquire loop takes it on success."""
        return self._grant_receipts.pop(token, None)

    def note_quota_headers(self, headers) -> None:
        """0.62.0 C2: adopt x-ratelimit-* headers into the sidecar (they override local)."""
        if self.quota is not None:
            self.quota.adopt_headers(headers, self._clock())

    def note_quota_refusal(self, dimension: str, reset_at: float) -> None:
        """0.62.0 C2: a quota refusal marks the dimension exhausted until its reset."""
        if self.quota is not None:
            self.quota.mark_exhausted(dimension, reset_at)

    def model_cooling(self, model: str) -> float:
        """Seconds left in that model's cooldown (0.0 = free to try). Read under the lock."""
        fd = self._locked()
        try:
            now = self._clock()
            _, _, _, _, models, _w = self._read(fd, now)
            entry = models.get(model)
            return max(0.0, float(entry.get("until", 0.0)) - now) if entry else 0.0
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _release(self, token: str) -> None:
        """Deregister one waiter (the timeout/exception path); a no-op when absent."""
        self._grant_receipts.pop(token, None)   # a receipt never taken dies with the wait
        fd = self._locked()
        try:
            now = self._clock()
            stamps, cd, c429, l429, models, waiters = self._read(fd, now)
            if waiters.pop(token, None) is not None:
                self._write(fd, stamps, cd, c429, l429, models)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def acquire(self, max_wait: float | None = None, priority: bool | None = None,
                kind: str | None = None, model: str | None = None,
                tokens: int = 0) -> float:
        """Block until a slot is taken; returns the total seconds waited.

        Registers one waiter token in the shared queue on the first failed try_acquire and
        heartbeats it on every poll; every sleep is clamped to waiter_poll_seconds, so a long
        machine-wide cooldown (max_cooldown up to 900s) never stops the heartbeats and the
        waiter never loses its queue position to the stale-waiter TTL."""
        waited, _rc = self.acquire_grant(max_wait=max_wait, priority=priority, kind=kind,
                                         model=model, tokens=tokens)
        return waited

    def acquire_grant(self, max_wait: float | None = None, priority: bool | None = None,
                      kind: str | None = None, model: str | None = None,
                      tokens: int = 0):
        """acquire(), returning (seconds waited, the reservation's Receipt | None) —
        the receipt a success settles against (0.63.3)."""
        limit = _max_wait(max_wait)
        waited = 0.0
        token = _waiter_token(_owner())
        poll = _cfg()["waiter_poll_seconds"]
        try:
            while True:
                need = self.try_acquire(priority=priority, token=token, kind=kind,
                                        model=model, tokens=tokens)
                if need == 0.0:
                    return waited, self._take_grant_receipt(token)
                if need == float("inf"):
                    raise RateLimitTimeout(
                        f"ineligible: {tokens} tokens can never fit this provider's limits")
                if waited + need > limit:
                    raise RateLimitTimeout(f"no NVIDIA slot within {limit:.0f}s")
                s = min(need, poll)
                time.sleep(s)
                waited += s
        finally:
            self._release(token)

    async def aacquire(self, max_wait: float | None = None, priority: bool | None = None,
                       kind: str | None = None, model: str | None = None,
                       tokens: int = 0) -> float:
        """Async twin of acquire() -- sleeps without blocking the proxy's event loop."""
        waited, _rc = await self.aacquire_grant(max_wait=max_wait, priority=priority,
                                                kind=kind, model=model, tokens=tokens)
        return waited

    async def aacquire_grant(self, max_wait: float | None = None,
                             priority: bool | None = None, kind: str | None = None,
                             model: str | None = None, tokens: int = 0):
        """aacquire(), returning (seconds waited, the reservation's Receipt | None) —
        the receipt a success settles against (0.63.3)."""
        limit = _max_wait(max_wait)
        waited = 0.0
        token = _waiter_token(_owner())
        poll = _cfg()["waiter_poll_seconds"]
        try:
            while True:
                need = self.try_acquire(priority=priority, token=token, kind=kind,
                                        model=model, tokens=tokens)
                if need == 0.0:
                    return waited, self._take_grant_receipt(token)
                if need == float("inf"):
                    raise RateLimitTimeout(
                        f"ineligible: {tokens} tokens can never fit this provider's limits")
                if waited + need > limit:
                    raise RateLimitTimeout(f"no NVIDIA slot within {limit:.0f}s")
                s = min(need, poll)
                await asyncio.sleep(s)
                waited += s
        finally:
            self._release(token)

    def status(self) -> dict:
        fd = self._locked()
        try:
            now = self._clock()
            stamps, cooldown_until, consecutive_429s, last_429_time, models, waiters = self._read(fd, now)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return {"path": str(self.path), "in_window": len(stamps), "limit": self.limit,
                "window_seconds": WINDOW_SECONDS,
                "cooldown_remaining": round(max(0.0, cooldown_until - now), 3),
                "consecutive_429s": consecutive_429s,
                "cooling_models": _cooling(models, now),
                "base_cooldown": self.base_cooldown,
                "max_cooldown": self.max_cooldown,
                "backoff_multiplier": self.backoff_multiplier,
                "waiters": [{"token": t, "owner": w.get("owner", ""),
                             "waited_s": round(now - float(w.get("arrived", now)), 1),
                             "class": w.get("class", "normal"), "kind": w.get("kind", "interactive"),
                             "granted": "granted" in w} for t, w in waiters.items()]}


def _max_wait(value: float | None) -> float:
    return float(value if value is not None else _setting("max_wait", DEFAULT_MAX_WAIT))




def owners_info(limiter) -> dict:
    """0.50.1: per-owner options + in-window counts (the de-anonymized bucket)."""
    fd = limiter._locked()
    try:
        now = limiter._clock()
        if isinstance(limiter, SlidingWindowLimiter):
            stamps, *_ = limiter._read(fd, now)
            counts = {}
            for s in stamps:
                _t, o = _stamp_parts(s)
                counts[o] = counts.get(o, 0) + 1
            owners = dict(getattr(limiter, "_owners", {}))
            waiters = dict(getattr(limiter, "_waiters", {}))
        else:
            limiter._load_state(fd, now)
            owners = dict(getattr(limiter, "_owners", {}))
            waiters = dict(getattr(limiter, "_waiters", {}))
        # per-owner live queue length (ungranted waiters only — a granted entry is a slot
        # that is already booked for that owner)
        waiting = {}
        for w in waiters.values():
            if "granted" not in w:
                o = w.get("owner", "")
                waiting[o] = waiting.get(o, 0) + 1
        if isinstance(limiter, SlidingWindowLimiter):
            return {"limit": limiter.limit, "in_window": len(stamps),
                    "waiting": waiting,
                    "owners": {o: {**opts, "in_window": counts.get(o, 0)}
                               for o, opts in owners.items()},
                    "unowned_in_window": counts.get("legacy", 0)}
        return {"capacity": limiter.capacity, "tokens": round(limiter._tokens, 2),
                "waiting": waiting,
                "owners": dict(getattr(limiter, "_owners", {}))}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def set_owner(limiter, owner: str, priority: bool | None, rpm_scale: float | None,
              kind: str | None = None, approved: bool = False) -> None:
    """0.50.1: live per-owner options, written under the lock — throttle or prioritize one
    session at any time (rate_limiter.py set-owner <owner> --rpm-scale 0.5 --priority).
    `kind` is the fairness-ladder request kind; `approved` records that an agent-initiated
    priority lane had user approval (--user-approved)."""
    fd = limiter._locked()
    try:
        now = limiter._clock()
        if isinstance(limiter, SlidingWindowLimiter):
            limiter._read(fd, now)
        else:
            limiter._load_state(fd, now)
        owners = dict(getattr(limiter, "_owners", {}))
        o = dict(owners.get(owner, {}))
        if priority is not None:
            o["priority"] = bool(priority)
        if rpm_scale is not None:
            o["rpm_scale"] = min(8.0, max(0.05, float(rpm_scale)))
        if kind is not None:
            o["kind"] = kind if kind in KINDS else "interactive"
        if approved:
            o["approved_by_user"] = True
        if o:
            owners[owner] = o
        else:
            owners.pop(owner, None)
        limiter._owners = owners
        if isinstance(limiter, SlidingWindowLimiter):
            stamps, cd, c429, l429, models, _w = limiter._read(fd, now)
            limiter._write(fd, stamps, cd, c429, l429, models, owners=owners)
        else:
            limiter._save_state(fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _queue_position(limiter) -> int | None:
    """This owner's best position among the live waiters (1 = next grant), or None when the
    owner is not queued. Informational (probe); ranked by the aging key."""
    fd = limiter._locked()
    try:
        now = limiter._clock()
        if isinstance(limiter, SlidingWindowLimiter):
            _s, _cd, _c, _l, _m, waiters = limiter._read(fd, now)
        else:
            limiter._load_state(fd, now)
            waiters = limiter._waiters
        cfg = _cfg()
        _prune_waiters(waiters, now, cfg["waiter_ttl_seconds"])
        owner = _owner()
        live = [tok for tok, w in waiters.items() if "granted" not in w]
        own = [tok for tok in live if waiters[tok].get("owner") == owner]
        if not own:
            return None
        ranked = sorted(live, key=lambda t: _rank(waiters[t], now, cfg), reverse=True)
        return ranked.index(own[0]) + 1
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def external_occupancy(limiter, owner: str) -> int:
    """0.50.1 probe: how many in-window slots belong to OTHER owners — the classifier that
    tells 'concurrent sessions ate my bucket' apart from a genuine defect."""
    fd = limiter._locked()
    try:
        now = limiter._clock()
        if isinstance(limiter, SlidingWindowLimiter):
            stamps, *_ = limiter._read(fd, now)
            return sum(1 for s in stamps if _stamp_parts(s)[1] not in (owner, "legacy"))
        limiter._load_state(fd, now)
        return 0
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _provider_limits(provider: str):
    """The provider's limits entry from provider-limits.json, or None. The file is strict:
    a set-but-invalid FA_PROVIDER_LIMITS raises LimitsError (loud by design — the proxy
    refuses to start rather than throttle on a broken file)."""
    from provider_limits import load
    return load().provider(provider)


def get_limiter(path: Path | str | None = None, rpm: float | None = None,
                clock=time.time, cooldown: float | None = None,
                provider: str = "nvidia"):
    """Factory. provider="nvidia" keeps TODAY's path/env/settings exactly
    (LA_NVIDIA_THROTTLE_STATE, saved mode, rpm). Any other provider: state file
    FA_LIMITER_DIR/<provider>.state (default ~/.claude/free-agents/limiter), rpm from
    provider-limits.json, and a quota sidecar <state>.quota when the provider has any
    tpm/rpd/rpw/tpd dimension. A provider with NO limits entry (or all dimensions null)
    gets None — no limiter, exactly today's unthrottled behavior ("limits unknown")."""
    quota = None
    if provider != "nvidia":
        lim = _provider_limits(provider)
        if lim is None or all(v is None for v in (lim.rpm, lim.tpm, lim.rpd, lim.rpw, lim.tpd)):
            return None
        if path is None:
            d = os.environ.get("FA_LIMITER_DIR") or str(
                Path.home() / ".claude" / "free-agents" / "limiter")
            path = Path(d) / f"{provider}.state"
        if rpm is None:
            rpm = lim.rpm
        if any(v is not None for v in (lim.tpm, lim.rpd, lim.rpw, lim.tpd)):
            from quota_ledger import QuotaLedger
            quota = QuotaLedger(Path(str(path) + ".quota"), lim, clock=clock)
        mode = "sliding_window"     # non-nvidia providers have no legacy writers
    else:
        lim = _provider_limits("nvidia")   # may raise LimitsError on a bad FA_PROVIDER_LIMITS (loud)
        if rpm is None:
            # today's order env > saved > 40, with the limits file between saved and default
            env_rpm = os.environ.get("LA_NVIDIA_RPM")
            if env_rpm:
                rpm = float(env_rpm)
            elif _saved_settings().get("rpm") is not None:
                rpm = float(_saved_settings()["rpm"])
            elif lim is not None and lim.rpm is not None:
                rpm = float(lim.rpm)
        if any(v is not None for v in ((lim.tpm, lim.rpd, lim.rpw, lim.tpd) if lim else ())):
            from quota_ledger import QuotaLedger
            side_path = Path(path or os.environ.get("LA_NVIDIA_THROTTLE_STATE") or DEFAULT_STATE)
            quota = QuotaLedger(Path(str(side_path) + ".quota"), lim, clock=clock)
        mode = str(_setting("mode", "sliding_window")).lower()
    if mode == "smooth_bucket":
        return SmoothTokenBucket(path=path, rpm=rpm, clock=clock, cooldown=cooldown,
                                 quota=quota)
    return SlidingWindowLimiter(path=path, rpm=rpm, clock=clock, cooldown=cooldown,
                                quota=quota)


def stretched_wait(provider: str, base: float, *, cap: float = 1800.0,
                   clock=time.time) -> float:
    """C3 (0.62.0): the wait budget for a bucketed provider, stretched by queue depth and an
    active cooldown, capped by the patient tier:
    min(cap, base * (1 + queued_waiters / max(1, rpm)) + remaining_cooldown).
    Unknown/unlimited providers get the plain base (capped)."""
    try:
        lim = _provider_limits(provider)
    except Exception:
        return min(cap, base)
    rpm = float(lim.rpm) if lim and lim.rpm else 40.0
    try:
        b = get_limiter(provider=provider, clock=clock)
    except Exception:
        return min(cap, base)
    if b is None:
        return min(cap, base)
    st = b.status()
    waiters = len(st.get("waiters", []))
    cooldown = float(st.get("cooldown_remaining", 0.0) or 0.0)
    return min(cap, base * (1 + waiters / max(1.0, rpm)) + cooldown)


def load_detected(provider: str, env=None, clock=time.time) -> bool:
    """C3 (0.62.0): True when the provider's bucket is loaded right now — active cooldown,
    consecutive 429s, or queued waiters >= rpm/2. Read-only peek: never creates state,
    never raises. Provider-generic (was nvidia-only)."""
    env = env if env is not None else os.environ
    try:
        lim = _provider_limits(provider)
    except Exception:
        return False
    if lim is None or all(v is None for v in (lim.rpm, lim.tpm, lim.rpd, lim.rpw, lim.tpd)):
        return False
    # resolve the state path WITHOUT creating it
    if provider == "nvidia":
        path = Path(env.get("LA_NVIDIA_THROTTLE_STATE")
                    or (Path.home() / ".claude" / "free-agents" / ".nvidia_throttle_state"))
    else:
        d = env.get("FA_LIMITER_DIR") or str(
            Path.home() / ".claude" / "free-agents" / "limiter")
        path = Path(d) / f"{provider}.state"
    if not path.is_file():
        return False
    try:
        b = get_limiter(path=path, provider=provider, clock=clock)
        if b is None:
            return False
        st = b.status()
    except Exception:
        return False
    rpm = float(lim.rpm or 40)
    return (float(st.get("cooldown_remaining", 0.0) or 0.0) > 0
            or int(st.get("consecutive_429s", 0) or 0) > 0
            or len(st.get("waiters", [])) >= rpm / 2)


def provider_headroom(provider: str, clock=time.time) -> float:
    """Read-only 0.0–1.0: min over the provider's dimensions of remaining/limit;
    1.0 for unlimited or unknown providers. Used by the ladders (Tasks 9/10)."""
    try:
        lim = _provider_limits(provider)
    except Exception:
        return 1.0
    if lim is None or all(v is None for v in (lim.rpm, lim.tpm, lim.rpd, lim.rpw, lim.tpd)):
        return 1.0
    try:
        b = get_limiter(provider=provider, clock=clock)
        if b is None:
            return 1.0
        parts = []
        if lim.rpm:
            st = b.status()
            in_window = st.get("in_window")
            if in_window is None:
                in_window = len(st.get("grants", []))
            parts.append(max(0.0, 1.0 - float(in_window) / float(lim.rpm)))
        if b.quota is not None:
            snap = b.quota.snapshot(clock())
            # 0.62.3 review fix: units matter per dimension — requests compare against
            # rpd/rpw, tokens against tpm/tpd. 0.62.0 compared day/week TOKENS against the
            # rpd/rpw REQUEST limits and read a tpm "tokens" key the snapshot never has.
            tpm = snap.get("tpm") or {}
            if lim.tpm and tpm.get("used") is not None:
                parts.append(max(0.0, 1.0 - float(tpm["used"]) / float(lim.tpm)))
            day = snap.get("day") or {}
            if lim.rpd:
                rem = day.get("remaining_rpd")
                used_req = (lim.rpd - rem) if rem is not None else day.get("requests", 0)
                parts.append(max(0.0, 1.0 - float(used_req) / float(lim.rpd)))
            if lim.tpd and day.get("tokens") is not None:
                parts.append(max(0.0, 1.0 - float(day["tokens"]) / float(lim.tpd)))
            week = snap.get("week") or {}
            if lim.rpw and week.get("requests") is not None:
                parts.append(max(0.0, 1.0 - float(week["requests"]) / float(lim.rpw)))
        return min(parts) if parts else 1.0
    except Exception:
        return 1.0


def _open_menu() -> int:
    """The interactive menu is the session picker's rate limiter screen (one UI, persisted)."""
    picker = Path(__file__).resolve().parent / "session-picker"
    os.execv(str(picker), [str(picker), "rate-limiter"])
    return 1  # not reached


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="rate_limiter.py",
        description="Machine-wide NVIDIA rate limiter (file-backed, fcntl-locked).",
        epilog="Env: LA_NVIDIA_THROTTLE_STATE, LA_NVIDIA_RPM (default 40), "
               "LA_NVIDIA_MODE (sliding_window|smooth_bucket), "
               "LA_NVIDIA_BUCKET_CAPACITY (default 6), "
               "LA_NVIDIA_MAX_WAIT (default 300), LA_NVIDIA_429_COOLDOWN (base, default 30), "
               "LA_NVIDIA_429_MAX_COOLDOWN (default 300), LA_NVIDIA_429_BACKOFF_MULTIPLIER (default 2.0), "
               "LA_NVIDIA_429_MAX_RETRIES (default 5); lane keys LA_LIMITER_PRIORITY / "
               "LA_LIMITER_RESERVE (lane share cap) / LA_LIMITER_KIND / LA_LIMITER_LANE_APPROVAL; "
               "saved picker settings below the environment.")
    sub = parser.add_subparsers(dest="cmd", required=False)
    acq = sub.add_parser("acquire", help="take one slot, waiting if needed")
    acq.add_argument("--max-wait", type=float, default=None)
    acq.add_argument("--priority", action="store_true",
                     help="register this request in the priority lane (queue-jump, "
                          "share-capped under contention)")
    sub.add_parser("status", help="print window state without taking a slot")
    sub.add_parser("owners", help="list known owners with their window counts and options")
    so = sub.add_parser("set-owner", help="set one owner's options live (throttle/prioritize)")
    so.add_argument("owner", help="owner id (session id, or pid-N)")
    so.add_argument("--priority", action="store_true", help="allow this owner into the reserve")
    so.add_argument("--no-priority", dest="priority", action="store_false")
    so.add_argument("--rpm-scale", type=float, default=None,
                    help="this owner's allowed fraction of the window (0.5 = half rate)")
    so.add_argument("--kind", choices=KINDS, default=None,
                    help="this owner's request kind for the fairness ladder "
                         "(interactive|dispatch|background)")
    so.add_argument("--user-approved", action="store_true",
                    help="record that the user approved an agent-initiated --priority lane "
                         "(required in ask mode outside a TTY)")
    sub.add_parser("probe", help="print this owner's external occupancy (other owners' slots in window)")
    lt = sub.add_parser("lane-toggle",
                        help="flip THIS owner's priority lane (a user action — the picker calls "
                             "it with --user-approved; gated like set-owner in ask mode)")
    lt.add_argument("--user-approved", action="store_true",
                    help="record that the user approved the flip (required in ask mode outside a TTY)")
    sub.add_parser("menu", help="open the session picker's rate limiter screen")
    args = parser.parse_args(argv)

    if args.cmd == "menu" or (not argv and sys.stdin.isatty()):
        return _open_menu()

    if not args.cmd:
        parser.print_help()
        return 2

    limiter = get_limiter()
    if args.cmd == "status":
        print(json.dumps(limiter.status()))
        return 0
    if args.cmd == "owners":
        info = owners_info(limiter)
        print(json.dumps(info, indent=2))
        return 0
    if args.cmd == "set-owner":
        if args.priority and not args.user_approved:
            # approval gate (lane_approval, default ask): an AGENT-initiated lane needs the
            # user. Picker clicks and launcher flags are the user acting — they pass
            # --user-approved. `agent` mode skips the gate entirely.
            approval = str(_hot("lane_approval", DEFAULT_LANE_APPROVAL)).lower()
            if approval != "agent":
                if sys.stdin.isatty():
                    ans = input(f"grant the priority lane to owner {args.owner!r}? [y/N] ")
                    if ans.strip().lower() not in ("y", "yes"):
                        print("rate_limiter: refused", file=sys.stderr)
                        return 2
                else:
                    print("rate_limiter: an agent-initiated priority lane needs user approval "
                          "(lane_approval=ask): ask the user first, then re-run with "
                          "--user-approved, or set lane_approval=agent", file=sys.stderr)
                    return 2
        set_owner(limiter, args.owner, priority=args.priority, rpm_scale=args.rpm_scale,
                  kind=args.kind, approved=bool(args.priority and args.user_approved))
        print(json.dumps(owners_info(limiter)))
        return 0
    if args.cmd == "probe":
        occ = external_occupancy(limiter, _owner())
        print(json.dumps({"owner": _owner(), "external_in_window": occ,
                          "queue_position": _queue_position(limiter)}))
        return 0
    if args.cmd == "lane-toggle":
        # the SAME approval gate as set-owner --priority: lane-toggle is a CLI any process
        # could call, so an agent must not self-grant a lane through it. The picker passes
        # --user-approved because the click IS the user acting.
        if not args.user_approved:
            approval = str(_hot("lane_approval", DEFAULT_LANE_APPROVAL)).lower()
            if approval != "agent":
                if sys.stdin.isatty():
                    ans = input(f"flip the priority lane for owner {_owner()!r}? [y/N] ")
                    if ans.strip().lower() not in ("y", "yes"):
                        print("rate_limiter: refused", file=sys.stderr)
                        return 2
                else:
                    print("rate_limiter: flipping a priority lane outside a TTY needs user "
                          "approval (lane_approval=ask): ask the user first, then re-run with "
                          "--user-approved, or set lane_approval=agent", file=sys.stderr)
                    return 2
        owner = _owner()
        info = owners_info(limiter)
        cur = bool((info.get("owners") or {}).get(owner, {}).get("priority"))
        set_owner(limiter, owner, priority=not cur, rpm_scale=None, approved=True)
        print(json.dumps({"owner": owner, "priority": not cur, "approved_by_user": True}))
        return 0
    try:
        waited = limiter.acquire(args.max_wait, priority=args.priority)
    except RateLimitTimeout as exc:
        print(f"rate_limiter: {exc}", file=sys.stderr)
        return 3
    print(f"{time.time():.3f} acquired waited={waited:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

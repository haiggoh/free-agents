#!/usr/bin/env python3
"""csl-model-probe.py — background reachability probe for catalog-listed models (R5, 0.65.0).

One 16-token request per model. The deadline is measured FROM PROVIDER SEND: time spent
queued in the limiter (full window or cooldown) never counts as "not responding". A probe
the limiter refused to send is "unknown", never "unreachable". A 200 is "reachable"; silence
past the deadline is "unreachable"; 429/other send failures are "unknown".

Marks live in <config-dir>/csl-unreachable-marks.local.json (config dir via the R1-A resolver,
session_menu_state.config_dir()) and carry a TTL — an expired mark is DROPPED on load, never
persisted: a still-listed model is "temporarily" unreachable, never "retired" (retired = 410 +
gone from the catalog, handled elsewhere).

Usage:
  csl-model-probe.py probe <alias> [--deadline S] [--ttl S]   probe once, print the state
  csl-model-probe.py marks [--drop-expired]                    print the marks file as JSON
  csl-model-probe.py --help

Environment:
  FREE_AGENTS_CONFIG_DIR   config dir (R1-A canonical resolver; alias LA_SESSION_MENU_CONFIG_DIR)
  LA_LIMITER_PRIORITY=1    run the probe in the priority lane (queue-jump under contention)
"""
from __future__ import annotations  # 3.9-safe

import argparse
import json
import os
import sys
import time
from pathlib import Path

MARKS_NAME = "csl-unreachable-marks.local.json"
DEFAULT_DEADLINE_S = 20.0
DEFAULT_TTL_S = 900.0
PROBE_MAX_TOKENS = 16
STATES = ("reachable", "unreachable", "unknown")


def config_dir() -> Path:
    """The ONE config-dir resolver (R1-A) — never read the env names directly."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import session_menu_state as sms
    return sms.config_dir()


def marks_file(cfg: Path | None = None) -> Path:
    return (cfg or config_dir()) / MARKS_NAME


def load_marks(cfg: Path | None = None, now: float | None = None) -> dict:
    """Marks with TTL expiry applied: an expired mark is dropped here, never persisted."""
    now = time.time() if now is None else now
    try:
        doc = json.loads(marks_file(cfg).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(doc, dict):
        return {}
    out = {}
    for alias, m in doc.items():
        if not isinstance(m, dict):
            continue
        try:
            if now - float(m.get("since", 0)) > float(m.get("ttl", DEFAULT_TTL_S)):
                continue                                   # expired -> dropped, not persisted
        except (TypeError, ValueError):
            continue
        out[str(alias)] = m
    return out


def save_mark(cfg: Path | None, alias: str, state: str, ttl_s: float, now: float) -> None:
    if state not in STATES:
        raise ValueError(f"unknown probe state {state!r} (one of {STATES})")
    f = marks_file(cfg)
    marks = load_marks(cfg, now=now)
    marks[alias] = {"state": state, "since": now, "ttl": ttl_s}
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(marks, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def classify_send(result, exc) -> str:
    """200 -> reachable; timeout at the provider -> unreachable; 429/other -> unknown."""
    if exc is not None:
        if isinstance(exc, TimeoutError):
            return "unreachable"
        return "unknown"
    status = result[0] if isinstance(result, tuple) else "200"
    if str(status) == "200":
        return "reachable"
    return "unknown"          # 429 throttled, 5xx, anything else: not evidence of unreachable


def limiter_gate(provider):
    """NON-BLOCKING limiter gate for one probe: True when the request may leave for the
    provider now, False when the bucket refuses (cooling/queue budget) — a refused probe
    is "unknown", never "unreachable" (the request never left). HIGH priority comes from
    the caller's environment (LA_LIMITER_PRIORITY=1), never set here."""
    try:
        import rate_limiter as rl
        limiter = rl.get_limiter(provider=provider)
        return limiter is None or limiter.try_acquire()
    except Exception:
        return True            # limiter absent/broken must never invent "unreachable"


def send_for_provider(provider, model_id, *, deadline_s=DEFAULT_DEADLINE_S,
                      max_tokens=PROBE_MAX_TOKENS):
    """ONE 16-token POST to the provider's chat endpoint. Raises TimeoutError when the
    provider stays silent past the deadline (that silence IS the unreachable evidence)."""
    import urllib.request
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import remote_provider_core as rpc
    p = rpc.get(provider)
    if p is None:
        raise ValueError(f"unknown provider {provider!r}")
    url = p.base().rstrip("/") + (p.chat_path or "/chat/completions")
    body = json.dumps({"model": model_id, "stream": False, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": "ping"}]}).encode()
    req = urllib.request.Request(url, data=body, headers={
        **p.auth_headers(), "Content-Type": "application/json"})
    import fa_tls
    with urllib.request.urlopen(req, timeout=max(1, int(deadline_s)),
                                context=fa_tls.client_context()) as r:
        return r.status, r.read()[:200]


def probe_provider(provider, model_id, *, gate_provider=None,
                   deadline_s=DEFAULT_DEADLINE_S, clock=time.monotonic):
    """One probe against the REAL provider plumbing: non-blocking limiter gate (queued
    time never counts — the clock only runs inside send), then the send."""
    gate_provider = gate_provider or provider
    allowed = limiter_gate(gate_provider)
    start = clock()
    if not allowed:
        return {"alias": model_id, "state": "unknown", "why": "not sent (limiter gate)",
                "queued_s": round(clock() - start, 3)}
    try:
        result = send_for_provider(provider, model_id, deadline_s=deadline_s)
        state = classify_send(result, None)
    except Exception as exc:
        state = classify_send(None, exc)
    elapsed = clock() - start
    if state == "reachable" and elapsed > deadline_s:
        state = "unreachable"
    return {"alias": model_id, "state": state, "elapsed_s": round(elapsed, 3)}


def probe_alias(alias, *, send, gate=lambda: True, deadline_s=DEFAULT_DEADLINE_S,
                clock=time.monotonic):
    """Probe one alias. `gate()` decides whether the request may leave for the provider
    (limiter queue/cooldown budget); `send()` performs the provider call and returns
    (status, body) or raises. The deadline covers ONLY the send."""
    if not gate():
        return {"alias": alias, "state": "unknown", "why": "not sent (limiter gate)"}
    start = clock()                       # deadline starts at provider send, not at enqueue
    try:
        result = send()
        state = classify_send(result, None)
    except Exception as exc:
        state = classify_send(None, exc)
    elapsed = clock() - start
    if state == "reachable" and elapsed > deadline_s:
        state = "unreachable"        # an answer that arrives past the deadline counts as none
    return {"alias": alias, "state": state, "elapsed_s": round(elapsed, 3)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="csl-model-probe.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="probe one alias (stub: always unknown without a provider)")
    p.add_argument("alias")
    p.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE_S)
    p.add_argument("--ttl", type=float, default=DEFAULT_TTL_S)
    m = sub.add_parser("marks", help="print the marks file as JSON")
    m.add_argument("--drop-expired", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "marks":
        now = time.time()
        marks = load_marks(now=now)
        print(json.dumps(marks, indent=2))
        return 0
    if args.cmd == "probe":
        # no real provider call in the CLI path: the limiter gate is the only honest source
        r = probe_alias(args.alias, send=lambda: ("200", b""), gate=lambda: False,
                        deadline_s=args.deadline)
        save_mark(None, args.alias, r["state"], ttl_s=args.ttl, now=time.time())
        print(r["state"])
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

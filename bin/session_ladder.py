#!/usr/bin/env python3
"""session_ladder.py — the session/handover ladder: ORDERED with spill (spec C7, Task 9).

pick() takes the saved order (config/session-preferences.json) and returns the first ELIGIBLE
alias whose predicted wait fits its stretched timeout, plus a (alias, "skipped: <why>") reason
for every skipped one — so the user can see WHY the top pick was passed over.

Eligibility: key present for the provider, provider not exhausted (headroom 0), model context
>= max(request need, session_min_context) and KNOWN, not dispatch_only, not broken.
Sessions must never gamble on an unknown context — "context unknown" is a skip reason.

Usage:
  session_ladder.py --help
  session_ladder.py pick --need-context N [--order A,B,...]   (demo; reads the repo config)

Environment: FA_PROVIDER_LIMITS (limits file override), FA_LIMITER_DIR, LA_NVIDIA_THROTTLE_STATE.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))


def pick(order, *, need_context, limits, headroom, predicted_wait, timeout, roster,
         keyed=None):
    """-> (alias | None, [(alias, "skipped: <why>"), ...]).

    order:   alias list (config/session-preferences.json "order")
    roster:  {alias: (provider, model_id)}
    keyed:   None or a callable provider->bool ("has a key on this machine")
    limits:  provider_limits.Limits
    headroom / predicted_wait / timeout: callables (provider or alias) -> float
    """
    reasons = []
    need = max(int(need_context), limits.session_min_context)
    for alias in order:
        prov, model_id = roster.get(alias, (None, None))
        if prov is None:
            reasons.append((alias, "skipped: not in the roster"))
            continue
        if keyed is not None and not keyed(prov):
            reasons.append((alias, f"skipped: no key for provider {prov}"))
            continue
        pl = limits.provider(prov)
        if pl is not None and pl.dispatch_only:
            reasons.append((alias, f"skipped: {prov} is dispatch_only (never a full session)"))
            continue
        facts = limits.model(prov, model_id)
        if facts is None or facts.context is None:
            # fix/runtime-model-facts: a candidate the caches cannot answer gets ONE bounded online
            # lookup (launch time, cached afterwards), so a model released after the snapshot is
            # judged on real facts instead of being skipped forever. A stub limits object without
            # the keyword keeps the offline answer.
            try:
                facts = limits.model(prov, model_id, online=True)
            except TypeError:
                pass
        if facts is not None and facts.broken:
            reasons.append((alias, f"skipped: {prov}:{model_id} is marked broken"))
            continue
        ctx = facts.context if facts is not None else None
        if ctx is None:
            reasons.append((alias, "skipped: context unknown (sessions never gamble)"))
            continue
        if ctx < need:
            reasons.append((alias, f"skipped: context {ctx} < session need {need}"))
            continue
        if headroom(prov) <= 0.0:
            reasons.append((alias, f"skipped: {prov} exhausted today/week (headroom 0)"))
            continue
        w = float(predicted_wait(alias))
        t = float(timeout(alias))
        if w > t:
            reasons.append((alias,
                            f"skipped: {prov} predicted wait {w:.0f}s > {t:.0f}s timeout"))
            continue
        return alias, reasons
    return None, reasons


def _roster() -> dict:
    """{alias: (provider, model_id)} from remote-session.sh --inventory."""
    import subprocess
    out = subprocess.run(["bash", str(REPO / "bin" / "remote-session.sh"), "--inventory"],
                         capture_output=True, text=True, timeout=300, check=True).stdout
    roster = {}
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) >= 8:
            roster[cols[0]] = (cols[1], cols[-1])
    return roster


def main(argv=None) -> int:
    import provider_limits as pl
    import rate_limiter as rl
    ap = argparse.ArgumentParser(
        prog="session_ladder.py",
        description="Pick the session/handover model: ordered + spill. Env: "
                    "FA_PROVIDER_LIMITS, FA_LIMITER_DIR, LA_NVIDIA_THROTTLE_STATE.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick", help="print the picked alias and the skip reasons (dry demo)")
    p.add_argument("--need-context", type=int, default=262145)
    p.add_argument("--order", default=None, help="comma-separated aliases (default: the saved order)")
    args = ap.parse_args(argv)
    limits = pl.load()
    order = args.order.split(",") if args.order else json.loads(
        (REPO / "config" / "session-preferences.json").read_text()).get("order") or []
    roster = _roster()
    import remote_provider_core as rpc
    keyed_providers = {pid for pid, p in rpc.PROVIDERS.items() if p.key_name and p.key()}
    alias, reasons = pick(
        order, need_context=args.need_context, limits=limits,
        headroom=rl.provider_headroom,
        predicted_wait=lambda a: rl.stretched_wait(roster[a][0], 300),
        timeout=lambda a: 600.0,
        roster=roster,
        keyed=lambda prov: prov in keyed_providers)
    print(alias or "<none eligible>")
    for a, why in reasons:
        print(f"  {why} [{a}]")
    return 0 if alias else 1


if __name__ == "__main__":
    sys.exit(main())

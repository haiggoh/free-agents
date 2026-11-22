#!/usr/bin/env python3
"""effort_resolve.py — the ONE choke point for "what effort does this launch use?" (0.62.0 C6).

Precedence (user, 2026-11-21/22): explicit --effort > the SAVED lane default (present in
session-menu.local.json — DEFAULT_EFFORT must NOT mask the last-launch layer) > the per-alias
last-launch (last-effort.py) > provider default. Lanes: remote session/handover/remote dispatch
-> remote_api_session; local session/local dispatch -> local_session.

Usage:
  effort_resolve.py --help
  effort_resolve.py resolve --lane LANE [--alias ALIAS] [--explicit LEVEL]
      prints "<level-or-empty> <provenance>" where provenance is one of
      explicit | saved-lane | last-launch | provider-default.

Environment:
  FREE_AGENTS_CONFIG_DIR   session-menu dir (default ~/.claude/free-agents/session-menu)
  LA_LAST_EFFORT_FILE      last-effort state file override (see bin/last-effort.py)
An invalid --explicit LEVEL aborts with ValueError naming it (exit 2 with the message on
stderr). A missing/corrupt state file fails OPEN (falls through to the next layer).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))

LANES = ("local_session", "remote_api_session", "lowkey")
LEVELS = ("low", "medium", "high", "xhigh", "max")
PROVENANCES = ("explicit", "saved-lane", "last-launch", "provider-default")


def _config_dir(config_dir=None) -> Path:
    if config_dir is not None:
        return Path(config_dir)
    env = os.environ.get("FREE_AGENTS_CONFIG_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "free-agents" / "session-menu"


def saved_effort(config_dir=None, lane: str = "remote_api_session") -> str | None:
    """The SAVED lane default: the lane key PRESENT in the session-menu.local.json FILE.
    None when the key is absent — DEFAULT_EFFORT must not mask the last-launch layer."""
    if lane not in LANES:
        raise ValueError(f"effort_resolve: unknown lane {lane!r} (one of {LANES})")
    path = _config_dir(config_dir) / "session-menu.local.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None                      # fail open: fall through to last-launch
    effort = doc.get("effort") if isinstance(doc, dict) else None
    if isinstance(effort, dict) and lane in effort:
        v = effort[lane]
        if v in LEVELS:
            return v
    return None


def _last_launch(alias: str) -> str | None:
    spec = importlib.util.spec_from_file_location("last_effort", REPO / "bin" / "last-effort.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("last_effort", mod)
    spec.loader.exec_module(mod)
    try:
        val = (mod.load(mod.state_file()) or {}).get(alias)
    except Exception:
        return None
    return val if val in LEVELS else None


def resolve(lane: str, alias: str | None = None, explicit: str | None = None,
            *, config_dir=None) -> tuple:
    """-> (level|None, provenance). level None = provider default (caller decides)."""
    if lane not in LANES:
        raise ValueError(f"effort_resolve: unknown lane {lane!r} (one of {LANES})")
    if explicit is not None:
        if explicit not in LEVELS:
            raise ValueError(
                f"effort_resolve: invalid explicit effort {explicit!r} — one of {', '.join(LEVELS)}")
        return explicit, "explicit"
    saved = saved_effort(config_dir, lane)
    if saved is not None:
        return saved, "saved-lane"
    if alias:
        last = _last_launch(alias)
        if last is not None:
            return last, "last-launch"
    return None, "provider-default"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="effort_resolve.py",
        description="Resolve the effort for a launch/dispatch/handover: explicit > saved lane "
                    "default (session-menu.local.json) > per-alias last-launch (last-effort.py) "
                    "> provider default. Env: FREE_AGENTS_CONFIG_DIR, LA_LAST_EFFORT_FILE.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("resolve", help="print '<level-or-empty> <provenance>'")
    r.add_argument("--lane", required=True, choices=LANES)
    r.add_argument("--alias", default=None)
    r.add_argument("--explicit", default=None, choices=LEVELS)
    r.add_argument("--config-dir", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "resolve":
        try:
            level, prov = resolve(args.lane, alias=args.alias, explicit=args.explicit,
                                  config_dir=args.config_dir)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"{level or ''} {prov}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

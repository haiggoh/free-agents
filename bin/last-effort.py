#!/usr/bin/env python3
"""last-effort.py — per-alias store of the effort used at the last remote-session launch (0.49.3).

remote-session.sh reads it when --effort is omitted (default = the alias's stored last-launch
effort, else the provider default) and records the effective effort on every real launch.
Incident 2026-10-31: a handover launched without --effort ran at medium instead of the user's
stored max.

Usage:
  last-effort.py get <alias>            print the stored effort (empty if none); exit 0
  last-effort.py set <alias> <level>    record <level> for <alias>
                                        (level = low|medium|high|xhigh|max; anything else exits 2)
  last-effort.py --help                 this text

Environment:
  LA_LAST_EFFORT_FILE   state file path (default: <repo>/config/remote-last-effort.local.json,
                        which matches the gitignored config/*.local.* pattern)

A corrupt or unreadable state file fails OPEN (prints nothing / keeps the old data on set only
when parseable) — it must never break a launch.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

LEVELS = ("low", "medium", "high", "xhigh", "max")


def state_file() -> Path:
    override = os.environ.get("LA_LAST_EFFORT_FILE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "config" / "remote-last-effort.local.json"


def load(f: Path) -> dict:
    try:
        data = json.loads(f.read_text()) if f.exists() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="last-effort.py",
        description="per-alias last-launch effort store used by remote-session.sh (0.49.3)",
        epilog="Environment: LA_LAST_EFFORT_FILE = state file path (default: "
               "<repo>/config/remote-last-effort.local.json, which matches the gitignored "
               "config/*.local.* pattern). A corrupt/unreadable state file fails open.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get", help="print the stored effort for an alias (empty if none)")
    g.add_argument("alias")
    s = sub.add_parser("set", help="record an effort level for an alias")
    s.add_argument("alias")
    s.add_argument("level", choices=LEVELS)
    args = ap.parse_args()

    f = state_file()
    data = load(f)
    if args.cmd == "get":
        print(data.get(args.alias, ""))
        return 0
    data[args.alias] = args.level
    f.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(f.parent), prefix=f.name + ".")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        os.replace(tmp, f)
        os.chmod(f, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())

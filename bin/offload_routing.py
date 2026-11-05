#!/usr/bin/env python3
"""offload_routing.py — read the ONE marked routing block from skills/offload/SKILL.md.

0.54.0 Task 8/9: the hooks (cloud-agent-intercept.py, offload-nudge.py) emit this block
VERBATIM instead of carrying their own copy-pasted routing text, so a skill edit can never
drift from what the hooks say. The block lives between `<!-- fa:routing:begin -->` and
`<!-- fa:routing:end -->` in SKILL.md.

Usage:
  offload_routing.py --help       this text
  offload_routing.py --print      print the block (or the loud missing-block line) and exit

Library:
  routing_block(plugin_root: str) -> str
      Reads <plugin_root>/skills/offload/SKILL.md. When the markers are missing or the file
      is unreadable, returns the literal warning line (never raises) so the hooks can emit
      a loud "routing block missing" instead of silently empty guidance.

Env: none. CLAUDE_PLUGIN_ROOT is the usual plugin-root hint; callers may pass the path
explicitly (the intercept falls back to its own parents[1]).
"""
import argparse
import sys
from pathlib import Path

BEGIN = "<!-- fa:routing:begin"
END = "<!-- fa:routing:end"

MISSING = ("⚠ free-agents: offload routing block missing from skills/offload/SKILL.md "
           "— route free via the offload skill")


def routing_block(plugin_root: str) -> str:
    """The marked block's inner text, stripped. Never raises."""
    try:
        text = Path(plugin_root).resolve() / "skills" / "offload" / "SKILL.md"
        raw = text.read_text(encoding="utf-8")
    except OSError:
        return MISSING
    i = raw.find(BEGIN)
    j = raw.find(END)
    if i < 0 or j < 0 or j < i:
        return MISSING
    block = raw[raw.find("-->", i) + 3: j]
    return block.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="offload_routing.py",
        description="Read the marked routing block (fa:routing:begin/end) from "
                    "skills/offload/SKILL.md — the single source the hooks emit verbatim.",
        epilog="Env: none. Library use: routing_block(plugin_root).")
    ap.add_argument("--print", action="store_true", help="print the routing block and exit")
    ap.add_argument("--plugin-root", default=None,
                    help="plugin root to read (default: CLAUDE_PLUGIN_ROOT or this repo)")
    args = ap.parse_args(argv)
    root = args.plugin_root or __import__("os").environ.get("CLAUDE_PLUGIN_ROOT") \
        or str(Path(__file__).resolve().parent.parent)
    print(routing_block(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())

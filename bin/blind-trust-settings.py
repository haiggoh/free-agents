#!/usr/bin/env python3
"""Single tested blind-trust settings generator with destructive deny list.

This is the ONLY source of blind-trust settings JSON for both launchers.
It replaces three hand-duplicated inline generations that claimed
"sandbox.enabled=true is the write boundary" (false since sandbox is now off).

The generator produces settings that:
- Use bypassPermissions mode (measured winner from blind_trust_mechanism_probe.py)
- Always include a deterministic destructive deny list
- Merge master + profile allowlists, de-duplicated, order preserved
- Drop mcp__* rules unless --enable-mcp
- Write mode 600
- Never include a sandbox key (decided by launch profile, now off)
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

DESTRUCTIVE_DENY = [
    "Bash(sudo:*)",
    "Bash(git push --force:*)", "Bash(git push -f:*)",
    "Bash(git reset --hard:*)",
    "Bash(gh release create:*)",
    "Bash(rm -rf /:*)", "Bash(rm -rf ~:*)", "Bash(rm -rf $HOME:*)",
    "Edit(~/.claude/settings.json)", "Edit(~/.claude/settings.local.json)",
    "Edit(~/.claude/plugins/**)",
]


def load_allowlist(path: str) -> list[str]:
    """Load allowlist from JSON file. Returns empty list on any error.
    Supports both array format and object format with permissions.allow.
    Prints a warning to stderr when file is missing or invalid."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("permissions", {}).get("allow"), list):
            return data["permissions"]["allow"]
    except FileNotFoundError:
        print(f"blind-trust-settings: warning: allowlist file not found: {path}", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"blind-trust-settings: warning: invalid JSON in {path}: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"blind-trust-settings: warning: failed to load {path}: {e}", file=sys.stderr)
        return []
    return []


def build_settings(
    master_allow: list[str],
    profile_allow: list[str],
    mechanism: str = "bypass",
    enable_mcp: bool = False,
) -> dict[str, Any]:
    """Build blind-trust settings dict.

    Args:
        master_allow: Master allowlist entries
        profile_allow: Profile-specific allowlist entries
        mechanism: "bypass" or "hook" (default: "bypass" - measured winner)
        enable_mcp: Whether to keep mcp__* allow rules

    Returns:
        Settings dict ready for JSON serialization
    """
    # Merge allowlists: master first, then profile, de-duplicated preserving order
    seen = set()
    merged_allow = []
    for item in master_allow + profile_allow:
        if item not in seen:
            seen.add(item)
            if not enable_mcp and item.startswith("mcp__"):
                continue
            merged_allow.append(item)

    if mechanism == "bypass":
        return {
            "permissions": {
                "defaultMode": "bypassPermissions",
                "allow": merged_allow,
                "deny": DESTRUCTIVE_DENY,
            }
        }
    elif mechanism == "hook":
        # Hook mechanism keeps auto mode but installs an allow-all PreToolUse hook
        hook_command = 'printf \'{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"blind-trust"}}\''
        return {
            "permissions": {
                "defaultMode": "auto",
                "allow": merged_allow,
                "deny": DESTRUCTIVE_DENY,
                "hooks": {
                    "PreToolUse": [{
                        "matcher": "*",
                        "hooks": [{
                            "type": "command",
                            "command": hook_command,
                        }]
                    }]
                }
            }
        }
    else:
        raise ValueError(f"Unknown mechanism: {mechanism}")


def write_settings(settings: dict[str, Any], out_path: Path) -> None:
    """Write settings file atomically with mode 600."""
    ensure_private_directory(out_path.parent)
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(settings, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    os.chmod(out_path, 0o600)
    mode = stat.S_IMODE(out_path.stat().st_mode)
    if mode != 0o600:
        raise RuntimeError(f"Settings file mode is {mode:o}, expected 600: {out_path}")


def ensure_private_directory(path: Path) -> None:
    """Create directory if needed and ensure it's private (mode 700).
    Only chmod directories we created (not pre-existing system dirs like /tmp)."""
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    if not existed:
        os.chmod(path, 0o700)
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != 0o700:
            raise RuntimeError(f"Private directory mode is {mode:o}, expected 700: {path}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate blind-trust settings JSON for local-agents launchers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment: none
Examples:
  %(prog)s --master /path/master.json --profile /path/profile.json --out /tmp/settings.json
  %(prog)s --master /path/master.json --profile /path/profile.json --out /tmp/settings.json --enable-mcp
  %(prog)s --master /path/master.json --profile /path/profile.json --out /tmp/settings.json --mechanism hook
""",
    )
    ap.add_argument("--master", required=True, help="Path to master allowlist JSON file")
    ap.add_argument("--profile", required=True, help="Path to profile allowlist JSON file")
    ap.add_argument("--out", required=True, help="Output settings JSON file path")
    ap.add_argument("--enable-mcp", action="store_true", help="Keep mcp__* allow rules")
    ap.add_argument("--mechanism", choices=["bypass", "hook"], default="bypass",
                    help="Permission mechanism (default: bypass - measured winner)")

    args = ap.parse_args()

    master_allow = load_allowlist(args.master)
    profile_allow = load_allowlist(args.profile)

    settings = build_settings(master_allow, profile_allow, args.mechanism, args.enable_mcp)
    write_settings(settings, Path(args.out))

    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
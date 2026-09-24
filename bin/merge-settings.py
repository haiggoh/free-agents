#!/usr/bin/env python3
"""merge-settings.py — merge two Claude Code settings JSON files.

This script merges two settings files into one, handling the specific keys
that remote-session.sh uses:
- permissions (from blind-trust settings): deep merge allow arrays
- sandbox (from blind-trust settings): take from blind-trust
- network (from blind-trust settings): take from blind-trust
- spinnerVerbs (from per-session settings): take from per-session

Usage:
  merge-settings.py --base <file> --overlay <file> [--output <file>]

The --base is the blind-trust settings (permissions + sandbox + network).
The --overlay is the per-session settings (spinnerVerbs).
"""

import json
import os
import sys
import argparse
from pathlib import Path


def load_json_file(path: Path) -> dict:
    """Load JSON file, return empty dict on error."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to load {path}: {e}", file=sys.stderr)
        return {}


def merge_settings(base: dict, overlay: dict) -> dict:
    """Merge two settings objects.

    Merge strategy:
    - permissions: deep merge, combine 'allow' arrays (dedup)
    - sandbox: take from base (blind-trust settings)
    - network: take from base (blind-trust settings)
    - spinnerVerbs: take from overlay (per-session settings)
    - Other keys: overlay wins
    """
    result = dict(base)  # Start with base

    # Handle permissions deep merge
    if 'permissions' in base and 'permissions' in overlay:
        base_perms = base['permissions']
        overlay_perms = overlay['permissions']
        merged_perms = dict(base_perms)

        # Merge allow arrays (dedup while preserving order)
        if 'allow' in base_perms and 'allow' in overlay_perms:
            seen = set()
            merged_allow = []
            for item in base_perms['allow'] + overlay_perms['allow']:
                if item not in seen:
                    seen.add(item)
                    merged_allow.append(item)
            merged_perms['allow'] = merged_allow
        elif 'allow' in overlay_perms:
            merged_perms['allow'] = overlay_perms['allow']

        # Other permission keys: overlay wins
        for k, v in overlay_perms.items():
            if k != 'allow':
                merged_perms[k] = v

        result['permissions'] = merged_perms
    elif 'permissions' in overlay:
        result['permissions'] = overlay['permissions']

    # Handle sandbox: base wins (blind-trust settings has it)
    if 'sandbox' in base:
        result['sandbox'] = base['sandbox']
    elif 'sandbox' in overlay:
        result['sandbox'] = overlay['sandbox']

    # Handle network: base wins (blind-trust settings has it)
    if 'network' in base:
        result['network'] = base['network']
    elif 'network' in overlay:
        result['network'] = overlay['network']

    # Handle spinnerVerbs: overlay wins (per-session settings has it)
    if 'spinnerVerbs' in overlay:
        result['spinnerVerbs'] = overlay['spinnerVerbs']
    elif 'spinnerVerbs' in base:
        result['spinnerVerbs'] = base['spinnerVerbs']

    # Any other keys from overlay
    for k, v in overlay.items():
        if k not in ('permissions', 'sandbox', 'network', 'spinnerVerbs'):
            result[k] = v

    return result


def main():
    parser = argparse.ArgumentParser(description='Merge two Claude Code settings files')
    parser.add_argument('--base', required=True, help='Base settings file (blind-trust)')
    parser.add_argument('--overlay', required=True, help='Overlay settings file (per-session)')
    parser.add_argument('--output', help='Output file (default: stdout)')
    args = parser.parse_args()

    base_path = Path(args.base)
    overlay_path = Path(args.overlay)

    if not base_path.exists():
        print(f"ERROR: Base settings file not found: {base_path}", file=sys.stderr)
        return 1
    if not overlay_path.exists():
        print(f"ERROR: Overlay settings file not found: {overlay_path}", file=sys.stderr)
        return 1

    base_settings = load_json_file(base_path)
    overlay_settings = load_json_file(overlay_path)

    if not base_settings and not overlay_settings:
        print("ERROR: Both settings files are empty or invalid", file=sys.stderr)
        return 1

    merged = merge_settings(base_settings, overlay_settings)

    output_json = json.dumps(merged, separators=(',', ':'))

    if args.output:
        try:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(output_json + '\n')
        except Exception as e:
            print(f"ERROR: Failed to write output: {e}", file=sys.stderr)
            return 1
    else:
        print(output_json)

    return 0


if __name__ == '__main__':
    sys.exit(main())
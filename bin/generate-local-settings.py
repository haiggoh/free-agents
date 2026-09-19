#!/usr/bin/env python3
"""generate-local-settings.py — build per-session settings overlay for local sessions.

This script consumes the session identity (from la-session-identity.sh) and
local theme/spinner data to produce a JSON settings object that can be passed
to Claude Code via --settings. It does NOT write to the user's persistent
~/.claude/settings.json — the launcher passes it as a transient overlay.

Usage:
  generate-local-settings.py --identity-json <json> [--output <file>]

Environment:
  LAUNCH_DIR — directory containing this script (auto-detected if unset)
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
    except Exception:
        return {}


def get_model_family(model_alias: str) -> str:
    """Extract model family from alias."""
    alias = model_alias.lower()
    # Check specific families first
    if alias.startswith('qwen38') or alias.startswith('qwen3.8'):
        return 'qwen38'
    if alias.startswith('qwen36') or alias.startswith('qwen3.6'):
        return 'qwen3'
    if alias.startswith('qwen'):
        return 'qwen'
    if alias.startswith('deepseek-r1') or 'r1' in alias:
        return 'deepseek-r1'
    if alias.startswith('deepseek'):
        return 'deepseek'
    if alias.startswith('devstral'):
        return 'devstral'
    if alias.startswith('ministral'):
        return 'ministral'
    if alias.startswith('mistral'):
        return 'mistral'
    if alias.startswith('gemma'):
        return 'gemma'
    if alias.startswith('kat'):
        return 'kat'
    if alias.startswith('nemotron'):
        return 'nemotron'
    if alias.startswith('granite'):
        return 'granite'
    if alias.startswith('llama'):
        return 'llama'
    if alias.startswith('kimi'):
        return 'kimi'
    if alias.startswith('glm'):
        return 'glm'
    if alias.startswith('ornith'):
        return 'ornith'
    if alias.startswith('bonsai'):
        return 'bonsai'
    if alias.startswith('falcon'):
        return 'falcon'
    return 'general'


def select_spinner_verbs(family: str, spinner_data: dict) -> list:
    """Select spinner verbs for model family, with fallback."""
    verbs = spinner_data.get(family, [])
    if not verbs:
        verbs = spinner_data.get('general', [])
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for v in verbs:
        if v not in seen and v.isprintable() and '\n' not in v and '\r' not in v:
            seen.add(v)
            unique.append(v)
    return unique


def main():
    parser = argparse.ArgumentParser(description='Generate per-session settings for local sessions')
    parser.add_argument('--identity-json', required=True, help='Session identity JSON from la-session-identity.sh')
    parser.add_argument('--output', help='Output file (default: stdout)')
    parser.add_argument('--launch-dir', help='Launch directory (auto-detected)')
    args = parser.parse_args()

    # Parse identity JSON
    try:
        identity = json.loads(args.identity_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid identity JSON: {e}", file=sys.stderr)
        return 1

    # Determine launch directory
    if args.launch_dir:
        launch_dir = Path(args.launch_dir)
    else:
        launch_dir = Path(os.environ.get('LAUNCH_DIR', Path(__file__).parent))

    config_dir = launch_dir.parent / 'config'

    # Load theme and spinner data
    theme_data = load_json_file(config_dir / 'local-theme.json')
    spinner_data = load_json_file(config_dir / 'local-spinner-verbs.json')

    # Extract identity fields
    theme_identifier = identity.get('theme_identifier', 'unknown')
    session_kind = identity.get('session_kind', 'unknown')
    session_emoji = identity.get('session_emoji', '❓')
    actual_model_id = identity.get('actual_model_id', 'unknown')
    actual_model_display = identity.get('actual_model_display', 'unknown')
    spinner_profile_id = identity.get('spinner_profile_id', 'unknown')

    # Only apply local theme for local sessions
    if session_kind != 'local':
        # Return minimal settings for non-local sessions
        settings = {}
    else:
        # Get model family and select verbs
        model_family = get_model_family(actual_model_id)
        spinner_verbs = select_spinner_verbs(model_family, spinner_data)

        # Build theme from data
        theme = theme_data.get('theme', {})
        fallback = theme_data.get('fallback', {})

        settings = {
            "spinnerVerbs": {
                "mode": "replace",
                "verbs": spinner_verbs
            }
        }

        # Add session name for terminal title (uses -n/--name in Claude Code)
        # This is handled by the launcher via -n flag, not settings

    # Output
    output_json = json.dumps(settings, separators=(',', ':'))
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
#!/usr/bin/env python3
"""generate-remote-settings.py — build per-session settings overlay for remote sessions.

This script consumes the session identity (from la-session-identity.sh) and
remote theme/spinner data to produce a JSON settings object that can be passed
to Claude Code via --settings. It does NOT write to the user's persistent
~/.claude/settings.json — the launcher passes it as a transient overlay.

Usage:
  generate-remote-settings.py --identity-json <json> [--output <file>]

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


def get_provider_family(provider: str) -> str:
    """Extract provider family from provider name."""
    provider = provider.lower()
    # Map known providers
    if provider in ('gemini', 'groq', 'nvidia', 'openrouter', 'cerebras',
                    'cloudflare', 'mistral', 'zai', 'siliconflow',
                    'sambanova', 'vercel', 'modelscope', 'llm7', 'kilo'):
        return provider
    # Handle model-based providers (e.g., nemotron, kimi, qwen, deepseek)
    if 'nemotron' in provider:
        return 'nemotron'
    if 'kimi' in provider:
        return 'kimi'
    if 'qwen' in provider:
        return 'qwen'
    if 'deepseek' in provider:
        return 'deepseek'
    return 'general'


def select_spinner_verbs(family: str, spinner_data: dict) -> list:
    """Select spinner verbs for provider family, with fallback."""
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
    parser = argparse.ArgumentParser(description='Generate per-session settings for remote sessions')
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
    theme_data = load_json_file(config_dir / 'remote-theme.json')
    spinner_data = load_json_file(config_dir / 'remote-spinner-verbs.json')

    # Extract identity fields
    theme_identifier = identity.get('theme_identifier', 'unknown')
    session_kind = identity.get('session_kind', 'unknown')
    session_emoji = identity.get('session_emoji', '❓')
    provider_display = identity.get('provider_display', 'unknown')
    actual_model_id = identity.get('actual_model_id', 'unknown')
    spinner_profile_id = identity.get('spinner_profile_id', 'unknown')

    # Only apply remote theme for free_api sessions
    if session_kind != 'free_api':
        # Return minimal settings for non-remote sessions
        settings = {}
    else:
        # Get provider family and select verbs
        provider_family = get_provider_family(provider_display)
        spinner_verbs = select_spinner_verbs(provider_family, spinner_data)

        # Build theme from data
        theme = theme_data.get('theme', {})
        fallback = theme_data.get('fallback', {})

        settings = {
            "spinnerVerbs": {
                "mode": "replace",
                "verbs": spinner_verbs
            }
        }

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
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


# Known provider families, matched as SUBSTRINGS of the resolver's display string.
# The resolver hands us a display name like "Free API (NVIDIA)", never a bare "nvidia",
# so an equality test against these names can never match — every provider fell through
# to 'general', which is not a key in remote-spinner-verbs.json, so the verb list came
# back EMPTY and the spinner silently kept Claude Code's defaults.
PROVIDER_FAMILIES = ('gemini', 'groq', 'nvidia', 'openrouter', 'cerebras',
                     'cloudflare', 'mistral', 'zai', 'siliconflow',
                     'sambanova', 'vercel', 'modelscope', 'llm7', 'kilo')


def get_provider_family(provider: str) -> str:
    """Extract provider family from a provider display name.

    Matches on substring because the input is a display string, not an identifier.
    Model-family names are checked FIRST: a Nemotron served by NVIDIA should pun on the
    model the user actually picked.
    """
    provider = (provider or '').lower()
    # Model-based families take precedence over the serving provider.
    for model_family in ('nemotron', 'kimi', 'qwen', 'deepseek'):
        if model_family in provider:
            return model_family
    for family in PROVIDER_FAMILIES:
        if family in provider:
            return family
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


def build_theme_file(config_dir):
    """Build the custom THEME FILE content for ~/.claude/themes/<slug>.json.

    Shape required by the CLI: {"name": ..., "base": "dark"|"light", "overrides": {role: hex}}.
    The role names below are the CLI's own colour vocabulary (extracted from the 2.1.278
    bundle): claude, permission, planMode, bashBorder, autoAccept, diffAdded/diffRemoved
    (+Dimmed), success, error, warning, suggestion, text, inverseText, thinking, remember.

    Only the IDENTITY roles are overridden, deliberately. Repainting success/error/warning
    would make a remote session harder to read, not more distinctive -- and recolouring a
    semantic signal like `error` is actively harmful. The accent is read from
    remote-theme.json so that file stays the single source of truth for the colour.
    """
    theme_data = load_json_file(config_dir / 'remote-theme.json')
    theme = theme_data.get('theme', {})
    fallback = theme_data.get('fallback', {})
    accent = theme.get('accent_color') or fallback.get('accent_color') or '#7CFC00'
    return {
        "name": theme.get('identity_label') or 'Free API session',
        "base": "dark",
        "overrides": {
            "claude": accent,
            "permission": accent,
            "planMode": accent,
            "bashBorder": accent,
            "suggestion": accent,
            "thinking": accent,
        },
    }


def main():
    parser = argparse.ArgumentParser(description='Generate per-session settings for remote sessions')
    parser.add_argument('--identity-json', help='Session identity JSON from la-session-identity.sh')
    parser.add_argument('--output', help='Output file (default: stdout)')
    parser.add_argument('--launch-dir', help='Launch directory (auto-detected)')
    parser.add_argument('--emit-theme-file', action='store_true',
                        help='Print the custom THEME FILE (for ~/.claude/themes/<slug>.json) '
                             'instead of a session settings overlay, and exit')
    args = parser.parse_args()

    # Determine launch directory (needed by both modes)
    if args.launch_dir:
        launch_dir = Path(args.launch_dir)
    else:
        launch_dir = Path(os.environ.get('LAUNCH_DIR', Path(__file__).parent))

    config_dir = launch_dir.parent / 'config'

    # --emit-theme-file is a standalone mode: it describes the THEME, which is a property of
    # the lane rather than of any one session, so it needs no identity.
    if args.emit_theme_file:
        out = json.dumps(build_theme_file(config_dir), indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(out)
        else:
            sys.stdout.write(out)
        return 0

    if not args.identity_json:
        print("ERROR: --identity-json is required unless --emit-theme-file is given", file=sys.stderr)
        return 2

    # Parse identity JSON
    try:
        identity = json.loads(args.identity_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid identity JSON: {e}", file=sys.stderr)
        return 1

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

        # Build the settings overlay from the theme DATA. Previously `theme` and
        # `fallback` were read into locals here and then never used, so the lime accent
        # in remote-theme.json reached nothing — the overlay carried spinner verbs only.
        theme = theme_data.get('theme', {})
        fallback = theme_data.get('fallback', {})

        accent = theme.get('accent_color') or fallback.get('accent_color')
        emoji = session_emoji or theme.get('session_emoji') or fallback.get('session_emoji')
        label = theme.get('identity_label') or fallback.get('identity_label')

        settings = {}

        # Spinner verbs stay STATIC per the plan — flavor only, never live metrics. An
        # empty list is omitted entirely: sending mode=replace with no verbs would strip
        # Claude Code's own vocabulary and leave the spinner blank.
        if spinner_verbs:
            settings["spinnerVerbs"] = {"mode": "replace", "verbs": spinner_verbs}

        # ★ THE LIME ACCENT IS NOW REALLY EMITTED (2026-09-21).
        #
        # 0.17.6 deliberately withheld this, having probed a `themes` SETTINGS MAP and found
        # it unconfirmable: a bogus key succeeded just as silently, so acceptance was not
        # evidence of support. That reasoning was correct about the KEY and wrong about the
        # MECHANISM. Custom themes are FILES, not a settings map: the CLI reads
        # ~/.claude/themes/<slug>.json and a session selects one with the ordinary `theme`
        # setting. Confirmed three ways against CLI 2.1.278 -- the loader path
        # userConfigDir("themes",[slug]) in the binary, a "[theme] watcher" for hot reload,
        # and `claude --help`, which lists "custom themes" among the customizations that
        # --safe-mode disables. So this is a documented feature, not a guess.
        #
        # Per-session via --settings, so the user's persistent theme is never touched.
        theme_slug = theme.get('name') or 'free-lime'
        settings["theme"] = theme_slug

        _ = (accent, emoji, label, theme_identifier, provider_display,
             actual_model_id, spinner_profile_id)  # retained for the banner/statusline path

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
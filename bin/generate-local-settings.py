#!/usr/bin/env python3
"""generate-local-settings.py — build per-session settings overlay for local sessions.

This script consumes the session identity (from la-session-identity.sh) and
local theme/spinner data to produce a JSON settings object that can be passed
to Claude Code via --settings. It does NOT write to the user's persistent
~/.claude/settings.json — the launcher passes it as a transient overlay.

Usage:
  generate-local-settings.py --identity-json <json> [--output <file>]
  generate-local-settings.py --emit-theme-file [--output <file>]
  generate-local-settings.py --emit-model-words --identity-json <json>

Environment:
  LAUNCH_DIR       — directory containing this script (auto-detected if unset)
  LA_MOD_ATTACHED  — "1" when the launcher attached the session-identity mod: spinnerVerbs
                     then APPENDS to Claude Code's own words (the mod sets the model share)
                     instead of replacing them
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


def theme_config_path(config_dir, name):
    """Where THEME colours live (user 2026-10-15: colours must survive plugin version
    installs and be shared by every copy — the version-keyed cache is wiped on update):
    1) LA_THEME_CONFIG_DIR (test escape hatch), 2) the stable user store
    ~/.claude/free-agents/themes/<name>, 3) this copy's shipped config/<name>.
    MUST MATCH ThemeConfigScreen._theme_path in session_picker_model.py — the picker
    writes the user store; the session theme must read the colours it wrote."""
    env = os.environ.get('LA_THEME_CONFIG_DIR')
    if env:
        return Path(env) / name
    user = Path.home() / '.claude' / 'free-agents' / 'themes' / name
    return user if user.exists() else config_dir / name


def build_theme_file(config_dir):
    """Custom THEME FILE for ~/.claude/themes/local-sky.json.

    Same shape and the same identity-only roles as generate-remote-settings.build_theme_file
    (see its docstring for why semantic colours stay untouched), plus clawd_body, which
    tints the welcome-screen mascot. The accent is read from local-theme.json — user
    store first (see theme_config_path).
    """
    theme_data = load_json_file(theme_config_path(config_dir, 'local-theme.json'))
    theme = theme_data.get('theme', {})
    accent = theme.get('accent_color') or '#00BFFF'
    border = theme.get('border_color') or accent      # softer prompt outline, same as remote
    overrides = {role: accent for role in (
        "claude", "permission", "planMode", "bashBorder", "suggestion", "thinking",
        "clawd_body")}
    overrides.update(promptBorder=border, promptBorderShimmer=border)
    return {
        "name": theme.get('identity_label') or 'Local inference session',
        "base": "dark",
        "overrides": overrides,
    }



def model_picker_label(label):
    """Show the RUNNING model's name in the welcome banner (next to Clawd) instead of the
    spoofed Claude id's name ("Opus 5.5"). Claude Code labels a model from a modelPicker row
    whose `model` matches the session's model id (verified on 2.1.296: the banner read the
    row's label). The id and label come from the launcher's environment: LA_SPOOF_MODEL_ID is
    the id claude is started with, LA_BANNER_MODEL_LABEL the name the statusline shows.
    behavesAs keeps the CLI treating the id as the Claude model it is spoofing. Empty when
    either is missing, so a hand run never gets a mislabelled picker."""
    model = os.environ.get('LA_SPOOF_MODEL_ID', '').strip()
    label = (label or '').strip()
    if not model or not label or label == 'unknown':
        return None
    return {"options": [{"model": model, "label": label, "behavesAs": model}]}


def main():
    parser = argparse.ArgumentParser(description='Generate per-session settings for local sessions')
    parser.add_argument('--identity-json', help='Session identity JSON from la-session-identity.sh')
    parser.add_argument('--output', help='Output file (default: stdout)')
    parser.add_argument('--launch-dir', help='Launch directory (auto-detected)')
    parser.add_argument('--emit-theme-file', action='store_true',
                        help='Print the custom THEME FILE (for ~/.claude/themes/local-sky.json) '
                             'instead of a session settings overlay, and exit')
    parser.add_argument('--emit-model-words', action='store_true',
                        help='Print the model-punning word pool for this identity as a JSON '
                             'array (read by the session-identity mod), and exit')
    args = parser.parse_args()

    if args.launch_dir:
        launch_dir = Path(args.launch_dir)
    else:
        launch_dir = Path(os.environ.get('LAUNCH_DIR', Path(__file__).parent))
    config_dir = launch_dir.parent / 'config'

    # --emit-theme-file describes the lane, not a session, so it needs no identity.
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
    theme_data = load_json_file(config_dir / 'local-theme.json')
    spinner_data = load_json_file(config_dir / 'local-spinner-verbs.json')

    # Extract identity fields
    theme_identifier = identity.get('theme_identifier', 'unknown')
    session_kind = identity.get('session_kind', 'unknown')
    session_emoji = identity.get('session_emoji', '❓')
    actual_model_id = identity.get('actual_model_id', 'unknown')
    actual_model_display = identity.get('actual_model_display', 'unknown')
    spinner_profile_id = identity.get('spinner_profile_id', 'unknown')

    if args.emit_model_words:
        words = []
        if session_kind == 'local':
            words = select_spinner_verbs(get_model_family(actual_model_id), spinner_data)
        print(json.dumps(words))
        return 0

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

        # With the session-identity mod attached, the engine must keep sampling Claude Code's
        # OWN words so the mod has a vanilla word to keep on the turns it leaves alone: append.
        # Without it (an older CLI), replace stays the only way to show model words at all.
        mode = "append" if os.environ.get('LA_MOD_ATTACHED') == '1' else "replace"
        settings = {}
        if spinner_verbs:
            settings["spinnerVerbs"] = {"mode": mode, "verbs": spinner_verbs}
        # Custom themes are selected as "custom:<slug>"; a bare slug never loads the file.
        settings["theme"] = "custom:" + (theme.get('name') or 'local-sky')
        picker = model_picker_label(os.environ.get('LA_BANNER_MODEL_LABEL') or actual_model_display)
        if picker:
            settings["modelPicker"] = picker

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
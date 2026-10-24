#!/usr/bin/env python3
"""generate-remote-settings.py — build per-session settings overlay for remote sessions.

This script consumes the session identity (from la-session-identity.sh) and
remote theme/spinner data to produce a JSON settings object that can be passed
to Claude Code via --settings. It does NOT write to the user's persistent
~/.claude/settings.json — the launcher passes it as a transient overlay.

Usage:
  generate-remote-settings.py --identity-json <json> [--output <file>]

  generate-remote-settings.py --emit-model-words --identity-json <json>

Environment:
  LAUNCH_DIR       — directory containing this script (auto-detected if unset)
  LA_MOD_ATTACHED  — "1" when the launcher attached the session-identity mod: spinnerVerbs
                     then APPENDS to Claude Code's own words instead of replacing them
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


import theme_colors


def theme_config_path(config_dir, name):
    """Where THEME colours live (user 2026-10-15: colours must survive plugin version
    installs and be shared by every copy — the version-keyed cache is wiped on update):
    1) LA_THEME_CONFIG_DIR (test escape hatch), 2) the stable user store
    ~/.claude/free-agents/themes/<name>, 3) this copy's shipped config/<name>.
    MUST MATCH ThemeConfigScreen._theme_path in session_picker_model.py — the picker
    writes the user store; the session theme must read the colours it wrote. Mirrors
    generate-local-settings.theme_config_path."""
    env = os.environ.get('LA_THEME_CONFIG_DIR')
    if env:
        return Path(env) / name
    user = Path.home() / '.claude' / 'free-agents' / 'themes' / name
    return user if user.exists() else config_dir / name


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
    theme_data = load_json_file(theme_config_path(config_dir, 'remote-theme.json'))
    theme = theme_data.get('theme', {})
    fallback = theme_data.get('fallback', {})
    accent = theme.get('accent_color') or fallback.get('accent_color') or '#ADFF2F'
    # The prompt outline uses a MUTED accent: full strength around the input was too distracting.
    border = theme.get('border_color') or accent
    return {
        "name": theme.get('identity_label') or 'Free API session',
        "base": "dark",
        "overrides": {
            "claude": accent,
            "permission": accent,
            "planMode": accent,
            "suggestion": accent,
            "thinking": accent,
            # Shell mode (`!`) gets its OWN derived colour, not the accent — stock Claude Code
            # paints it magenta, and the accent made shell mode look like normal input
            # (user 2026-10-23; the recipe is the theme colour shifted +20 deg, S/V 100%).
            "bashBorder": theme_colors.shell_mode_color(accent),
            # The prompt input's outline (user request 2026-10-14). The CLI draws the border
            # with promptBorder and animates it with promptBorderShimmer; overriding only one
            # would flash the default grey on every shimmer frame.
            "promptBorder": border,
            "promptBorderShimmer": border,
            # The welcome-screen mascot. Without it the theme file a main-line launch rewrites
            # dropped the green mascot that the identity branch had written (0.34.5).
            "clawd_body": accent,
        },
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
    parser = argparse.ArgumentParser(description='Generate per-session settings for remote sessions')
    parser.add_argument('--identity-json', help='Session identity JSON from la-session-identity.sh')
    parser.add_argument('--output', help='Output file (default: stdout)')
    parser.add_argument('--launch-dir', help='Launch directory (auto-detected)')
    parser.add_argument('--emit-theme-file', action='store_true',
                        help='Print the custom THEME FILE (for ~/.claude/themes/<slug>.json) '
                             'instead of a session settings overlay, and exit')
    parser.add_argument('--emit-model-words', action='store_true',
                        help='Print the model-punning word pool for this identity as a JSON '
                             'array (read by the session-identity mod), and exit')
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
    theme_data = load_json_file(theme_config_path(config_dir, 'remote-theme.json'))
    spinner_data = load_json_file(config_dir / 'remote-spinner-verbs.json')

    # Extract identity fields
    theme_identifier = identity.get('theme_identifier', 'unknown')
    session_kind = identity.get('session_kind', 'unknown')
    session_emoji = identity.get('session_emoji', '❓')
    provider_display = identity.get('provider_display', 'unknown')
    actual_model_id = identity.get('actual_model_id', 'unknown')
    actual_model_display = identity.get('actual_model_display', 'unknown')
    spinner_profile_id = identity.get('spinner_profile_id', 'unknown')

    if args.emit_model_words:
        words = []
        if session_kind == 'free_api':
            words = select_spinner_verbs(get_provider_family(provider_display), spinner_data)
        print(json.dumps(words))
        return 0

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
            # append when the session-identity mod is attached: it needs Claude Code's own
            # words to keep on the turns it leaves alone (see generate-local-settings.py).
            mode = "append" if os.environ.get('LA_MOD_ATTACHED') == '1' else "replace"
            settings["spinnerVerbs"] = {"mode": mode, "verbs": spinner_verbs}

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
        #
        # ★ The value needs the `custom:` PREFIX (found 2026-10-13, 2.1.296): startup only calls
        # loadCustomThemes when the theme setting startsWith("custom:"). A bare slug is an
        # unknown built-in name and falls back silently, which is why no remote session ever
        # showed the lime accent between 0.17.7 and 0.31.0 although the file was installed.
        theme_slug = theme.get('name') or 'free-lime'
        settings["theme"] = "custom:" + theme_slug
        picker = model_picker_label(os.environ.get('LA_BANNER_MODEL_LABEL') or actual_model_display)
        if picker:
            settings["modelPicker"] = picker

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
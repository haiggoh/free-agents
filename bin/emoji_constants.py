#!/usr/bin/env python3
"""emoji_constants.py — load emoji constants from config/emoji.sh

This module parses the bash emoji.sh file and exposes the emoji constants
as Python variables. This ensures a single source of truth for all emojis
used across the session picker.
"""

import re
from pathlib import Path

BIN = Path(__file__).resolve().parent
EMOJI_SH = BIN.parent / "config" / "emoji.sh"

_EMOJI_CACHE: dict[str, str] | None = None


def _load_emojis() -> dict[str, str]:
    """Parse emoji.sh and return emoji constants as a dictionary."""
    global _EMOJI_CACHE
    if _EMOJI_CACHE is not None:
        return _EMOJI_CACHE

    if not EMOJI_SH.exists():
        # Fallback defaults if emoji.sh not found
        _EMOJI_CACHE = {
            "SESSION_EMOJI_LOCAL": "🦾",
            "SESSION_EMOJI_FREE_API": "📡",
            "SESSION_EMOJI_CLOUD": "☁️",
            "SESSION_EMOJI_UNKNOWN": "❓",
            "EMOJI_TELEMETRY_ON": "🛰️",
            "EMOJI_TELEMETRY_OFF": "🔇",
            "EMOJI_WATCHER": "🔭",
            "EMOJI_AUTO_MODE": "🤖",
            "EMOJI_STOP_HOOK": "🪝",
            "EMOJI_HOME": "🏠",
            "EMOJI_EFFORT": "⚙️",
            "EMOJI_KEY": "🔑",
            "EMOJI_TOOLS": "🔧",
            "EMOJI_MCP": "🔌",
        }
        return _EMOJI_CACHE

    content = EMOJI_SH.read_text()
    emojis: dict[str, str] = {}

    # Parse lines like: SESSION_EMOJI_LOCAL="🦾"
    pattern = re.compile(r'^(\w+)\s*=\s*"([^"]*)"')
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        match = pattern.match(line)
        if match:
            key, value = match.groups()
            emojis[key] = value

    _EMOJI_CACHE = emojis
    return _EMOJI_CACHE


def get_emoji(key: str, default: str = "") -> str:
    """Get an emoji by key, with optional default."""
    return _load_emojis().get(key, default)


def _get(key: str, default: str = "") -> str:
    return get_emoji(key, default)


# Session kind emojis (lazy-loaded functions)
def session_emoji_local() -> str:
    return _get("SESSION_EMOJI_LOCAL", "🦾")


def session_emoji_free_api() -> str:
    return _get("SESSION_EMOJI_FREE_API", "📡")


def session_emoji_cloud() -> str:
    return _get("SESSION_EMOJI_CLOUD", "☁️")


def session_emoji_unknown() -> str:
    return _get("SESSION_EMOJI_UNKNOWN", "❓")


# Feature emojis
def emoji_telemetry_on() -> str:
    return _get("EMOJI_TELEMETRY_ON", "🛰️")


def emoji_telemetry_off() -> str:
    return _get("EMOJI_TELEMETRY_OFF", "🔇")


def emoji_watcher() -> str:
    return _get("EMOJI_WATCHER", "🔭")


def emoji_auto_mode() -> str:
    return _get("EMOJI_AUTO_MODE", "🤖")


def emoji_stop_hook() -> str:
    return _get("EMOJI_STOP_HOOK", "🪝")


def emoji_home() -> str:
    return _get("EMOJI_HOME", "🏠")


def emoji_effort() -> str:
    return _get("EMOJI_EFFORT", "⚙️")


def emoji_key() -> str:
    return _get("EMOJI_KEY", "🔑")


def emoji_tools() -> str:
    return _get("EMOJI_TOOLS", "🔧")


def emoji_mcp() -> str:
    return _get("EMOJI_MCP", "🔌")


# Lowkey emojis
def lk_emoji_model() -> str:
    return _get("LK_EMOJI_MODEL", "🦾")


def lk_emoji_effort() -> str:
    return _get("LK_EMOJI_EFFORT", "⚙️")


def lk_emoji_session() -> str:
    return _get("LK_EMOJI_SESSION", "💾")


def lk_emoji_convo() -> str:
    return _get("LK_EMOJI_CONVO", "💬")


def lk_emoji_oneshot() -> str:
    return _get("LK_EMOJI_ONESHOT", "🎯")


def lk_emoji_more() -> str:
    return _get("LK_EMOJI_MORE", "🔧")


def lk_emoji_toggle_on() -> str:
    return _get("LK_EMOJI_TOGGLE_ON", "✅")


def lk_emoji_toggle_off() -> str:
    return _get("LK_EMOJI_TOGGLE_OFF", "🚫")


def lk_emoji_back() -> str:
    return _get("LK_EMOJI_BACK", "🏠")


# String constants (evaluated once at module load)
_EMOJIS = _load_emojis()
SESSION_EMOJI_LOCAL_STR = _EMOJIS.get("SESSION_EMOJI_LOCAL", "🦾")
SESSION_EMOJI_FREE_API_STR = _EMOJIS.get("SESSION_EMOJI_FREE_API", "📡")
SESSION_EMOJI_CLOUD_STR = _EMOJIS.get("SESSION_EMOJI_CLOUD", "☁️")
SESSION_EMOJI_UNKNOWN_STR = _EMOJIS.get("SESSION_EMOJI_UNKNOWN", "❓")
EMOJI_TELEMETRY_ON_STR = _EMOJIS.get("EMOJI_TELEMETRY_ON", "🛰️")
EMOJI_TELEMETRY_OFF_STR = _EMOJIS.get("EMOJI_TELEMETRY_OFF", "🔇")
EMOJI_WATCHER_STR = _EMOJIS.get("EMOJI_WATCHER", "🔭")
EMOJI_AUTO_MODE_STR = _EMOJIS.get("EMOJI_AUTO_MODE", "🤖")
EMOJI_STOP_HOOK_STR = _EMOJIS.get("EMOJI_STOP_HOOK", "🪝")
EMOJI_HOME_STR = _EMOJIS.get("EMOJI_HOME", "🏠")
EMOJI_EFFORT_STR = _EMOJIS.get("EMOJI_EFFORT", "⚙️")
EMOJI_KEY_STR = _EMOJIS.get("EMOJI_KEY", "🔑")
EMOJI_TOOLS_STR = _EMOJIS.get("EMOJI_TOOLS", "🔧")
EMOJI_MCP_STR = _EMOJIS.get("EMOJI_MCP", "🔌")
EMOJI_NVIDIA_RATE_LIMITER_STR = _EMOJIS.get("EMOJI_NVIDIA_RATE_LIMITER", "🚦")

# Lowkey string versions
LK_EMOJI_MODEL_STR = _EMOJIS.get("LK_EMOJI_MODEL", "🦾")
LK_EMOJI_EFFORT_STR = _EMOJIS.get("LK_EMOJI_EFFORT", "⚙️")
LK_EMOJI_SESSION_STR = _EMOJIS.get("LK_EMOJI_SESSION", "💾")
LK_EMOJI_CONVO_STR = _EMOJIS.get("LK_EMOJI_CONVO", "💬")
LK_EMOJI_ONESHOT_STR = _EMOJIS.get("LK_EMOJI_ONESHOT", "🎯")
LK_EMOJI_MORE_STR = _EMOJIS.get("LK_EMOJI_MORE", "🔧")
LK_EMOJI_TOGGLE_ON_STR = _EMOJIS.get("LK_EMOJI_TOGGLE_ON", "✅")
LK_EMOJI_TOGGLE_OFF_STR = _EMOJIS.get("LK_EMOJI_TOGGLE_OFF", "🚫")
LK_EMOJI_BACK_STR = _EMOJIS.get("LK_EMOJI_BACK", "🏠")


if __name__ == "__main__":
    # Print all loaded emojis for debugging
    for k, v in sorted(_load_emojis().items()):
        print(f"{k}={v}")
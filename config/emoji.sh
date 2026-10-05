#!/usr/bin/env bash
# emoji.sh — single source of truth for session kind emojis.
#
# Sourced by: csl (picker), la-session-identity.sh (resolver), and any script
# that needs to display a session kind icon without running the full resolver.
#
# The resolver (la-session-identity.sh) exports LA_SESSION_KIND_EMOJI at runtime
# based on the detected endpoint. This file provides the CONSTANT definitions
# so the picker can show the same icons before launch.

# Session kind emojis — one per session_kind value
# These are the canonical mappings used by la-session-identity.sh
SESSION_EMOJI_LOCAL="🦾"
SESSION_EMOJI_FREE_API="📡"     # was 🌐 (globe) — changed to satellite dish
SESSION_EMOJI_CLOUD="☁️"        # cloud/gateway sessions
SESSION_EMOJI_REMOTE_API="📡"   # alias for free_api
SESSION_EMOJI_UNKNOWN="❓"      # was 🧭 (compass) — reserved for waypoints plugin

# Telemetry / feature emojis (not session kinds, but shared across scripts)
EMOJI_TELEMETRY_ON="🛰️ "
EMOJI_TELEMETRY_OFF="🔇"
EMOJI_WATCHER="🔭"
EMOJI_AUTO_MODE="🤖"
EMOJI_STOP_HOOK="🪝"
EMOJI_HOME="🏠"
EMOJI_EFFORT="⚙️ "      # was 🔆 (sun) — changed to gear
EMOJI_KEY="🔑"
EMOJI_TOOLS="🔧"
EMOJI_MODEL_CHOICE="⚙️ " # was 🔆 (sun) — changed to gear
EMOJI_MCP="🔌"
EMOJI_BROKEN_MODELS="🚧"  # unworking models filter (remote menu `u`)
EMOJI_TEMPERATURE="🌡️"     # temperature preset (picker `o`, bash remote menu `O`)
EMOJI_GO_LAUNCH="🚀"       # picker `g`: launch the last-launched model
EMOJI_TRIALS="🔖"          # picker remote `h`: limited-trial providers shown/hidden
EMOJI_LOCAL_CAPABLE="🏷️"   # picker remote `f`: locally-runnable models shown/hidden
EMOJI_HIDDEN_REPORT="📋"   # hidden-model report (picker `x`, bash `R`)
EMOJI_FILTER="🔍"           # filter submenu (picker remote `y`)
EMOJI_DOWNLOAD="📥"         # download models (picker `d`, distinct from backend manager)

# Status / progress emojis (picker)
EMOJI_OK="✅"
EMOJI_MISSING="❌"
EMOJI_WARNING="⚠️"
EMOJI_LOADING="⏳"          # loading animation frame 1 (sand flowing)
EMOJI_LOADING_DONE="⌛️"    # loading animation frame 2

# API-key provider emojis (picker API keys screen; slug-keyed, upper-cased)
EMOJI_PROVIDER_GEMINI="🔍"
EMOJI_PROVIDER_GROQ="⚡"
EMOJI_PROVIDER_OPENROUTER="🔀"
EMOJI_PROVIDER_CLOUDFLARE="☁️"
EMOJI_PROVIDER_MISTRAL="🌊"
EMOJI_PROVIDER_ZAI="🤖"
EMOJI_PROVIDER_SILICONFLOW="⚙️"
EMOJI_PROVIDER_LLM7="7️⃣"
EMOJI_PROVIDER_KILO="🔑"
EMOJI_PROVIDER_VERCEL="▲"
EMOJI_PROVIDER_SAMBANOVA="💎"
EMOJI_PROVIDER_MODELSCOPE="🔬"
EMOJI_PROVIDER_CEREBRAS="🧠"
EMOJI_PROVIDER_NVIDIA="🚦"

# Backend manager actions (picker `v` screen)
EMOJI_BACKEND="🧩"          # backend selector (Rapid-MLX / vllm-mlx / oMLX / llama.cpp / LiteLLM)
EMOJI_RELEASES="📜"         # list installable releases
EMOJI_INSTALL="📦"          # install a release
EMOJI_VALIDATE="🩺"         # validate an installed version
EMOJI_INFO="ℹ️"             # show info for an installed version
EMOJI_BACK="↩️"             # generic Back (to the screen that opened this one); Home uses EMOJI_HOME

# NVIDIA rate limiter emoji
EMOJI_NVIDIA_RATE_LIMITER="🚦"

# Launchd management emojis (backend update checks; 0.21.0's csl diff, landed in the picker in 0.22.0)
EMOJI_LAUNCHD_INSTALL="📥"
EMOJI_LAUNCHD_UNINSTALL="📤"
EMOJI_LAUNCHD_STATUS="📊"
EMOJI_LAUNCHD_RUN_ONCE="▶️"
EMOJI_LAUNCHD="🔧"  # same as EMOJI_TOOLS, but explicit for clarity

# Lowkey picker emojis (shared with csl for consistency) — LK_ prefix for lowkey namespace
LK_EMOJI_MODEL="${SESSION_EMOJI_LOCAL}"        # links to SESSION_EMOJI_LOCAL (single source of truth)
LK_EMOJI_EFFORT="⚙️ "
LK_EMOJI_SESSION="💾"
LK_EMOJI_CONVO="💬"
LK_EMOJI_ONESHOT="🎯"                          # target/direct for single shot (was ⚡)
LK_EMOJI_MORE="🔧"
LK_EMOJI_TOGGLE_ON="✅"
LK_EMOJI_TOGGLE_OFF="🚫"
LK_EMOJI_BACK="🏠"

# Backwards-compat: scripts that used to hardcode these can now source this file
# and use the variables. Old hardcoded values:
#   🌐  -> SESSION_EMOJI_FREE_API (now 📡)
#   📡  -> EMOJI_TELEMETRY_ON (now 🛰️)
#   🦾  -> SESSION_EMOJI_LOCAL
#   🧭  -> SESSION_EMOJI_UNKNOWN

# Helper: get emoji for a session_kind
# Usage: session_emoji_for <session_kind>
session_emoji_for() {
  local kind="${1:-unknown}"
  case "$kind" in
    local)      printf '%s' "$SESSION_EMOJI_LOCAL" ;;
    free_api)   printf '%s' "$SESSION_EMOJI_FREE_API" ;;
    cloud)      printf '%s' "$SESSION_EMOJI_CLOUD" ;;
    *)          printf '%s' "$SESSION_EMOJI_UNKNOWN" ;;
  esac
}
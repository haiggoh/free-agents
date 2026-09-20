#!/usr/bin/env bash
# la-session-identity.sh — resolve and emit a deterministic session identity object.
#
# This is the single source of truth for session identity (local vs cloud vs free API,
# actual model, backend, provider, etc.). All launchers, hooks, statusline, and transcript
# markers MUST consume this resolver rather than deriving identity independently.
#
# Contract:
#   - Invoked with optional arguments for dry-run/inspection
#   - Prints ONE JSON object on stdout; exits 0 on success
#   - Exits 1 on unresolvable identity (with error on stderr)
#   - Deterministic: same inputs always produce same output
#
# Environment inputs (set by launchers before invocation):
#   ANTHROPIC_BASE_URL   — the endpoint the session will use (authoritative)
#   MODEL_ALIAS          — the registered alias (e.g. qwen38-27b-4bit)
#   MODEL_SPOOF          — the compatibility model ID (e.g. claude-opus-5)
#   BACKEND              — resolved backend (rapid|vllm|mlx_lm|llama_cpp)
#   BACKEND_DECLARED     — declared backend from config (may be generic "mlx")
#   LA_CUR_DIR           — model directory on disk
#   LA_CUR_THINK         — thinking mode (true|false)
#   LA_CUR_EFFORT        — effort level
#   LA_CUR_SERVE         — same as BACKEND
#   LA_CUR_SPOOF         — same as MODEL_SPOOF
#   LA_CUR_TOOLP         — tool call parser
#   LA_CUR_REASONP       — reasoning parser
#   LA_CUR_ROLES         — comma-separated roles
#   LA_CUR_REPO          — HF repo ID
#   LA_CUR_SIZE          — size in GB
#   LA_CUR_RAPID_SPEC_CONFIG — Rapid speculative config JSON
#   LA_SESSION_LAUNCHER  — which launcher initiated the session
#   LA_AUTO_MODE         — auto mode state (0|1)
#   LA_BLIND_AUTO        — blind trust auto mode (0|1)
#   LA_SESSION_ID        — stable session ID (optional, for transition tracking)
#   LA_PREV_SESSION_KIND — previous session kind for transition detection (optional)
#
# Output fields (schema version 1):
#   schema_version         — integer, increment on breaking changes
#   session_kind         — "local" | "free_api" | "cloud" | "unknown"
#   session_emoji        — display emoji for the session kind
#   compatibility_model_id   — the Claude-shaped ID the session presents as
#   actual_model_id        — the registered alias (canonical name)
#   actual_model_display   — human-readable model name
#   provider_display       — "Local (Rapid-MLX)" | "Local (vllm-mlx)" | "NVIDIA Nemotron" | "Anthropic" | etc.
#   backend_display        — resolved backend name
#   role_profile           — comma-separated roles or "untagged"
#   effort                 — effort level
#   thinking_mode          — "on" | "off" | "unknown"
#   context_policy         — autocompaction setting or "default"
#   theme_identifier       — "local-sky" | "free-lime" | "cloud-default" | "unknown"
#   spinner_profile_id     — identifier for spinner vocabulary
#   transcript_marker_version — version of the transcript marker format
#   evidence               — how session_kind was determined
#   unknown_fallback       — true if identity had to fall back to unknown
#   session_id             — stable unique session identifier
#   transition             — null or object with from_kind, to_kind, transition_type, timestamp
#   launcher               — which launcher initiated the session

set -uo pipefail

# --- Argument parsing (BEFORE any work) -----------------------------------
case "${1:-}" in
  -h|--help)
    sed -n '2,/^set -uo pipefail/{ /^set -uo pipefail/d; s/^# \{0,1\}//; p; }' "$0"
    exit 0 ;;
  --dry-run|--inspect) DRY_RUN=1 ;;
  "") DRY_RUN=0 ;;
  *)
    printf 'la-session-identity.sh: unrecognised argument: %s\n' "$1" >&2
    printf "Try 'la-session-identity.sh --help'.\n" >&2
    exit 2 ;;
esac

# --- Source config-lib for role/alias resolution --------------------------
_s="${BASH_SOURCE[0]}"; while [ -h "$_s" ]; do _d="$(cd -P "$(dirname "$_s")" && pwd)"; _s="$(readlink "$_s")"; case "$_s" in /*) ;; *) _s="$_d/$_s";; esac; done
IDENTITY_DIR="$(cd -P "$(dirname "$_s")" && pwd)"
# shellcheck source=/dev/null
. "$IDENTITY_DIR/../config/config-lib.sh"
la_load_config || exit 1

# --- Resolve model alias if provided ---------------------------------------
if [ -n "${MODEL_ALIAS:-}" ]; then
  _resolved="$(la_resolve_target "$MODEL_ALIAS" 2>/dev/null || true)"
  if [ -n "$_resolved" ] && [ "$_resolved" != "$MODEL_ALIAS" ]; then
    MODEL_ALIAS="$_resolved"
  fi
  if la_lookup "$MODEL_ALIAS"; then
    # Override with registry values
    MODEL_SPOOF="${LA_CUR_SPOOF%%,*}"
    BACKEND="$LA_CUR_SERVE"
    BACKEND_DECLARED="$LA_SERVE_DECLARED[$MODEL_ALIAS]"
    LA_CUR_THINK="${LA_CUR_THINK:-false}"
    LA_CUR_EFFORT="${LA_CUR_EFFORT:-medium}"
    LA_CUR_ROLES="${LA_CUR_ROLES:-}"
    LA_CUR_REPO="${LA_CUR_REPO:-}"
    LA_CUR_SIZE="${LA_CUR_SIZE:-}"
    LA_CUR_RAPID_SPEC_CONFIG="${LA_CUR_RAPID_SPEC_CONFIG:-}"
  fi
fi

# --- Determine session kind from ENDPOINT (authoritative) ------------------
# NEVER use CLAUDE_IS_LOCAL — it leaks into later cloud sessions.
# ANTHROPIC_BASE_URL is per-process and honest.
session_kind="unknown"
session_emoji="❓"
evidence="endpoint=unset"
provider_display="Unknown"
theme_identifier="unknown"
spinner_profile_id="unknown"

case "${ANTHROPIC_BASE_URL:-}" in
  http://localhost:800[0-9]|http://localhost:8010|http://127.0.0.1:800[0-9]|http://127.0.0.1:8010)
    session_kind="local"
    session_emoji="🦾"
    evidence="endpoint=localhost:8000-8010 (local MLX)"
    theme_identifier="local-sky"
    # Determine provider/backing from backend
    case "${BACKEND:-}" in
      rapid) provider_display="Local (Rapid-MLX)"; spinner_profile_id="rapid-local" ;;
      vllm)  provider_display="Local (vllm-mlx)"; spinner_profile_id="vllm-local" ;;
      mlx_lm) provider_display="Local (mlx_lm.server)"; spinner_profile_id="mlx_lm-local" ;;
      llama_cpp) provider_display="Local (llama.cpp)"; spinner_profile_id="llama_cpp-local" ;;
      *) provider_display="Local (unknown backend)"; spinner_profile_id="local-unknown" ;;
    esac
    ;;
  http://localhost:4141|http://127.0.0.1:4141)
    session_kind="free_api"
    session_emoji="🌐"
    evidence="endpoint=localhost:4141 (free API proxy)"
    provider_display="Free API (NVIDIA Nemotron)"
    theme_identifier="free-lime"
    spinner_profile_id="free-api"
    ;;
  https://api.anthropic.com*|*anthropic.com*|*llmgw*)
    session_kind="cloud"
    session_emoji="☁️"
    evidence="endpoint=Anthropic/gateway"
    provider_display="Anthropic (cloud)"
    theme_identifier="cloud-default"
    spinner_profile_id="cloud"
    ;;
  *)
    session_kind="unknown"
    session_emoji="❓"
    evidence="endpoint=unrecognized (${ANTHROPIC_BASE_URL:-unset})"
    provider_display="Unknown"
    theme_identifier="unknown"
    spinner_profile_id="unknown"
    ;;
esac

# --- Build actual model display name ---------------------------------------
actual_model_display="${MODEL_ALIAS:-unknown}"
if [ -n "${LA_CUR_REPO:-}" ] && [ "${LA_CUR_REPO}" != "" ]; then
  actual_model_display="${actual_model_display} (${LA_CUR_REPO})"
fi

# --- Determine role profile ------------------------------------------------
role_profile="${LA_CUR_ROLES:-untagged}"

# --- Determine thinking mode -----------------------------------------------
thinking_mode="unknown"
case "${LA_CUR_THINK:-}" in
  true) thinking_mode="on" ;;
  false) thinking_mode="off" ;;
esac

# --- Determine context policy ----------------------------------------------
context_policy="default"
if [ -n "${LA_AUTO_COMPACT_WINDOW:-}" ]; then
  context_policy="${LA_AUTO_COMPACT_WINDOW}"
fi

# --- Determine transcript marker version -----------------------------------
transcript_marker_version=1

# --- Determine unknown fallback --------------------------------------------
unknown_fallback=false
if [ "$session_kind" = "unknown" ]; then
  unknown_fallback=true
fi
if [ -z "${MODEL_ALIAS:-}" ]; then
  unknown_fallback=true
fi

# --- Generate stable session ID ----------------------------------------------
# Use a deterministic ID based on launch time, PID, and model alias.
# If LA_SESSION_ID is provided (e.g., on resume), use it instead.
if [ -n "${LA_SESSION_ID:-}" ]; then
  session_id="${LA_SESSION_ID}"
else
  # Generate: YYYYMMDD-HHMMSS-PID-alias-hash
  _ts=$(date -u +"%Y%m%d-%H%M%S")
  _pid=$$
  _alias_hash=$(printf '%s' "${MODEL_ALIAS:-unknown}" | cksum | cut -d' ' -f1 | cut -c1-6)
  session_id="${_ts}-${_pid}-${_alias_hash}"
fi

# Escape function for JSON strings (defined early for use in transition detection)
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

# --- Detect transition -------------------------------------------------------
# If LA_PREV_SESSION_KIND is set and differs from current session_kind,
# emit a transition object. This handles resume from cloud->local, local->free_api, etc.
transition_json="null"
if [ -n "${LA_PREV_SESSION_KIND:-}" ] && [ "${LA_PREV_SESSION_KIND}" != "${session_kind}" ]; then
  _transition_type="resume"
  _from="${LA_PREV_SESSION_KIND}"
  _to="${session_kind}"
  case "${_from}:${_to}" in
    cloud:local) _transition_type="cloud-to-local" ;;
    local:cloud) _transition_type="local-to-cloud" ;;
    cloud:free_api) _transition_type="cloud-to-free-api" ;;
    free_api:cloud) _transition_type="free-api-to-cloud" ;;
    local:free_api) _transition_type="local-to-free-api" ;;
    free_api:local) _transition_type="free-api-to-local" ;;
    unknown:*) _transition_type="unknown-to-${_to}" ;;
    *:unknown) _transition_type="${_from}-to-unknown" ;;
  esac
  _transition_ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  transition_json=$(printf '{"from_kind":"%s","to_kind":"%s","transition_type":"%s","timestamp":"%s"}' \
    "$(json_escape "${_from}")" \
    "$(json_escape "${_to}")" \
    "$(json_escape "${_transition_type}")" \
    "$(json_escape "${_transition_ts}")")
fi

# --- Emit JSON -------------------------------------------------------------

schema_version=1

MODEL_ALIAS_ESC=$(json_escape "${MODEL_ALIAS:-unknown}")
MODEL_SPOOF_ESC=$(json_escape "${MODEL_SPOOF:-unknown}")
ACTUAL_MODEL_DISPLAY_ESC=$(json_escape "$actual_model_display")
PROVIDER_DISPLAY_ESC=$(json_escape "$provider_display")
BACKEND_DISPLAY_ESC=$(json_escape "${BACKEND:-unknown}")
ROLE_PROFILE_ESC=$(json_escape "$role_profile")
EFFORT_ESC=$(json_escape "${LA_CUR_EFFORT:-medium}")
THINKING_MODE_ESC=$(json_escape "$thinking_mode")
CONTEXT_POLICY_ESC=$(json_escape "$context_policy")
THEME_IDENTIFIER_ESC=$(json_escape "$theme_identifier")
SPINNER_PROFILE_ESC=$(json_escape "$spinner_profile_id")
SESSION_KIND_ESC=$(json_escape "$session_kind")
SESSION_EMOJI_ESC=$(json_escape "$session_emoji")
EVIDENCE_ESC=$(json_escape "$evidence")
LAUNCHER_ESC=$(json_escape "${LA_SESSION_LAUNCHER:-unknown}")
SESSION_ID_ESC=$(json_escape "$session_id")
# transition_json is already a valid JSON string (null or object), don't escape it

printf '{"schema_version":%d,"session_kind":"%s","session_emoji":"%s","compatibility_model_id":"%s","actual_model_id":"%s","actual_model_display":"%s","provider_display":"%s","backend_display":"%s","role_profile":"%s","effort":"%s","thinking_mode":"%s","context_policy":"%s","theme_identifier":"%s","spinner_profile_id":"%s","transcript_marker_version":%d,"evidence":"%s","unknown_fallback":%s,"launcher":"%s","session_id":"%s","transition":%s}\n' \
  "$schema_version" \
  "$SESSION_KIND_ESC" \
  "$SESSION_EMOJI_ESC" \
  "$MODEL_SPOOF_ESC" \
  "$MODEL_ALIAS_ESC" \
  "$ACTUAL_MODEL_DISPLAY_ESC" \
  "$PROVIDER_DISPLAY_ESC" \
  "$BACKEND_DISPLAY_ESC" \
  "$ROLE_PROFILE_ESC" \
  "$EFFORT_ESC" \
  "$THINKING_MODE_ESC" \
  "$CONTEXT_POLICY_ESC" \
  "$THEME_IDENTIFIER_ESC" \
  "$SPINNER_PROFILE_ESC" \
  "$transcript_marker_version" \
  "$EVIDENCE_ESC" \
  "$unknown_fallback" \
  "$LAUNCHER_ESC" \
  "$SESSION_ID_ESC" \
  "$transition_json"

exit 0
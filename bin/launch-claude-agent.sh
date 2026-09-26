#!/usr/bin/env bash
# launch-claude-agent.sh — start an interactive Claude Code session driven by a LOCAL model.
#
# Config-driven (config/config.local.sh overlay). Routes Claude Code DIRECTLY to vllm-mlx's
# Anthropic-compatible endpoint — no proxy, no relay — so native transcripts + history.jsonl
# persist normally. Works whether you invoke plain `claude` (vanilla) or wrap it.
#
# Usage:  launch-claude-agent.sh <alias> [effort-override]
#   <alias>          a model registered in your config (see config.example.sh)
#   [effort-override] optional: low|medium|high|xhigh|max (overrides the alias's default effort)
#
# Flags:
#   --help       Show this help and exit
#   --dry-run    Validate configuration and show what would launch, without starting a session
set -uo pipefail

# Argument parsing BEFORE any work (must support --help without side effects)
case "${1:-}" in
  -h|--help)
    cat <<'HELP'
launch-claude-agent.sh — start a LOCAL Claude Code session (MLX inference on this machine)

Usage: launch-claude-agent.sh <alias> [effort-override]
       launch-claude-agent.sh --help
       launch-claude-agent.sh --dry-run <alias> [effort-override]
       launch-claude-agent.sh --dry-run-skip-preflight <alias> [effort-override]
       launch-claude-agent.sh --enable-mcp <alias> [effort-override]

  <alias>           Model alias from config (e.g., qwen-3.8-operator, deepseek-r1-architect)
                    Role names also accepted: operator, reasoner, validator, utility
  [effort-override] Optional: low | medium | high | xhigh | max (overrides config default)

Flags:
  --help                  Show this help and exit
  --dry-run               Validate config, show resolved model/backend/effort/port, do NOT launch
  --dry-run-skip-preflight  Dry-run without RAM preflight (quick config inspection)
  --enable-mcp            Enable MCP tools in blind-trust auto mode (sets LA_ENABLE_MCP=1)

Environment (set by csl or caller):
  LA_AUTO_MODE=1                    Enable auto mode (permission-mode=auto)
  LA_BLIND_AUTO=1                   Blind-trust auto mode (no classifier)
  LA_TELEMETRY=0|1                  Disable/enable nonessential outbound traffic (default 0)
  LA_QUEUE_STOP_HOOK=0|1            Queued-prompt stop hook (default 1)
  LA_STRICT_MCP=true|false          Exclude MCP servers from prompt (default true)
  LA_SKIP_RAM_PREFLIGHT=1           Bypass RAM check (not recommended)
  LA_DRY_RUN_SKIP_PREFLIGHT=1       Skip RAM preflight in --dry-run (quick config inspection)
  LA_ENABLE_MCP=1                   Enable MCP tools in blind-trust auto mode (default 0)
                                    Ignored when LA_STRICT_MCP=true (MCP servers excluded from prompt)

Examples:
  launch-claude-agent.sh qwen-3.8-operator
  launch-claude-agent.sh operator high
  launch-claude-agent.sh --dry-run deepseek-r1-architect max
  launch-claude-agent.sh --dry-run-skip-preflight qwen-3.8-operator
  launch-claude-agent.sh --enable-mcp qwen-3.8-operator
HELP
    exit 0
    ;;
  --dry-run)
    DRY_RUN=1
    shift
    ;;
  --dry-run-skip-preflight)
    DRY_RUN=1
    export LA_DRY_RUN_SKIP_PREFLIGHT=1
    shift
    ;;
  --enable-mcp)
    export LA_ENABLE_MCP=1
    shift
    ;;
  -?*)
    printf 'launch-claude-agent.sh: unrecognised option: %s\n' "$1" >&2
    printf "Try 'launch-claude-agent.sh --help'.\n" >&2
    exit 2
    ;;
esac

# Resolve symlinks so invocation via a symlink (e.g. ~/.claude/scripts/local-inference/…) still
# finds this repo's config. Portable (no readlink -f, works on macOS bash 3.2).
_s="${BASH_SOURCE[0]}"; while [ -h "$_s" ]; do _d="$(cd -P "$(dirname "$_s")" && pwd)"; _s="$(readlink "$_s")"; case "$_s" in /*) ;; *) _s="$_d/$_s";; esac; done
LAUNCH_DIR="$(cd -P "$(dirname "$_s")" && pwd)"
# shellcheck source=/dev/null
. "$LAUNCH_DIR/../config/config-lib.sh"
# Load shared emoji constants (single source of truth)
# shellcheck source=../config/emoji.sh
. "$LAUNCH_DIR/../config/emoji.sh"
la_load_config || exit 1

MODEL_ALIAS="${1:-}"
EFFORT_OVERRIDE="${2:-}"

# No alias provided -> show usage with registered aliases, do NOT redirect to csl.
# csl is the interactive front-end; this script is the direct launcher.
# A missing alias is a usage error here — the caller (or user) should invoke csl for the menu.
if [ -z "$MODEL_ALIAS" ]; then
    echo "Usage: $0 <alias> [effort-override]" >&2
    echo "Registered aliases:" >&2
    la_aliases_help >&2
    echo >&2
    echo "Tip: run '$LAUNCH_DIR/csl' with no arguments to pick from a numbered menu instead." >&2
    exit 1
fi
# --auto: hand off to the local auto-mode launcher (Phase 8). Instead of accepting edits, this
# launches a session in `auto` permission mode whose SEPARATE safety-classifier is pointed at a
# warmed local backend (with a free slot), so a cloud 429 / budget-limit can't force the acceptEdits
# fallback.
#
# Auto mode is controlled by the env var LA_AUTO_MODE=1 (set by the CSL toggle). The --auto
# positional flag is retained for backward compatibility (launch-local-auto-mode.sh dispatch path)
# but is superseded by the env var — callers should set LA_AUTO_MODE=1 instead.
#
# When auto mode is on we also set LA_HOTSWAP_FORCE_FRESH=1 so the server is (re)started with
# --max-num-seqs=2, giving the classifier a slot even if a matching server was already running.
# The --max-num-seqs default is 2 (see config-lib.sh), so LA_RAPID_MAX_NUM_SEQS needs no override.
if [ "${1:-}" = "--auto" ]; then
    exec "$LAUNCH_DIR/launch-local-auto-mode.sh" "${@:2}"
fi
: "${LA_AUTO_MODE:=0}"
: "${LA_BLIND_AUTO:=0}"

# Give repeated classifier calls message-aligned transcript blocks so a backend
# with a trimmable prefix cache can reuse the stable history and prefill only
# the newly appended delta. This is local-launch scoped, defaults on only for
# Auto Mode, and can be disabled for one launch with:
#   LA_AUTO_MODE_SEGMENTED_TRANSCRIPT=0
#
# BLIND-TRUST MODE (LA_BLIND_AUTO=1, LA_AUTO_MODE=1): this is auto mode with NO
# classifier in the loop. The classifier env is still enabled (the backend may
# be used by something else), but the launcher must NEVER attempt to boot a
# classifier server or verify classifier readiness — that is the whole point of
# blind-trust. The guard below ensures LA_BLIND_AUTO=1 can never be silently
# ignored and fall back to acceptEdits.
la_configure_auto_mode_env "$LA_AUTO_MODE" || exit 2

# oMLX is the preferred local Auto Mode runtime when explicitly selected.
# It serves the session and classifier as different model IDs on one endpoint,
# and its paged prefix cache has demonstrated near-complete reuse of growing
# classifier transcripts. Rapid remains available with:
#   LA_AUTO_MODE_RUNTIME=rapid
: "${LA_AUTO_MODE_RUNTIME:=rapid}"
case "$LA_AUTO_MODE_RUNTIME" in
    rapid) ;;
    omlx)
        if [ "$LA_AUTO_MODE" = "1" ]; then
            exec "$LAUNCH_DIR/launch-claude-agent-omlx.sh" \
                "$MODEL_ALIAS" "$EFFORT_OVERRIDE"
        fi
        ;;
    *)
        echo "ERROR: LA_AUTO_MODE_RUNTIME must be rapid or omlx" >&2
        exit 2
        ;;
esac

if [ "$LA_AUTO_MODE" = "1" ]; then
    # Keep the configured second slot for compatibility, but do not mistake it
    # for the long-context fix: measured failing classifier calls were already
    # admitted with running=1 waiting=0. Their failure was prefill latency.
    : "${LA_HOTSWAP_FORCE_FRESH:=1}"
fi

# Nonessential outbound traffic. LA_TELEMETRY=0 (the DEFAULT for a local session) suppresses it;
# set LA_TELEMETRY=1 to keep stock Claude Code behaviour.
#
# Why default it off here rather than only in the picker: "local" is the promise this launcher makes,
# and a session that still reports to Statsig/Sentry and polls for updates is not local, however local
# the inference is. Measured 2026-09-05 — a session whose every inference request provably went to
# 127.0.0.1 still held outbound HTTPS sockets to Anthropic (160.79.104.10) and Google/Statsig
# (34.149.66.165). Neither is the gateway, so neither shows up in cost or routing checks.
#
# The umbrella variable is the one the installed build documents in its own refusal messages
# ("... has been disabled via the CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC environment variable").
# The three granular ones are set as well, deliberately: they are independently honoured, so a build
# that ever narrows the umbrella still gets the specific suppressions rather than silently resuming.
#
# KNOWN TRADE-OFF, not a bug: the umbrella also disables /design-sync and Projects, and the update
# check. All three are irrelevant to a local session (DesignSync is already in LA_DENY_TOOLS), and
# backend updates are handled by the weekly launchd check, not by the CLI's own poller.
: "${LA_TELEMETRY:=0}"
if [ "$LA_TELEMETRY" = "0" ]; then
    export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
    export DISABLE_TELEMETRY=1
    export DISABLE_ERROR_REPORTING=1
    export DISABLE_AUTOUPDATER=1
fi
# Accept a ROLE NAME (operator/reasoner/validator/utility) wherever an alias is accepted, so a
# caller never has to hardcode a model name that goes stale silently. An alias still wins, so this
# cannot change what any existing invocation does.
if [ -n "$MODEL_ALIAS" ]; then
    _resolved="$(la_resolve_target "$MODEL_ALIAS" 2>/dev/null || true)"
    if [ -n "$_resolved" ] && [ "$_resolved" != "$MODEL_ALIAS" ]; then
        echo "🎯 role '$MODEL_ALIAS' -> $_resolved (resolved from the on-disk role bindings)"
        MODEL_ALIAS="$_resolved"
    fi
fi
if [ -z "$MODEL_ALIAS" ] || ! la_lookup "$MODEL_ALIAS"; then
    la_retired_hint "$MODEL_ALIAS" || true
    echo "Usage: $0 <alias> [effort-override]"; echo "Registered aliases:"; la_aliases_help
    # Signpost the menu front-end for the bad-alias path (the no-arg path goes there automatically).
    echo; echo "Tip: run '$LAUNCH_DIR/csl' with no arguments to pick from a numbered menu instead."
    exit 1
fi
# spoof_id may be a comma-separated preference list (newest Claude model first). The SERVER
# answers to all of them; the CLIENT must be handed exactly one, so use the preferred (first).
MODEL_SPOOF="${LA_CUR_SPOOF%%,*}"
# If no effort override was passed and the alias's configured effort is the default (medium),
# prompt interactively so the user can pick — mirroring what remote-session.sh's e) picker does.
EFFORT="${EFFORT_OVERRIDE:-$LA_CUR_EFFORT}"
if [ -z "$EFFORT_OVERRIDE" ] && [ "$EFFORT" = "medium" ]; then
    _efforts=("low" "medium" "high" "xhigh" "max")
    _def=2
    _i=1
    echo "  Effort levels (higher = more thinking, slower):" >&2
    for _eff in "${_efforts[@]}"; do
        printf "    %d) %s\n" "$_i" "$_eff" >&2
        _i=$((_i + 1))
    done
    read -r -p "  Select effort [$_def]: " _sel >&2
    _sel="${_sel:-$_def}"
    if [[ "$_sel" =~ ^[0-9]+$ ]] && [ "$_sel" -ge 1 ] && [ "$_sel" -le ${#_efforts[@]} ]; then
        EFFORT="${_efforts[$((_sel - 1))]}"
        echo "  Effort set to: $EFFORT" >&2
    else
        echo "  Invalid selection, keeping: $EFFORT" >&2
    fi
fi

# Export MODEL_ALIAS so la-session-identity.sh can resolve the actual model name
export MODEL_ALIAS

EFFORT_FLAG="--effort $EFFORT"

# Optional, validated per-launch Claude Code controls. These deliberately avoid
# unrestricted argument forwarding so callers cannot override launcher-owned
# routing, compatibility identity, permission mode, or runtime safety.
: "${LA_AGENT_PROMPT_FILE:=$LAUNCH_DIR/../config/local-agent-system-prompt.txt}"
: "${LA_CLAUDE_SETTINGS:=}"
: "${LA_CLAUDE_TOOLS:=}"
: "${LA_AUTO_COMPACT_WINDOW:=}"

CLAUDE_EXTRA_ARGS=()

if [ ! -r "$LA_AGENT_PROMPT_FILE" ]; then
    echo "❌ Local agent prompt file is not readable: $LA_AGENT_PROMPT_FILE"
    exit 1
fi

if [ -n "$LA_CLAUDE_SETTINGS" ]; then
    if [ ! -f "$LA_CLAUDE_SETTINGS" ]; then
        echo "❌ LA_CLAUDE_SETTINGS is not a file: $LA_CLAUDE_SETTINGS"
        exit 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "❌ python3 is required to validate LA_CLAUDE_SETTINGS."
        exit 1
    fi
    if ! python3 -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' \
        "$LA_CLAUDE_SETTINGS" >/dev/null 2>&1
    then
        echo "❌ LA_CLAUDE_SETTINGS is not valid JSON: $LA_CLAUDE_SETTINGS"
        exit 1
    fi
    CLAUDE_EXTRA_ARGS+=(--settings "$LA_CLAUDE_SETTINGS")
fi

if [ -n "$LA_CLAUDE_TOOLS" ]; then
    case "$LA_CLAUDE_TOOLS" in
        *[!A-Za-z0-9_,]*|,*|*,|*,,*)
            echo "❌ LA_CLAUDE_TOOLS must be a comma-separated built-in tool list."
            exit 1
            ;;
    esac

    if [ -n "${LA_DENY_TOOLS:-}" ]; then
        _la_tool_overlap=$(
            python3 -c '
import sys
allowed = {item for item in sys.argv[1].split(",") if item}
denied = {item for item in sys.argv[2].split(",") if item}
print(",".join(sorted(allowed & denied)))
' "$LA_CLAUDE_TOOLS" "$LA_DENY_TOOLS"
        )
        if [ -n "$_la_tool_overlap" ]; then
            echo "❌ Tool(s) appear in both LA_CLAUDE_TOOLS and LA_DENY_TOOLS: $_la_tool_overlap"
            echo "   For an Agent-enabled research profile, remove Agent from that launch's LA_DENY_TOOLS."
            exit 1
        fi
    fi

    CLAUDE_EXTRA_ARGS+=(--tools "$LA_CLAUDE_TOOLS")
fi

if [ -n "$LA_AUTO_COMPACT_WINDOW" ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "❌ python3 is required to validate LA_AUTO_COMPACT_WINDOW."
        exit 1
    fi
    if ! python3 -c '
import re
import sys

value = sys.argv[1].strip().lower()
if value == "auto":
    raise SystemExit(0)

match = re.fullmatch(r"([0-9]+)([km]?)", value)
if not match:
    raise SystemExit(1)

amount = int(match.group(1))
suffix = match.group(2)
multiplier = {"": 1, "k": 1000, "m": 1000000}[suffix]
tokens = amount * multiplier
raise SystemExit(0 if 100000 <= tokens <= 1000000 else 1)
' "$LA_AUTO_COMPACT_WINDOW"
    then
        echo "❌ LA_AUTO_COMPACT_WINDOW must be auto or 100k–1m tokens."
        exit 1
    fi
    CLAUDE_EXTRA_ARGS+=(--autocompact "$LA_AUTO_COMPACT_WINDOW")
fi
# Preserve the registry values under backend-neutral local names. The old
# banner read an unset THINK variable and therefore displayed "off" even
# when LA_CUR_THINK=true and the server reasoning parser was enabled.
THINK="$LA_CUR_THINK"
# Resolved backend (rapid | vllm | mlx_lm), plus the declaration it came from. The BANNER shows the
# combined "rapid (mlx->rapid)" form because it is for humans; the session LOG keeps them as two
# separate single-token fields, because it is space-separated key=value and local-watch.sh greps it
# — a value containing a space would corrupt the field it sits in.
BACKEND="$LA_CUR_SERVE"
BACKEND_DECLARED="${LA_SERVE_DECLARED[$MODEL_ALIAS]:-}"
BACKEND_DISPLAY="$(la_serve_display "$MODEL_ALIAS")"

# DRY-RUN with RAM preflight skip option: LA_DRY_RUN_SKIP_PREFLIGHT=1 or --dry-run-skip-preflight
# Allows seeing config without the preflight warning when you just want to inspect settings.
if [ "${DRY_RUN:-0}" = "1" ] && [ "${LA_DRY_RUN_SKIP_PREFLIGHT:-0}" = "1" ]; then
    # Skip RAM preflight for quick config inspection
    :
else
    # RAM PREFLIGHT — before any weights load. Booting a model while other servers hold RAM has
    # frozen this machine hard (Terminal AND the force-quit menu became unresponsive), and there is no
    # graceful recovery from that state, so the check must precede the load, not follow a failure.
    # It short-circuits: if the model already fits, it never even looks at the other ports.
    # This MUST run even in --dry-run to warn about concurrent sessions that would block the launch.
    if [ -x "$LAUNCH_DIR/la-ram-preflight.sh" ]; then
        if ! "$LAUNCH_DIR/la-ram-preflight.sh" "$MODEL_ALIAS"; then
            echo
            echo "🛑 Not launching $MODEL_ALIAS — see the RAM preflight above."
            echo "   Override with LA_SKIP_RAM_PREFLIGHT=1 if you are certain the numbers are wrong."
            [ "${LA_SKIP_RAM_PREFLIGHT:-0}" = "1" ] || exit 1
            echo "   LA_SKIP_RAM_PREFLIGHT=1 set — continuing at your own risk."
        fi
    fi
fi

# DRY-RUN: show resolved configuration and exit without launching
if [ "${DRY_RUN:-0}" = "1" ]; then
    # Try to determine what port would be used (without starting server)
    # For dry-run we can't know the exact port, but we can show the range
    echo "=== DRY RUN — Configuration validated, would launch: ==="
    echo "  Model alias   : $MODEL_ALIAS"
    echo "  Spoof ID      : ${LA_CUR_SPOOF%%,*}"
    echo "  Backend       : $LA_CUR_SERVE (declared: ${LA_SERVE_DECLARED[$MODEL_ALIAS]:-$LA_CUR_SERVE})"
    echo "  Backend display: $(la_serve_display "$MODEL_ALIAS")"
    echo "  Effort        : $EFFORT"
    echo "  Thinking      : $([ "$LA_CUR_THINK" = "true" ] && echo "ON" || echo "OFF")"
    echo "  Auto mode     : $([ "$LA_AUTO_MODE" = "1" ] && echo "ON ($([ "$LA_BLIND_AUTO" = "1" ] && echo "blind-trust" || echo "classifier"))" || echo "OFF")"
    echo "  Telemetry     : $([ "$LA_TELEMETRY" = "0" ] && echo "OFF" || echo "ON")"
    echo "  Stop hook     : $([ "$LA_QUEUE_STOP_HOOK" = "1" ] && echo "ON" || echo "OFF")"
    echo "  Strict MCP    : $([ "${LA_STRICT_MCP:-true}" = "true" ] && echo "ON (excluded)" || echo "OFF (included)")"
    if [ "${LA_AUTO_MODE:-0}" = "1" ] && [ "${LA_BLIND_AUTO:-0}" = "1" ] && [ "${LA_ENABLE_MCP:-0}" = "1" ] && [ "${LA_BLIND_TRUST_OPTION:-B}" = "B" ]; then
        echo "  MCPs (blind)  : ENABLED (mcp__* allowlisted in generated settings)"
    else
        echo "  MCPs (blind)  : DISABLED (set LA_ENABLE_MCP=1 with blind-trust Option B to enable)"
    fi
    echo "  Port range    : $LA_PORT_START - $LA_PORT_MAX"
    echo "  Endpoint      : http://localhost:<port> (NO /v1 — Claude Code appends /v1/messages)"
    echo "  Auth token    : local (backend ignores)"
    echo "  Max output    : $LA_MAX_OUTPUT_TOKENS tokens"
    echo "  Timeouts      : API_TIMEOUT_MS=$LA_API_TIMEOUT_MS, FORCE_IDLE_TIMEOUT=0, STREAM_WATCHDOG=0"
    echo "  Session name  : ${SESSION_EMOJI_LOCAL} $MODEL_ALIAS"
    echo "  Prompt file   : $LA_AGENT_PROMPT_FILE"
    [ -n "${LA_CLAUDE_SETTINGS:-}" ] && echo "  Settings      : $LA_CLAUDE_SETTINGS"
    [ -n "${LA_CLAUDE_TOOLS:-}" ] && echo "  Tools         : $LA_CLAUDE_TOOLS"
    [ -n "${LA_AUTO_COMPACT_WINDOW:-}" ] && echo "  Autocompact   : $LA_AUTO_COMPACT_WINDOW"
    [ -n "${LA_MCP_CONFIG:-}" ] && echo "  MCP config    : $LA_MCP_CONFIG"
    exit 0
fi

echo "⏳ Initializing local engine for $MODEL_ALIAS..."
LAUNCH_OUTPUT=$("$LAUNCH_DIR/local-llm-hotswap.sh" "$MODEL_ALIAS"); echo "$LAUNCH_OUTPUT"
VLLM_PORT=$(echo "$LAUNCH_OUTPUT" | grep -o "SUCCESS_PORT=[0-9]*" | cut -d'=' -f2)
[ -z "$VLLM_PORT" ] && { echo "❌ Could not determine the server port."; exit 1; }

# Parse DISPATCH_MODEL from hotswap output (new contract: tells caller which model ID
# the server actually serves for dispatch). This is the primary source of truth.
# For Rapid-MLX: DISPATCH_MODEL=spoof_id (claude-opus-5)
# For vllm-mlx: DISPATCH_MODEL=alias (qwen-3.8-operator)
# For mlx_lm: DISPATCH_MODEL=model_dir
DISPATCH_MODEL=$(echo "$LAUNCH_OUTPUT" | grep -o "DISPATCH_MODEL=[^[:space:]]*" | cut -d'=' -f2 | tail -1)

# Pick a spoof id THIS PORT ACTUALLY SERVES. hotswap REUSES an already-healthy server rather
# than relaunching, so the resolved port may be hosting a process started from an older config
# that predates a spoof_id change — handing it our newest preferred id would just 404:
#   {"detail":"The model `claude-opus-5` does not exist. Available models: `claude-opus-4-8`..."}
# Intersecting the configured preference list with /v1/models makes the launcher correct against
# servers of any vintage, with no restart required.
_served=$(curl -s --max-time 5 "http://localhost:$VLLM_PORT/v1/models" 2>/dev/null \
          | grep -oE '"id"[[:space:]]*:[[:space:]]*"[^"]*"' | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/' | tr '\n' ' ')
MODEL_SPOOF=""

# For Rapid-MLX, DISPATCH_MODEL is the spoof ID (only served model). Use it directly.
# For vllm-mlx, DISPATCH_MODEL is the alias; we still need a spoof ID for Claude Code.
# For mlx_lm, DISPATCH_MODEL is model_dir; not used for interactive sessions.
if [ "$LA_CUR_SERVE" = "rapid" ] && [ -n "$DISPATCH_MODEL" ]; then
    MODEL_SPOOF="$DISPATCH_MODEL"
    echo "[i] Rapid-MLX: using DISPATCH_MODEL as spoof ID: $MODEL_SPOOF"
else
    # vllm-mlx or fallback: find a spoof ID from /v1/models
    for _cand in $(printf '%s' "$LA_CUR_SPOOF" | tr ',' ' '); do
        case " $_served " in *" $_cand "*) MODEL_SPOOF="$_cand"; break ;; esac
    done
    if [ -z "$MODEL_SPOOF" ]; then
        MODEL_SPOOF="${LA_CUR_SPOOF%%,*}"
        echo "⚠️  Port $VLLM_PORT advertises none of the configured spoof ids ($LA_CUR_SPOOF)."
        echo "    Served: ${_served:-<none>}"
        echo "    Falling back to '$MODEL_SPOOF'. If the session 404s, restart that server so it"
        echo "    picks up the current config (its ids are fixed at launch time)."
    fi
fi

# mlx_lm.server tiers (e.g. Llama-4) serve OpenAI-only under a path id — no Anthropic /v1/messages,
# so a direct interactive session can't reach them. They stay dispatch-only (curl / librarian-dispatch).
if [ "$LA_CUR_SERVE" = "mlx_lm" ]; then
    echo "ℹ️  $MODEL_ALIAS is dispatch-only (mlx_lm.server). Dispatch against port $VLLM_PORT with"
    echo "    model=\"$LA_CUR_DIR\" via curl / bin/librarian-dispatch.py."; exit 0
fi

# --- DIRECT routing (no proxy/relay): the backend (rapid-mlx or vllm-mlx) serves the Anthropic
# /v1/messages endpoint itself, under the spoof id. Both expose it; mlx_lm.server does not.
export ANTHROPIC_BASE_URL="http://localhost:${VLLM_PORT}"    # NO /v1 — Claude Code appends /v1/messages
export ANTHROPIC_AUTH_TOKEN="local"                           # backend ignores auth; avoids the API-key prompt
export CLAUDE_IS_LOCAL="true"                                 # generic signal that this session is local
export LA_SESSION_LAUNCHER="launch-claude-agent.sh"            # names the launcher for plugin hooks (stop-hook gate)
# Queued-prompt Stop hook. ON unless the caller (csl `s`, or the env) turned it off.
# Exported explicitly so the hook sees an unambiguous value rather than inheriting one.
export LA_QUEUE_STOP_HOOK="${LA_QUEUE_STOP_HOOK:-1}"
export CLAUDE_CODE_MAX_OUTPUT_TOKENS="$LA_MAX_OUTPUT_TOKENS"   # bound worst-case turn time + stay under max_model_len
# Timeouts: Claude Code's defaults assume a fast cloud endpoint. A local 27B doing a big prefill over
# many tools routinely exceeds them, causing a "Request timed out" + retry-loop mid-session. Relax both
# for local sessions (see README "Timeouts"): API_TIMEOUT_MS = overall per-request cap (default 600000
# =10min); API_FORCE_IDLE_TIMEOUT=0 disables the 5-min "no bytes arrived yet" abort that a slow first
# token (long prefill) would otherwise trip before generation even starts.
export API_TIMEOUT_MS="$LA_API_TIMEOUT_MS"
export API_FORCE_IDLE_TIMEOUT=0
# THIRD guard, and the one that actually bit (diagnosed 2026-08-19). CLI 2.1.196 turned on a separate
# "streaming idle watchdog ... on by default for all providers — it aborts and retries when a response
# stream produces no events for 5 minutes". API_FORCE_IDLE_TIMEOUT=0 does NOT cover it, so a local
# session silently regained a ceiling: two 5-minute windows (abort + one retry) killed every turn at
# almost exactly 600s having emitted just 2 chunks, because a big prefill emits NOTHING while it runs.
# Observed on a real session: 8 turns dead at elapsed=600.0s / 2 chunks, while turns that got as far as
# emitting tokens survived to 689s. Because each dead turn still grew the context, it could never
# recover — a permanent retry loop, not slowness.
# Turning the watchdog off is safe here rather than reckless: API_TIMEOUT_MS above still caps the
# request, and vllm-mlx's own --timeout bounds it server-side, so a genuinely hung stream is still
# bounded. On a local model a silent multi-minute prefill is normal, not a hang.
export CLAUDE_ENABLE_STREAM_WATCHDOG=0

# SESSION ID GENERATION — create stable session ID before identity resolution.
# This ID persists across the transcript lifecycle and enables transition detection.
# Format: YYYYMMDD-HHMMSS-PID-alias-hash
_ts=$(date -u +"%Y%m%d-%H%M%S")
_pid=$$
_alias_hash=$(printf '%s' "${MODEL_ALIAS:-unknown}" | cksum | cut -d' ' -f1 | cut -c1-6)
LA_SESSION_ID="${_ts}-${_pid}-${_alias_hash}"
export LA_SESSION_ID

# SESSION IDENTITY RESOLUTION — emit deterministic identity for consumers
# (statusline, transcript marker, hooks). Must run AFTER endpoint is known.
if [ -x "$LAUNCH_DIR/la-session-identity.sh" ]; then
    SESSION_IDENTITY=$("$LAUNCH_DIR/la-session-identity.sh" 2>/dev/null || true)
    if [ -n "$SESSION_IDENTITY" ]; then
        export LA_SESSION_IDENTITY="$SESSION_IDENTITY"
        # Export individual fields for easy consumption by hooks/statusline
        export LA_SESSION_KIND=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"session_kind":"[^"]*"' | cut -d'"' -f4)
        export LA_ACTUAL_MODEL=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"actual_model_id":"[^"]*"' | cut -d'"' -f4)
        export LA_PROVIDER_DISPLAY=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"provider_display":"[^"]*"' | cut -d'"' -f4)
        export LA_THEME_IDENTIFIER=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"theme_identifier":"[^"]*"' | cut -d'"' -f4)
        export LA_SPINNER_PROFILE=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"spinner_profile_id":"[^"]*"' | cut -d'"' -f4)
        export LA_TRANSCRIPT_MARKER_VERSION=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"transcript_marker_version":[0-9]*' | cut -d':' -f2)
        export LA_SESSION_KIND_EMOJI=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"session_emoji":"[^"]*"' | cut -d'"' -f4)
        export LA_SESSION_ID=$(printf '%s' "$SESSION_IDENTITY" | grep -o '"session_id":"[^"]*"' | cut -d'"' -f4)
    fi
fi

# PER-SESSION SETTINGS — generate theme + spinner overlay for local sessions only.
# This creates a transient settings file passed via --settings, NOT written to user's
# persistent ~/.claude/settings.json. Only applied for local sessions (session_kind=local).
if [ "${LA_SESSION_KIND:-}" = "local" ] && [ -x "$LAUNCH_DIR/generate-local-settings.py" ]; then
    SETTINGS_FILE="$(
        mktemp "${TMPDIR:-/tmp}/local-agents-settings.XXXXXX.json"
    )"
    chmod 600 "$SETTINGS_FILE"
    LAUNCH_DIR="$LAUNCH_DIR" "$LAUNCH_DIR/generate-local-settings.py" \
        --identity-json "$LA_SESSION_IDENTITY" \
        --output "$SETTINGS_FILE" 2>/dev/null || true
    if [ -f "$SETTINGS_FILE" ] && [ -s "$SETTINGS_FILE" ]; then
        CLAUDE_EXTRA_ARGS+=(--settings "$SETTINGS_FILE")
    fi
fi

# SESSION NAME — use model alias + emoji for terminal title and /resume picker.
# Requires CLI 2.1.270+ (verified). Set via -n/--name flag.
SESSION_NAME="${LA_SESSION_KIND_EMOJI:-$SESSION_EMOJI_LOCAL} ${MODEL_ALIAS}"

# STARTUP BANNER — deliberately loud. A local session's identity used to be a couple of plain
# lines that the long waypoints banner buried, leaving no way to tell at a glance which model is
# actually driving the session. Box-drawing + emoji survive that noise.
_think_label=$([ "${THINK:-false}" = "true" ] && echo "ON 🧠" || echo "off")
cat <<BANNER

╔══════════════════════════════════════════════════════════════════════════╗
║  🖥️  LOCAL SESSION — inference runs on THIS MACHINE, \$0 per token         ║
╚══════════════════════════════════════════════════════════════════════════╝
   🤖 model    : ${MODEL_ALIAS}   (presenting as ${MODEL_SPOOF})
   ⚙️  backend  : ${BACKEND_DISPLAY}
   🎚️  effort   : ${EFFORT}
   🧠 thinking : ${_think_label}
   🔌 port     : ${VLLM_PORT}   →  ${ANTHROPIC_BASE_URL}
   📊 watch it : ${LAUNCH_DIR}/local-watch.sh --attach <session-pid>
   ⚠️  NOT the cloud model. Budget/cap warnings from hooks do not apply here.
──────────────────────────────────────────────────────────────────────────────
BANNER

# TRANSCRIPT MARKER — now handled by SessionStart hook (hooks/transcript-identity.py)
# which appends FREE_AGENTS_SESSION_IDENTITY_V{version}|<json> to the transcript file.
# This ensures transition markers on resume and idempotent SessionStart handling.
# The hook receives the transcript path via stdin and LA_SESSION_IDENTITY via env.

# --- prompt weight: keep the tool surface off the local model's prefill path -------------------
# The dominant cost of a local interactive turn is PREFILL, and tool definitions dominate the
# prompt. --strict-mcp-config drops the configured MCP servers' tools (measured on this stack:
# 99 -> 28 defs, ~46.9k -> ~23.9k tokens). Announce the choice either way: a silently missing MCP
# tool would look like a broken session instead of a deliberate speed trade.
STRICT_FLAG=""
case "${LA_STRICT_MCP:-true}" in
    true|1|yes)
        STRICT_FLAG="--strict-mcp-config"
        # --strict-mcp-config is all-or-nothing on its own, but it composes with --mcp-config: point
        # LA_MCP_CONFIG at a small JSON declaring only the servers worth their prefill (e.g. one that
        # supplies a search tool) and you get exactly those, instead of choosing between all and none.
        if [ -n "${LA_MCP_CONFIG:-}" ] && [ -f "$LA_MCP_CONFIG" ]; then
            STRICT_FLAG="$STRICT_FLAG --mcp-config $LA_MCP_CONFIG"
            echo "🪶 Lean prompt: only the MCP servers in $LA_MCP_CONFIG are loaded (all others excluded)."
        elif [ -n "${LA_MCP_CONFIG:-}" ]; then
            echo "⚠️  LA_MCP_CONFIG points at a missing file ($LA_MCP_CONFIG) — loading NO MCP servers."
            echo "🪶 Lean prompt: MCP servers excluded (--strict-mcp-config) — ~23k fewer prefill tokens/turn."
        else
            echo "🪶 Lean prompt: MCP servers excluded (--strict-mcp-config) — ~23k fewer prefill tokens/turn."
            echo "   MCP tools are NOT available in this session. Set LA_STRICT_MCP=false to keep them all,"
            echo "   or LA_MCP_CONFIG=<file.json> to keep only the ones you actually want."
        fi
        ;;
    *)
        echo "🐢 MCP servers included (LA_STRICT_MCP=false) — their tool definitions add ~23k tokens the"
        echo "   local model must prefill. Expect slower turns than a lean session."
        ;;
esac

# Built-in tools are the other half of the prompt, and --disallowedTools (unlike --allowedTools) drops
# their DEFINITIONS from the payload, not just their permission to run. Withhold the ones a local
# session cannot use anyway — see LA_DENY_TOOLS in config-lib.sh for the per-tool reasoning.
DENY_FLAG=""
if [ -n "${LA_DENY_TOOLS:-}" ]; then
    DENY_FLAG="--disallowedTools $LA_DENY_TOOLS"
    _deny_n=$(printf '%s' "$LA_DENY_TOOLS" | tr ',' '\n' | grep -c .)
    echo "🚫 Withholding $_deny_n built-in tool definitions this session (LA_DENY_TOOLS)."
    echo "   They are absent, not merely denied — that is the point (a definition costs prefill even"
    echo "   when unused). Set LA_DENY_TOOLS= (empty) in your config to send the full tool surface."
fi

# Local-model behavior is maintained as a data template rather than embedded
# launcher prose. The file is read, never sourced or executed.
AGENT_PROMPT=$(cat "$LA_AGENT_PROMPT_FILE")
AGENT_PROMPT=${AGENT_PROMPT//__LA_MODEL_ALIAS__/$MODEL_ALIAS}
AGENT_PROMPT=${AGENT_PROMPT//__LA_MODEL_SPOOF__/$MODEL_SPOOF}
AGENT_PROMPT=${AGENT_PROMPT//__LA_BACKEND__/$BACKEND}
AGENT_PROMPT=${AGENT_PROMPT//__LA_CURRENT_PORT__/$VLLM_PORT}
AGENT_PROMPT=${AGENT_PROMPT//__LA_PORT_START__/$LA_PORT_START}
AGENT_PROMPT=${AGENT_PROMPT//__LA_PORT_MAX__/$LA_PORT_MAX}
AGENT_PROMPT=${AGENT_PROMPT//__LA_HOTSWAP_PATH__/$LAUNCH_DIR\/local-llm-hotswap.sh}

if printf '%s' "$AGENT_PROMPT" | grep -Eq '__LA_[A-Z0-9_]+__'; then
    echo "❌ Unresolved placeholder in local agent prompt: $LA_AGENT_PROMPT_FILE"
    exit 1
fi

# SHARED shipping/verification rules, appended from ONE file that the remote launcher reads
# too. These are lane-independent (they are about how to verify and ship work, not about
# local vs remote), so keeping them in a single file is what stops the two prompts drifting.
# Deliberately INLINED rather than pointed at: a weaker model reliably ignores a "go read
# this file" instruction, and these rules exist because such models skipped exactly these
# steps. Hard-fail rather than continue silently: a session launched WITHOUT them looks
# identical to one with them until it ships something broken.
: "${LA_SHARED_RULES_FILE:=$LAUNCH_DIR/../config/shared-agent-shipping-rules.txt}"
if [ -r "$LA_SHARED_RULES_FILE" ]; then
    AGENT_PROMPT="$AGENT_PROMPT

$(cat "$LA_SHARED_RULES_FILE")"
else
    echo "❌ Shared agent rules file is not readable: $LA_SHARED_RULES_FILE"
    exit 1
fi

echo "🧾 Local agent prompt: $LA_AGENT_PROMPT_FILE ($(printf '%s' "$AGENT_PROMPT" | wc -c | tr -d ' ') bytes)"

# Optional per-machine additions from config (only if set):
[ -n "${LA_MEMORY_DIR:-}" ] && AGENT_PROMPT="$AGENT_PROMPT Your Claude Code auto-memory lives at ${LA_MEMORY_DIR} — read from there, don't guess memory paths."
[ -n "${LA_COUNCIL_NOTE:-}" ] && AGENT_PROMPT="$AGENT_PROMPT ${LA_COUNCIL_NOTE}"

# Log which model drives this session (the spoof id is shared across tiers, so the alias lives here).
mkdir -p "$HOME/.claude/logs"
if [ "${LA_BLIND_AUTO:-0}" = "1" ] && [ "$LA_AUTO_MODE" = "1" ]; then _LA_MODE="auto (blind)"
elif [ "$LA_AUTO_MODE" = "1" ]; then _LA_MODE="auto (classifier)"
else _LA_MODE="direct"; fi
echo "$(date '+%Y-%m-%d %H:%M:%S')  alias=$MODEL_ALIAS  spoof=$MODEL_SPOOF effort=$EFFORT  backend=$BACKEND  declared=$BACKEND_DECLARED  vllm_port=$VLLM_PORT  mode=$_LA_MODE" >> "$HOME/.claude/logs/local-agents-sessions.log"

# Boxed headline — matches the remote picker's style
_session_emoji="${LA_SESSION_KIND_EMOJI:-$SESSION_EMOJI_LOCAL}"
echo "╔══════════════════════════════════════════════════════════╗"
printf '║  %s Local Session: %-41s ║\n' "$_session_emoji" "$MODEL_ALIAS"
printf '║  backend=%-20s effort=%-6s mode=%-10s  ║\n' "$BACKEND" "$EFFORT" "$_LA_MODE"
echo "╠══════════════════════════════════════════════════════════╣"
echo "$_session_emoji Session engine: $MODEL_ALIAS  (direct; logged to ~/.claude/logs/local-agents-sessions.log)"
# State the traffic posture out loud. A suppression the user cannot see is indistinguishable from one
# that silently stopped working, and this one has no other visible symptom.
if [ "$LA_TELEMETRY" = "0" ]; then
    echo "$EMOJI_TELEMETRY_OFF Telemetry: OFF — no nonessential outbound traffic (LA_TELEMETRY=1 restores stock behaviour)"
else
    echo "$EMOJI_TELEMETRY_ON Telemetry: ON — stock Claude Code reporting and update checks are active"
fi

# Record WHICH transcript this session writes, so watchers never have to guess it.
# Why the launcher and not the watcher: Claude Code exposes no session id on the process, and it
# does NOT hold a lasting file handle on its .jsonl (measured 2026-08-18: lsof on a live `claude`
# shows ZERO jsonl handles across repeated samples), so the old lsof correlation in local-watch.sh
# could never resolve a running session. Content-matching is worse than useless from a supervising
# session, because watching a log copies the watched session's prompts into the WATCHER's own
# transcript. The launcher is the one uncontaminated observer: it knows its own start instant and
# cwd, so the first transcript appearing in this project dir afterwards is this session's.
# Fully detached and best-effort — every failure is swallowed, so it can never affect the session.
# Publish the PORT immediately too (the transcript only appears on turn 1, but a watcher can start
# tailing engine health right away). Keyed by the same pid, so `local-watch.sh --attach <pid>` can
# find both halves of one session without guessing. NOTE this works because csl/`exec` REPLACES the
# shell with the launcher, so $$ here is the very pid a watcher will see in pgrep.
printf '%s\n' "$VLLM_PORT" > "$HOME/.claude/logs/local-agents-session-$$.port" 2>/dev/null || true

_LA_SIDECAR="$HOME/.claude/logs/local-agents-session-$$.transcript"
(
  _la_proj="$HOME/.claude/projects/$(pwd | sed 's/[^a-zA-Z0-9]/-/g')"
  # FIXED 2026-08-22: this used `find -newer <marker>`, which matches any transcript MODIFIED since
  # launch — so with a second local session already live, that session's constantly-appended
  # transcript won the race and this sidecar pointed a watcher at the WRONG session (observed:
  # --attach on the deepseek launcher showed the qwen session). Track NEW FILES instead: snapshot
  # the set that exists now, then accept only a path that was not in it.
  _la_before=$(mktemp -t la-before) || exit 0
  find "$_la_proj" -maxdepth 1 -name '*.jsonl' 2>/dev/null | sort > "$_la_before"
  # Poll for up to ~20 min: the .jsonl is created on the FIRST TURN, not at startup (measured gap
  # of 8.5 min on a real session), so a short window would miss it on a slow local model.
  _la_i=0
  while [ "$_la_i" -lt 600 ]; do
    _la_new=$(find "$_la_proj" -maxdepth 1 -name '*.jsonl' 2>/dev/null | sort | comm -13 "$_la_before" - | head -1)
    if [ -n "$_la_new" ]; then printf '%s\n' "$_la_new" > "$_LA_SIDECAR"; break; fi
    _la_i=$((_la_i+1)); sleep 2
  done
  rm -f "$_la_before"
) >/dev/null 2>&1 &
# Drop sidecars whose launcher is gone, so the dir does not grow without bound.
for _la_old in "$HOME"/.claude/logs/local-agents-session-*.transcript "$HOME"/.claude/logs/local-agents-session-*.port; do
  [ -e "$_la_old" ] || continue
  _la_pid=${_la_old##*-session-}; _la_pid=${_la_pid%.transcript}; _la_pid=${_la_pid%.port}
  case "$_la_pid" in ''|*[!0-9]*) continue;; esac
  kill -0 "$_la_pid" 2>/dev/null || rm -f "$_la_old"
done

# --permission-mode: auto when LA_AUTO_MODE=1 (set by the CSL triple-toggle), acceptEdits otherwise.
# In Auto Mode, unresolved consequential calls are judged by the separate classifier.
# The classifier follows the local Anthropic endpoint. Segmented transcripts are enabled
# above so repeated long-context decisions can expose stable cache boundaries; routing
# locally is not itself proof that the backend reuses those boundaries.
#
# CSL triple-toggle maps to these env vars:
#   state=0 (blind-trust)  → LA_AUTO_MODE=1 LA_BLIND_AUTO=1
#   state=1 (classifier)   → LA_AUTO_MODE=1 LA_BLIND_AUTO=0
#   state=2 (off)          → LA_AUTO_MODE=0
#
# BLIND-TRUST MODE (LA_BLIND_AUTO=1, LA_AUTO_MODE=1): auto mode with NO classifier in the loop.
# Every consequential call is routed directly to auto without waiting for classifier approval.
# The launcher NEVER boots a classifier server or verifies classifier readiness in this mode —
# that is the whole point. This is the DEFAULT state because the genuine classifier path is not
# yet reliable; blind-trust gives auto-mode behaviour immediately.
#
# Guard: LA_BLIND_AUTO=1 requires LA_AUTO_MODE=1. If someone passes LA_BLIND_AUTO=1 without
# LA_AUTO_MODE, refuse rather than silently picking acceptEdits — that would contradict the user's
# intent (blind-trust IS an auto-mode variant, not a subset of acceptEdits).
if [ "${LA_BLIND_AUTO:-0}" = "1" ] && [ "$LA_AUTO_MODE" != "1" ]; then
    echo "ERROR: LA_BLIND_AUTO=1 requires LA_AUTO_MODE=1. Set auto mode (CSL_AUTO_MODE_STATE=0 or 1, or 'a'/'b' in csl) first." >&2
    exit 2
fi
# BLIND-TRUST MODE SELECTION
# Option A: acceptEdits + auto-yes wrapper (bypasses classifier entirely)
# Option B: auto + mock classifier (keeps auto mode semantics, sandbox guards active)
# Default to Option B (mock classifier) as it preserves more auto-mode behavior.
: "${LA_BLIND_TRUST_OPTION:=B}"

# MCP enablement for blind-trust mode
# If LA_ENABLE_MCP=1 and we're in blind-trust (Option B), generate a settings file
# with the merged allowlist from master + profile so MCP tools work without prompts.
BLIND_TRUST_SETTINGS_FILE=""
if [ "${LA_AUTO_MODE:-0}" = "1" ] && [ "${LA_BLIND_AUTO:-0}" = "1" ] && [ "$LA_BLIND_TRUST_OPTION" = "B" ] && [ "${LA_ENABLE_MCP:-0}" = "1" ]; then
    BLIND_TRUST_SETTINGS_FILE="${TMPDIR:-/tmp}/claude-blind-trust-settings-local-$$.json"
    # Read the merged allowlist from master + profile
    _MASTER_ALLOWLIST_FILE="$HOME/.claude/launch-profiles/allowlist-master.json"
    _PROFILE_ALLOWLIST_FILE="${LA_CLAUDE_SETTINGS:-$HOME/.claude/launch-profiles/lean-local-general.json}"
    python3 - <<'PYEOF' "$_MASTER_ALLOWLIST_FILE" "$_PROFILE_ALLOWLIST_FILE" "$BLIND_TRUST_SETTINGS_FILE" >/dev/null
import json, sys

def load_allowlist(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('permissions', {}).get('allow', [])
    except Exception:
        return []

master_allow = load_allowlist(sys.argv[1])
profile_allow = load_allowlist(sys.argv[2])

# Merge: master + profile (profile wins on conflicts, preserve order)
seen = set()
merged_allow = []
for item in master_allow + profile_allow:
    if item not in seen:
        seen.add(item)
        merged_allow.append(item)

settings = {
    "_comment": "Blind-trust settings for LOCAL free-agents session. sandbox.enabled changes the WRITE BOUNDARY; it does NOT stop the classifier being consulted, so the verbs a session needs must be allowlisted explicitly or every one of them prompts. MCP tools allowed via merged allowlist from master + profile.",
    "permissions": {
        "defaultMode": "auto",
        "allow": merged_allow
    },
    "sandbox": {
        "enabled": True,
        "excludedCommands": ["gh"]
    },
    "network": {
        "allowedDomains": ["github.com", "api.github.com"]
    }
}

with open(sys.argv[3], 'w', encoding='utf-8') as f:
    json.dump(settings, f, separators=(',', ':'))
PYEOF
    CLAUDE_EXTRA_ARGS+=(--settings "$BLIND_TRUST_SETTINGS_FILE")
    echo "🔌 MCPs ENABLED in blind-trust mode (LA_ENABLE_MCP=1) — MCP tools allowlisted via merged master+profile allowlist"
fi

if [ "$LA_AUTO_MODE" = "1" ]; then
    _PERM_MODE="auto"
    if [ "${LA_BLIND_AUTO:-0}" = "1" ]; then
        if [ "$LA_BLIND_TRUST_OPTION" = "A" ]; then
            # Option A: use acceptEdits with auto-yes wrapper
            _PERM_MODE="acceptEdits"
            _AUTO_MODE_APPEND="You are running in LOCAL auto mode with blind-trust (acceptEdits + auto-yes). Every consequential action is allowed without waiting for a safety check."
        else
            # Option B (default): keep auto mode, use mock classifier
            _AUTO_MODE_APPEND="You are running in LOCAL auto mode with blind-trust (mock classifier). Every consequential action is allowed without waiting for a safety check."
            # Set mock classifier for blind-trust Option B
            export LA_CLASSIFIER_CMD="python3 $LAUNCH_DIR/mock-classifier.py"
        fi
    else
        _AUTO_MODE_APPEND="You are running in LOCAL auto mode with a local safety-classifier backend."
    fi
else
    _PERM_MODE="acceptEdits"
    _AUTO_MODE_APPEND=""
fi
# --append-system-prompt with auto mode to remind the session of its mode (no-op in acceptEdits).
if [ -n "$_AUTO_MODE_APPEND" ]; then
    CLAUDE_EXTRA_ARGS+=(--append-system-prompt "$_AUTO_MODE_APPEND")
fi

# Startup announcement for local sessions — shows route and real model at a glance.
# This is a bounded experiment; if placement is unreliable, status line and session
# title remain the authoritative surfaces.
if [ "${LA_SESSION_KIND:-}" = "local" ]; then
    printf '%s Local inference session — %s (via %s)\n' "$SESSION_EMOJI_LOCAL" "${MODEL_ALIAS}" "${BACKEND_DISPLAY}"
fi

claude -n "$SESSION_NAME" --model "$MODEL_SPOOF" $EFFORT_FLAG $STRICT_FLAG $DENY_FLAG --permission-mode "$_PERM_MODE" --append-system-prompt "$AGENT_PROMPT" "${CLAUDE_EXTRA_ARGS[@]}"

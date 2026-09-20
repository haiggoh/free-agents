#!/usr/bin/env bash
# la-telemetry-token-rate.sh — local-agents token generation rate telemetry.
#
# WHY THIS FILE EXISTS: the status line renderer needs live tok/s data but has
# no business parsing vllm logs or knowing where session logs live. That
# knowledge is local-agents' domain. So local-agents MEASURES here, and any
# renderer just places the result.
#
# CONTRACT (stable, so either side can change independently):
#   invoked with NO arguments; prints ONE line of JSON on stdout; exits 0.
#     {"text":"~4.2 tok/s","rate":4.2,"fresh":true,"age_s":3,"level":"ok"}
#   `text` is opaque to the consumer — WE choose units, precision and wording.
#   `rate` is the numeric tok/s (for programmatic use).
#   `fresh` is true if the sample is recent (< REFRESH_MAX_AGE_S, default 30s).
#   `age_s` is seconds since the sample was taken.
#   `level` is "ok" | "stale" | "unknown" — the consumer never re-derives this.
#   Prints NOTHING and still exits 0 when there is no meaningful data (not a
#   local session, no log, no recent sample). Silence is a valid answer.
#
# Usage:
#   la-telemetry-token-rate.sh           # emit the JSON segment (or nothing)
#   la-telemetry-token-rate.sh --help
#
# Environment:
#   LA_TELEMETRY_LOG_GLOB  glob for vllm log files (default ~/.claude/logs/vllm_*.log)
#   LA_TELEMETRY_SESSION_ID  session ID to match (optional; if set, only returns
#                            data for that session's port)
#   LA_TELEMETRY_MAX_AGE_S   max age for fresh sample (default 30)
#   LA_TELEMETRY_PORT        specific port to watch (default: scan 8000-8010)
set -uo pipefail

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set -uo pipefail/{ /^set -uo pipefail/d; s/^# \{0,1\}//; p; }' "$0"
    exit 0 ;;
  "") : ;;
  *)
    printf 'la-telemetry-token-rate.sh: unrecognised argument: %s\n' "$1" >&2
    printf "Try 'la-telemetry-token-rate.sh --help'.\n" >&2
    exit 2 ;;
esac

# GATE ON THE ENDPOINT, NEVER ON CLAUDE_IS_LOCAL. That flag is exported by the
# local launcher and LEAKS into a later gateway `claude` from the same shell,
# which would report a local instrument for a PAID session. ANTHROPIC_BASE_URL
# is per-process and honest.
case "${ANTHROPIC_BASE_URL:-}" in
  *localhost*|*127.0.0.1*|*'[::1]'*) ;;
  *) exit 0 ;;
esac

LOG_GLOB="${LA_TELEMETRY_LOG_GLOB:-$HOME/.claude/logs/vllm_*.log}"
MAX_AGE="${LA_TELEMETRY_MAX_AGE_S:-30}"
SESSION_ID="${LA_TELEMETRY_SESSION_ID:-}"
SPECIFIC_PORT="${LA_TELEMETRY_PORT:-}"

# Find the most recent log file with token rate data
latest_rate=""
latest_ts=""
latest_port=""

# If a specific session ID is provided, try to find its port from the session log
if [ -n "$SESSION_ID" ]; then
    # Try to find the port from the session sidecar
    port_file="$HOME/.claude/logs/local-agents-session-$(echo "$SESSION_ID" | cut -d'-' -f4).port"
    if [ -f "$port_file" ]; then
        SPECIFIC_PORT=$(head -1 "$port_file" 2>/dev/null)
    fi
fi

# Function to extract token rate from a log file
extract_rate_from_log() {
    local log_file="$1"
    local port="$2"

    # Look for the most recent "N tokens in Ts (X tok/s)" line
    # Pattern: "123 tokens in 4.56s (27.0 tok/s)"
    local rate_line
    rate_line=$(grep -E "tokens in .* tok/s" "$log_file" 2>/dev/null | tail -1)
    [ -n "$rate_line" ] || return 1

    # Extract the tok/s value
    local rate
    rate=$(printf '%s' "$rate_line" | sed -n 's/.*(\([0-9.]*\) tok\/s).*/\1/p')
    [ -n "$rate" ] || return 1

    # Get the timestamp of the log line (approximate from file mtime)
    local ts
    ts=$(stat -f %m "$log_file" 2>/dev/null) || ts=$(date +%s)

    printf '%s %s %s\n' "$rate" "$ts" "$port"
}

now=$(date +%s)
best_rate=""
best_ts=0
best_port=""

if [ -n "$SPECIFIC_PORT" ]; then
    log_file="$HOME/.claude/logs/vllm_$SPECIFIC_PORT.log"
    if [ -f "$log_file" ]; then
        result=$(extract_rate_from_log "$log_file" "$SPECIFIC_PORT")
        if [ -n "$result" ]; then
            read -r best_rate best_ts best_port <<EOF
$result
EOF
        fi
    fi
else
    # Scan all vllm logs
    for log_file in $LOG_GLOB; do
        [ -f "$log_file" ] || continue
        port=$(basename "$log_file" | sed 's/vllm_\([0-9]*\)\.log/\1/')
        result=$(extract_rate_from_log "$log_file" "$port")
        if [ -n "$result" ]; then
            read -r rate ts port <<EOF
$result
EOF
            if [ "$ts" -gt "$best_ts" ]; then
                best_rate="$rate"
                best_ts="$ts"
                best_port="$port"
            fi
        fi
    done
fi

[ -n "$best_rate" ] || exit 0

age=$((now - best_ts))
fresh=false
level="stale"
if [ "$age" -lt "$MAX_AGE" ]; then
    fresh=true
    level="ok"
fi

# Format the text output
text="~${best_rate} tok/s"
if [ "$fresh" = "false" ]; then
    text="${text} (stale ${age}s)"
fi

# Hand-built JSON is fine for known-shaped scalar fields
printf '{"text":"%s","rate":%s,"fresh":%s,"age_s":%d,"level":"%s"}\n' \
    "$text" "$best_rate" "$fresh" "$age" "$level"
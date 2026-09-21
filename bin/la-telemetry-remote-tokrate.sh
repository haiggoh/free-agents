#!/usr/bin/env bash
# la-telemetry-remote-tokrate.sh - free-agents remote API token rate telemetry.
set -uo pipefail

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set -uo pipefail/{ /^set -uo pipefail/d; s/^# \{0,1\}//; p; }' "$0"
    exit 0 ;;
  "") : ;;
  *)
    printf 'la-telemetry-remote-tokrate.sh: unrecognised argument: %s\n' "$1" >&2
    exit 2 ;;
esac

# GATE ON THE ENDPOINT - only for free_api sessions (ports 4141-4151)
case "${ANTHROPIC_BASE_URL:-}" in
  http://localhost:414[1-9]|http://localhost:415[01]|http://127.0.0.1:414[1-9]|http://127.0.0.1:415[01]) ;;
  *) exit 0 ;;
esac

LOG_FILE="${LA_TELEMETRY_REMOTE_LOG:-}"
if [ -z "$LOG_FILE" ]; then
    SESSION_ID="${LA_TELEMETRY_SESSION_ID:-${CLAUDE_CODE_SESSION_ID:-}}"
    if [ -n "$SESSION_ID" ]; then
        LOG_FILE="$HOME/.claude/logs/remote-streaming-$SESSION_ID.log"
    else
        LOG_FILE=$(ls -1t "$HOME/.claude/logs/remote-streaming-*.log" 2>/dev/null | head -1)
    fi
fi
[ -n "$LOG_FILE" ] && [ -f "$LOG_FILE" ] || exit 0

MAX_AGE="${LA_TELEMETRY_MAX_AGE_S:-30}"
now=$(date +%s)

best_rate=""
best_ts=0

while IFS= read -r line; do
    [ -n "$line" ] || continue
    read -r ts rate tokens <<EOF
$line
EOF
    [ -n "$ts" ] && [ -n "$rate" ] || continue
    # ts can have decimal point for milliseconds (e.g., 1790024317.039)
    case "$ts" in ''|*[!0-9.]*) continue ;; esac
    case "$rate" in ''|*[!0-9.]*) continue ;; esac
    # Compare using integer seconds (truncate decimal)
    ts_int="${ts%%.*}"
    if [ "$ts_int" -gt "$best_ts" ]; then
        best_ts="$ts_int"
        best_rate="$rate"
    fi
done < "$LOG_FILE"

[ -n "$best_rate" ] || exit 0

age=$((now - best_ts))
fresh=false
level="stale"
if [ "$age" -lt "$MAX_AGE" ]; then
    fresh=true
    level="ok"
fi

text="~${best_rate} tok/s"
if [ "$fresh" = "false" ]; then
    text="${text} (stale ${age}s)"
fi

printf '{"text":"%s","rate":%s,"fresh":%s,"age_s":%d,"level":"%s"}\n' \
    "$text" "$best_rate" "$fresh" "$age" "$level"

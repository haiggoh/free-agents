#!/usr/bin/env bash
# la-remote-telemetry-wrapper.sh — wrapper for claude that captures streaming token rate
# for remote free-API sessions and writes to a session-specific log file.
#
# Usage: la-remote-telemetry-wrapper.sh <session_id> <claude_args...>
#
# The wrapper:
# 1. Runs claude with --debug-file pointing to a temp file
# 2. Starts a background monitor that reads the debug file and extracts token rate
# 3. Writes token rate (timestamp tok/s cumulative_tokens) to ~/.claude/logs/remote-streaming-<session_id>.log
# 4. Cleans up on exit

set -uo pipefail

SESSION_ID="${1:-}"
if [[ -z "$SESSION_ID" ]]; then
    echo "la-remote-telemetry-wrapper: missing session ID" >&2
    exit 1
fi
shift

CLAUDE_ARGS=("$@")
DEBUG_FILE="/tmp/claude-debug-${SESSION_ID}.log"
LOG_DIR="${HOME}/.claude/logs"
LOG_FILE="${LOG_DIR}/remote-streaming-${SESSION_ID}.log"

mkdir -p "$LOG_DIR"
touch "$LOG_FILE"
chmod 600 "$LOG_FILE" 2>/dev/null || true

# Cleanup function
_cleanup() {
    # Kill background monitor
    if [[ -n "${MONITOR_PID:-}" ]]; then
        kill "$MONITOR_PID" 2>/dev/null || true
        wait "$MONITOR_PID" 2>/dev/null || true
    fi
    # Clean up debug file
    rm -f "$DEBUG_FILE" 2>/dev/null || true
}
trap _cleanup EXIT INT TERM HUP

# Background monitor: reads debug file and extracts token rate from streaming chunks
# The debug file should contain JSON lines for each streaming chunk when using --output-format=stream-json
# But since we can't change the output format (it breaks the UI), we'll monitor the debug file
# for any token usage info that claude logs.

monitor_debug_file() {
    local debug_file="$1"
    local log_file="$2"
    local session_id="$3"

    # Wait for debug file to be created
    local waited=0
    while [[ ! -f "$debug_file" && $waited -lt 30 ]]; do
        sleep 0.5
        waited=$((waited + 1))
    done

    if [[ ! -f "$debug_file" ]]; then
        return 0
    fi

    local last_token_count=0
    local last_timestamp=""
    local cumulative_tokens=0

    # Monitor the debug file for new lines
    # We use tail -f to follow the file
    tail -f "$debug_file" 2>/dev/null | while IFS= read -r line; do
        # Look for token usage patterns in debug output
        # claude debug output may contain lines like:
        # "usage": {"input_tokens": 123, "output_tokens": 456}
        # or streaming chunk info

        # Try to extract output_tokens from JSON in the line
        if [[ "$line" == *"output_tokens"* ]]; then
            # Extract the number after "output_tokens":
            local tokens
            tokens=$(printf '%s' "$line" | python3 -c '
import json, sys, re
line = sys.stdin.read()
# Find output_tokens in JSON-like structures
matches = re.findall(r"\"output_tokens\"[:\s]*(\d+)", line)
if matches:
    print(matches[-1])
' 2>/dev/null)

            if [[ -n "$tokens" && "$tokens" =~ ^[0-9]+$ ]]; then
                local now
                now=$(python3 -c 'import time; print(f"{time.time():.3f}")')

                if [[ -n "$last_timestamp" ]]; then
                    local elapsed
                    elapsed=$(printf '%.3f' "$(echo "$now - $last_timestamp" | bc -l)" 2>/dev/null || echo "0")
                    if (( $(echo "$elapsed > 0" | bc -l 2>/dev/null || echo 0) )); then
                        local delta_tokens=$((tokens - last_token_count))
                        if (( delta_tokens > 0 )); then
                            local tok_per_sec
                            tok_per_sec=$(printf '%.2f' "$(echo "$delta_tokens / $elapsed" | bc -l)" 2>/dev/null || echo "0")
                            cumulative_tokens=$((cumulative_tokens + delta_tokens))
                            printf '%s %.2f %d\n' "$now" "$tok_per_sec" "$cumulative_tokens" >> "$log_file"
                        fi
                    fi
                fi

                last_token_count=$tokens
                last_timestamp=$now
            fi
        fi

        # Also check for completion/usage summary at end of response
        if [[ "$line" == *"usage"* && "$line" == *"completion_tokens"* ]]; then
            local tokens
            tokens=$(printf '%s' "$line" | python3 -c '
import json, sys, re
line = sys.stdin.read()
matches = re.findall(r"\"completion_tokens\"[:\s]*(\d+)", line)
if matches:
    print(matches[-1])
' 2>/dev/null)

            if [[ -n "$tokens" && "$tokens" =~ ^[0-9]+$ ]]; then
                local now
                now=$(python3 -c 'import time; print(f"{time.time():.3f}")')
                if [[ -n "$last_timestamp" ]]; then
                    local elapsed
                    elapsed=$(printf '%.3f' "$(echo "$now - $last_timestamp" | bc -l)" 2>/dev/null || echo "0")
                    if (( $(echo "$elapsed > 0" | bc -l 2>/dev/null || echo 0) )); then
                        local delta_tokens=$((tokens - last_token_count))
                        if (( delta_tokens > 0 )); then
                            local tok_per_sec
                            tok_per_sec=$(printf '%.2f' "$(echo "$delta_tokens / $elapsed" | bc -l)" 2>/dev/null || echo "0")
                            cumulative_tokens=$((cumulative_tokens + delta_tokens))
                            printf '%s %.2f %d\n' "$now" "$tok_per_sec" "$cumulative_tokens" >> "$log_file"
                        fi
                    fi
                fi
                last_token_count=$tokens
                last_timestamp=$now
            fi
        fi
    done
}

# Start background monitor
monitor_debug_file "$DEBUG_FILE" "$LOG_FILE" "$SESSION_ID" &
MONITOR_PID=$!

# Run claude with debug file
# We need to insert --debug-file into the claude args
# Find the position after 'claude' (first arg)
NEW_ARGS=("${CLAUDE_ARGS[0]}" --debug-file "$DEBUG_FILE" "${CLAUDE_ARGS[@]:1}")

exec "${NEW_ARGS[@]}"
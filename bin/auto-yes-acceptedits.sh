#!/usr/bin/env bash
# auto-yes-acceptedits.sh — Wrapper for Option A blind-trust mode
#
# Runs claude with --permission-mode acceptEdits and automatically answers "yes"
# to all permission prompts. This bypasses the classifier entirely since
# acceptEdits only prompts for edits (file writes, bash commands, etc.).
#
# Usage:
#   LA_BLIND_AUTO=1 ./auto-yes-acceptedits.sh [claude args...]
#
# The script uses a named pipe to feed "y\n" to claude's stdin for prompts.
# It runs claude in the background, monitors its stdout/stderr, and injects
# "y\n" whenever a permission prompt is detected.

set -uo pipefail

# Check if we should use auto-yes mode
if [ "${LA_BLIND_AUTO:-0}" != "1" ]; then
    # Not in blind-trust mode, just exec claude directly
    exec claude "$@"
fi

# Create a named pipe for feeding "yes" to claude
PIPE=$(mktemp -u --suffix=-claude-yes)
mkfifo "$PIPE"

# Function to feed "y\n" continuously to the pipe
feed_yes() {
    while true; do
        echo "y" > "$PIPE" 2>/dev/null || break
        sleep 0.1
    done
}

# Start the yes-feeder in background
feed_yes &
FEEDER_PID=$!

# Cleanup function
cleanup() {
    kill $FEEDER_PID 2>/dev/null
    rm -f "$PIPE"
    wait $FEEDER_PID 2>/dev/null
}
trap cleanup EXIT INT TERM

# Run claude with stdin from the pipe
# The pipe provides "y\n" for every permission prompt
exec claude --permission-mode acceptEdits "$@" < "$PIPE"
#!/usr/bin/env bash
# tests/test_sudo_in_terminal.sh — the Terminal driver's --verify must never kill the driver.
#
# Usage: bash tests/test_sudo_in_terminal.sh [--help]
#
# COVERED: a --verify predicate containing `exit` (the natural shape of a port/LISTEN check) must
# be evaluated in a SUBSHELL: the ALREADY_SATISFIED pre-check must fail, the window must open, and
# the final report must be VERIFIED or NOT_VERIFIED — never a silent exit-1 with no output.
# The AppleScript step is stubbed: STUB_OSASCRIPT records the script and exits 0.
set -uo pipefail
case "${1:-}" in -h|--help) sed -n 4,11p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; "") ;; *) echo "usage: $0 [--help]" >&2; exit 2 ;; esac

DRIVER="$HOME/.claude/scripts/sudo-in-terminal.sh"
[ -r "$DRIVER" ] || { echo "FATAL: driver not found at $DRIVER" >&2; exit 1; }
SB="$(mktemp -d "${TMPDIR:-/tmp}/la-driver-test.XXXXXX")"
trap 'rm -rf "$SB"' EXIT
mkdir -p "$SB/shim"
cat > "$SB/shim/osascript" <<SHIM
#!/bin/bash
printf '%s\n' "\$*" >> "$SB/osascript.log"
exit 0
SHIM
chmod +x "$SB/shim/osascript"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$2"; else FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$2"; fi; }

run_driver() {  # run_driver <verify-predicate> <timeout> -> stdout+stderr, exit code in $RC
  OUT=$(PATH="$SB/shim:$PATH" bash "$DRIVER" --command "true" --verify "$1" --timeout "$2" 2>&1)
  RC=$?
}

echo "== a --verify containing exit must not kill the driver =="
: > "$SB/osascript.log"
# Port 1 can never be LISTENing, so the inner `exit 0` never fires and the predicate's own
# `exit 1` is what the driver must survive evaluating.
run_driver '( for p in 1; do lsof -nP -iTCP:$p -sTCP:LISTEN >/dev/null 2>&1 && exit 0; done; exit 1 )' 0
printf '%s\n' "$OUT" | grep -qE '^(VERIFIED|NOT_VERIFIED)'
check $? "the driver reports VERIFIED/NOT_VERIFIED — not ALREADY_SATISFIED, not silence (got: $(printf '%s' "$OUT" | head -1))"
[ "$RC" -eq 1 ]; check $? "a false predicate ends in NOT_VERIFIED with rc=1: rc=$RC"
expect_window() { [ -s "$SB/osascript.log" ]; }
if expect_window; then check 0 "a Terminal window was requested (the predicate was false yet the driver proceeded)"; else check 1 "a Terminal window was requested"; fi

echo "== an already-satisfied predicate still short-circuits =="
: > "$SB/osascript.log"
run_driver 'true' 2
printf '%s\n' "$OUT" | grep -q '^ALREADY_SATISFIED'
check $? "ALREADY_SATISFIED is reported for a true predicate"
[ "$RC" -eq 0 ]; check $? "exit 0 for ALREADY_SATISFIED: rc=$RC"

echo "== unknown flags and missing --command still fail loudly =="
bash "$DRIVER" --nope >/dev/null 2>&1; [ $? -eq 2 ]; check $? "unknown flag exits 2"
bash "$DRIVER" >/dev/null 2>&1; [ $? -eq 2 ]; check $? "missing --command exits 2"
bash "$DRIVER" --help | grep -q 'ALREADY_SATISFIED'; check $? "--help documents the verdict lines"

echo "PASS: $PASS  FAIL: $FAIL"
[ "$FAIL" -eq 0 ]
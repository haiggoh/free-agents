#!/usr/bin/env bash
# tests/test_hotswap_wait_ready.sh — wait_ready notices a DEAD server within seconds.
#
# Usage: bash tests/test_hotswap_wait_ready.sh
#
# wait_ready polled /v1/models until HOTSWAP_READY_TIMEOUT (480 s default) and only then asked
# whether the server pid was alive. A server that dies at once (a dead interpreter, a bad flag, an
# OOM at load) therefore looked like eight minutes of "loading" — the stall the user saw on
# 2026-10-30. The function is extracted VERBATIM from the shipped script and sourced, so the test
# exercises the real code. Scratch ports 18190+ only; nothing binds them.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$2"
          else FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$2"; fi; }
has() { printf '%s' "$2" | grep -qF -- "$1"; check $? "$3"; }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/la-waitready-test.XXXXXX")"
LIVE_PID=""
trap '[ -n "$LIVE_PID" ] && kill "$LIVE_PID" 2>/dev/null; rm -rf "$TMP"' EXIT

sed -n '/^wait_ready() {/,/^}/p' "$REPO/bin/local-llm-hotswap.sh" > "$TMP/wait_ready.sh"
[ -s "$TMP/wait_ready.sh" ] || { echo "FATAL: wait_ready() not found in hotswap" >&2; exit 1; }
LOG="$TMP/server.log"
echo "Traceback: boom (fixture log line)" > "$LOG"

echo "== a server that already exited is reported DEAD within seconds =="
sh -c 'exit 3' & DEAD_PID=$!; wait "$DEAD_PID" 2>/dev/null
start=$SECONDS
out="$( HOTSWAP_READY_TIMEOUT=60 bash -c ". '$TMP/wait_ready.sh'; wait_ready 18191 '$LOG' fake-model $DEAD_PID" 2>&1 )"; rc=$?
elapsed=$((SECONDS - start))
check $(( rc == 0 )) "wait_ready fails for a dead pid (rc=$rc)"
check $(( elapsed > 6 )) "it fails within 6 s, not at the 60 s deadline (${elapsed}s)"
has "DEAD" "$out" "the message says the server is dead"
has "boom (fixture log line)" "$out" "the server log tail is shown"

echo "== a live server that never answers still reaches the deadline path =="
sleep 30 & LIVE_PID=$!
start=$SECONDS
out="$( HOTSWAP_READY_TIMEOUT=4 bash -c ". '$TMP/wait_ready.sh'; wait_ready 18192 '$LOG' slow-model $LIVE_PID" 2>&1 )"; rc=$?
elapsed=$((SECONDS - start))
check $(( rc == 0 )) "wait_ready fails at the deadline for a live-but-silent server (rc=$rc)"
check $(( elapsed < 4 )) "it waited the full deadline (${elapsed}s >= 4)"
has "ALIVE" "$out" "the deadline message says the pid is ALIVE (loading/wedged)"

echo
echo "test_hotswap_wait_ready: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]

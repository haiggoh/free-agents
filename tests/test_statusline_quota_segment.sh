#!/usr/bin/env bash
# tests/test_statusline_quota_segment.sh — framework-free tests for bin/la-statusline-quota.sh
# (0.52.0 statusline quota segment): the segment renders ONLY when the cost-tracker ledger
# holds at least one real line; missing/empty/corrupt ledger -> no output, exit 0; the
# today-figure sums (cum - baseline) over lines dated today UTC; legacy 2-field lines read
# with baseline 0; --help prints usage; unknown flag exits non-zero.

set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SCRIPT="$REPO/bin/la-statusline-quota.sh"

PASS=0
FAIL=0

check() {
    local status="$1" label="$2"
    if [ "$status" -eq 0 ]; then PASS=$((PASS + 1)); printf '  PASS: %s\n' "$label"
    else FAIL=$((FAIL + 1)); printf '  FAIL: %s\n' "$label"; fi
}

expect_ok() {  # expect_ok <label> <cmd...> — pass when cmd exits 0
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then check 0 "$label"; else check 1 "$label"; fi
}

expect_fail() {  # expect_fail <label> <cmd...> — pass when cmd exits non-zero
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then check 1 "$label"; else check 0 "$label"; fi
}

expect_silent_ok() {  # pass when cmd exits 0 AND prints nothing
    local label="$1"; shift
    local out rc
    out="$("$@" 2>/dev/null)"; rc=$?
    if [ "$rc" -eq 0 ] && [ -z "$out" ]; then check 0 "$label"; else check 1 "$label (rc=$rc out=$out)"; fi
}

expect_out() {  # expect_out <label> <needle> <cmd...> — pass when exit 0 and output contains needle
    local label="$1" needle="$2"; shift 2
    local out rc
    out="$("$@" 2>/dev/null)"; rc=$?
    if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qF "$needle"; then check 0 "$label"
    else check 1 "$label (rc=$rc out=$out)"; fi
}

TODAY="$(date -u +%F)"
YESTERDAY="$(date -u -d 'yesterday' +%F 2>/dev/null || date -u -v-1d +%F)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- help / argument handling ---------------------------------------------------
expect_ok "--help exits 0" "$SCRIPT" --help
"$SCRIPT" --help 2>/dev/null | grep -q 'LA_STATUSLINE_LEDGER_DIR'
check $? "--help documents the ledger-dir env var"
expect_fail "unknown flag exits non-zero" "$SCRIPT" --bogus

# --- missing ledger dir -> no segment, exit 0 ------------------------------------
expect_silent_ok "missing ledger dir -> no output, exit 0" env LA_STATUSLINE_LEDGER_DIR="$TMP/no-such-dir" "$SCRIPT"

# --- empty ledger dir -> no segment -----------------------------------------------
mkdir -p "$TMP/empty"
expect_silent_ok "empty ledger dir -> no output, exit 0" env LA_STATUSLINE_LEDGER_DIR="$TMP/empty" "$SCRIPT"

# --- corrupt-only ledger -> no segment --------------------------------------------
mkdir -p "$TMP/corrupt"
printf 'garbage line\nnot a ledger line\n' > "$TMP/corrupt/sess1"
expect_silent_ok "corrupt-only ledger -> no output, exit 0" env LA_STATUSLINE_LEDGER_DIR="$TMP/corrupt" "$SCRIPT"

# --- one real line today -> segment with (cum - baseline) --------------------------
mkdir -p "$TMP/real"
printf '%s 12.50 10.00\n' "$TODAY" > "$TMP/real/sess1"
expect_out 'real line today -> quota segment with today delta $2.50 of $40' 'today $2.50/$40' \
    env LA_STATUSLINE_LEDGER_DIR="$TMP/real" "$SCRIPT"
expect_out "segment carries the quota label" '"label":"quota"' env LA_STATUSLINE_LEDGER_DIR="$TMP/real" "$SCRIPT"

# --- legacy 2-field line counts as real, baseline 0 --------------------------------
mkdir -p "$TMP/legacy"
printf '%s 3.00\n' "$TODAY" > "$TMP/legacy/sess1"
expect_out "legacy 2-field line reads with baseline 0" 'today $3.00/$40' env LA_STATUSLINE_LEDGER_DIR="$TMP/legacy" "$SCRIPT"

# --- yesterday's line is real (segment shows) but not in today's spend --------------
mkdir -p "$TMP/old"
printf '%s 9.00 1.00\n' "$YESTERDAY" > "$TMP/old/sess1"
expect_out 'yesterday-only ledger -> segment present, today $0.00' 'today $0.00/$40' \
    env LA_STATUSLINE_LEDGER_DIR="$TMP/old" "$SCRIPT"

# --- corrupt lines next to a real one are skipped, real still counts ---------------
mkdir -p "$TMP/mixed"
printf 'junk\n%s 5.00 2.00\nmore junk\n' "$TODAY" > "$TMP/mixed/sess1"
expect_out "corrupt lines skipped, real line counted" 'today $3.00/$40' env LA_STATUSLINE_LEDGER_DIR="$TMP/mixed" "$SCRIPT"

# --- warn level at >= 80% of cap -----------------------------------------------------
mkdir -p "$TMP/warn"
printf '%s 33.00 0.00\n' "$TODAY" > "$TMP/warn/sess1"   # $33 of $40 = 82.5%
expect_out "82.5% of cap -> level warn" '"level":"warn"' env LA_STATUSLINE_LEDGER_DIR="$TMP/warn" "$SCRIPT"

# --- cap override is honoured ----------------------------------------------------------
mkdir -p "$TMP/cap"
printf '%s 5.00 0.00\n' "$TODAY" > "$TMP/cap/sess1"
expect_out "LA_STATUSLINE_CAP_USD override shown in the segment" 'today $5.00/$100' \
    env LA_STATUSLINE_LEDGER_DIR="$TMP/cap" LA_STATUSLINE_CAP_USD=100 "$SCRIPT"

# The corrupt/empty cases above ARE the planted negative for the "render unconditionally"
# mutation: an always-on segment would print there and fail them.

printf '%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

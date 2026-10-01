#!/usr/bin/env bash
# Framework-free tests for bin/local-inference-readonly-inventory.zsh argument handling.
#
# Never runs a real inventory: every case is --help, --dry-run, or an argument error, each
# executed in a fresh temp dir with HOME pointed at it, and each asserts that NOTHING was
# created — the original defect was `--help` silently creating a ./--help/ report directory.

set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SCRIPT="$REPO/bin/local-inference-readonly-inventory.zsh"

PASS=0
FAIL=0

check() {
    local status="$1"
    local label="$2"

    if [ "$status" -eq 0 ]; then
        PASS=$((PASS + 1))
        printf 'PASS  %s\n' "$label"
    else
        FAIL=$((FAIL + 1))
        printf 'FAIL  %s\n' "$label"
    fi
}

# run_case <args...> — runs the script in a fresh temp cwd + HOME; sets OUT, ERR, RC, LEFT.
run_case() {
    local tmp
    tmp="$(mktemp -d)"
    OUT="$(cd "$tmp" && HOME="$tmp" zsh "$SCRIPT" "$@" 2>"$tmp/.stderr")"
    RC=$?
    ERR="$(cat "$tmp/.stderr")"
    rm -f "$tmp/.stderr"
    LEFT="$(cd "$tmp" && find . -mindepth 1 | sort)"
    rm -rf "$tmp"
}

run_case --help
check $(( RC != 0 )) "--help exits 0"
printf '%s' "$OUT" | grep -q -- '--dry-run'; check $? "--help documents --dry-run"
printf '%s' "$OUT" | grep -q 'HF_HOME'; check $? "--help documents environment variables"
[ -z "$LEFT" ]; check $? "--help creates nothing (was: ./--help/ report dir)"

run_case -h
check $(( RC != 0 )) "-h exits 0"

run_case --dry-run
check $(( RC != 0 )) "--dry-run exits 0"
printf '%s' "$OUT" | grep -q 'Would create report directory: .*/.claude/reports/local-inference-inventory-'
check $? "--dry-run reports the default directory under HOME"
printf '%s' "$OUT" | grep -q -- '- MODEL AND CACHE ROOTS'; check $? "--dry-run lists sections"
[ -z "$LEFT" ]; check $? "--dry-run creates nothing"

run_case --dry-run ./custom-report
printf '%s' "$OUT" | grep -q 'Would create report directory: ./custom-report'
check $? "--dry-run honours an explicit REPORT_DIR"
[ -z "$LEFT" ]; check $? "--dry-run with REPORT_DIR creates nothing"

run_case --bogus
check $(( RC != 2 )) "unknown option exits 2"
printf '%s' "$ERR" | grep -q 'unknown option: --bogus'; check $? "unknown option is named on stderr"
[ -z "$LEFT" ]; check $? "unknown option creates nothing"

run_case one two
check $(( RC != 2 )) "two positional arguments exit 2"

run_case --dry-run -- -dash-dir
printf '%s' "$OUT" | grep -q 'Would create report directory: -dash-dir'
check $? "-- allows a REPORT_DIR starting with a dash"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

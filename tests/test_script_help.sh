#!/usr/bin/env bash
# Every script answers --help and fails loudly on an unknown flag (standing rule, user
# 2026-10-29) — and probing with --help must NEVER run the script's real work. Covers the
# four shell scripts that lacked a CLI surface plus the python mains.   Usage: tests/test_script_help.sh [--help]
set -uo pipefail
case "${1:-}" in -h|--help) sed -n 2,4p "$0" | sed 's/^# //'; exit 0 ;; "") ;; *) echo "usage: $0 [--help]" >&2; exit 2 ;; esac
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pass=0 fail=0; ok(){ pass=$((pass+1)); echo "  PASS: $1"; }; bad(){ fail=$((fail+1)); echo "  FAIL: $1"; }

SH_SCRIPTS="bin/la-hw-detect.sh bin/la-memguard-run.sh bin/launch-local-auto-mode.sh bin/omlx-auto-prewarm-gate.sh"
PY_SCRIPTS="bin/emoji_constants.py bin/remote_provider_registry.py bin/read-acquisition-catalog.py install/manage-rapid-mlx.py"

for s in $SH_SCRIPTS; do
    out=$(bash "$ROOT/$s" --help 2>&1); rc=$?
    if [ $rc -eq 0 ] && [[ "$out" == *--help* ]]; then ok "$s --help exits 0, prints usage"; else bad "$s --help rc=$rc out=${out:0:100}"; fi
    err=$(bash "$ROOT/$s" --definitely-not-a-flag 2>&1 >/dev/null); rc=$?
    if [ $rc -ne 0 ] && [[ "$err" == *usage* ]]; then ok "$s unknown flag exits $rc with usage on stderr"; else bad "$s unknown flag rc=$rc err=${err:0:100}"; fi
done

for s in $PY_SCRIPTS; do
    out=$(python3 "$ROOT/$s" --help 2>&1); rc=$?
    if [ $rc -eq 0 ] && [[ "$out" == *--help* || "$out" == *usage:* || "$out" == *Usage:* ]]; then ok "$s --help exits 0"; else bad "$s --help rc=$rc out=${out:0:100}"; fi
done

# --help on the detector prints help and must NOT run detection (no LA_* dump)
out=$(bash "$ROOT/bin/la-hw-detect.sh" --help 2>&1)
if [[ "$out" != *"LA_HARDWARE="* && "$out" == *LA_HARDWARE* ]]; then ok "la-hw-detect.sh --help prints help, not a detection dump"; else bad "la-hw-detect.sh --help ran detection"; fi

# sourcing still exports everything, including the new LA_DISTRO_PRETTY_NAME
src=$(bash -c 'source "$1"; IFS="|"; printf "%s|%s|%s" "$LA_OS" "$LA_HARDWARE" "$LA_DISTRO_PRETTY_NAME"' _ "$ROOT/bin/la-hw-detect.sh")
IFS='|' read -r os hw pretty <<< "$src"
if [ -n "$os" ] && [ -n "$hw" ]; then ok "sourcing exports LA_OS=$os LA_HARDWARE=$hw"; else bad "sourcing broken: $src"; fi
if [ -n "$pretty" ]; then ok "LA_DISTRO_PRETTY_NAME=$pretty"; else bad "LA_DISTRO_PRETTY_NAME empty: $src"; fi

# the two python mains must not run their payload on an unknown flag
python3 "$ROOT/bin/emoji_constants.py" --bogus >/dev/null 2>&1; rc=$?
if [ $rc -eq 2 ]; then ok "emoji_constants.py unknown flag exits 2"; else bad "emoji_constants.py unknown flag rc=$rc"; fi
python3 "$ROOT/bin/remote_provider_registry.py" --bogus >/dev/null 2>&1; rc=$?
if [ $rc -eq 2 ]; then ok "remote_provider_registry.py unknown flag exits 2"; else bad "remote_provider_registry.py unknown flag rc=$rc"; fi

echo "$pass passed, $fail failed"
[ $fail -eq 0 ]

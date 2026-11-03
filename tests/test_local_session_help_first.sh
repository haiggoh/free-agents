#!/usr/bin/env bash
# tests/test_local_session_help_first.sh — local-session.sh answers --help BEFORE loading config.
#
# Regression (2026-11-03): local-session.sh ran la_load_config (the registry and catalog scan,
# ~0.9 s idle) before its argument loop, so under load (load average 28, 12 xdist workers) its
# --help ran past test_every_script_has_help.sh's 15 s alarm and exited 142. It also meant --help
# failed outright (exit 2) on a machine without a config. Help is static text; it must not
# depend on the config. A missing config dir makes the dependency visible deterministically,
# with no timing involved.
#
# Usage: bash tests/test_local_session_help_first.sh [--help]
case "${1:-}" in
  -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) echo "usage: bash $0 [--help]" >&2; exit 2 ;;
esac
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0
ok()  { echo "  PASS: $1"; pass=$((pass + 1)); }
bad() { echo "  FAIL: $1"; fail=$((fail + 1)); }

EMPTY=$(mktemp -d); trap 'rm -rf "$EMPTY"' EXIT
for flag in --help -h; do
  out=$(LA_CONFIG_DIR="$EMPTY" bash "$REPO/bin/local-session.sh" "$flag" 2>&1 </dev/null); rc=$?
  if [ "$rc" -eq 0 ]; then ok "$flag exits 0 with no config present"; else bad "$flag exited $rc with no config present: ${out:0:160}"; fi
  if printf '%s' "$out" | grep -q 'Usage:'; then ok "$flag prints the usage"; else bad "$flag printed no usage"; fi
done
# Planted positive: a real run (no --help) with the same missing config must still fail, or the
# cases above would pass because config loading was dropped altogether rather than reordered.
LA_CONFIG_DIR="$EMPTY" bash "$REPO/bin/local-session.sh" --dry-run-skip-preflight operator >/dev/null 2>&1 </dev/null; rc=$?
if [ "$rc" -ne 0 ]; then ok "a non-help run still loads config (fails without one, rc=$rc)"; else bad "a non-help run succeeded with no config — config loading was lost"; fi

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]

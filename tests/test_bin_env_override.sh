#!/usr/bin/env bash
# tests/test_bin_env_override.sh — an explicit backend-binary override from the ENVIRONMENT wins
# over the live config, and a set-but-broken one aborts naming it.
#
# Usage: bash tests/test_bin_env_override.sh
#
# Measured 2026-10-30: `LA_RAPID_BIN=<healthy 1.0.0> local-llm-hotswap.sh …` still launched the
# dead 0.16.1, because la_load_config SOURCES config.local.sh and its plain `LA_RAPID_BIN=…` line
# overwrote the environment. So the one emergency escape hatch silently did nothing.
set -uo pipefail
unset LA_LIVE_CONFIG_DIR _LA_LIVE_FOR LA_CONFIG_DIR LA_RAPID_BIN LA_OMLX_BIN LA_CUDA_BIN LA_LLAMA_SERVER LA_RAPID_AUTO_BIN

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$2"
          else FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$2"; fi; }
has() { printf '%s' "$2" | grep -qF -- "$1"; check $? "$3"; }

SB="$(mktemp -d "${TMPDIR:-/tmp}/la-binoverride-test.XXXXXX")"
trap 'rm -rf "$SB"' EXIT
H="$SB/home"; mkdir -p "$H/.venvs" "$SB/config"
cp -RL "$REPO/config/." "$SB/config/"
rm -f "$SB/config/config.local.sh"

mkbin() {  # mkbin <name> -> a runnable stub binary
  printf '#!/bin/sh\necho %s\n' "$1" > "$SB/$1"; chmod +x "$SB/$1"; echo "$SB/$1"
}
PINNED="$(mkbin pinned-rapid)"
ENVBIN="$(mkbin env-rapid)"
OMLX_ENV="$(mkbin env-omlx)"
cat > "$SB/config/config.local.sh" <<CFG
LA_RAPID_BIN="$PINNED"
LA_OMLX_BIN="$PINNED"
CFG

load() {  # load <env assignments…> -> prints the resolved values, or the error
  ( cd "$SB" && env HOME="$H" "$@" bash -c 'source config/config-lib.sh >/dev/null 2>&1
      la_load_config || exit $?
      printf "RAPID=%s\nOMLX=%s\n" "$LA_RAPID_BIN" "$LA_OMLX_BIN"' 2>&1 )
}

echo "== no override: the live config's pin is used =="
out="$(load)"
has "RAPID=$PINNED" "$out" "LA_RAPID_BIN comes from the live config"

echo "== an environment override wins over the live config =="
out="$(load LA_RAPID_BIN="$ENVBIN" LA_OMLX_BIN="$OMLX_ENV")"
has "RAPID=$ENVBIN" "$out" "env LA_RAPID_BIN beats config.local.sh's plain assignment"
has "OMLX=$OMLX_ENV" "$out" "the same rule holds for LA_OMLX_BIN"

echo "== a set-but-broken override aborts naming the variable =="
out="$(load LA_RAPID_BIN="$SB/does-not-exist")"; rc=$?
check $(( rc == 0 )) "la_load_config fails (rc=$rc)"
has "LA_RAPID_BIN" "$out" "the error names LA_RAPID_BIN"
printf '%s' "$out" | grep -qF "RAPID=$PINNED"; check $(( $? == 0 )) "it never falls through to the config's pin"

echo
echo "test_bin_env_override: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]

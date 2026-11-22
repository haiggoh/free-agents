#!/usr/bin/env bash
# tests/test_csl_inventory_facts.sh — csl --inventory: ONE models-dir scan, and the context/subdir columns.
#
# Usage: bash tests/test_csl_inventory_facts.sh [--help]
#
# Sandbox: the real bin/ + config/ copied into a mktemp dir, HOME inside it, a fixture config.local.sh.
# A `find` shim on PATH logs every call, so the number of models-dir CANDIDATE scans
# (`find -L <models> -mindepth 1 -maxdepth 1`) is counted from what actually ran.
# COVERED: the auto-scan runs once per inventory (waypoint csl-inventory-runs-la-auto); registered rows
# carry column 6 = la_get_effective_context and column 7 = subdir; auto rows carry column 7 = folder;
# columns 1-5 are unchanged for both row shapes.
set -uo pipefail
case "${1:-}" in -h|--help) sed -n 2,11p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; "") ;; *) echo "usage: $0 [--help]" >&2; exit 2 ;; esac

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$2"; else FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$2"; fi; }
expect() {  # expect <label> <command...>: PASS when the command succeeds
  local label="$1"; shift
  if "$@"; then check 0 "$label"; else check 1 "$label"; fi
}

SB="$(mktemp -d "${TMPDIR:-/tmp}/la-inv-facts.XXXXXX")"
trap 'rm -rf "$SB"' EXIT
M="$SB/home/.models"
mkdir -p "$M/gemma-4-31B-it-OptiQ-4bit" "$M/Fixture-Model-7B" "$SB/shim" "$SB/home/.claude/logs"
cp -RL "$REPO/bin" "$SB/bin"; cp -RL "$REPO/config" "$SB/config"
[ -L "$SB/config/config.local.sh" ] && { echo "FATAL: sandbox config is a symlink" >&2; exit 1; }
for d in gemma-4-31B-it-OptiQ-4bit Fixture-Model-7B; do
  head -c 2097152 /dev/zero > "$M/$d/model.safetensors"
done
echo '{"model_type":"gemma4"}' > "$M/gemma-4-31B-it-OptiQ-4bit/config.json"
echo '{"model_type":"llama","max_position_embeddings":32768}' > "$M/Fixture-Model-7B/config.json"

cat > "$SB/config/config.local.sh" <<'CFG'
LA_MODELS_DIR="$HOME/.models"
la_register gemma-fixture gemma-4-31B-it-OptiQ-4bit rapid "" "" true claude-opus-5-5 high
CFG

FIND_LOG="$SB/find.log"
REAL_FIND="$(command -v find)"
cat > "$SB/shim/find" <<SHIM
#!/bin/bash
printf '%s\n' "\$*" >> "$FIND_LOG"
exec "$REAL_FIND" "\$@"
SHIM
chmod +x "$SB/shim/find"

INV=$(cd "$SB" && HOME="$SB/home" PATH="$SB/shim:$PATH" bash "$SB/bin/csl" --inventory 2>"$SB/inv.err")
rc=$?
check "$rc" "csl --inventory exits 0"
# A process substitution on a for-loop starts its scan in the BACKGROUND and csl exits right
# after the loop, so that scan can log AFTER csl returned (the race that let a re-added
# double scan read as "1"). Wait until the shim log stops growing before counting.
last=-1; stable=0
for _ in $(seq 1 30); do
  size=$(wc -c < "$FIND_LOG" 2>/dev/null || echo 0)
  if [ "$size" = "$last" ]; then stable=$((stable+1)); else stable=0; fi
  [ "$stable" -ge 3 ] && break
  last=$size; sleep 0.5
done

scans=$(grep -c -- "$M -mindepth 1 -maxdepth 1" "$FIND_LOG" 2>/dev/null)
expect "the find shim saw at least one candidate scan (positive control: ${scans:-0})" [ "${scans:-0}" -ge 1 ]
expect "the models dir is candidate-scanned exactly ONCE per inventory (saw ${scans:-0})" [ "${scans:-0}" -eq 1 ]

reg=$(printf '%s\n' "$INV" | awk -F'\t' '$1=="gemma-fixture"')
auto=$(printf '%s\n' "$INV" | awk -F'\t' '$4=="auto-scanned" && $5=="Fixture-Model-7B"')
expect "registered fixture row present" [ -n "$reg" ]
expect "auto-scanned fixture row present" [ -n "$auto" ]
expect "registered row has 7 columns: $reg" [ "$(printf '%s' "$reg" | awk -F'\t' '{print NF}')" = 7 ]
expect "auto row has 7 columns: $auto" [ "$(printf '%s' "$auto" | awk -F'\t' '{print NF}')" = 7 ]
expected_ctx=$(cd "$SB" && HOME="$SB/home" bash -c '. config/config-lib.sh; la_load_config >/dev/null 2>&1 || exit 9; la_get_effective_context gemma-fixture')
expect "la_get_effective_context answers for the fixture (positive control: ${expected_ctx:-none})" [ -n "$expected_ctx" ]
expect "registered col6 == la_get_effective_context ($expected_ctx)" [ "$(printf '%s' "$reg" | awk -F'\t' '{print $6}')" = "$expected_ctx" ]
expect "registered col7 == its subdir" [ "$(printf '%s' "$reg" | awk -F'\t' '{print $7}')" = "gemma-4-31B-it-OptiQ-4bit" ]
expect "auto col7 == its folder" [ "$(printf '%s' "$auto" | awk -F'\t' '{print $7}')" = "Fixture-Model-7B" ]
expect "registered cols 1-4 keep their shape" [ "$(printf '%s' "$reg" | cut -f1-4)" = "$(printf 'gemma-fixture\t%s\thigh\t%s' "$(printf '%s' "$reg" | cut -f2)" "$(printf '%s' "$reg" | cut -f4)")" ]

echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]

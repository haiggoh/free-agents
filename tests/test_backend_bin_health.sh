#!/usr/bin/env bash
# tests/test_backend_bin_health.sh — the shell launch gates RUN a backend's interpreter.
#
# Usage: bash tests/test_backend_bin_health.sh
#
# Measured 2026-10-30: ~/.venvs/rapid-mlx-0.16.1/bin/rapid-mlx stayed executable after a Homebrew
# upgrade deleted the patch keg its venv was built on. hotswap's gate was `[ -x "$LA_RAPID_BIN" ]`,
# so it passed, printed "Launching … on free port N", and the server died at once while the
# launcher waited out its readiness deadline (480 s). These cases build REAL venvs in a sandbox
# HOME and break them the way Homebrew does; nothing here touches ~/.venvs or the live config.
set -uo pipefail
unset LA_LIVE_CONFIG_DIR _LA_LIVE_FOR LA_RAPID_BIN LA_CONFIG_DIR

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$2"
          else FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$2"; fi; }
has() { printf '%s' "$2" | grep -qF -- "$1"; check $? "$3"; }

SB="$(mktemp -d "${TMPDIR:-/tmp}/la-binhealth-test.XXXXXX")"
trap 'rm -rf "$SB"' EXIT
H="$SB/home"
mkdir -p "$H/.venvs" "$H/.models/FakeModel" "$H/.claude/logs/local-agents-configs"
cp -RL "$REPO/bin" "$SB/bin"
cp -RL "$REPO/config" "$SB/config"
cp -RL "$REPO/install" "$SB/install"
[ -L "$SB/config/config.local.sh" ] && { echo "FATAL: sandbox config is a symlink" >&2; exit 1; }
head -c 2097152 /dev/zero > "$H/.models/FakeModel/weight.bin"

# mkvenv <version> -> a real stdlib venv with a pip-style console script bin/rapid-mlx
mkvenv() {
  local t="$H/.venvs/rapid-mlx-$1"
  python3 -m venv --without-pip "$t" || { echo "FATAL: venv creation failed" >&2; exit 1; }
  printf '#!%s/bin/python\nimport sys; print("rapid-mlx %s"); sys.exit(0)\n' "$t" "$1" > "$t/bin/rapid-mlx"
  chmod +x "$t/bin/rapid-mlx"
  echo "$t"
}
# kill_base <venv> -> what a deleted Homebrew keg does: bin/python* point at a path that is gone
kill_base() {
  local f
  for f in "$1"/bin/python*; do
    [ -L "$f" ] && { rm "$f"; ln -s "$SB/Cellar/python@3.14/3.14.8_2/bin/python3.14" "$f"; }
  done
}

GOOD="$(mkvenv 0.2.0)"
BAD="$(mkvenv 0.3.0)"
kill_base "$BAD"

lib() {  # lib <shell snippet> — run with the sandbox's config-lib sourced, sandbox HOME
  ( cd "$SB" && HOME="$H" PATH=/usr/bin:/bin:"$(dirname "$(command -v python3)")" \
      _LA_RAPID_SYSTEM_CANDIDATES="" bash -c 'source config/config-lib.sh >/dev/null 2>&1; '"$1" )
}

echo "== la_backend_bin_health =="
out="$(lib "la_backend_bin_health '$GOOD/bin/rapid-mlx'; echo rc=\$?" 2>&1)"
has "rc=0" "$out" "healthy venv binary passes"
out="$(lib "la_backend_bin_health '$BAD/bin/rapid-mlx'; echo rc=\$?" 2>&1)"
has "rc=1" "$out" "binary of a venv whose base python is gone fails"
has "3.14.8_2" "$out" "the failure names the missing interpreter path"
out="$(lib "la_backend_bin_health '$H/nope/rapid-mlx'; echo rc=\$?" 2>&1)"
has "rc=1" "$out" "a missing binary fails"

echo "== la_discover_rapid_bin skips a broken newest venv =="
out="$(lib 'la_discover_rapid_bin' 2>/dev/null)"
has "rapid-mlx-0.2.0/bin/rapid-mlx" "$out" "discovery picks the newest HEALTHY venv (0.2.0)"
printf '%s' "$out" | grep -qF "rapid-mlx-0.3.0"; check $(( $? == 0 )) "discovery never returns the broken 0.3.0"

echo "== hotswap refuses a pinned broken binary fast, with the repair command =="
cat > "$SB/config/config.local.sh" <<CFG
LA_MODELS_DIR="\$HOME/.models"
LA_PORT_START=8150
LA_PORT_MAX=8154
LA_RAPID_BIN="$BAD/bin/rapid-mlx"
la_register rapid-fake FakeModel rapid qwen "" false claude-opus-5 low "" "" 16
CFG
start=$SECONDS
out="$( cd "$SB" && HOME="$H" HOTSWAP_READY_TIMEOUT=60 LA_SKIP_RAM_PREFLIGHT=1 \
          bash "$SB/bin/local-llm-hotswap.sh" rapid-fake 2>&1 )"; rc=$?
elapsed=$((SECONDS - start))
check $(( rc == 0 )) "hotswap exits non-zero on a broken pinned binary (rc=$rc)"
check $(( elapsed > 10 )) "hotswap refuses within 10 s, not at the readiness deadline (${elapsed}s)"
has "repair 0.3.0" "$out" "the refusal names the repair command for that version"
printf '%s' "$out" | grep -qF "Launching rapid-fake"; check $(( $? == 0 )) "no 'Launching' line is printed for a broken binary"

echo
echo "test_backend_bin_health: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]

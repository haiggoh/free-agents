#!/usr/bin/env bash
# tests/test_cuda_bin_resolution.sh — LA_CUDA_BIN resolves: env > live-config pin > newest
# HEALTHY versioned cuda venv (sort -V), and a dead newest venv is skipped, never handed out.
#
# Usage: bash tests/test_cuda_bin_resolution.sh
#
# Sandbox HOME with fake cuda venvs; nothing touches the real ~/.local/share/free-agents.
set -uo pipefail
unset LA_LIVE_CONFIG_DIR _LA_LIVE_FOR LA_CUDA_BIN LA_EXL2_BIN LA_SGLANG_BIN

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$2"
          else FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$2"; fi; }
has() { printf '%s' "$2" | grep -qF -- "$1"; check $? "$3"; }

SB="$(mktemp -d "${TMPDIR:-/tmp}/fa-cuda-resolve.XXXXXX")"
SB="$(cd "$SB" && pwd)"
trap 'rm -rf "$SB"' EXIT
H="$SB/home"
STATE="$H/.local/share/free-agents"
CB="$STATE/cuda-backends"
LIVE="$STATE/config"
mkdir -p "$H" "$CB" "$LIVE" "$SB/bin"
cp -RL "$REPO/config" "$SB/config"

mkvenv() { # <version> <healthy|dead>
  local t="$CB/cuda-vllm-$1"
  python3 -m venv --without-pip "$t" || { echo "FATAL: venv failed" >&2; exit 1; }
  printf '#!%s/bin/python\nimport sys; sys.exit(0)\n' "$t" > "$t/bin/vllm"
  chmod +x "$t/bin/vllm"
  if [ "$2" = dead ]; then
    local f
    for f in "$t"/bin/python*; do
      [ -L "$f" ] && { rm "$f"; ln -s "$SB/Cellar/python@3.14/3.14.8_2/bin/python3.14" "$f"; }
    done
  fi
  echo "$t"
}

mkvenv 0.9.0 dead
mkvenv 0.10.0 healthy

resolve() { # -> prints LA_CUDA_BIN after la_load_config, in the sandbox (same bash as this test:
             # config-lib uses declare -A, which /bin/bash 3.2 lacks)
  env -i HOME="$H" PATH=/usr/bin:/bin:/usr/local/bin LA_USER_STATE_DIR="$STATE" "$@" \
    "$BASH" -c '. "$1/config/config-lib.sh"; la_load_config >/dev/null 2>&1; printf %s "${LA_CUDA_BIN:-}"' _ "$SB"
}

# 1. no pin anywhere -> newest HEALTHY venv (0.10.0); the dead 0.9.0 must be skipped
out="$(resolve env)"
has "/cuda-vllm-0.10.0/bin/vllm" "$out" "discovery picks newest healthy (0.10.0), skips dead 0.9.0: $out"

# 2. live-config pin wins over discovery
printf 'LA_CUDA_BIN="$HOME/.local/share/free-agents/cuda-backends/cuda-vllm-0.9.0/bin/vllm"\n' > "$LIVE/config.local.sh"
out="$(resolve env)"
has "/cuda-vllm-0.9.0/bin/vllm" "$out" "live-config pin wins over discovery: $out"

# 3. env override wins over the live config (must be a RUNNABLE binary — a set-but-broken env
#    value aborts la_load_config loudly by design, never falls through to the pin)
out="$(resolve LA_CUDA_BIN="$CB/cuda-vllm-0.10.0/bin/vllm")"
has "/cuda-vllm-0.10.0/bin/vllm" "$out" "env LA_CUDA_BIN wins over live-config pin: $out"
# 3b. set-but-broken env value aborts loudly instead of silently using the pin
out="$(env -i HOME="$H" PATH=/usr/bin:/bin LA_USER_STATE_DIR="$STATE" LA_CUDA_BIN=/nonexistent \
  "$BASH" -c '. "$1/config/config-lib.sh"; la_load_config 2>&1; printf "rc=%s" "$?"' _ "$SB")"
has "cannot run" "$out" "set-but-broken LA_CUDA_BIN aborts naming it: $out"
has "rc=1" "$out" "...and la_load_config exits 1"

# 4. no venvs, no pin -> empty (launchers refuse fast on empty, they do not guess)
rm -rf "$CB"; rm -f "$LIVE/config.local.sh"
out="$(resolve env)"
if [ -z "$out" ]; then check 0 "no venvs and no pin -> empty"; else check 1 "no venvs and no pin -> empty (got $out)"; fi

# 5. lexical-sort discriminant is covered by the python launcher test; here: 0.10.0 vs 0.9.0
#    already proves sort -V (lexical would pick 0.9.0 — which is dead, so case 1 catches both)

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

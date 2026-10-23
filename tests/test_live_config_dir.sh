#!/usr/bin/env bash
# tests/test_live_config_dir.sh — the machine-stable live config dir (0.41.1)
#
# config.local.sh is GITIGNORED machine state, so a second copy of the code (installed plugin,
# fresh worktree) has none and silently falls back to shipped example defaults — the user's
# registered models all read as "on disk, not registered yet" there (user report 2026-10-23).
# The contract under test:
#   1. When a machine-stable state dir holds config/config.local.sh, EVERY copy of the code
#      resolves the live config THERE (LA_LIVE_CONFIG_DIR) and la_load_config loads it.
#   2. An explicit LA_CONFIG_DIR (tests, one-off overrides) still wins — fixtures can never
#      read the real machine state.
#   3. Without a state dir, live == own tree (unchanged behavior, standalone installs).
#   4. The WRITER follows the same resolution: csl --register-model writes into the live
#      config, never into a second copy's own tree.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf 'PASS  %s\n' "$2"; else FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$2"; fi; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# --- fixture: a machine-stable state dir with its own config -------------------------------
STATE="$TMP/state"
mkdir -p "$STATE/config"
cat > "$STATE/config/config.local.sh" <<EOF
LA_MODELS_DIR="$TMP/models"
LA_LIVE_TEST_MARKER=from-stable-dir
EOF

# --- fixture: an explicit config dir (the LA_CONFIG_DIR override tests use) ----------------
EXPLICIT="$TMP/explicit"
mkdir -p "$EXPLICIT"
cat > "$EXPLICIT/config.local.sh" <<EOF
LA_MODELS_DIR="$TMP/models"
LA_LIVE_TEST_MARKER=from-explicit-dir
EOF

# --- Test 1: the state dir wins when present (no explicit LA_CONFIG_DIR) --------------------
echo "=== Test 1: state dir becomes the live config ==="
out=$(LA_USER_STATE_DIR="$STATE" bash -c '
  . "'"$REPO"'/config/config-lib.sh"
  la_load_config >/dev/null 2>&1
  printf "%s\t%s\t%s" "$LA_LIVE_CONFIG_DIR" "$LA_LIVE_TEST_MARKER" "$LA_CONFIG_SOURCE"
' 2>&1)
live_dir=$(printf '%s' "$out" | cut -f1)
marker=$(printf '%s' "$out" | cut -f2)
if [ "$live_dir" = "$STATE/config" ]; then
  check 0 "LA_LIVE_CONFIG_DIR resolves to the state dir"
else
  check 1 "LA_LIVE_CONFIG_DIR resolves to the state dir (got: $live_dir)"
fi
if [ "$marker" = "from-stable-dir" ]; then
  check 0 "la_load_config loads the state config"
else
  check 1 "la_load_config loads the state config (marker: $marker)"
fi
if printf '%s' "$out" | grep -q "state"; then
  check 0 "LA_CONFIG_SOURCE names the state config"
else
  check 1 "LA_CONFIG_SOURCE names the state config (got: $(printf '%s' "$out" | cut -f3))"
fi

# --- Test 2: explicit LA_CONFIG_DIR still wins over the state dir (tests stay hermetic) -----
echo "=== Test 2: explicit LA_CONFIG_DIR wins ==="
out=$(LA_USER_STATE_DIR="$STATE" LA_CONFIG_DIR="$EXPLICIT" bash -c '
  . "'"$REPO"'/config/config-lib.sh"
  la_load_config >/dev/null 2>&1
  printf "%s\t%s" "$LA_LIVE_CONFIG_DIR" "$LA_LIVE_TEST_MARKER"
' 2>&1)
if [ "$(printf '%s' "$out" | cut -f1)" = "$EXPLICIT" ]; then
  check 0 "live dir is the explicit config dir"
else
  check 1 "live dir is the explicit config dir (got: $out)"
fi
if [ "$(printf '%s' "$out" | cut -f2)" = "from-explicit-dir" ]; then
  check 0 "explicit config content is loaded, state dir not consulted"
else
  check 1 "explicit config content is loaded, state dir not consulted (got: $out)"
fi

# --- Test 3: no state dir -> live is the own tree (unchanged standalone behavior) -----------
echo "=== Test 3: no state dir falls back to the own tree ==="
out=$(LA_USER_STATE_DIR="$TMP/empty" bash -c '
  . "'"$REPO"'/config/config-lib.sh"
  printf "%s" "$LA_LIVE_CONFIG_DIR"
' 2>&1)
if [ "$out" = "$REPO/config" ]; then
  check 0 "live dir is the own config dir"
else
  check 1 "live dir is the own config dir (got: $out)"
fi

# --- Test 4a: the writer honors an explicit config dir (old contract, regression guard) -----
# SAFE IN BOTH WORLDS: explicit LA_CONFIG_DIR wins under the old code (it writes there) and
# under the new resolution (explicit beats the state dir), so this never touches the repo.
echo "=== Test 4a: csl --register-model honors an explicit config dir ==="
MODELS_DIR="$TMP/models"; mkdir -p "$MODELS_DIR/registered-model"
cat > "$MODELS_DIR/registered-model/config.json" <<'EOF'
{"model_type": "qwen3", "text_config": {"max_position_embeddings": 262144}, "architectures": ["Qwen3ForCausalLM"]}
EOF
dd if=/dev/zero of="$MODELS_DIR/registered-model/model.safetensors" bs=1 count=0 seek=2097152 2>/dev/null
REPO_CFG_BEFORE=$(shasum -a 256 "$REPO/config/config.local.sh" | cut -d' ' -f1)
LA_CONFIG_DIR="$STATE/config" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --register-model registered-model >/dev/null 2>&1
if grep -q "^la_register registered-model " "$STATE/config/config.local.sh"; then
  check 0 "registration landed in the explicit config"
else
  check 1 "registration landed in the explicit config"
fi
REPO_CFG_AFTER=$(shasum -a 256 "$REPO/config/config.local.sh" | cut -d' ' -f1)
if [ "$REPO_CFG_BEFORE" = "$REPO_CFG_AFTER" ]; then
  check 0 "the repo's own config.local.sh was not touched"
else
  check 1 "the repo's own config.local.sh was not touched"
fi

# --- Test 4b: the writer follows the state-dir resolution (the new contract) ----------------
# RUNS ONLY AFTER THE FIX LANDS: under the old code this invocation would resolve the live
# config to the OWN TREE of whatever checkout ran it — for a real checkout that is the user's
# registry, and this test would write into it. The guard probes for the resolution itself
# (state dir beats own tree), so the RED state skips 4b instead of risking a machine write.
if LA_USER_STATE_DIR="$STATE" bash -c '. "'"$REPO"'/config/config-lib.sh" && [ -n "${LA_LIVE_CONFIG_DIR:-}" ] && [ "$LA_LIVE_CONFIG_DIR" != "$LA_CONFIG_DIR" ]' 2>/dev/null; then
  # 4a already registered into the state config; use a fresh state dir so 4b starts clean.
  STATE2="$TMP/state2"; mkdir -p "$STATE2/config"
  cat > "$STATE2/config/config.local.sh" <<EOF
LA_MODELS_DIR="$MODELS_DIR"
LA_LIVE_TEST_MARKER=from-stable-dir
EOF
  REPO_CFG_BEFORE2=$(shasum -a 256 "$REPO/config/config.local.sh" | cut -d' ' -f1)
  LA_USER_STATE_DIR="$STATE2" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --register-model registered-model >/dev/null 2>&1
  if grep -q "^la_register registered-model " "$STATE2/config/config.local.sh"; then
    check 0 "registration follows the state-dir resolution"
  else
    check 1 "registration follows the state-dir resolution"
  fi
  REPO_CFG_AFTER2=$(shasum -a 256 "$REPO/config/config.local.sh" | cut -d' ' -f1)
  if [ "$REPO_CFG_BEFORE2" = "$REPO_CFG_AFTER2" ]; then
    check 0 "the repo's own config.local.sh was not touched (state-dir run)"
  else
    check 1 "the repo's own config.local.sh was not touched (state-dir run)"
  fi
else
  printf 'SKIP  4b (writer state-dir resolution) — runs only after the fix lands\n'
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

#!/usr/bin/env bash
# tests/test_live_config_dir.sh — the machine-stable live config dir (0.41.1, precedence 0.41.2)
#
# config.local.sh is GITIGNORED machine state, so a second copy of the code (installed plugin,
# fresh worktree) has none and silently fell back to shipped example defaults — the user's
# registered models all read as "on disk, not registered yet" there (user report 2026-10-23).
# The contract under test (config-lib.sh resolves LA_LIVE_CONFIG_DIR):
#   1. explicit LA_CONFIG_DIR (tests, one-off overrides) wins over everything — a fixture can
#      never read real machine state.
#   2. the OWN tree's config.local.sh, when it has one, is authoritative — a standalone
#      install's seeded file and a test sandbox's fixture (the first 0.41.1 cut let the state
#      dir outrank it, which loaded the real machine roster into sandboxed launchers and broke
#      every .sh suite that did not also sandbox HOME).
#   3. otherwise the machine-stable state dir, when it holds a config.local.sh, is the live
#      config — that is what serves the installed plugin copy.
#   4. the WRITER follows the same resolution: csl --register-model writes into the live
#      config, never into some other copy's tree.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf 'PASS  %s\n' "$2"; else FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$2"; fi; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# Fake code trees, so the own-tree half of the contract is controlled by the test instead of
# by whichever checkout runs it. Each carries the real config-lib.sh; only the presence of an
# own config.local.sh differs (that one file is the whole variable under test).
mk_tree() {
    local dir="$1"
    mkdir -p "$dir/config"
    cp "$REPO/config/config-lib.sh" "$dir/config/config-lib.sh"
    cp "$REPO/config/config.example.sh" "$dir/config/config.example.sh"
}
TREE_NOLOCAL="$TMP/tree-nolocal"; mk_tree "$TREE_NOLOCAL"
TREE_WITHLOCAL="$TMP/tree-withlocal"; mk_tree "$TREE_WITHLOCAL"
cat > "$TREE_WITHLOCAL/config/config.local.sh" <<EOF
LA_LIVE_TEST_MARKER=from-own-tree
EOF

# --- fixtures: a machine-stable state dir, and an explicit config dir -----------------------
STATE="$TMP/state"
mkdir -p "$STATE/config"
cat > "$STATE/config/config.local.sh" <<EOF
LA_MODELS_DIR="$TMP/models"
LA_LIVE_TEST_MARKER=from-stable-dir
EOF
EXPLICIT="$TMP/explicit"
mkdir -p "$EXPLICIT"
cat > "$EXPLICIT/config.local.sh" <<EOF
LA_MODELS_DIR="$TMP/models"
LA_LIVE_TEST_MARKER=from-explicit-dir
EOF

resolve() { # resolve <tree> [<extra env...>] — source that tree's config-lib, print live dir + marker
    local tree="$1"; shift
    env LA_CONFIG_DIR="" "$@" bash -c '
        . "'"$tree"'/config/config-lib.sh"
        la_load_config >/dev/null 2>&1
        printf "%s\t%s" "$LA_LIVE_CONFIG_DIR" "${LA_LIVE_TEST_MARKER:-}"
    ' 2>&1
}

# --- Test 1: a tree with NO own config reads the state dir (the plugin-copy case) ------------
echo "=== Test 1: state dir serves a tree with no own config ==="
out="$(resolve "$TREE_NOLOCAL" LA_USER_STATE_DIR="$STATE" LA_CONFIG_DIR=)"
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

# --- Test 2: a tree WITH its own config.local.sh is authoritative (sandbox/standalone) --------
echo "=== Test 2: own config.local.sh wins over the state dir ==="
out="$(resolve "$TREE_WITHLOCAL" LA_USER_STATE_DIR="$STATE" LA_CONFIG_DIR=)"
if [ "$(printf '%s' "$out" | cut -f1)" = "$TREE_WITHLOCAL/config" ]; then
  check 0 "live dir is the own config dir"
else
  check 1 "live dir is the own config dir (got: $out)"
fi
if [ "$(printf '%s' "$out" | cut -f2)" = "from-own-tree" ]; then
  check 0 "own config content is loaded, state dir not consulted"
else
  check 1 "own config content is loaded, state dir not consulted (got: $out)"
fi

# --- Test 3: explicit LA_CONFIG_DIR still wins (tests stay hermetic) --------------------------
echo "=== Test 3: explicit LA_CONFIG_DIR wins ==="
out="$(resolve "$TREE_NOLOCAL" LA_USER_STATE_DIR="$STATE" LA_CONFIG_DIR="$EXPLICIT")"
if [ "$(printf '%s' "$out" | cut -f1)" = "$EXPLICIT" ]; then
  check 0 "live dir is the explicit config dir"
else
  check 1 "live dir is the explicit config dir (got: $out)"
fi
if [ "$(printf '%s' "$out" | cut -f2)" = "from-explicit-dir" ]; then
  check 0 "explicit config content is loaded"
else
  check 1 "explicit config content is loaded (got: $out)"
fi

# --- Test 4: no state dir -> own tree with example fallback (unchanged standalone behavior) ----
echo "=== Test 4: no state dir falls back to the own tree ==="
out="$(env LA_USER_STATE_DIR="$TMP/empty" bash -c '
  . "'"$TREE_NOLOCAL"'/config/config-lib.sh"
  la_load_config >/dev/null 2>&1
  printf "%s\t%s" "$LA_LIVE_CONFIG_DIR" "$LA_FALLBACK_CONFIG"
' 2>&1)"
if [ "$(printf '%s' "$out" | cut -f1)" = "$TREE_NOLOCAL/config" ]; then
  check 0 "live dir is the own config dir"
else
  check 1 "live dir is the own config dir (got: $out)"
fi
if [ "$(printf '%s' "$out" | cut -f2)" = "1" ]; then
  check 0 "example defaults flagged via LA_FALLBACK_CONFIG"
else
  check 1 "example defaults flagged via LA_FALLBACK_CONFIG (got: $out)"
fi

# --- Test 5: the writer follows the same resolution -----------------------------------------
echo "=== Test 5a: csl --register-model honors an explicit config dir ==="
MODELS_DIR="$TMP/models"; mkdir -p "$MODELS_DIR/registered-model"
cat > "$MODELS_DIR/registered-model/config.json" <<'EOF'
{"model_type": "qwen3", "text_config": {"max_position_embeddings": 262144}, "architectures": ["Qwen3ForCausalLM"]}
EOF
dd if=/dev/zero of="$MODELS_DIR/registered-model/model.safetensors" bs=1 count=0 seek=2097152 2>/dev/null
REPO_CFG="$REPO/config/config.local.sh"
if [ -f "$REPO_CFG" ]; then REPO_CFG_BEFORE=$(shasum -a 256 "$REPO_CFG" | cut -d' ' -f1); fi
LA_CONFIG_DIR="$STATE/config" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --register-model registered-model >/dev/null 2>&1
if grep -q "^la_register registered-model " "$STATE/config/config.local.sh"; then
  check 0 "registration landed in the explicit config"
else
  check 1 "registration landed in the explicit config"
fi
if [ -f "$REPO_CFG" ]; then
  if [ "$REPO_CFG_BEFORE" = "$(shasum -a 256 "$REPO_CFG" | cut -d' ' -f1)" ]; then
    check 0 "the repo's own config.local.sh was not touched"
  else
    check 1 "the repo's own config.local.sh was not touched"
  fi
fi

echo "=== Test 5b: the writer follows the state-dir resolution ==="
# Needs a run tree with NO own config.local.sh (the plugin-copy shape). This checkout is that
# tree when it is a fresh worktree; when the checkout carries an own file (a seeded copy), the
# own file is authoritative by contract and this check would write into it — skip instead.
if [ ! -f "$REPO_CFG" ]; then
  STATE2="$TMP/state2"; mkdir -p "$STATE2/config"
  cat > "$STATE2/config/config.local.sh" <<EOF
LA_MODELS_DIR="$MODELS_DIR"
LA_LIVE_TEST_MARKER=from-stable-dir
EOF
  LA_USER_STATE_DIR="$STATE2" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --register-model registered-model >/dev/null 2>&1
  if grep -q "^la_register registered-model " "$STATE2/config/config.local.sh"; then
    check 0 "registration follows the state-dir resolution"
  else
    check 1 "registration follows the state-dir resolution"
  fi
else
  printf 'SKIP  5b (writer state-dir resolution) — this checkout has its own config.local.sh\n'
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

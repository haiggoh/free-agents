#!/usr/bin/env bash
# tests/test_inventory_auto_scan.sh — auto-scan models appear in inventory
#
# Tests that:
# 1. csl --inventory includes auto-scanned models (marked as auto-scanned)
# 2. local-session.sh --inventory includes auto-scanned models
# 3. Registered models still appear normally
# 4. Auto-scanned models don't duplicate registered ones
#
# Uses temp HOME and temp models dir via LA_MODELS_DIR override.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf 'PASS  %s\n' "$2"; else FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$2"; fi; }

# Create temp directory structure
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
MODELS_DIR="$TMP/models"
mkdir -p "$MODELS_DIR"

# Create registered model
mkdir -p "$MODELS_DIR/registered-model"
cat > "$MODELS_DIR/registered-model/config.json" <<'EOF'
{"text_config": {"max_position_embeddings": 262144}, "architectures": ["Qwen3ForCausalLM"]}
EOF
# la_on_disk requires a real weight file (>1 MB), not just a config: a sparse 2 MB placeholder.
dd if=/dev/zero of="$MODELS_DIR/registered-model/model.safetensors" bs=1 count=0 seek=2097152 2>/dev/null

# Create auto-scanned model (not registered)
mkdir -p "$MODELS_DIR/auto-scanned-model"
cat > "$MODELS_DIR/auto-scanned-model/config.json" <<'EOF'
{"text_config": {"max_position_embeddings": 131072}, "architectures": ["LlamaForCausalLM"]}
EOF
dd if=/dev/zero of="$MODELS_DIR/auto-scanned-model/model.safetensors" bs=1 count=0 seek=2097152 2>/dev/null
# A config.json-only shell (aborted download) must NOT be auto-scanned.
mkdir -p "$MODELS_DIR/metadata-only-shell"
echo '{"architectures": ["LlamaForCausalLM"]}' > "$MODELS_DIR/metadata-only-shell/config.json"

# Create excluded model (should not appear)
mkdir -p "$MODELS_DIR/flux-model"
cat > "$MODELS_DIR/flux-model/config.json" <<'EOF'
{"text_config": {"max_position_embeddings": 1024}}
EOF

# Create config.local.sh with registered model
cat > "$TMP/config.local.sh" <<EOF
LA_MODELS_DIR="$MODELS_DIR"
la_register registered-model registered-model rapid qwen3_coder_xml "" true "" medium "operator" "" "" ""
EOF

# Test 1: csl --inventory includes auto-scanned model
echo "=== Test 1: csl --inventory includes auto-scanned model ==="
out=$(LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/csl" --inventory 2>&1)
echo "$out" | grep -q "^registered-model\t" && check 0 "registered model appears in csl inventory" || check 1 "registered model appears in csl inventory"
echo "$out" | grep -q "^auto-scanned-model\t" && check 0 "auto-scanned model appears in csl inventory" || check 1 "auto-scanned model appears in csl inventory"
echo "$out" | grep "^auto-scanned-model\t" | grep -q "auto-scanned" && check 0 "auto-scanned model marked with auto-scanned role" || check 1 "auto-scanned model marked with auto-scanned role"
echo "$out" | grep -q "^flux-model\t" && check 1 "excluded model (flux) does not appear" || check 0 "excluded model (flux) does not appear"

# Test 2: local-session.sh --inventory includes auto-scanned model
echo "$out" | grep -q "^metadata-only-shell" && check 1 "metadata-only shell is not listed" || check 0 "metadata-only shell is not listed"

echo "=== Test 2: local-session.sh --inventory includes auto-scanned model ==="
out=$(LA_CONFIG_DIR="$TMP" LA_MODELS_DIR="$MODELS_DIR" "$REPO/bin/local-session.sh" --inventory 2>&1)
echo "$out" | grep -q "^registered-model\t" && check 0 "registered model appears in local-session inventory" || check 1 "registered model appears in local-session inventory"
echo "$out" | grep -q "^auto-scanned-model\t" && check 0 "auto-scanned model appears in local-session inventory" || check 1 "auto-scanned model appears in local-session inventory"
echo "$out" | grep "^auto-scanned-model\t" | grep -q "auto-scanned" && check 0 "auto-scanned model marked with auto-scanned role in local-session" || check 1 "auto-scanned model marked with auto-scanned role in local-session"

# Test 3: Auto-scanned model doesn't duplicate registered model with same folder
echo "=== Test 3: No duplicate for registered model ==="
# The registered-model folder should only appear once (as registered-model)
count=$(echo "$out" | grep -c "^registered-model\t" || true)
check $(( (count == 1) ? 0 : 1 )) "registered model appears exactly once (no duplicate)"

# Test 4: Auto-scanned model has correct TSV format (5 columns)
echo "=== Test 4: TSV format has 5 columns ==="
line=$(echo "$out" | grep "^auto-scanned-model\t" | head -1)
cols=$(echo "$line" | tr '\t' '\n' | wc -l)
check $(( (cols == 5) ? 0 : 1 )) "auto-scanned model TSV has 5 columns"

# Test 5: csl dry-run shows auto-scanned model info
echo "=== Test 5: csl --dry-run shows auto-scanned info ==="
# This tests the dry-run output, which iterates LA_ALIASES - auto-scanned models
# won't appear there since they're not in LA_ALIASES. That's expected behavior.
# The inventory is the main discovery mechanism.

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
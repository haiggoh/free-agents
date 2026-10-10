#!/usr/bin/env bash
# tests/test_manifest_gate.sh — manifest gate validation in launch-claude-agent.sh
#
# Tests that:
# 1. Valid manifest -> launcher dry-run passes the gate
# 2. Invalid manifest (effective context above server context) -> exits non-zero BEFORE a stub claude on PATH is called
# 3. Missing manifest -> one warning, continues
#
# Uses temp HOME and temp models dir via sandbox.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -P "$HERE/.." && pwd)"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf 'PASS  %s\n' "$2"; else FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$2"; fi; }

. "$REPO/tests/lib/sandbox.sh"

# Create sandbox
SB="$(mktemp -d "${TMPDIR:-/tmp}/la-manifest-gate-test.XXXXXX")"
SB="$(cd -P "$SB" && pwd -P)"
trap 'rm -rf "$SB"' EXIT INT TERM HUP

la_test_sandbox "$SB" local-agent-system-prompt.txt shared-agent-shipping-rules.txt || { echo "sandbox build failed"; exit 1; }
mkdir -p "$SB/home/.models"

# Helper to run launcher with stubbed PATH
# Pass an effort override to avoid interactive prompt
run_launcher() {
    PATH="$SB/tmp/stub-bin:$PATH" "$SB/bin/launch-claude-agent.sh" --dry-run "$@" "high" 2>&1
}

# Create stub claude
mkdir -p "$SB/tmp/stub-bin"
cat > "$SB/tmp/stub-bin/claude" <<'EOF'
#!/usr/bin/env bash
echo "STUB CLAUDE CALLED" > "$SB/tmp/claude_was_called"
exit 0
EOF
chmod +x "$SB/tmp/stub-bin/claude"

# Test 1: Valid manifest passes
echo "=== Test 1: Valid manifest ==="
mkdir -p "$SB/home/.models/valid-model"
cat > "$SB/home/.models/valid-model/config.json" <<'EOF'
{"text_config": {"max_position_embeddings": 262144}, "architectures": ["Qwen3ForCausalLM"]}
EOF
cat > "$SB/home/.models/valid-model/.local-model-manifest.json" <<'EOF'
{
  "schema_version": 1,
  "artifact": {"kind": "model", "launchable": true, "session_eligible": true, "directory_name": "valid-model", "format": "mlx"},
  "capabilities": {"native_context_tokens": 262144, "configured_context_tokens": 262144, "context_floor_100k_tokens": 200000, "claude_autocompact_tokens": 200000},
  "runtime_qualification": {"server_context_tokens": 262144},
  "acquisition": {"status": "complete"}
}
EOF

cat > "$SB/config/config.local.sh" <<EOF
LA_MODELS_DIR="$SB/home/.models"
la_register valid-model valid-model rapid qwen3_coder_xml "" true "" medium "operator" "" "" ""
EOF

rm -f "$SB/tmp/claude_was_called"
out=$(PATH="$SB/tmp/stub-bin:$PATH" "$SB/bin/launch-claude-agent.sh" --dry-run valid-model high 2>&1)
r=$?
check $r "valid manifest passes dry-run"
if [ -f "$SB/tmp/claude_was_called" ]; then
    check 1 "claude stub was not called for valid manifest"
else
    check 0 "claude stub was not called for valid manifest"
fi

# Test 2: Invalid manifest (autocompaction > effective context) fails
echo "=== Test 2: Invalid manifest (autocompaction > effective context) ==="
mkdir -p "$SB/home/.models/invalid-model"
cat > "$SB/home/.models/invalid-model/config.json" <<'EOF'
{"text_config": {"max_position_embeddings": 131072}, "architectures": ["Qwen3ForCausalLM"]}
EOF
cat > "$SB/home/.models/invalid-model/.local-model-manifest.json" <<'EOF'
{
  "schema_version": 1,
  "artifact": {"kind": "model", "launchable": true, "session_eligible": true, "directory_name": "invalid-model", "format": "mlx"},
  "capabilities": {"native_context_tokens": 131072, "configured_context_tokens": 131072, "context_floor_100k_tokens": 100000, "claude_autocompact_tokens": 200000},
  "runtime_qualification": {"server_context_tokens": 131072},
  "acquisition": {"status": "complete"}
}
EOF

cat > "$SB/config/config.local.sh" <<EOF
LA_MODELS_DIR="$SB/home/.models"
la_register invalid-model invalid-model rapid qwen3_coder_xml "" true "" medium "operator" "" "" ""
EOF

rm -f "$SB/tmp/claude_was_called"
out=$(PATH="$SB/tmp/stub-bin:$PATH" "$SB/bin/launch-claude-agent.sh" --dry-run invalid-model high 2>&1)
r=$?
check $(( (r != 0) ? 0 : 1 )) "invalid manifest fails dry-run"
if [ -f "$SB/tmp/claude_was_called" ]; then
    check 1 "claude stub was not called for invalid manifest"
else
    check 0 "claude stub was not called for invalid manifest"
fi
echo "$out" | grep -q "server_context_tokens.*< autocompaction" && check 0 "error message shows server < autocompaction" || check 1 "error message shows server < autocompaction"

# Test 3: Missing manifest -> warning, continues
echo "=== Test 3: Missing manifest (legacy fallback) ==="
mkdir -p "$SB/home/.models/no-manifest-model"
cat > "$SB/home/.models/no-manifest-model/config.json" <<'EOF'
{"text_config": {"max_position_embeddings": 262144}, "architectures": ["Qwen3ForCausalLM"]}
EOF

cat > "$SB/config/config.local.sh" <<EOF
LA_MODELS_DIR="$SB/home/.models"
la_register no-manifest-model no-manifest-model rapid qwen3_coder_xml "" true "" medium "operator" "" "" ""
EOF

rm -f "$SB/tmp/claude_was_called"
out=$(PATH="$SB/tmp/stub-bin:$PATH" "$SB/bin/launch-claude-agent.sh" --dry-run no-manifest-model high 2>&1)
r=$?
check $(( (r == 0) ? 0 : 1 )) "missing manifest passes dry-run (legacy fallback)"
warning_count=$(echo "$out" | grep -c "No manifest found" || true)
check $(( (warning_count == 1) ? 0 : 1 )) "exactly one warning line for missing manifest"
if [ -f "$SB/tmp/claude_was_called" ]; then
    check 1 "claude stub was not called for missing manifest"
else
    check 0 "claude stub was not called for missing manifest"
fi

# Test 4: Invalid manifest (server context < autocompaction) fails
echo "=== Test 4: Invalid manifest (server context < autocompaction) ==="
mkdir -p "$SB/home/.models/invalid-model2"
cat > "$SB/home/.models/invalid-model2/config.json" <<'EOF'
{"text_config": {"max_position_embeddings": 262144}, "architectures": ["Qwen3ForCausalLM"]}
EOF
cat > "$SB/home/.models/invalid-model2/.local-model-manifest.json" <<'EOF'
{
  "schema_version": 1,
  "artifact": {"kind": "model", "launchable": true, "session_eligible": true, "directory_name": "invalid-model2", "format": "mlx"},
  "capabilities": {"native_context_tokens": 262144, "configured_context_tokens": 262144, "context_floor_100k_tokens": 200000, "claude_autocompact_tokens": 200000},
  "runtime_qualification": {"server_context_tokens": 100000},
  "acquisition": {"status": "complete"}
}
EOF

cat > "$SB/config/config.local.sh" <<EOF
LA_MODELS_DIR="$SB/home/.models"
la_register invalid-model2 invalid-model2 rapid qwen3_coder_xml "" true "" medium "operator" "" "" ""
EOF

rm -f "$SB/tmp/claude_was_called"
out=$(PATH="$SB/tmp/stub-bin:$PATH" "$SB/bin/launch-claude-agent.sh" --dry-run invalid-model2 high 2>&1)
r=$?
check $(( (r != 0) ? 0 : 1 )) "invalid manifest (server < autocompact) fails dry-run"
if [ -f "$SB/tmp/claude_was_called" ]; then
    check 1 "claude stub was not called"
else
    check 0 "claude stub was not called"
fi
echo "$out" | grep -q "server_context_tokens.*< autocompaction" && check 0 "error message shows server < autocompaction" || check 1 "error message shows server < autocompaction"

# Test 5: Invalid manifest (non-launchable kind) fails
echo "=== Test 5: Invalid manifest (non-launchable kind) ==="
mkdir -p "$SB/home/.models/tts-model"
cat > "$SB/home/.models/tts-model/config.json" <<'EOF'
{"text_config": {"max_position_embeddings": 262144}}
EOF
cat > "$SB/home/.models/tts-model/.local-model-manifest.json" <<'EOF'
{
  "schema_version": 1,
  "artifact": {"kind": "tts_model", "launchable": true, "session_eligible": false, "directory_name": "tts-model", "format": "mlx"},
  "capabilities": {"native_context_tokens": 262144, "configured_context_tokens": 262144},
  "runtime_qualification": {"server_context_tokens": 262144},
  "acquisition": {"status": "complete"}
}
EOF

cat > "$SB/config/config.local.sh" <<EOF
LA_MODELS_DIR="$SB/home/.models"
la_register tts-model tts-model rapid qwen3_coder_xml "" true "" medium "operator" "" "" ""
EOF

rm -f "$SB/tmp/claude_was_called"
out=$(PATH="$SB/tmp/stub-bin:$PATH" "$SB/bin/launch-claude-agent.sh" --dry-run tts-model high 2>&1)
r=$?
check $(( (r != 0) ? 0 : 1 )) "non-launchable kind fails dry-run"
if [ -f "$SB/tmp/claude_was_called" ]; then
    check 1 "claude stub was not called"
else
    check 0 "claude stub was not called"
fi
echo "$out" | grep -q "artifact kind=tts_model is not session-eligible" && check 0 "error message shows non-session-eligible kind" || check 1 "error message shows non-session-eligible kind"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
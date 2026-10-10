#!/usr/bin/env bash
# test_local_temperature.sh — test local-session.sh --temperature flag
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_SESSION="$REPO_ROOT/bin/local-session.sh"

# Test 1: --help shows temperature option
echo "Test 1: --help shows --temperature flag"
output=$("$LOCAL_SESSION" --help 2>&1)
if echo "$output" | grep -q -- "--temperature"; then
    echo "✅ PASS: --help mentions --temperature"
else
    echo "❌ FAIL: --help does not mention --temperature"
    exit 1
fi

# Test 2: --temperature 0.3 is accepted (dry-run mode)
echo "Test 2: --temperature 0.3 accepted in --dry-run"
# Use a valid alias from the config that we know exists
output=$("$LOCAL_SESSION" --dry-run-skip-preflight --temperature 0.3 qwen-3.8-operator 2>&1 || true)
# Should not fail on temperature parsing, should show the dry-run config
if echo "$output" | grep -q "Temperature.*0.7"; then
    echo "❌ FAIL: shows wrong temperature"
    exit 1
elif echo "$output" | grep -q "Temperature.*0.3"; then
    echo "✅ PASS: temperature parsed and shown in dry-run"
else
    echo "⚠️  UNCLEAR: output: $output"
    exit 1
fi

# Test 3: --temperature 2.5 is rejected (out of range)
echo "Test 3: --temperature 2.5 rejected"
output=$("$LOCAL_SESSION" --dry-run-skip-preflight --temperature 2.5 qwen-3.8-operator 2>&1 || true)
if echo "$output" | grep -q "temperature must be a number"; then
    echo "✅ PASS: temperature 2.5 rejected"
else
    echo "❌ FAIL: temperature 2.5 not rejected"
    exit 1
fi

# Test 4: --temperature -0.1 is rejected (out of range)
echo "Test 4: --temperature -0.1 rejected"
output=$("$LOCAL_SESSION" --dry-run-skip-preflight --temperature -0.1 qwen-3.8-operator 2>&1 || true)
if echo "$output" | grep -q "temperature must be a number"; then
    echo "✅ PASS: temperature -0.1 rejected"
else
    echo "❌ FAIL: temperature -0.1 not rejected"
    exit 1
fi

# Test 5: --temperature abc is rejected (not a number)
echo "Test 5: --temperature abc rejected"
output=$("$LOCAL_SESSION" --dry-run-skip-preflight --temperature abc qwen-3.8-operator 2>&1 || true)
if echo "$output" | grep -q "temperature must be a number"; then
    echo "✅ PASS: temperature abc rejected"
else
    echo "❌ FAIL: temperature abc not rejected"
    exit 1
fi

# Test 6: LA_TEMPERATURE env var is exported to launcher (via --dry-run output)
echo "Test 6: LA_TEMPERATURE appears in --dry-run output"
output=$("$LOCAL_SESSION" --dry-run-skip-preflight --temperature 0.7 qwen-3.8-operator 2>&1 || true)
if echo "$output" | grep -q "Temperature.*0.7"; then
    echo "✅ PASS: temperature shown in dry-run output"
else
    echo "❌ FAIL: temperature not shown in dry-run output"
    echo "Output: $output"
    exit 1
fi

# Test 7: effort is NOT overwritten when temperature is provided
echo "Test 7: effort preserved when temperature provided"
# This tests the launcher chain: local-session.sh -> launch-claude-agent.sh -> local-llm-hotswap.sh
# We can't fully test without models, but we can verify the argv construction
output=$("$LOCAL_SESSION" --dry-run-skip-preflight --temperature 0.3 qwen-3.8-operator high 2>&1 || true)
if echo "$output" | grep -q "Effort.*high"; then
    echo "✅ PASS: effort override preserved with temperature"
else
    echo "❌ FAIL: effort not preserved"
    echo "Output: $output"
    exit 1
fi

# Test 8: launch-claude-agent.sh accepts --temperature
echo "Test 8: launch-claude-agent.sh --help shows --temperature"
output=$("$REPO_ROOT/bin/launch-claude-agent.sh" --help 2>&1)
if echo "$output" | grep -q -- "--temperature"; then
    echo "✅ PASS: launch-claude-agent.sh --help mentions --temperature"
else
    echo "❌ FAIL: launch-claude-agent.sh --help does not mention --temperature"
    exit 1
fi

# Test 9: launch-claude-agent.sh validates temperature
echo "Test 9: launch-claude-agent.sh rejects invalid temperature"
output=$("$REPO_ROOT/bin/launch-claude-agent.sh" --temperature 5.0 fake-alias 2>&1 || true)
if echo "$output" | grep -q "temperature must be a number"; then
    echo "✅ PASS: launch-claude-agent.sh rejects invalid temperature"
else
    echo "❌ FAIL: launch-claude-agent.sh does not reject invalid temperature"
    exit 1
fi

# Test 10: local-llm-hotswap.sh accepts --temperature
echo "Test 10: local-llm-hotswap.sh validates temperature"
output=$("$REPO_ROOT/bin/local-llm-hotswap.sh" --temperature 5.0 fake-alias 2>&1 || true)
if echo "$output" | grep -q "temperature must be a number"; then
    echo "✅ PASS: local-llm-hotswap.sh rejects invalid temperature"
else
    echo "❌ FAIL: local-llm-hotswap.sh does not reject invalid temperature"
    exit 1
fi

echo ""
echo "All tests passed! ✅"
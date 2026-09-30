#!/usr/bin/env bash
# tests/test_remote_autocompaction.sh — test LA_AUTO_COMPACT_WINDOW support in remote-session.sh

set -uo pipefail
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -P "$HERE/.." && pwd)"

PASS=0
FAIL=0

check() {
  if [ "$1" -eq 0 ]; then
    PASS=$((PASS + 1))
    printf '  PASS: %s\n' "$2"
  else
    FAIL=$((FAIL + 1))
    printf '  FAIL: %s\n' "$2"
  fi
}

TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/la-remote-auto-test.XXXXXX")"
trap 'rm -rf "$TEST_DIR"' EXIT INT TERM HUP

cat > "$TEST_DIR/claude" <<'STUBEOF'
#!/usr/bin/env bash
# Stub claude that writes its args to a file
printf '%s\n' "$@" > "$TEST_DIR/claude_args.txt"
exit 0
STUBEOF
chmod +x "$TEST_DIR/claude"

run_remote_session() {
  local env_var="$1"
  local env_val="$2"
  local args="$3"
  local output_file="$4"
  
  if [ -n "$env_var" ]; then
    env "$env_var=$env_val" PATH="$TEST_DIR:$PATH" "$REPO/bin/remote-session.sh" $args 2>&1 | tee "$output_file"
  else
    PATH="$TEST_DIR:$PATH" "$REPO/bin/remote-session.sh" $args 2>&1 | tee "$output_file"
  fi
}

echo "== Test 1: nemotron-ultra defaults to 1m autocompaction =="
output=$(run_remote_session "" "" "--dry-run nvidia-nemotron-ultra" "$TEST_DIR/out1")
if echo "$output" | grep -q "autocompact.*--autocompact 1m"; then
  check 0 "nemotron-ultra shows 1m in dry-run output"
else
  check 1 "nemotron-ultra does not show 1m in dry-run output"
  echo "Output: $output"
fi

echo "== Test 2: explicit LA_AUTO_COMPACT_WINDOW=200k overrides default =="
output=$(run_remote_session "LA_AUTO_COMPACT_WINDOW" "200k" "--dry-run nvidia-nemotron-ultra" "$TEST_DIR/out2")
if echo "$output" | grep -q "autocompact.*--autocompact 200k"; then
  check 0 "explicit 200k shown in dry-run output"
else
  check 1 "explicit 200k not shown in dry-run output"
  echo "Output: $output"
fi

echo "== Test 3: invalid LA_AUTO_COMPACT_WINDOW rejected =="
output=$(run_remote_session "LA_AUTO_COMPACT_WINDOW" "50k" "--dry-run nvidia-nemotron-ultra" "$TEST_DIR/out3" 2>&1)
if echo "$output" | grep -q "must be"; then
  check 0 "invalid 50k rejected"
else
  check 1 "invalid 50k not rejected"
  echo "Output: $output"
fi

echo "== Test 4: auto value accepted =="
output=$(run_remote_session "LA_AUTO_COMPACT_WINDOW" "auto" "--dry-run nvidia-nemotron-ultra" "$TEST_DIR/out4")
if echo "$output" | grep -q "autocompact.*--autocompact auto"; then
  check 0 "auto value accepted"
else
  check 1 "auto value not accepted"
  echo "Output: $output"
fi

echo "== Test 5: gemini-flash (no default) works without autocompaction =="
output=$(run_remote_session "" "" "--dry-run gemini-flash" "$TEST_DIR/out5")
if ! echo "$output" | grep -q "autocompact"; then
  check 0 "gemini-flash has no autocompact in dry-run output"
else
  check 1 "gemini-flash unexpectedly has autocompact"
  echo "Output: $output"
fi

echo "== Test 6: nvidia-laguna defaults to 1m =="
output=$(run_remote_session "" "" "--dry-run nvidia-laguna" "$TEST_DIR/out6")
if echo "$output" | grep -q "autocompact.*--autocompact 1m"; then
  check 0 "nvidia-laguna shows 1m in dry-run output"
else
  check 1 "nvidia-laguna does not show 1m in dry-run output"
  echo "Output: $output"
fi

echo "== Test 7: 100k value accepted (minimum) =="
output=$(run_remote_session "LA_AUTO_COMPACT_WINDOW" "100k" "--dry-run nvidia-nemotron-ultra" "$TEST_DIR/out7")
if echo "$output" | grep -q "autocompact.*--autocompact 100k"; then
  check 0 "100k value accepted"
else
  check 1 "100k value not accepted"
  echo "Output: $output"
fi

echo "== Test 8: 1m value accepted (maximum) =="
output=$(run_remote_session "LA_AUTO_COMPACT_WINDOW" "1m" "--dry-run nvidia-nemotron-ultra" "$TEST_DIR/out8")
if echo "$output" | grep -q "autocompact.*--autocompact 1m"; then
  check 0 "1m value accepted"
else
  check 1 "1m value not accepted"
  echo "Output: $output"
fi

echo "== Test 9: 2m value rejected (exceeds maximum) =="
output=$(run_remote_session "LA_AUTO_COMPACT_WINDOW" "2m" "--dry-run nvidia-nemotron-ultra" "$TEST_DIR/out9" 2>&1)
if echo "$output" | grep -q "must be"; then
  check 0 "2m value rejected"
else
  check 1 "2m value not rejected"
  echo "Output: $output"
fi

echo ""
printf '%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

#!/usr/bin/env bash
# tests/test_thinking_override_local.sh — the LOCAL lane's session thinking override
# (waypoint fa-thinking-toggle). The picker's remembered choice travels as
# LA_THINKING_OVERRIDE=true|false and must beat the alias's configured LA_THINK value on
# every consumer that resolves through config-lib's la_lookup — launcher, hotswap, RAM
# preflight and session identity all read LA_CUR_THINK, so ONE consumption point keeps them
# agreeing. The hotswap profile-mode branch gets a static pin (exercising it behaviorally
# would load a model); the dry-run display is behavioral.
#
# METHOD — sandbox, never the live machine (the test_serve_default.sh recipe): config-lib.sh
# is copied beside a GENERATED config.local.sh, weights are faked with a >1 MB file, and no
# server is started. The real (gitignored) config.local.sh is never read or written.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PASS=0; FAIL=0
assert_eq() {
  if [ "$1" = "$2" ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$3"
  else FAIL=$((FAIL+1)); printf '  FAIL: %s\n     expected: %s\n     actual:   %s\n' "$3" "$1" "$2"; fi
}
assert_grep() {
  if printf '%s' "$2" | grep -qF -- "$1"; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$3"
  else FAIL=$((FAIL+1)); printf '  FAIL: %s\n     needle: %s\n' "$3" "$1"; fi
}
assert_grep_file() {  # assert_grep_file <needle> <file> <label>
  if grep -qF -- "$1" "$2"; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$3"
  else FAIL=$((FAIL+1)); printf '  FAIL: %s\n     needle: %s\n     file:   %s\n' "$3" "$1" "$2"; fi
}

SB="$(mktemp -d)"
trap 'rm -rf "$SB"' EXIT
mkdir -p "$SB/config" "$SB/bin" "$SB/models/FakeWeights"
dd if=/dev/zero of="$SB/models/FakeWeights/weights.safetensors" bs=1m count=2 2>/dev/null
cp "$REPO/config/config-lib.sh" "$SB/config/config-lib.sh"
cp "$REPO/bin/local-session.sh" "$SB/bin/local-session.sh"
cp "$REPO/config/emoji.sh" "$SB/config/emoji.sh" 2>/dev/null || true

cat > "$SB/config/config.local.sh" <<CFG
LA_MODELS_DIR="$SB/models"
la_register think-on  FakeWeights rapid qwen3 qwen3 true  claude-opus-5 high
la_register think-off FakeWeights rapid qwen3 qwen3 false claude-opus-5 high
CFG

resolve() { # resolve <alias> [override] -> prints LA_CUR_THINK; stderr carried through
  local alias="$1" override="${2:-}"
  LA_THINKING_OVERRIDE="$override" bash -c '
    source "$1" >/dev/null 2>&1
    la_load_config >/dev/null 2>&1
    la_lookup "$2" >/dev/null || exit 3
    printf "%s" "$LA_CUR_THINK"
  ' resolver "$SB/config/config-lib.sh" "$alias" 2> "$SB/err.txt"
}

echo "la_lookup override resolution:"

# Defaults: no override, the alias's configured value stands (today's behavior).
assert_eq "true"  "$(resolve think-on)"  "no override: think-on alias keeps true"
assert_eq "false" "$(resolve think-off)" "no override: think-off alias keeps false"

# The override beats the alias config, in BOTH directions.
assert_eq "true"  "$(resolve think-on true)"   "override true: think-on stays true"
assert_eq "true"  "$(resolve think-off true)"  "override true: think-off flips to true"
assert_eq "false" "$(resolve think-on false)"  "override false: think-on flips to false"
assert_eq "false" "$(resolve think-off false)" "override false: think-off stays false"

# An invalid value is ignored LOUDLY: alias config stands, stderr names the variable.
OUT="$(resolve think-on yes)"; ERR="$(cat "$SB/err.txt")"
assert_eq "true" "$OUT" "invalid override: alias value stands"
assert_grep "LA_THINKING_OVERRIDE" "$ERR" "invalid override: warned on stderr"

echo "local-session.sh --dry-run display:"

DRY_OUT="$(LA_THINKING_OVERRIDE=true bash "$SB/bin/local-session.sh" --dry-run think-off 2>&1)"
assert_grep "Thinking: true" "$DRY_OUT" "dry-run prints the RESOLVED value under override"
assert_grep "LA_THINKING_OVERRIDE=true" "$DRY_OUT" "dry-run names the override"
DRY_OUT="$(bash "$SB/bin/local-session.sh" --dry-run think-off 2>&1)"
assert_grep "Thinking: false" "$DRY_OUT" "dry-run prints the alias value without override"

echo "hotswap profile-mode pin (static; a behavioral run would load a model):"
assert_grep_file 'LA_THINKING_OVERRIDE' "$REPO/bin/local-llm-hotswap.sh" \
  "hotswap consumes the override in profile mode too (profile THINK must not beat the session choice)"

echo "launch-claude-agent.sh banner pin (static):"
assert_grep_file 'LA_THINKING_OVERRIDE' "$REPO/bin/launch-claude-agent.sh" \
  "the launcher banner states the override"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

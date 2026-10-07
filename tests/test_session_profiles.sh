#!/usr/bin/env bash
# tests/test_session_profiles.sh — model-specific csl auto-compaction.
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

assert_eq() {
  if [ "$1" = "$2" ]; then
    check 0 "$3"
  else
    printf '     expected: %s\n     actual:   %s\n' "$1" "$2"
    check 1 "$3"
  fi
}

SB="$(mktemp -d "${TMPDIR:-/tmp}/la-session-profile-test.XXXXXX")"
SB="$(cd -P "$SB" && pwd -P)"
trap 'rm -rf "$SB"' EXIT INT TERM HUP

# Fixture roster as variables: every expectation below is derived from these, so renaming a
# fixture model or changing the override value cannot leave a stale literal behind.
PROFILED_ALIAS=ornith-1.5-35b
PROFILED_DIR=Ornith
PROFILED_WINDOW=500k   # deliberately NOT what any fixture manifest derives, so "explicit wins" is observable
PLAIN_ALIAS=alpha
PLAIN_DIR=Alpha
DEFAULT_EFFORT=high
CUSTOM_EFFORT=max

. "$REPO/tests/lib/sandbox.sh"
la_test_sandbox "$SB" || { echo "sandbox build failed"; exit 1; }
mkdir -p "$SB/home/.models/$PLAIN_DIR" "$SB/home/.models/$PROFILED_DIR"
head -c 2097152 /dev/zero > "$SB/home/.models/$PLAIN_DIR/weights.bin"
head -c 2097152 /dev/zero > "$SB/home/.models/$PROFILED_DIR/weights.bin"

cat > "$SB/config/config.local.sh" <<CFG
LA_MODELS_DIR="\$HOME/.models"
LA_RAPID_BIN=/nonexistent/rapid-mlx

la_register $PLAIN_ALIAS $PLAIN_DIR rapid qwen "" false claude-opus-5 $DEFAULT_EFFORT
la_register $PROFILED_ALIAS $PROFILED_DIR rapid hermes "" false claude-opus-5 $DEFAULT_EFFORT

LA_SESSION_AUTO_COMPACT["$PROFILED_ALIAS"]=$PROFILED_WINDOW
CFG

cat > "$SB/bin/stub-launcher" <<'STUB'
#!/usr/bin/env bash
printf '%s|%s|%s\n' \
  "${1:-}" \
  "${2:-}" \
  "${LA_AUTO_COMPACT_WINDOW:-}" > "$CSL_TEST_RESULT"
STUB
chmod +x "$SB/bin/stub-launcher"

run_csl() {
  local result="$1"
  shift
  env -u LA_AUTO_COMPACT_WINDOW \
    HOME="$SB/home" \
    CSL_LAUNCHER="$SB/bin/stub-launcher" \
    CSL_TEST_RESULT="$result" \
    bash "$SB/bin/csl" "$@" </dev/null 2>&1
}

# The interactive picker (Textual, bin/session_picker.py) starts a local session by running
# `csl --picker-launch <alias> <effort>` as its child — that is the seam where csl applies the
# selected model's profile, so it is what these checks drive. The picker's own key handling is
# covered by tests/test_session_picker_*.py.
echo "== picker-launched $PROFILED_ALIAS applies $PROFILED_WINDOW =="
run_csl "$SB/profiled-picker" --picker-launch "$PROFILED_ALIAS" "$DEFAULT_EFFORT" >/dev/null
assert_eq \
  "$PROFILED_ALIAS|$DEFAULT_EFFORT|$PROFILED_WINDOW" \
  "$(cat "$SB/profiled-picker" 2>/dev/null)" \
  "picker-launched $PROFILED_ALIAS receives $PROFILED_WINDOW"

echo "== direct csl $PROFILED_ALIAS launch applies $PROFILED_WINDOW =="
run_csl "$SB/profiled-direct" "$PROFILED_ALIAS" "$DEFAULT_EFFORT" >/dev/null
assert_eq \
  "$PROFILED_ALIAS|$DEFAULT_EFFORT|$PROFILED_WINDOW" \
  "$(cat "$SB/profiled-direct" 2>/dev/null)" \
  "direct csl $PROFILED_ALIAS receives $PROFILED_WINDOW"

echo "== unprofiled model receives no override =="
run_csl "$SB/plain" --picker-launch "$PLAIN_ALIAS" "$DEFAULT_EFFORT" >/dev/null
assert_eq \
  "$PLAIN_ALIAS|$DEFAULT_EFFORT|" \
  "$(cat "$SB/plain" 2>/dev/null)" \
  "$PLAIN_ALIAS (no profile) receives no auto-compaction override"

echo "== a custom effort keeps the selected model's profile =="
run_csl "$SB/profiled-custom" --picker-launch "$PROFILED_ALIAS" "$CUSTOM_EFFORT" >/dev/null
assert_eq \
  "$PROFILED_ALIAS|$CUSTOM_EFFORT|$PROFILED_WINDOW" \
  "$(cat "$SB/profiled-custom" 2>/dev/null)" \
  "custom-effort $PROFILED_ALIAS still receives $PROFILED_WINDOW"

echo "== the override is scoped to the child, never the caller's shell =="
LA_AUTO_COMPACT_WINDOW= run_csl "$SB/scoped" --picker-launch "$PROFILED_ALIAS" "$DEFAULT_EFFORT" >/dev/null
assert_eq "" "${LA_AUTO_COMPACT_WINDOW:-}" "the test shell still has no LA_AUTO_COMPACT_WINDOW afterwards"

echo "== manifest-driven derivation (precedence: explicit > manifest > LA_MAX_MODEL_LEN) =="
# Expectations are DERIVED from the shared manifest fixtures, never typed in: the floor-to-100k rule
# is applied to the fixture's own min(context limits), so editing a fixture keeps this honest.
MANIFESTS="$REPO/tests/fixtures/model-manifests"
expected_autocompact() {  # <manifest.json> -> floor(min(limits), 100k) capped at 1M, or "" below 100k
  python3 - "$1" <<'PY_EXPECT'
import json, sys
d = json.load(open(sys.argv[1]))
caps, rq = d.get("capabilities") or {}, d.get("runtime_qualification") or {}
lims = [v for v in (caps.get("configured_context_tokens"), caps.get("native_context_tokens"),
                    caps.get("extended_context_tokens"), rq.get("server_context_tokens"),
                    rq.get("tested_safe_context_tokens")) if isinstance(v, int)]
eff = min(lims) if lims else 0
print(min(eff // 100000 * 100000, 1000000) if eff >= 100000 else "")
PY_EXPECT
}
add_manifest_model() {  # <alias> <dir> <manifest.json>
  mkdir -p "$SB/home/.models/$2"
  head -c 2097152 /dev/zero > "$SB/home/.models/$2/weights.bin"
  cp "$3" "$SB/home/.models/$2/.local-model-manifest.json"
  printf 'la_register %s %s rapid qwen "" false claude-opus-5 %s\n' "$1" "$2" "$DEFAULT_EFFORT" >> "$SB/config/config.local.sh"
}
LONG_MANIFEST="$MANIFESTS/valid-model-262144.json"
SHORT_MANIFEST="$MANIFESTS/valid-sub-100k-no-autocompact.json"
add_manifest_model long-ctx LongCtx "$LONG_MANIFEST"
add_manifest_model short-ctx ShortCtx "$SHORT_MANIFEST"
# A profiled model WITH a manifest: the explicit LA_SESSION_AUTO_COMPACT entry must still win.
cp "$LONG_MANIFEST" "$SB/home/.models/$PROFILED_DIR/.local-model-manifest.json"

LONG_EXPECT="$(expected_autocompact "$LONG_MANIFEST")"
SHORT_EXPECT="$(expected_autocompact "$SHORT_MANIFEST")"
[ -n "$LONG_EXPECT" ] && [ "$LONG_EXPECT" != "$PROFILED_WINDOW" ]
check $? "fixture sanity: the long manifest derives a window ($LONG_EXPECT) distinct from the explicit $PROFILED_WINDOW"

run_csl "$SB/long" --picker-launch long-ctx "$DEFAULT_EFFORT" >/dev/null
assert_eq "long-ctx|$DEFAULT_EFFORT|$LONG_EXPECT" "$(cat "$SB/long" 2>/dev/null)" \
  "a manifest with >=100k context derives the floored window"
run_csl "$SB/short" --picker-launch short-ctx "$DEFAULT_EFFORT" >/dev/null
assert_eq "short-ctx|$DEFAULT_EFFORT|$SHORT_EXPECT" "$(cat "$SB/short" 2>/dev/null)" \
  "a manifest below 100k derives NO window (and the 32k default fallback adds none)"
run_csl "$SB/explicit-wins" --picker-launch "$PROFILED_ALIAS" "$DEFAULT_EFFORT" >/dev/null
assert_eq "$PROFILED_ALIAS|$DEFAULT_EFFORT|$PROFILED_WINDOW" "$(cat "$SB/explicit-wins" 2>/dev/null)" \
  "an explicit LA_SESSION_AUTO_COMPACT entry beats the model's manifest"

echo "== LA_MAX_MODEL_LEN fallback when no manifest and no override =="
FALLBACK_LEN=345678
FALLBACK_EXPECT=$(( FALLBACK_LEN / 100000 * 100000 ))
printf 'LA_MAX_MODEL_LEN=%s\n' "$FALLBACK_LEN" >> "$SB/config/config.local.sh"
run_csl "$SB/fallback" --picker-launch "$PLAIN_ALIAS" "$DEFAULT_EFFORT" >/dev/null
assert_eq "$PLAIN_ALIAS|$DEFAULT_EFFORT|$FALLBACK_EXPECT" "$(cat "$SB/fallback" 2>/dev/null)" \
  "with no manifest, LA_MAX_MODEL_LEN=$FALLBACK_LEN floors to $FALLBACK_EXPECT"
run_csl "$SB/manifest-over-fallback" --picker-launch long-ctx "$DEFAULT_EFFORT" >/dev/null
assert_eq "long-ctx|$DEFAULT_EFFORT|$LONG_EXPECT" "$(cat "$SB/manifest-over-fallback" 2>/dev/null)" \
  "a manifest still beats the LA_MAX_MODEL_LEN fallback"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

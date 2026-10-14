#!/usr/bin/env bash
# test_mod_attach.sh — the session-identity mod is attached to free sessions only, the
# launchers hand it the model and word pool, and the local launcher folds every settings
# layer into ONE --settings (Claude Code keeps only the last one; measured on 2.1.296).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0
check() { if [ "$1" = "$2" ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); echo "FAIL: $3 (got '$1', want '$2')"; fi; }

attach() { env -i HOME="$HOME" PATH=/usr/bin:/bin "$@" bash "$ROOT/bin/la-mod-attach.sh"; }
MOD="$(cd -P "$ROOT/mods/session-identity" && pwd)"

check "$(attach LA_MOD_CLAUDE_VERSION=2.1.296)" "" "no session kind (gateway) never attaches"
check "$(attach LA_SESSION_KIND=cloud LA_MOD_CLAUDE_VERSION=2.1.296)" "" "a cloud session never attaches"
check "$(attach LA_SESSION_KIND=local LA_MOD_CLAUDE_VERSION=2.1.296)" "$MOD" "a local session attaches at the minimum version"
check "$(attach LA_SESSION_KIND=free_api LA_MOD_CLAUDE_VERSION=2.2.0)" "$MOD" "a free-API session attaches on a newer minor"
check "$(attach LA_SESSION_KIND=free_api 'LA_MOD_CLAUDE_VERSION=2.1.296 (Claude Code)')" "$MOD" "the version banner's suffix is ignored"
check "$(attach LA_SESSION_KIND=local LA_MOD_CLAUDE_VERSION=2.1.250)" "" "an older CLI (no mods API) never attaches"
check "$(attach LA_SESSION_KIND=local LA_MOD_CLAUDE_VERSION=garbage)" "" "an unparseable version never attaches"
check "$(attach LA_SESSION_KIND=local LA_MOD_CLAUDE_VERSION=2.1.296 LA_MOD_DISABLE=1)" "" "LA_MOD_DISABLE=1 is honoured"
bash "$ROOT/bin/la-mod-attach.sh" --bogus >/dev/null 2>&1; check "$?" "2" "an unknown flag exits 2 instead of running"

# Both launchers call the helper, export the word pool, and add --plugin-dir from its output.
for L in launch-claude-agent.sh remote-session.sh; do
  src="$ROOT/bin/$L"
  check "$(grep -c 'la-mod-attach.sh' "$src" | tr -d ' ')" "1" "$L calls la-mod-attach.sh once"
  check "$(grep -c -- '--plugin-dir "\$LA_MOD_DIR"' "$src" | tr -d ' ')" "1" "$L adds --plugin-dir from the helper"
  check "$(grep -c 'export LA_MODEL_SPINNER_WORDS' "$src" | tr -d ' ')" "1" "$L exports the word pool"
  check "$(grep -c 'export LA_MOD_ATTACHED' "$src" | tr -d ' ')" "1" "$L tells the generator the mod is attached"
done

# The local launcher passes --settings exactly once (the merged file), never once per layer.
check "$(grep -c -- '(--settings ' "$ROOT/bin/launch-claude-agent.sh" | tr -d ' ')" "1" \
      "launch-claude-agent.sh appends --settings at exactly one site"

# merge-settings keeps blind-trust's deny list and the overlay's theme + spinner together.
T="$(mktemp -d)"
printf '{"permissions":{"defaultMode":"bypassPermissions","deny":["Bash(rm -rf:*)"]}}' > "$T/base.json"
printf '{"theme":"custom:local-sky","spinnerVerbs":{"mode":"append","verbs":["Qwening"]},"permissions":{"deny":[]}}' > "$T/over.json"
merged="$(python3 "$ROOT/bin/merge-settings.py" --base "$T/base.json" --overlay "$T/over.json")"
check "$(printf '%s' "$merged" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["theme"], d["spinnerVerbs"]["mode"], d["permissions"]["defaultMode"], len(d["permissions"]["deny"]))')" \
      "custom:local-sky append bypassPermissions 1" "merged settings keep theme, spinner, bypass mode and the deny list"
rm -f "$T/base.json" "$T/over.json"; rmdir "$T"

echo
echo "=== Summary ==="
echo "Pass: $PASS  Fail: $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
echo "mod attach: PASS"

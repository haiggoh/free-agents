#!/usr/bin/env bash
# tests/test_rtc_hooks.sh — the .rtc/ hooks that rtc-ship.sh runs, tested in both directions.
#
# These hooks hold the free-agents-only ship steps extracted from the old ~/.claude/scripts/
# fa-ship.sh. Two of them carry gates, and a gate that has never been observed to fail is
# indistinguishable from a decorative one — so each is run against a scratch directory with stub
# interpreters and a stub published manifest. NOTHING here touches the real venvs, the real
# dist/, or the real plugin cache: paths come from env overrides into a mktemp dir.
#
# Usage: bash tests/test_rtc_hooks.sh
set -uo pipefail
export LC_ALL=C
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -P "$HERE/.." && pwd)"
pass=0; fail=0
ok()  { pass=$((pass+1)); printf '  ok   %s\n' "$1"; }
bad() { fail=$((fail+1)); printf '  FAIL %s\n     expected: %s\n     actual:   %s\n' "$1" "$2" "$3"; }
is()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "$2" "$3"; fi; }
has() { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "contains: $3" "$2" ;; esac; }

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# --- the hooks exist and are executable: rtc-ship.sh gates on [ -x ], so a non-executable hook is
#     a silently skipped gate, which is the failure this whole extraction is meant to remove.
for h in pre-flight pre-package post-release; do
  if [ -f "$ROOT/.rtc/$h.sh" ]; then r=0; else r=1; fi
  is "$h.sh exists" "0" "$r"
  if [ -x "$ROOT/.rtc/$h.sh" ]; then r=0; else r=1; fi
  is "$h.sh is executable (rtc-ship.sh gates on -x)" "0" "$r"
done

# --- pre-flight: a MISSING venv must FAIL. fa-ship.sh warned and succeeded here, i.e. it reported
#     a green gate while covering nothing — the exact shape rtc-ship.sh exists to prevent.
export FA_LITELLM_PY="$TMP/no-such-litellm-python"
export FA_PICKER_PY="$TMP/no-such-picker-python"
out=$("$ROOT/.rtc/pre-flight.sh" 2>&1); rc=$?
is "pre-flight FAILS when a venv is missing" "1" "$rc"
has "and names the missing interpreter" "$out" "no-such-litellm-python"
has "and says the gate would not run" "$out" "would NOT run"
is "and does not claim the gates passed" "0" "$(printf '%s' "$out" | grep -c 'gate is NOT run')"

# The documented escape hatch: knowingly shipping without a gate is a choice, not a default.
out=$(FA_ALLOW_MISSING_VENVS=1 "$ROOT/.rtc/pre-flight.sh" 2>&1); rc=$?
is "FA_ALLOW_MISSING_VENVS=1 keeps the old warning behaviour" "0" "$rc"
has "and it still SAYS the gate was not run" "$out" "gate is NOT run"

# --- pre-flight with STUB interpreters: the wiring, without two real venvs. A stub that "passes"
#     proves the hook reaches pytest with the right files; a stub that fails proves it can fail.
stub="$TMP/stub-python"; cat > "$stub" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" >> "$ARGS_FILE"
# mimic pytest's summary line so the hook's own grep has something to read
echo "3 passed in 0.01s"
exit "${STUB_RC:-0}"
STUB
chmod +x "$stub"
ARGS_FILE="$TMP/args" FA_LITELLM_PY="$stub" FA_PICKER_PY="$stub" \
  "$ROOT/.rtc/pre-flight.sh" >"$TMP/pre-ok" 2>&1
is "pre-flight passes when both gates pass" "0" "$?"
has "and reports the suite it ran" "$(cat "$TMP/pre-ok")" "litellm:"
has "and the picker suite too" "$(cat "$TMP/pre-ok")" "picker:"
args=$(cat "$TMP/args")
has "the LiteLLM gate gets its three contract files" "$args" "tests/test_litellm_contract.py"
has "the LiteLLM gate gets the anthropic route test" "$args" "tests/test_anthropic_route.py"
has "the picker gate gets the pty pilot" "$args" "tests/test_session_picker_pty.py"

ARGS_FILE="$TMP/args2" STUB_RC=1 FA_LITELLM_PY="$stub" FA_PICKER_PY="$stub" \
  "$ROOT/.rtc/pre-flight.sh" >"$TMP/pre-bad" 2>&1
is "pre-flight FAILS when a gate fails" "1" "$?"
has "and says the gate failed" "$(cat "$TMP/pre-bad")" "FAILED"

# --- pre-package: it must confirm the assets exist before rtc-ship.sh globs them. Run in a scratch
#     copy with a stub package-release.sh, so no real tarball is built.
PD="$TMP/pkg"; mkdir -p "$PD/scripts" "$PD/dist"
printf '0.99.0\n' > "$PD/VERSION"
printf '#!/usr/bin/env bash\nexit 0\n' > "$PD/scripts/package-release.sh"   # builds nothing
printf '#!/usr/bin/env bash\necho install > "$1"\n' > "$PD/scripts/install-free-agents.sh"
chmod +x "$PD/scripts/package-release.sh" "$PD/scripts/install-free-agents.sh"
for d in bin config install skills hooks tests; do mkdir -p "$PD/$d"; done
cp -p "$ROOT/.rtc/pre-package.sh" "$PD/.rtc-pre-package.sh"
out=$(cd "$PD" && VERSION=0.99.0 bash ./.rtc-pre-package.sh 2>&1); rc=$?
is "pre-package FAILS when the tarball was not built" "1" "$rc"
has "and names the missing asset with its version" "$out" "dist/free-agents-0.99.0.tar.gz"

# ...and succeeds when the stub builds exactly what rtc-ship.json declares.
printf '#!/usr/bin/env bash\necho tarball > "dist/free-agents-0.99.0.tar.gz"\n' > "$PD/scripts/package-release.sh"
chmod +x "$PD/scripts/package-release.sh"
out=$(cd "$PD" && bash ./.rtc-pre-package.sh 2>&1); rc=$?
is "pre-package passes when the assets exist" "0" "$rc"
has "and reports how many it built" "$out" "2 assets"
n=0; for f in "$PD/dist"/install-free-agents*; do [ -f "$f" ] && n=$((n+1)); done
is "and produced ONE installer (the .command name is no longer shipped)" "1" "$n"
is "and no legacy .command asset survives" "0" "$([ -f "$PD/dist/install-free-agents.command" ] && echo 1 || echo 0)"

# --- post-release: the installed-copy check is FATAL, and it must speak on STDERR because
#     rtc-ship.sh discards the hook's stdout.
fake_cache() {  # fake_cache <dir> <version>
  rm -rf "$1/cache"
  mkdir -p "$1/cache/haiggoh/free-agents/$2/.claude-plugin"
  printf '{\n  "version": "%s"\n}\n' "$2" > "$1/cache/haiggoh/free-agents/$2/.claude-plugin/plugin.json"
}
CD="$TMP/post"; mkdir -p "$CD"; printf '0.99.0\n' > "$CD/VERSION"
cp -p "$ROOT/.rtc/post-release.sh" "$CD/.rtc-post-release.sh"
fake_cache "$CD" 0.99.0
out=$(cd "$CD" && RTC_PLUGIN_CACHE="$CD/cache" FA_SKIP_BREW=1 bash ./.rtc-post-release.sh 2>/dev/null); rc=$?
is "post-release passes when the installed copy is at VERSION" "0" "$rc"
is "and writes its report to STDERR (rtc-ship.sh discards stdout)" "0" "$(printf '%s' "$out" | grep -c 'installed copy')"

fake_cache "$CD" 0.98.0
out=$(cd "$CD" && RTC_PLUGIN_CACHE="$CD/cache" FA_SKIP_BREW=1 bash ./.rtc-post-release.sh 2>&1 >/dev/null); rc=$?
is "post-release FAILS when the installed copy is stale" "1" "$rc"
has "and names both versions" "$out" "0.98.0"
has "and gives the retry command" "$out" "get-haiggoh apply --only free-agents"

fake_cache "$CD" ""
is "post-release FAILS when there is no installed copy at all" \
   "1" "$(cd "$CD" && RTC_PLUGIN_CACHE="$CD/cache" FA_SKIP_BREW=1 bash ./.rtc-post-release.sh >/dev/null 2>&1; echo $?)"

# --- rtc-ship.json: the DATA half. A typo here is a silently dropped asset or a missed surface.
if [ -f "$ROOT/rtc-ship.json" ]; then r=0; else r=1; fi
is "rtc-ship.json exists" "0" "$r"
cfg() { python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],"") or "")' "$ROOT/rtc-ship.json" "$1"; }
is "the tag prefix is v (0.40.0+ tags are v-prefixed)" "v" "$(cfg tag_prefix)"
is "it ships the tarball" "dist/free-agents-*.tar.gz" "$(cfg assets | tr ' ' '\n' | grep tar.gz)"
is "it ships ONE installer name (the .command superseded since 0.29.3)" "1" "$(cfg assets | tr ' ' '\n' | grep -c install-free-agents)"
is "and that is 2 assets in total" "2" "$(cfg assets | tr ' ' '\n' | grep -c .)"
is "with no .command asset left in the config" "0" "$(cfg assets | tr ' ' '\n' | grep -c '\.command')"
is "it refreshes the installed plugin" "free-agents" "$(cfg refresh_plugin)"

printf '\npassed %d, failed %d\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

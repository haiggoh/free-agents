#!/usr/bin/env bash
# test_settings_tempfiles.sh — launchers must get a FRESH settings temp file on every launch,
# and the remote settings generator must be executable (both gate the theme overlay).
#
# 0.34.3 regressions: BSD mktemp only randomises TRAILING Xs, so "name.XXXXXX.json" created
# one literal file and every later launch failed "File exists" — the theme silently vanished.
# And generate-remote-settings.py lost +x in 41d4698, so the `[ -x ]` gate skipped the remote
# theme entirely. Usage: bash tests/test_settings_tempfiles.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  PASS: $1"; }
bad() { fail=$((fail+1)); echo "  FAIL: $1"; }

# 1. No launcher keeps a template whose Xs are not at the end.
hits=$(grep -n 'mktemp[^|]*XXXXXX\.[a-z]' "$ROOT"/bin/*.sh 2>/dev/null || true)
[ -z "$hits" ] && ok "no mid-name mktemp templates in bin/*.sh" || bad "mid-name mktemp template: $hits"

# 2. The helper makes distinct .json files, twice in a row, mode 600.
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
eval "$(sed -n '/^la_mktemp_json() {/,/^}/p' "$ROOT/bin/remote-session.sh")"
a="$(la_mktemp_json "$T/s")"; b="$(la_mktemp_json "$T/s")"
[ -n "$a" ] && [ -n "$b" ] && [ "$a" != "$b" ] && ok "two calls give two files" || bad "la_mktemp_json not unique ($a / $b)"
case "$a" in *.json) ok "ends in .json";; *) bad "no .json suffix: $a";; esac
[ "$(stat -f %Lp "$a" 2>/dev/null || stat -c %a "$a")" = "600" ] && ok "mode 600" || bad "mode not 600"

# 2b. Each launcher defines the helper itself (inlined).
for l in launch-claude-agent.sh launch-claude-agent-rapid-auto.sh remote-session.sh; do
    grep -q "^la_mktemp_json() {" "$ROOT/bin/$l" && ok "$l defines la_mktemp_json" || bad "$l lacks la_mktemp_json"
done

# 3. Every generator a launcher gates on `[ -x ]` is executable in the tree.
for g in generate-remote-settings.py generate-local-settings.py merge-settings.py; do
    [ -x "$ROOT/bin/$g" ] && ok "$g is executable" || bad "$g is NOT executable (launcher -x gate skips it)"
done

echo "=== Summary ==="; echo "Pass: $pass  Fail: $fail"
[ "$fail" -eq 0 ]

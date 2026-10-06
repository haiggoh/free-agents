#!/usr/bin/env bash
# Tests for rtk handling: bin/rtk-lane-shim/rtk (free-API lane turns rtk's hook rewrites off),
# install/setup-rtk-excludes.sh (merges config/rtk/exclude-commands.txt into rtk's config), and
# the remote-session.sh wiring. A fake rtk stands in for the real one, so the suite needs no rtk
# installed and never reads or writes the user's rtk config.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SHIM_DIR="$ROOT/bin/rtk-lane-shim"
SETUP="$ROOT/install/setup-rtk-excludes.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL  %s -- %s\n' "$1" "$2"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$3] got [$2]"; fi; }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
mkdir -p "$T/fake" "$T/empty"
# Fake rtk: answers a hook call with a rewrite decision, anything else with its args.
cat > "$T/fake/rtk" <<'EOF'
#!/bin/sh
if [ "$1" = hook ]; then cat >/dev/null; echo '{"hookSpecificOutput":{"updatedInput":{"command":"rtk git status"}}}'; exit 0; fi
echo "REAL $*"
EOF
chmod +x "$T/fake/rtk"
PAYLOAD='{"tool_name":"Bash","tool_input":{"command":"git status"}}'
BASE="/usr/bin:/bin"

echo "rtk lane shim"
out="$(echo "$PAYLOAD" | LA_RTK_HOOK=off PATH="$SHIM_DIR:$T/fake:$BASE" rtk hook claude)"; rc=$?
check "LA_RTK_HOOK=off: hook call emits no decision (no rewrite)" "$out" ""
check "LA_RTK_HOOK=off: hook call exits 0" "$rc" "0"
out="$(echo "$PAYLOAD" | PATH="$SHIM_DIR:$T/fake:$BASE" rtk hook claude)"
case "$out" in *updatedInput*) ok "without LA_RTK_HOOK=off the hook reaches the real rtk" ;;
  *) bad "without LA_RTK_HOOK=off the hook reaches the real rtk" "got [$out]" ;; esac
out="$(LA_RTK_HOOK=off PATH="$SHIM_DIR:$T/fake:$BASE" rtk gain --daily)"
check "non-hook calls pass through to the real rtk with their args" "$out" "REAL gain --daily"
out="$(echo "$PAYLOAD" | LA_RTK_HOOK=off PATH="$SHIM_DIR:$T/empty:$BASE" rtk hook claude)"; rc=$?
check "rtk NOT installed: hook call is a silent no-op" "$out|$rc" "|0"
out="$(echo "$PAYLOAD" | PATH="$SHIM_DIR:$T/empty:$BASE" rtk hook claude)"; rc=$?
check "rtk NOT installed, hook not switched off: still never fails the tool call" "$out|$rc" "|0"
PATH="$SHIM_DIR:$T/empty:$BASE" rtk gain >/dev/null 2>&1; rc=$?
check "rtk NOT installed: other calls fail like a missing command (127)" "$rc" "127"
out="$(PATH="$SHIM_DIR:$SHIM_DIR:$T/fake:$BASE" rtk x)"
check "shim skips itself when listed twice on PATH (no exec loop)" "$out" "REAL x"

echo "remote-session.sh wiring"
RS="$ROOT/bin/remote-session.sh"
# The export must sit on the free_api branch and before claude is executed.
block="$(awk '/rtk OFF for free-API sessions/{f=1} f&&/^fi$/{print; exit} f' "$RS")"
[ -n "$block" ] || bad "rtk block found in remote-session.sh" "not found"
# OUTCOME, not text: run the block itself and read the environment it leaves behind.
# shellcheck disable=SC2016  # the probe expands inside the child shell, on purpose
PROBE='printf "%s|%s" "${LA_RTK_HOOK:-}" "${PATH%%:*}"'
run_block() { env -i PATH="$BASE" "$@" SCRIPT_DIR="$ROOT/bin" bash -c "$block
$PROBE"; }
check "free_api: LA_RTK_HOOK=off and the shim first on PATH" "$(run_block LA_SESSION_KIND=free_api)" "off|$SHIM_DIR"
check "local/cloud sessions are untouched" "$(run_block LA_SESSION_KIND=local)" "|/usr/bin"
check "LA_RTK_IN_FREE_API=1 keeps rtk on" "$(run_block LA_SESSION_KIND=free_api LA_RTK_IN_FREE_API=1)" "|/usr/bin"
l_set="$(grep -n 'export LA_RTK_HOOK=off' "$RS" | head -1 | cut -d: -f1)"
l_run="$(grep -n '"\${claude_cmd\[@\]}" )' "$RS" | tail -1 | cut -d: -f1)"
if [ -n "$l_set" ] && [ -n "$l_run" ] && [ "$l_set" -lt "$l_run" ]; then ok "the switch is set before claude runs"
else bad "the switch is set before claude runs" "set=$l_set run=$l_run"; fi
grep -q 'LA_RTK_IN_FREE_API' "$RS" && ok "LA_RTK_IN_FREE_API opt-out is wired and documented" \
  || bad "LA_RTK_IN_FREE_API opt-out is wired and documented" "missing"

echo "setup-rtk-excludes.sh"
out="$("$SETUP" --help)"; case "$out" in *Usage*) ok "--help prints usage" ;; *) bad "--help prints usage" "$out" ;; esac
"$SETUP" --bogus >/dev/null 2>&1; check "unknown flag exits 2" "$?" "2"
LA_RTK_BIN="$T/empty/rtk" LA_RTK_CONFIG="$T/none.toml" "$SETUP" >/dev/null; rc=$?
check "rtk not installed: exit 0 and no config written" "$rc|$([ -e "$T/none.toml" ] && echo y || echo n)" "0|n"
printf '[tracking]\nenabled = true\n\n[hooks]\nexclude_commands = [\n  "curl",\n]\n\n[limits]\ngrep_max_results = 200\n' > "$T/c.toml"
chmod 600 "$T/c.toml"
LA_RTK_BIN="$T/fake/rtk" LA_RTK_CONFIG="$T/c.toml" "$SETUP" --check >/dev/null; check "--check reports missing patterns (exit 1)" "$?" "1"
LA_RTK_BIN="$T/fake/rtk" LA_RTK_CONFIG="$T/c.toml" "$SETUP" >/dev/null
want="$(grep -v '^#' "$ROOT/config/rtk/exclude-commands.txt" | grep -c .)"
got="$(python3 -c 'import re,sys; s=open(sys.argv[1]).read(); print(len(re.findall(r"\"[^\"]*\"", re.search(r"exclude_commands = \[(.*)\]", s).group(1))))' "$T/c.toml")"
check "merged list keeps the user's pattern and adds every shipped one" "$got" "$((want + 1))"
grep -q '"curl"' "$T/c.toml" && grep -q '^grep_max_results = 200' "$T/c.toml" && grep -q '^\[tracking\]' "$T/c.toml" \
  && ok "other tables and the user's own pattern survive" || bad "other tables and the user's own pattern survive" "$(cat "$T/c.toml")"
check "file mode is preserved (600)" "$(stat -f '%Lp' "$T/c.toml" 2>/dev/null || stat -c '%a' "$T/c.toml")" "600"
ls "$T"/c.toml.bak-* >/dev/null 2>&1 && ok "a timestamped backup was taken" || bad "a timestamped backup was taken" "none"
before="$(cat "$T/c.toml")"
LA_RTK_BIN="$T/fake/rtk" LA_RTK_CONFIG="$T/c.toml" "$SETUP" >/dev/null
check "second run is a no-op" "$(cat "$T/c.toml")" "$before"
LA_RTK_BIN="$T/fake/rtk" LA_RTK_CONFIG="$T/c.toml" "$SETUP" --check >/dev/null; check "--check passes once merged" "$?" "0"
if command -v rtk >/dev/null 2>&1 && rtk hook check 'git status' >/dev/null 2>&1; then
  # Real rtk present: confirm it honours what we write (it reads its config from a fixed path,
  # so this only reads the user's config, never writes it).
  case "$(rtk hook check 'git show HEAD:README.md' 2>&1)" in
    *"No rewrite"*) ok "installed rtk does not rewrite git show (excludes active)" ;;
    *) printf 'INFO  installed rtk still rewrites git show — run install/setup-rtk-excludes.sh\n' ;;
  esac
fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

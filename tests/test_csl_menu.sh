#!/usr/bin/env bash
# tests/test_csl_menu.sh — deterministic coverage for csl, the session launcher front-end.
#
# Uses a sandboxed HOME, a sandboxed copy of the runtime (tests/lib/sandbox.sh), fake model
# files, and stub launchers. It never reads private configuration, starts a server, opens a
# watcher, or touches a live model.
#
# What csl IS since 0.22 (the Textual picker replaced the numbered home/local menus):
#   * `csl` / `csl local` / `csl lowkey` / `csl download-models` exec bin/session-picker, whose
#     keys and screens are covered by tests/test_session_picker_*.py — NOT here.
#   * `csl --inventory` is the picker's model list; `csl --picker-launch <alias> <effort>` is how
#     the picker starts a local session (csl applies the profile, watcher and toggles there).
#   * `csl --dry-run` prints the resolved roster and toggle defaults without launching.
#   * `csl remote` still opens remote-session.sh's numbered menu as a csl-owned child; that
#     menu is the one interactive text UI left, so its navigation and filter are tested here.
#
# Every expectation is derived from the fixture variables below; rename a fixture model and the
# assertions follow.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -P "$HERE/.." && pwd)"

case "${1:-}" in
  -h|--help) sed -n '2,/^set -uo/{/^set -uo/d;s/^# \{0,1\}//;p;}' "$0"; exit 0 ;;
  "") ;;
  *) echo "usage: tests/test_csl_menu.sh [--help]" >&2; exit 2 ;;
esac

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

assert_grep()    { printf '%s' "$2" | grep -qF -- "$1"; check $? "$3"; }
assert_no_grep() { if printf '%s' "$2" | grep -qF -- "$1"; then check 1 "$3"; else check 0 "$3"; fi; }
# Regex match for cosmetic text (labels, emoji) that may be reworded without changing behaviour.
assert_grep_flexible() { printf '%s' "$2" | grep -qE -- "$1"; check $? "$3"; }
assert_eq() {
  if [ "$1" = "$2" ]; then check 0 "$3"
  else printf '     expected: %s\n     actual:   %s\n' "$1" "$2"; check 1 "$3"; fi
}

SB="$(mktemp -d "${TMPDIR:-/tmp}/la-csl-menu-test.XXXXXX")"
SB="$(cd -P "$SB" && pwd -P)"
trap 'rm -rf "$SB"' EXIT INT TERM HUP

# --- fixture roster ------------------------------------------------------------------------
# name|dir|serve|thinking|effort|role   (role "" = untagged). ON-disk rows get a weight file.
SESSION_RAPID=alpha;    SESSION_RAPID_DIR=ModelAlpha;   SESSION_RAPID_EFFORT=high;   SESSION_RAPID_ROLE=operator
SESSION_VLLM=beta;      SESSION_VLLM_DIR=ModelBeta;     SESSION_VLLM_EFFORT=medium;  SESSION_VLLM_ROLE=reasoner
ABSENT=absent;          ABSENT_DIR=ModelAbsent
DISPATCH_ONLY=dispatch-only; DISPATCH_ONLY_DIR=DispatchOnly
ON_DISK_SESSION_MODELS=("$SESSION_RAPID" "$SESSION_VLLM")

# Remote roster: alias|provider|model|display|tier|note. HIDDEN_* is classified local-capable by
# the policy below; it sits BETWEEN the two visible rows so an index that tracks the unfiltered
# roster instead of the filtered one picks the wrong model.
REMOTE_ONE=remoteone;     REMOTE_ONE_NAME="Remote One";     REMOTE_ONE_PROVIDER=gemini
REMOTE_HIDDEN=remotehidden; REMOTE_HIDDEN_NAME="Remote Hidden"; REMOTE_HIDDEN_PROVIDER=nvidia
REMOTE_HIDDEN_MODEL=nvidia/local-capable-model
REMOTE_TWO=remotetwo;     REMOTE_TWO_NAME="Remote Two";     REMOTE_TWO_PROVIDER=groq
REMOTE_TWO_MODEL=groq-model
REMOTE_TOTAL=3
REMOTE_HIDDEN_COUNT=1
REMOTE_VISIBLE=$((REMOTE_TOTAL - REMOTE_HIDDEN_COUNT))

. "$REPO/tests/lib/sandbox.sh"
la_test_sandbox "$SB" || { echo "sandbox build failed"; exit 1; }
mkdir -p "$SB/home/.models/$SESSION_RAPID_DIR" "$SB/home/.models/$SESSION_VLLM_DIR" \
         "$SB/home/.models/$ABSENT_DIR" "$SB/home/.models/$DISPATCH_ONLY_DIR" "$SB/install" "$SB/tmp"
for d in "$SESSION_RAPID_DIR" "$SESSION_VLLM_DIR" "$DISPATCH_ONLY_DIR"; do
  head -c 2097152 /dev/zero > "$SB/home/.models/$d/weights.bin"
done

cat > "$SB/config/config.local.sh" <<CSL_TEST_CONFIG
LA_MODELS_DIR="\$HOME/.models"
LA_RAPID_BIN=/nonexistent/rapid-mlx

la_register $SESSION_RAPID $SESSION_RAPID_DIR rapid qwen "" false claude-opus-5 $SESSION_RAPID_EFFORT
la_register $SESSION_VLLM $SESSION_VLLM_DIR vllm qwen qwen3 true claude-opus-5 $SESSION_VLLM_EFFORT
la_register $ABSENT $ABSENT_DIR rapid qwen "" false claude-opus-5 low
la_register $DISPATCH_ONLY $DISPATCH_ONLY_DIR mlx_lm llama "" false claude-haiku-4-5-20251001 low

la_role $SESSION_RAPID_ROLE $SESSION_RAPID $SESSION_RAPID_EFFORT both
la_role $SESSION_VLLM_ROLE $SESSION_VLLM $SESSION_VLLM_EFFORT both
CSL_TEST_CONFIG

cat > "$SB/config/remote-agents.sh" <<ROSTER
LA_REMOTE_AGENTS=(
  "$REMOTE_ONE|$REMOTE_ONE_PROVIDER|gemini-3.8-flash|$REMOTE_ONE_NAME|renewing_free|note"
  "$REMOTE_HIDDEN|$REMOTE_HIDDEN_PROVIDER|$REMOTE_HIDDEN_MODEL|$REMOTE_HIDDEN_NAME|renewing_free|note"
  "$REMOTE_TWO|$REMOTE_TWO_PROVIDER|$REMOTE_TWO_MODEL|$REMOTE_TWO_NAME|renewing_free|note"
)
ROSTER
# One hidden row; everything else is unclassified and therefore visible (fail-open).
cat > "$SB/config/local-capable-remote-models.psv" <<POLICY
$REMOTE_HIDDEN_PROVIDER|$REMOTE_HIDDEN_MODEL|local-capable|SomeLocalModel|rapid|20|has a local MLX artifact
POLICY

# Stubs. The picker and the key checker are replaced; everything else is the real runtime.
cat > "$SB/bin/session-picker" <<'PICKER_STUB'
#!/usr/bin/env bash
printf 'session-picker %s\n' "$*" >> "$CSL_TEST_LOG"
PICKER_STUB
cat > "$SB/bin/la-roles.sh" <<'ROLES_STUB'
#!/usr/bin/env bash
printf 'la-roles %s\n' "$*" >> "$CSL_TEST_LOG"
ROLES_STUB
printf '#!/usr/bin/env bash\nexit 0\n' > "$SB/bin/remote-keys.sh"
cat > "$SB/install/setup-api-keys.py" <<'SETUP_STUB'
import os, sys
open(os.environ["CSL_TEST_LOG"], "a").write("setup-api-keys %s\n" % " ".join(sys.argv[1:]))
SETUP_STUB
# The launcher stub records what the launcher would RECEIVE — the contract, not menu text.
cat > "$SB/bin/stub-launcher" <<'CSL_STUB_LAUNCHER'
#!/usr/bin/env bash
printf '%s|%s|auto=%s|blind=%s|telemetry=%s|stop=%s|intercept=%s|mcp=%s\n' \
  "${1:-}" "${2:-}" "${LA_AUTO_MODE:-unset}" "${LA_BLIND_AUTO:-unset}" "${LA_TELEMETRY:-unset}" \
  "${LA_QUEUE_STOP_HOOK:-unset}" "${INTERCEPT_AGENTS:-unset}" "${LA_ENABLE_MCP:-unset}" > "$CSL_TEST_RESULT"
CSL_STUB_LAUNCHER
chmod +x "$SB/bin/session-picker" "$SB/bin/la-roles.sh" "$SB/bin/remote-keys.sh" "$SB/bin/stub-launcher"

LOG="$SB/calls.log"
# Scrub every CSL_*/LA_* toggle from the caller so the user's own shell cannot change a default.
CLEAN_ENV=(-u CSL_WATCH -u CSL_AUTO_MODE_STATE -u CSL_TELEMETRY -u CSL_STOP_HOOK -u CSL_LOCAL_CAPABLE
           -u CSL_INTERCEPT_AGENTS -u CSL_ENABLE_MCP -u LA_QUEUE_STOP_HOOK -u INTERCEPT_AGENTS
           -u LA_ENABLE_MCP -u LA_AUTO_MODE -u LA_BLIND_AUTO -u LA_TELEMETRY -u LA_AUTO_COMPACT_WINDOW)
csl() {  # csl [VAR=value ...] -- <args...>   (stdin from /dev/null unless piped)
  local -a extra=()
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do extra+=("$1"); shift; done
  [ "${1:-}" = "--" ] && shift
  : > "$LOG"
  env "${CLEAN_ENV[@]}" HOME="$SB/home" TMPDIR="$SB/tmp" CSL_NO_WATCH_WINDOW=1 \
    CSL_LAUNCHER="$SB/bin/stub-launcher" CSL_TEST_RESULT="$SB/result" CSL_TEST_LOG="$LOG" \
    ${extra[@]+"${extra[@]}"} bash "$SB/bin/csl" "$@" 2>&1
}
result() { cat "$SB/result" 2>/dev/null; rm -f "$SB/result"; }

echo "== 1. entry points dispatch to the picker / tools, launching nothing =="
csl -- </dev/null >/dev/null
assert_eq "session-picker home" "$(cat "$LOG")" 'bare csl opens the picker at Home'
csl -- local </dev/null >/dev/null
assert_eq "session-picker local" "$(cat "$LOG")" 'csl local opens the Local picker directly'
csl -- lowkey </dev/null >/dev/null
assert_eq "session-picker lowkey" "$(cat "$LOG")" 'csl lowkey with no args opens the Lowkey picker'
csl -- roles --json </dev/null >/dev/null
assert_eq "la-roles --json" "$(cat "$LOG")" 'csl roles passes its arguments to la-roles.sh'
csl -- setup-remote --check </dev/null >/dev/null
assert_eq "setup-api-keys --check" "$(cat "$LOG")" 'csl setup-remote reaches the key wizard'
[ ! -e "$SB/result" ]; check $? 'none of the entry points launched a session'

out="$(csl -- --help)"; rc=$?
assert_eq 0 "$rc" '--help exits 0'
assert_grep 'Usage: csl' "$out" '--help prints usage'
[ ! -s "$LOG" ]; check $? '--help does no work (no picker, no tool)'
out="$(csl -- --no-such-flag)"; rc=$?
assert_eq 2 "$rc" 'an unknown flag exits 2 instead of running anything'
assert_grep 'unrecognised option' "$out" 'an unknown flag names the problem'

echo "== 2. --inventory is exactly the on-disk, session-capable roster =="
inv="$(csl -- --inventory)"
assert_eq "${#ON_DISK_SESSION_MODELS[@]}" "$(printf '%s\n' "$inv" | grep -c .)" \
  "inventory lists ${#ON_DISK_SESSION_MODELS[@]} models"
assert_eq "$SESSION_RAPID	rapid	$SESSION_RAPID_EFFORT	$SESSION_RAPID_ROLE	" \
  "$(printf '%s\n' "$inv" | grep "^$SESSION_RAPID	")" 'a Rapid row carries backend, configured effort and role'
assert_eq "$SESSION_VLLM	vllm	$SESSION_VLLM_EFFORT	$SESSION_VLLM_ROLE	" \
  "$(printf '%s\n' "$inv" | grep "^$SESSION_VLLM	")" 'a vllm row carries backend, configured effort and role'
assert_no_grep "$ABSENT	" "$inv" 'a registered model with no weights is omitted'
assert_no_grep "$DISPATCH_ONLY	" "$inv" 'a dispatch-only backend (no /v1/messages) is omitted'

echo "== 3. --dry-run shows the roster and the toggle defaults =="
out="$(csl -- --dry-run-skip-preflight)"
for a in "${ON_DISK_SESSION_MODELS[@]}"; do
  assert_grep_flexible "^  $a +backend=" "$out" "dry run lists $a"
done
assert_grep_flexible "$SESSION_VLLM +backend=vllm +thinking=true +effort=$SESSION_VLLM_EFFORT +roles=$SESSION_VLLM_ROLE" "$out" \
  'dry run shows backend, thinking, effort and role per model'
assert_no_grep "  $ABSENT " "$out" 'dry run omits the absent model'
assert_grep 'Auto-mode: blind-trust' "$out" 'auto mode defaults to blind-trust (state 0)'
assert_grep 'Telemetry: OFF' "$out" 'telemetry defaults OFF (a local session stays local)'
assert_grep 'Watcher:   OFF' "$out" 'the watcher defaults OFF'
assert_grep 'Stop hook: OFF' "$out" 'the queued-prompt hook defaults OFF (since 0.22.3)'
assert_grep_flexible 'Intercept Agents: ON' "$out" 'agent interception defaults ON'
assert_no_grep 'Using public fallback defaults' "$out" 'no fallback notice when config.local.sh exists'
for case_ in "CSL_AUTO_MODE_STATE=1|Auto-mode: classifier" "CSL_AUTO_MODE_STATE=2|Auto-mode: off" \
             "CSL_TELEMETRY=1|Telemetry: ON" "CSL_WATCH=1|Watcher:   ON" "CSL_STOP_HOOK=1|Stop hook: ON" \
             "CSL_INTERCEPT_AGENTS=0|Intercept Agents: OFF"; do
  var="${case_%%|*}"; want="${case_#*|}"
  assert_grep "$want" "$(csl "$var" -- --dry-run-skip-preflight)" "$var shows '$want'"
done

echo "== 4. --picker-launch hands the launcher what the picker chose =="
# The picker always exports LA_AUTO_MODE / LA_BLIND_AUTO / LA_TELEMETRY / LA_ENABLE_MCP itself;
# csl only owns the two toggles it re-derives (stop hook, intercept), so those are its defaults.
csl -- --picker-launch "$SESSION_RAPID" "$SESSION_RAPID_EFFORT" >/dev/null
assert_grep "$SESSION_RAPID|$SESSION_RAPID_EFFORT|" "$(cat "$SB/result" 2>/dev/null)" 'the alias and effort reach the launcher'
assert_grep '|stop=0|intercept=1|' "$(result)" 'with nothing set, csl hands the launcher hook OFF and intercept ON'
# The picker exports its toggles as the LAUNCHER's variable names. csl used to re-derive
# LA_QUEUE_STOP_HOOK and INTERCEPT_AGENTS from CSL_* and silently drop the picker's choice.
csl LA_AUTO_MODE=1 LA_BLIND_AUTO=0 LA_TELEMETRY=1 LA_QUEUE_STOP_HOOK=1 INTERCEPT_AGENTS=0 LA_ENABLE_MCP=1 \
  -- --picker-launch "$SESSION_VLLM" max >/dev/null
assert_eq "$SESSION_VLLM|max|auto=1|blind=0|telemetry=1|stop=1|intercept=0|mcp=1" "$(result)" \
  'every toggle the picker exports reaches the launcher unchanged'
csl CSL_STOP_HOOK=1 CSL_INTERCEPT_AGENTS=0 -- --picker-launch "$SESSION_RAPID" low >/dev/null
assert_grep '|stop=1|intercept=0|' "$(result)" 'CSL_STOP_HOOK / CSL_INTERCEPT_AGENTS still set the defaults'
out="$(csl CSL_WATCH=1 -- --picker-launch "$SESSION_RAPID" high)"
assert_grep_flexible 'watcher .*--attach [0-9]+' "$out" 'CSL_WATCH=1 offers the watcher, following the session pid'
out="$(csl -- --picker-launch "$SESSION_RAPID" high)"
assert_no_grep '--attach' "$out" 'the watcher is not opened unless asked for'
csl -- "$SESSION_VLLM" xhigh >/dev/null
assert_grep "$SESSION_VLLM|xhigh|" "$(result)" 'csl <alias> <effort> passes straight through to the launcher'

echo "== 5. installed-copy fallback notice =="
FB="$SB/fallback"; la_test_sandbox "$FB" || exit 1
mkdir -p "$FB/home/.models/$SESSION_RAPID_DIR"
head -c 2097152 /dev/zero > "$FB/home/.models/$SESSION_RAPID_DIR/weights.bin"
# Deliberately config.example.sh, NOT config.local.sh, so la_load_config takes the fallback branch.
cat > "$FB/config/config.example.sh" <<FALLBACK_CONFIG
LA_MODELS_DIR="\$HOME/.models"
LA_RAPID_BIN=/nonexistent/rapid-mlx
la_register $SESSION_RAPID $SESSION_RAPID_DIR rapid qwen "" false claude-opus-5 high
FALLBACK_CONFIG
out="$(env "${CLEAN_ENV[@]}" HOME="$FB/home" bash "$FB/bin/csl" --dry-run-skip-preflight 2>&1)"
assert_grep 'Using public fallback defaults' "$out" 'the fallback notice appears when only config.example.sh exists'

# --- the remote numbered menu (csl remote) ----------------------------------------------------
NAV="$SB/nav"
remote_menu() {  # remote_menu <keys> [VAR=value ...] — drives the csl-owned menu, nav token in $NAV
  local keys="$1"; shift
  rm -f "$NAV"
  printf '%b' "$keys" | env "${CLEAN_ENV[@]}" HOME="$SB/home" TMPDIR="$SB/tmp" "$@" \
    bash "$SB/bin/remote-session.sh" --csl-owner --csl-nav-file "$NAV" 2>&1
}
nav_target() { head -1 "$NAV" 2>/dev/null; }
nav_state()  { grep "^$1=" "$NAV" 2>/dev/null | cut -d= -f2; }

echo "== 6. csl remote opens the remote numbered menu =="
out="$(printf 'q\n' | env "${CLEAN_ENV[@]}" HOME="$SB/home" TMPDIR="$SB/tmp" bash "$SB/bin/csl" remote 2>&1)"
assert_grep_flexible 'Remote cloud-API agents' "$out" 'csl remote reaches the remote menu'
assert_no_grep 'unknown remote alias' "$out" 'quitting never falls through to alias resolution'

echo "== 7. the local-capable filter: HIDDEN by default, fail-open, f toggles, R reports =="
out="$(remote_menu 'q\n')"
assert_grep_flexible "\($REMOTE_VISIBLE available\)" "$out" "the header counts $REMOTE_VISIBLE visible of $REMOTE_TOTAL"
assert_grep_flexible 'Local-capable: HIDDEN' "$out" 'the filter is HIDDEN by default'
assert_no_grep "$REMOTE_HIDDEN_NAME" "$out" 'the classified model is absent from the roster by default'
assert_grep "$REMOTE_ONE_NAME" "$out" 'an unclassified model stays visible (fail-open)'
assert_grep "$REMOTE_TWO_NAME" "$out" 'every unclassified model stays visible'
assert_no_grep 'local-cap:' "$out" 'the ambiguous abbreviation is gone'
assert_eq quit "$(nav_target)" 'q writes the quit nav token'

out="$(remote_menu 'f\nq\n')"
assert_grep_flexible "\($REMOTE_TOTAL available\)" "$out" "after f, all $REMOTE_TOTAL are counted"
assert_grep_flexible 'Local-capable: SHOWN' "$out" 'f flips the filter to SHOWN'
assert_grep "$REMOTE_HIDDEN_NAME" "$out" 'after f, the previously hidden model is listed'
assert_eq 1 "$(nav_state LOCAL_CAPABLE)" 'the SHOWN state is handed back to csl'

out="$(remote_menu 'q\n' CSL_LOCAL_CAPABLE=1)"
# csl seeds the child through its own args; driving the child directly must still start HIDDEN,
# so this documents that the child does NOT read CSL_LOCAL_CAPABLE itself.
assert_no_grep "$REMOTE_HIDDEN_NAME" "$out" 'the remote child does not read CSL_LOCAL_CAPABLE on its own'

out="$(remote_menu 'R\n\nq\n')"
assert_no_grep '(report unavailable)' "$out" 'R renders the hidden-model report'
assert_grep_flexible "^$REMOTE_HIDDEN\$" "$out" 'R names the hidden model'
assert_grep 'press enter to return to menu' "$out" 'R tells the user how to get back'
assert_eq 2 "$(printf '%s' "$out" | grep -c 'Remote cloud-API agents')" 'after R, the menu redraws (does not exit)'

echo "== 8. a number resolves against the rows ON SCREEN, filtered or not =="
# --dry-run prints the resolved model id and stops before launching, so the selection is observable.
pick() { rm -f "$NAV"; printf '%b' "$1" | env "${CLEAN_ENV[@]}" HOME="$SB/home" TMPDIR="$SB/tmp" \
  bash "$SB/bin/remote-session.sh" --csl-owner --csl-nav-file "$NAV" --dry-run 2>&1; }
assert_grep_flexible "model +: $REMOTE_TWO_MODEL\$" "$(pick '2\n')" \
  "filtered row 2 is $REMOTE_TWO ($REMOTE_HIDDEN sits between it and row 1 in the unfiltered roster)"
assert_grep_flexible "model +: $REMOTE_HIDDEN_MODEL\$" "$(pick 'f\n2\n')" \
  "after f, unfiltered row 2 is $REMOTE_HIDDEN, not a stale filtered index"

echo "== 9. navigation tokens and state sync back to csl =="
remote_menu 'h\n' >/dev/null
assert_eq home "$(nav_target)" 'h hands control back to Home'
remote_menu 's\n' >/dev/null
assert_eq local "$(nav_target)" 's jumps to the local picker'
remote_menu 'a\nh\n' >/dev/null
assert_eq 1 "$(nav_state AUTO_MODE_STATE)" 'a cycles blind-trust -> classifier, and csl is told'
remote_menu 'a\na\na\nh\n' >/dev/null
assert_eq 0 "$(nav_state AUTO_MODE_STATE)" 'three presses of a wrap back to blind-trust'
remote_menu 't\nh\n' >/dev/null
assert_eq 1 "$(nav_state TELEMETRY)" 't flips telemetry, and csl is told'

echo "== 10. every framed row is the same display width, and none overflows =="
# Alignment cannot be asserted with grep: rows differ in CHARACTER count on purpose, and it is
# their DISPLAY width that must match. Emoji count two columns; U+FE0F counts zero.
out="$(remote_menu 'f\nq\n')"
box_widths="$(printf '%s' "$out" | LC_ALL=en_US.UTF-8 python3 -c '
import sys, unicodedata
def width(text):
    total = 0
    for char in text:
        if char == "️" or unicodedata.combining(char):
            continue
        total += 2 if (unicodedata.east_asian_width(char) in ("W", "F")
                       or ord(char) >= 0x1F300) else 1
    return total
rows = [l.split("Select [")[-1] for l in sys.stdin.read().split("\n")]
seen = {width(r) for r in rows if r.startswith(("║", "╔", "╠", "╚"))}
print(len(seen), sorted(seen))
')"
[ "${box_widths%% *}" = "1" ]
check $? "all framed rows share ONE display width (got: $box_widths)"
assert_no_grep '║.*║.*║' "$out" 'no row renders a stray frame character mid-line'

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

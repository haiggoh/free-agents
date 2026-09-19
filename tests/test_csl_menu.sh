#!/usr/bin/env bash
# tests/test_csl_menu.sh — deterministic coverage for the interactive csl menu.
#
# Uses a sandboxed HOME, copied config loader, fake model files, and a stub
# launcher. It never reads private configuration, starts a server, opens a
# watcher, or touches a live model.
#
# csl now has TWO screens before a model launches: a home lane selector
# (local/remote/install/quit), then the local picker (or the remote picker in
# bin/remote-session.sh). Every input sequence below sends '1' first to enter
# the local lane unless the test is specifically about the home screen itself
# or the remote lane.
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

assert_grep() {
  printf '%s' "$2" | grep -qF -- "$1"
  check $? "$3"
}

assert_no_grep() {
  if printf '%s' "$2" | grep -qF -- "$1"; then
    check 1 "$3"
  else
    check 0 "$3"
  fi
}

# Flexible grep for cosmetic/menu text that may change (emojis, wording).
# Uses regex matching; pattern should match the essential text without
# depending on specific emojis or exact phrasing.
assert_grep_flexible() {
  printf '%s' "$2" | grep -qE -- "$1"
  check $? "$3"
}

assert_no_grep_flexible() {
  if printf '%s' "$2" | grep -qE -- "$1"; then
    check 1 "$3"
  else
    check 0 "$3"
  fi
}

SB="$(mktemp -d "${TMPDIR:-/tmp}/la-csl-menu-test.XXXXXX")"
trap 'rm -rf "$SB"' EXIT INT TERM HUP

mkdir -p \
  "$SB/bin" \
  "$SB/config" \
  "$SB/home/.models/ModelAlpha" \
  "$SB/home/.models/ModelBeta" \
  "$SB/home/.models/ModelAbsent" \
  "$SB/home/.models/DispatchOnly"

cp "$REPO/bin/csl" "$SB/bin/csl"
cp "$REPO/bin/remote-session.sh" "$SB/bin/remote-session.sh"
cp "$REPO/bin/local-capable-filter.sh" "$SB/bin/local-capable-filter.sh"
cp "$REPO/config/config-lib.sh" "$SB/config/config-lib.sh"
mkdir -p "$SB/install"
cat > "$SB/install/setup-api-keys.py" <<'SETUP_STUB'
print("KEY_SETUP_REACHED")
SETUP_STUB

head -c 2097152 /dev/zero > "$SB/home/.models/ModelAlpha/weights.bin"
head -c 2097152 /dev/zero > "$SB/home/.models/ModelBeta/weights.bin"
head -c 2097152 /dev/zero > "$SB/home/.models/DispatchOnly/weights.bin"

cat > "$SB/config/config.local.sh" <<'CSL_TEST_CONFIG'
LA_MODELS_DIR="$HOME/.models"
LA_RAPID_BIN=/nonexistent/rapid-mlx

la_register alpha ModelAlpha rapid qwen "" false claude-opus-5 high
la_register beta ModelBeta vllm qwen qwen3 true claude-opus-5 medium
la_register absent ModelAbsent rapid qwen "" false claude-opus-5 low
la_register dispatch-only DispatchOnly mlx_lm llama "" false claude-haiku-4-5-20251001 low

la_role operator alpha high both
la_role reasoner beta medium both
CSL_TEST_CONFIG

# Minimal remote roster + policy so `r`/`2` navigation into the remote lane
# doesn't fail on a missing config — these tests only exercise navigation and
# state, not real remote launch, so no API keys or provider routes are needed.
cat > "$SB/config/remote-agents.sh" <<'ROSTER'
LA_REMOTE_AGENTS=(
  "remoteone|gemini|gemini-3.8-flash|Remote One|renewing_free|note"
  "remotehidden|nvidia|nvidia/local-capable-model|Remote Hidden|renewing_free|note"
  "remotetwo|groq|groq-model|Remote Two|renewing_free|note"
)
ROSTER
# remotehidden is classified local-capable and hidden by default; remoteone/remotetwo
# have no policy row and stay visible (fail-open on unknown/unclassified entries).
cat > "$SB/config/local-capable-remote-models.psv" <<'POLICY'
# test policy — one hidden row (remotehidden's provider|model_id), everything else
# unclassified and therefore visible (fail-open).
nvidia|nvidia/local-capable-model|local-capable|SomeLocalModel|rapid|20|has a local MLX artifact
POLICY
cat > "$SB/bin/remote-keys.sh" <<'KEYS_STUB'
#!/usr/bin/env bash
exit 1
KEYS_STUB
chmod +x "$SB/bin/remote-keys.sh"

cat > "$SB/bin/stub-launcher" <<'CSL_STUB_LAUNCHER'
#!/usr/bin/env bash
# Record LA_AUTO_MODE too: the menu text is cosmetic, but the value the launcher
# actually receives is the contract, so assert on that.
printf '%s|%s|auto=%s|blind=%s|telemetry=%s\n' "${1:-}" "${2:-}" "${LA_AUTO_MODE:-unset}" "${LA_BLIND_AUTO:-unset}" "${LA_TELEMETRY:-unset}" > "$CSL_TEST_RESULT"
CSL_STUB_LAUNCHER
chmod +x "$SB/bin/csl" "$SB/bin/remote-session.sh" "$SB/bin/local-capable-filter.sh" "$SB/bin/stub-launcher"

run_csl() {
  local input="$1"
  local result_file="$2"

  printf '%b' "$input" |
    HOME="$SB/home" \
    CSL_LAUNCHER="$SB/bin/stub-launcher" \
    CSL_TEST_RESULT="$result_file" \
      bash "$SB/bin/csl" 2>&1
}

echo "== 0. home screen appears before any model list =="
out="$(run_csl 'q\n' "$SB/no-launch")"
assert_grep 'Claude Code Session Launcher' "$out" 'home lane selector is the first screen'
assert_grep_flexible '1\).*Local' "$out" 'home screen offers the local lane'
assert_grep_flexible '2\).*Remote' "$out" 'home screen offers the remote lane'
assert_grep_flexible 'k\).*Install.*set up remote API keys' "$out" 'home screen offers key setup'
assert_grep 'q) Quit' "$out" 'home screen offers quit'
assert_no_grep '1) alpha' "$out" 'the model list is NOT shown before a lane is chosen'
assert_grep 'Auto-mode: blind-trust' "$out" 'home screen displays auto-mode state'
assert_grep 'Telemetry: OFF' "$out" 'home screen displays telemetry state'
assert_grep 'Watcher:   OFF' "$out" 'home screen displays watcher state'
assert_no_grep 'Local-cap' "$out" \
  'the home screen does NOT carry the local-capable row: the filter only affects the REMOTE roster, and an abbreviated "Local-cap" read ambiguously beside the Local LANE (removed 0.15.1)'
assert_no_grep '/l]' "$out" 'the home key prompt no longer advertises the removed l) key'
assert_grep '(2 on disk)' "$out" 'home screen shows the correct on-disk session-model count'

echo "== 1. entering the local lane lists every available session model =="
out="$(run_csl '1\nq\n' "$SB/no-launch2")"
assert_grep 'Available models (on disk, session-capable' "$out" \
  'local picker describes its availability filter'
assert_grep '1) alpha' "$out" 'first on-disk Rapid model is directly listed'
assert_grep '2) beta' "$out" 'second on-disk vllm model is directly listed'
assert_grep 'backend=rapid' "$out" 'menu displays resolved backend'
assert_grep 'thinking=true' "$out" 'menu displays thinking mode'
assert_grep 'effort=medium' "$out" 'menu displays configured effort'
assert_grep 'roles=operator' "$out" 'menu displays role metadata'
assert_no_grep 'absent ' "$out" 'absent model is omitted'
assert_no_grep 'dispatch-only' "$out" 'non-session backend is omitted'
assert_no_grep 'Recommended pairings' "$out" \
  'role recommendations no longer replace the model list'
assert_grep 'watcher: OFF' "$out" 'watcher defaults off'
assert_no_grep 'watcher: ON' "$out" 'watcher is not enabled implicitly'
assert_grep 'auto-mode: blind-trust' "$out" 'auto mode defaults to blind-trust (state 0)'
assert_no_grep 'auto-mode: classifier' "$out" 'classifier state is not the default'
assert_no_grep 'auto-mode: off' "$out" 'off state is not the default'
assert_grep 'telemetry: OFF' "$out" 'telemetry defaults off (a local session stays local)'
assert_no_grep 'telemetry: ON' "$out" 'telemetry is not silently enabled'
assert_grep_flexible 'c\).*choose a listed model × custom effort' "$out" \
  'custom effort composition remains available'
assert_grep_flexible 'h\).*back to lane selector' "$out" 'local picker offers a way back to the home lane'

echo "== 1c. the queued-prompt hook toggle in the LOCAL picker is REACHABLE =="
# Regression: the toggle was advertised on 's', but 's' was already bound to
# switch-to-remote EARLIER in the same case statement, so the first arm won and
# the toggle was dead code -- pressing it left the picker instead of toggling.
# It now lives on 'p'. These assertions fail against the shadowed binding.
out="$(run_csl '1\np\nq\n' "$SB/stophook-toggle")"
assert_grep_flexible 'p\).*queued-prompt hook: OFF' "$out" \
  'pressing p in the local picker actually flips the queued-prompt hook OFF (default is ON, so OFF proves the keypress was HANDLED -- asserting ON would also pass for an ignored key)'
assert_no_grep 'Invalid selection' "$out" \
  'p is a recognised key in the local picker, not falling through to the numeric branch'
assert_no_grep 'Remote Session' "$out" \
  'pressing the queued-prompt key does NOT navigate to the remote lane (the shadowing bug)'

out="$(run_csl '1\nq\n' "$SB/stophook-default")"
assert_grep_flexible 'p\).*queued-prompt hook: ON' "$out" \
  'the queued-prompt hook renders on p and defaults ON (CSL_STOP_HOOK:-1)'
out="$(CSL_STOP_HOOK=0 run_csl '1\nq\n' "$SB/stophook-envoff")"
assert_grep_flexible 'p\).*queued-prompt hook: OFF' "$out" \
  'CSL_STOP_HOOK=0 defaults the hook OFF, and it still renders on p'

echo "== 0c. every framed box row is exactly the same display width =="
# Alignment cannot be asserted with grep: the rows differ in CHARACTER count on purpose,
# and it is their DISPLAY width that must match. Emoji count two columns, and U+FE0F
# variation selectors count zero -- the two facts that broke the hand-counted padding.
out="$(run_csl 'q\n' "$SB/box-align")"
box_widths="$(printf '%s' "$out" | LC_ALL=en_US.UTF-8 python3 -c '
import sys, unicodedata
def width(text):
    total = 0
    for char in text:
        if char == "\ufe0f" or unicodedata.combining(char):
            continue
        total += 2 if (unicodedata.east_asian_width(char) in ("W", "F")
                       or ord(char) >= 0x1F300) else 1
    return total
seen = {width(line.rstrip("\n")) for line in sys.stdin
        if line.startswith(("\u2551", "\u2554", "\u2560", "\u255a"))}
print(len(seen), sorted(seen))
')"
[ "${box_widths%% *}" = "1" ]
check $? "all framed rows share ONE display width (got: $box_widths)"

echo "== 0d. no framed row overflows the frame =="
# Truncation matters as much as padding: content wider than the box breaks the border too.
assert_no_grep '║.*║.*║' "$out" 'no row renders a stray frame character mid-line'

echo "== 1b. h from the local picker returns to the home lane, still in one process =="
out="$(run_csl '1\nh\nq\n' "$SB/no-launch3")"
occurrences="$(printf '%s' "$out" | grep -c 'Claude Code Session Launcher')"
[ "$occurrences" -eq 2 ]
check $? 'h) from the local picker redraws the home screen (2 total renders: initial + after h)'

echo "== 2. numbered choice uses the model default effort =="
rm -f "$SB/default-result"
run_csl '1\n2\n' "$SB/default-result" >/dev/null
assert_grep 'beta|medium' "$(cat "$SB/default-result" 2>/dev/null)" \
  'numbered beta selection launches with configured medium effort'

echo "== 3. custom composition overrides effort explicitly =="
rm -f "$SB/custom-result"
run_csl '1\nc\n1\n5\n' "$SB/custom-result" >/dev/null
assert_grep 'alpha|max' "$(cat "$SB/custom-result" 2>/dev/null)" \
  'custom composition launches alpha at max effort'

echo "== 4. watcher remains an explicit opt-in =="
out="$(
  printf '1\nq\n' |
    HOME="$SB/home" \
    CSL_WATCH=1 \
    CSL_LAUNCHER="$SB/bin/stub-launcher" \
    CSL_TEST_RESULT="$SB/watch-result" \
      bash "$SB/bin/csl" 2>&1
)"
assert_grep 'watcher: ON' "$out" 'CSL_WATCH=1 opts in by default'
assert_grep 'Watcher:   ON' "$out" 'CSL_WATCH=1 is also reflected on the home screen'

echo "== 5. auto mode can be set to classifier or off by default =="
# State 2 = off
out="$(
  printf '1\nq\n' |
    HOME="$SB/home" \
    CSL_AUTO_MODE_STATE=2 \
    CSL_LAUNCHER="$SB/bin/stub-launcher" \
    CSL_TEST_RESULT="$SB/auto-off-result" \
      bash "$SB/bin/csl" 2>&1
)"
assert_grep 'auto-mode: off' "$out" 'CSL_AUTO_MODE_STATE=2 shows off in the menu'
assert_no_grep 'auto-mode: blind-trust' "$out" 'the opt-out is not overridden by the new default'
# State 1 = classifier
out="$(
  printf '1\nq\n' |
    HOME="$SB/home" \
    CSL_AUTO_MODE_STATE=1 \
    CSL_LAUNCHER="$SB/bin/stub-launcher" \
    CSL_TEST_RESULT="$SB/auto-class-result" \
      bash "$SB/bin/csl" 2>&1
)"
assert_grep 'auto-mode: classifier' "$out" 'CSL_AUTO_MODE_STATE=1 shows classifier in the menu'

echo "== 6. the launcher RECEIVES the auto-mode default (blind-trust), not just the menu text =="
rm -f "$SB/auto-launch-result"
run_csl '1\n1\n' "$SB/auto-launch-result" >/dev/null
assert_grep 'alpha|high|auto=1|blind=1|' "$(cat "$SB/auto-launch-result" 2>/dev/null)" \
  'default blind-trust launch hands LA_AUTO_MODE=1 LA_BLIND_AUTO=1'

echo "== 7. pressing a cycles blind-trust→classifier, all the way to the launcher =="
rm -f "$SB/classifier-launch-result"
run_csl '1\na\n1\n' "$SB/classifier-launch-result" >/dev/null
assert_grep 'alpha|high|auto=1|blind=0|' "$(cat "$SB/classifier-launch-result" 2>/dev/null)" \
  'a cycles state 0→1: LA_AUTO_MODE=1 LA_BLIND_AUTO=0'

echo "== 7b. pressing a again cycles classifier→off, all the way to the launcher =="
rm -f "$SB/off-launch-result"
run_csl '1\na\na\n1\n' "$SB/off-launch-result" >/dev/null
assert_grep 'alpha|high|auto=0' "$(cat "$SB/off-launch-result" 2>/dev/null)" \
  'a cycles state 1→2: LA_AUTO_MODE=0 (blind flag irrelevant)'

echo "== 7c. pressing a third time cycles off→blind-trust =="
rm -f "$SB/blind-back-launch-result"
run_csl '1\na\na\na\n1\n' "$SB/blind-back-launch-result" >/dev/null
assert_grep 'alpha|high|auto=1|blind=1|' "$(cat "$SB/blind-back-launch-result" 2>/dev/null)" \
  'a cycles state 2→0: back to blind-trust'

echo "== 8. the b shortcut jumps straight to blind-trust, regardless of current state =="
rm -f "$SB/blind-shortcut-result"
run_csl '1\na\na\nb\n1\n' "$SB/blind-shortcut-result" >/dev/null
assert_grep 'alpha|high|auto=1|blind=1|' "$(cat "$SB/blind-shortcut-result" 2>/dev/null)" \
  'b shortcut from state 2 reaches launcher as LA_AUTO_MODE=1 LA_BLIND_AUTO=1'

echo "== 9. auto-mode state set at home survives the switch into the local picker =="
out="$(
  printf 'q\n' |
    HOME="$SB/home" \
    CSL_AUTO_MODE_STATE=2 \
    CSL_LAUNCHER="$SB/bin/stub-launcher" \
    CSL_TEST_RESULT="$SB/home-state-result" \
      bash "$SB/bin/csl" 2>&1
)"
assert_grep 'Auto-mode: off' "$out" 'home-set state 2 shows off at the home screen'
out2="$(
  printf '1\nq\n' |
    HOME="$SB/home" \
    CSL_AUTO_MODE_STATE=2 \
    CSL_LAUNCHER="$SB/bin/stub-launcher" \
    CSL_TEST_RESULT="$SB/home-state-result2" \
      bash "$SB/bin/csl" 2>&1
)"
assert_grep 'auto-mode: off' "$out2" \
  'state set before entering the local lane is NOT reset when the local picker draws'

echo "== 11. telemetry suppression reaches the launcher, and can be opted back in =="
rm -f "$SB/telemetry-result"
run_csl '1\n1\n' "$SB/telemetry-result" >/dev/null
assert_grep '|telemetry=0' "$(cat "$SB/telemetry-result" 2>/dev/null)" \
  'a default launch hands the launcher LA_TELEMETRY=0'
out="$(
  printf '1\nq\n' |
    HOME="$SB/home" \
    CSL_TELEMETRY=1 \
    CSL_LAUNCHER="$SB/bin/stub-launcher" \
    CSL_TEST_RESULT="$SB/telemetry-on-result" \
      bash "$SB/bin/csl" 2>&1
)"
assert_grep 'telemetry: ON' "$out" 'CSL_TELEMETRY=1 opts back in by default'

echo "== 12. pressing t flips telemetry, all the way to the launcher =="
rm -f "$SB/telemetry-toggled"
run_csl '1\nt\n1\n' "$SB/telemetry-toggled" >/dev/null
assert_grep '|telemetry=1' "$(cat "$SB/telemetry-toggled" 2>/dev/null)" \
  'the t toggle reaches the launcher as LA_TELEMETRY=1'

echo "== 10. install choice opens key setup and returns to menu =="
out="$(run_csl '1\nk\nq\n' "$SB/setup-no-launch")"
assert_grep_flexible 'k\).*Install.*set up remote API keys' "$out" 'key setup is discoverable in the local menu'
assert_grep 'KEY_SETUP_REACHED' "$out" 'install choice reaches setup without launching a session'

out="$(run_csl 'k\nq\n' "$SB/setup-no-launch-home")"
assert_grep_flexible 'k\).*Install.*set up remote API keys' "$out" 'key setup is also discoverable in the home menu'
assert_grep 'KEY_SETUP_REACHED' "$out" 'home install choice reaches setup without launching a session'

echo "== 13. bidirectional navigation: home -> remote -> home -> local works in one process =="
out="$(run_csl '2\nh\n1\nq\n' "$SB/remote-roundtrip")"
assert_grep_flexible 'Remote API Session Picker' "$out" 'entering 2 at home reaches the remote picker'
occurrences="$(printf '%s' "$out" | grep -c 'Claude Code Session Launcher')"
[ "$occurrences" -eq 2 ]
check $? 'h) from the remote picker returns to home (2 home renders: initial + after h)'
assert_grep 'Available models (on disk, session-capable' "$out" \
  'entering 1 at home after returning from remote reaches the local picker, still in one process'
assert_no_grep 'unknown remote alias' "$out" \
  'navigating away from the remote picker never falls through to alias resolution (regression: PID-mismatch nav file)'

echo "== 13b. bidirectional navigation: home -> remote -> local (via l) works =="
out="$(run_csl '2\ns\nq\n' "$SB/remote-to-local")"
assert_grep 'Available models (on disk, session-capable' "$out" \
  's) from the remote picker jumps directly to the local picker'
assert_no_grep 'unknown remote alias' "$out" \
  'l) navigation never falls through to alias resolution'

echo "== 13c. state changed inside the remote picker survives the trip back through home =="
out="$(run_csl '2\na\nh\n1\nq\n' "$SB/remote-state-persist")"
assert_grep 'Auto-mode: classifier' "$out" \
  'auto-mode toggled inside the remote picker (a) is reflected on the home screen after h'

echo "== 14. local-capable filter is HIDDEN by default in the remote picker, f) toggles it, R) reports it =="
out="$(run_csl '2\nq\n' "$SB/remote-default-hidden")"
assert_grep_flexible 'f\).*locally-runnable models: HIDDEN' "$out" \
  'remote picker shows the filter HIDDEN by default, spelled out rather than abbreviated'
assert_no_grep 'local-cap:' "$out" \
  'the ambiguous abbreviation is gone from the remote picker too'
assert_no_grep 'remotehidden' "$out" \
  'the model classified local-capable is actually absent from the roster table by default'
assert_no_grep 'Remote Hidden' "$out" \
  'its display name is absent too, not just its alias (catches a filter that hides the row but leaks the label elsewhere)'
assert_grep 'remoteone' "$out" 'an unclassified model stays visible (fail-open)'
assert_grep '2 model(s) visible  (hidden: 1)' "$out" \
  'the visible/hidden counts in the header reflect the one classified-hidden entry, not zero'

out="$(run_csl '2\nf\nh\nq\n' "$SB/remote-toggled-shown")"
assert_grep_flexible 'f\).*locally-runnable models: SHOWN' "$out" \
  'pressing f in the remote picker flips the filter to SHOWN'
assert_grep 'remoteone' "$out" 'the roster still renders after the toggle'
assert_grep 'remotehidden' "$out" \
  'after toggling to SHOWN, the previously-hidden model actually appears in the roster table'
assert_grep '3 model(s) visible  (hidden: 0)' "$out" \
  'after toggling to SHOWN, the header counts reflect all three entries visible, none hidden'

out="$(run_csl '2\nf\nh\n2\nq\n' "$SB/remote-toggle-persists")"
occurrences="$(printf '%s' "$out" | grep -cE 'locally-runnable models: SHOWN')"
[ "$occurrences" -ge 1 ]
check $? 'local-capable SHOWN state set inside the remote picker survives a trip home and back'

out="$(
  printf '2\nq\n' |
    HOME="$SB/home" \
    CSL_LOCAL_CAPABLE=1 \
    CSL_LAUNCHER="$SB/bin/stub-launcher" \
    CSL_TEST_RESULT="$SB/home-to-remote-lc" \
      bash "$SB/bin/csl" 2>&1
)"
assert_grep_flexible 'locally-runnable models: SHOWN' "$out" \
  'CSL_LOCAL_CAPABLE=1 set before any lane is still honoured by the remote picker, even though the home screen no longer displays it'
assert_grep 'remotehidden' "$out" \
  'that home-set SHOWN state actually made the classified-hidden model selectable in the remote picker (not just cosmetic)'

out="$(run_csl '2\nR\n\nq\n' "$SB/remote-hidden-report")"
assert_no_grep '(report unavailable)' "$out" \
  'R) the hidden-model report renders without error against the test policy/roster'
assert_grep 'nvidia' "$out" \
  'R) the report actually names the hidden entry, grouped under its provider'
assert_grep 'press enter to return to menu' "$out" \
  'R) the report screen tells the user how to get back, and does (same menu redraws after)'
occurrences="$(printf '%s' "$out" | grep -c -E 'Remote API Session Picker')"
[ "$occurrences" -ge 2 ]
check $? 'R) after viewing the report, the remote picker itself redraws (does not exit or lose state)'

echo "== 16. numeric selection resolves to the CORRECT alias, not a stale unfiltered index =="
# Separate sandbox: remote-session.sh invoked directly with --dry-run so the launch
# banner (which prints the resolved alias) is reachable without a real credential —
# the run_csl harness's stub-launcher never sees remote-session.sh's own argv, only
# what csl's local picker passes, so this needs remote-session.sh driven directly.
cat > "$SB/bin/remote-keys-ok.sh" <<'KEYS_OK'
#!/usr/bin/env bash
exit 0
KEYS_OK
chmod +x "$SB/bin/remote-keys-ok.sh"
cp "$SB/bin/remote-keys.sh" "$SB/bin/remote-keys.sh.bak"
cp "$SB/bin/remote-keys-ok.sh" "$SB/bin/remote-keys.sh"

out="$(printf '2\n' | bash "$SB/bin/remote-session.sh" --dry-run 2>&1)"
assert_grep 'agent    : remotetwo' "$out" \
  'with the filter HIDDEN, filtered row 2 is remotetwo (remotehidden sits BETWEEN remoteone and remotetwo in the unfiltered roster) — proves current_choices tracks the FILTERED list, not roster position'

out="$(printf 'f\n2\n' | bash "$SB/bin/remote-session.sh" --dry-run 2>&1)"
assert_grep 'agent    : remotehidden' "$out" \
  'after f) reveals all 3 entries, unfiltered row 2 is remotehidden — NOT a stale filtered index left over from the previous render (regression: current_choices must track the same list as the numbered rows currently on screen)'

cp "$SB/bin/remote-keys.sh.bak" "$SB/bin/remote-keys.sh"

echo "== 15. installed-copy fallback-config notice: shown when config.local.sh is absent, silent when present =="
FB="$(mktemp -d "${TMPDIR:-/tmp}/la-csl-fallback-test.XXXXXX")"
mkdir -p "$FB/bin" "$FB/config" "$FB/install" "$FB/home/.models/ModelAlpha"
cp "$REPO/bin/csl" "$FB/bin/csl"
cp "$REPO/config/config-lib.sh" "$FB/config/config-lib.sh"
cat > "$FB/install/setup-api-keys.py" <<'SETUP_STUB'
print("KEY_SETUP_REACHED")
SETUP_STUB
head -c 2097152 /dev/zero > "$FB/home/.models/ModelAlpha/weights.bin"
# Deliberately write config.example.sh, NOT config.local.sh, so la_load_config takes
# the fallback branch and sets LA_FALLBACK_CONFIG=1 (config-lib.sh's structured predicate).
cat > "$FB/config/config.example.sh" <<'FALLBACK_CONFIG'
LA_MODELS_DIR="$HOME/.models"
LA_RAPID_BIN=/nonexistent/rapid-mlx
la_register alpha ModelAlpha rapid qwen "" false claude-opus-5 high
la_role operator alpha high both
FALLBACK_CONFIG
chmod +x "$FB/bin/csl"
out_fallback="$(printf 'q\n' | HOME="$FB/home" bash "$FB/bin/csl" 2>&1)"
assert_grep 'Using public fallback defaults' "$out_fallback" \
  'the installed-copy fallback notice appears on the home screen when only config.example.sh exists'
rm -rf "$FB"

out_private="$(run_csl 'q\n' "$SB/no-fallback-notice")"
assert_no_grep 'Using public fallback defaults' "$out_private" \
  'the fallback notice does NOT appear when a private config.local.sh is present (main test sandbox)'

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

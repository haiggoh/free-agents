#!/usr/bin/env bash
# tests/test_launch_remote_window.sh — 0.50.8 Task 7: bin/launch-remote-window.sh and the
# handover SKILL.md prompt-link flip.
#
# Covers: --help (run, exit 0), unknown flag (exit 3 + usage), no args (exit 2), headless
# refusal (stdin not a TTY -> exit 2 + one-line message), TTY-present launch (pty harness,
# stubbed csl records argv), and SKILL.md text assertions (link form, no --prompt-file launch).
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -P "$HERE/.." && pwd)"
SCRIPT="$REPO/bin/launch-remote-window.sh"
SKILL="$REPO/skills/free-session-handover/SKILL.md"

case "${1:-}" in
  -h|--help) sed -n '2,/^set -uo/{/^set -uo/d;s/^# \{0,1\}//;p;}' "$0"; exit 0 ;;
  "") ;;
  *) echo "usage: tests/test_launch_remote_window.sh [--help]" >&2; exit 2 ;;
esac

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$2"; else FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$2"; fi; }
assert_grep() { printf '%s' "$2" | grep -qF -- "$1"; check $? "$3"; }
assert_no_grep() { if printf '%s' "$2" | grep -qF -- "$1"; then check 1 "$3"; else check 0 "$3"; fi; }
assert_eq() { if [ "$1" = "$2" ]; then check 0 "$3"; else printf '     expected: %s\n     actual:   %s\n' "$1" "$2"; check 1 "$3"; fi; }

SB="$(mktemp -d "${TMPDIR:-/tmp}/la-lrw-test.XXXXXX")"
trap 'rm -rf "$SB"' EXIT INT TERM HUP

echo "== 1. --help prints usage and exits 0 (verified by RUNNING it) =="
out="$(bash "$SCRIPT" --help 2>&1)"; rc=$?
assert_eq 0 "$rc" '--help exits 0'
assert_grep 'launch-remote-window.sh' "$out" '--help names the script'
assert_grep '--prompt TEXT' "$out" '--help documents the pass-through flags'
assert_grep 'LAUNCH_REMOTE_WINDOW_CSL' "$out" '--help documents the env override'

echo "== 2. unknown flag -> exit 3 + usage on stderr =="
out="$(bash "$SCRIPT" nvidia-x --bogus </dev/null 2>&1)"; rc=$?
assert_eq 3 "$rc" 'unknown flag exits 3'
assert_grep 'unknown flag: --bogus' "$out" 'the unknown flag is named'
assert_grep 'Usage:' "$out" 'usage is printed'

echo "== 3. no args -> exit 2 + usage =="
out="$(bash "$SCRIPT" </dev/null 2>&1)"; rc=$?
assert_eq 2 "$rc" 'no args exits 2'
assert_grep 'Usage:' "$out" 'usage is printed'

echo "== 4. headless (stdin not a TTY) -> exit 2 + clear refusal =="
out="$(bash "$SCRIPT" nvidia-x --effort max </dev/null 2>&1)"; rc=$?
assert_eq 2 "$rc" 'headless invocation exits 2'
assert_grep 'refusing to start a headless session' "$out" 'the refusal says why and where to run it'

echo "== 5. TTY present -> execs csl remote with the args (pty harness, stubbed csl) =="
STUBCSL="$SB/csl-stub"; ARGVLOG="$SB/argv.log"
printf '#!/bin/sh\nprintf "%%s\\n" "$@" > "%s"\nexit 0\n' "$ARGVLOG" > "$STUBCSL"
chmod +x "$STUBCSL"
# Drive the script under a pty so stdin IS a tty; the stub records what csl was called with.
pty_out="$(LAUNCH_REMOTE_WINDOW_CSL="$STUBCSL" python3 - "$SCRIPT" <<'PY'
import os, pty, select, sys, time
script = sys.argv[1]
pid, fd = pty.fork()
if pid == 0:
    os.execve("/bin/bash", ["bash", script, "nvidia-x", "--effort", "max",
                             "--prompt", "Read /tmp/handoff.md first and follow it exactly"],
              {**os.environ, "LAUNCH_REMOTE_WINDOW_CSL": os.environ["LAUNCH_REMOTE_WINDOW_CSL"], "TERM": "xterm"})
buf = b""
end = time.time() + 15
while time.time() < end:
    r, _, _ = select.select([fd], [], [], 0.05)
    if r:
        try:
            c = os.read(fd, 65536)
        except OSError:
            break
        if not c:
            break
        buf += c
    done, status = os.waitpid(pid, os.WNOHANG)
    if done:
        break
try:
    _, status = os.waitpid(pid, 0)
    print("EXIT=" + str(os.waitstatus_to_exitcode(status)))
except ChildProcessError:
    print("EXIT=" + str(os.waitstatus_to_exitcode(status)))
PY
)"
assert_grep 'EXIT=0' "$pty_out" 'under a pty the launch succeeds (exit 0)'
assert_eq 'remote
nvidia-x
--effort
max
--prompt
Read /tmp/handoff.md first and follow it exactly' "$(cat "$ARGVLOG")" \
  'csl was execed as: csl remote <alias> --effort max --prompt <text>'

echo "== 6. SKILL.md uses the prompt-LINK form and names the launcher =="
assert_grep 'launch-remote-window.sh <alias>' "$(cat "$SKILL")" 'SKILL.md launch step names launch-remote-window.sh'
assert_grep -- '--prompt "Read <handoff.md> first' "$(cat "$SKILL")" 'SKILL.md passes the handoff as --prompt link text'
if grep -qF -- '--prompt-file <handoff.md>' "$SKILL"; then check 1 'no --prompt-file launch instruction left in SKILL.md'; else check 0 'no --prompt-file launch instruction left in SKILL.md'; fi
assert_grep -- '--expect' "$(cat "$SKILL")" 'handover-verify note kept'
assert_grep 'the handoff PATH DOES land in the transcript' "$(cat "$SKILL")" 'verify note explains the link form lands the path in the transcript'

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

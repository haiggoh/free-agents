#!/usr/bin/env bash
# C0 (v0.46.0): --prompt-file / --prompt — a launch can carry its first prompt.
#
# A prompt-less launch yields an idle session (measured twice on 2026-10-25: the Step 5
# launch, then the GLM launch). The PreToolUse guard blocks agent-side launches without
# one; THIS is the supported path. Validation fails BEFORE any catalog or proxy work.
#
# The argv proof runs the REAL launch choreography with PATH-stubbed claude and the
# documented LA_LITELLM_CMD test stub (a stub proxy that answers the health endpoint);
# the stub claude records its argv to a temp file and never prints real argv.
#
# Usage: bash tests/test_c0_prompt_launch.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
RS="$ROOT/bin/remote-session.sh"
HV="$ROOT/bin/handover-verify.sh"
ALIAS="nvidia-glm53-full-thinking"   # resolves with a catalog read only, no generation
passed=0; failed=0
# Two call shapes:  check <rc> -eq <want> "<label>"     (a comparison)
#                   check <0|1>      "<label>"          (an already-computed verdict)
check() {
  local ok=1 label
  if [ "$#" -eq 2 ]; then
    label="$2"; [ "$1" = "0" ] && ok=0
  else
    label="$4"
    if [ "$2" = "-eq" ]; then [ "$1" -eq "$3" ] && ok=0
    elif [ "$2" = "-ne" ]; then [ "$1" -ne "$3" ] && ok=0
    fi
  fi
  if [ "$ok" -eq 0 ]; then passed=$((passed + 1)); echo "  PASS: $label";
  else failed=$((failed + 1)); echo "  FAIL: $label"; fi
}

T="$(mktemp -d "${TMPDIR:-/tmp}/c0-test-XXXXXX")"
# Safety net: a stub proxy that outlives this run holds a port in the launcher's range forever
# (2026-10-27: eight leaked stubs filled 4141-4151, so this suite AND real launches found no free
# port). The launcher's own teardown is asserted below; this only guarantees no leak on failure.
_c0_cleanup() {
  local pid
  for pid in $(pgrep -f "$T/stubs/litellm" 2>/dev/null); do kill "$pid" 2>/dev/null; done
  rm -rf "$T"
}
trap _c0_cleanup EXIT

# ---------------------------------------------------------------------------
echo "== C0c: validation fails before any work, naming the path =="

out=$("$RS" "$ALIAS" --prompt-file "$T/missing.md" 2>&1); rc=$?
check $rc -eq 2 "a MISSING prompt file is refused (exit 2, got $rc)"
case "$out" in *"$T/missing.md"*) check 0 "the refusal names the missing path";;
                 *) check 1 "the refusal names the missing path (got: $out)";; esac
case "$out" in *DRY\ RUN*|*litellm*) check 1 "refusal fires BEFORE any proxy/catalog work";;
                 *) check 0 "refusal fires BEFORE any proxy/catalog work";; esac

: > "$T/empty.md"
out=$("$RS" "$ALIAS" --prompt-file "$T/empty.md" 2>&1); rc=$?
check $rc -eq 2 "a ZERO-BYTE prompt file is refused (C0c)"
case "$out" in *"$T/empty.md"*) check 0 "the empty-file refusal names the path";;
               *) check 1 "the empty-file refusal names the path";; esac

printf '   \n\t\n' > "$T/ws.md"
out=$("$RS" "$ALIAS" --prompt-file "$T/ws.md" 2>&1); rc=$?
check $rc -eq 2 "a WHITESPACE-ONLY prompt file is refused (C0c)"
case "$out" in *"$T/ws.md"*) check 0 "the whitespace refusal names the path";;
               *) check 1 "the whitespace refusal names the path";; esac

out=$("$RS" "$ALIAS" --prompt "" 2>&1); rc=$?
check $rc -eq 2 "an empty --prompt is refused (C0c)"

out=$("$RS" "$ALIAS" --prompt-file "$T/empty.md" --prompt "x" 2>&1); rc=$?
check $rc -eq 2 "--prompt-file and --prompt together are refused (mutually exclusive)"

out=$("$RS" --list --prompt-file "$T/empty.md" 2>&1); rc=$?
check $rc -eq 2 "--prompt-file with a non-launch mode is refused"

# ---------------------------------------------------------------------------
echo "== C0: --help documents both flags =="
"$RS" --help > "$T/help.txt" 2>&1; rc=$?
check $rc -eq 0 "--help exits 0"
/usr/bin/grep -q -- '--prompt-file' "$T/help.txt"; check $? -eq 0 "--help documents --prompt-file"
/usr/bin/grep -q -- '--prompt ' "$T/help.txt"; check $? -eq 0 "--help documents --prompt"

# ---------------------------------------------------------------------------
echo "== C0: dry-run names the prompt source, never its content =="
printf 'Read the handoff at ~/.claude/plans/h.md first, then continue autonomously.' > "$T/prompt.md"
out=$("$RS" "$ALIAS" --prompt-file "$T/prompt.md" --dry-run 2>&1); rc=$?
check $rc -eq 0 "a valid prompt file + --dry-run completes (got $rc)"
case "$out" in *"$T/prompt.md"*) check 0 "the dry-run names the prompt source file";;
               *) check 1 "the dry-run names the prompt source file";; esac
case "$out" in *Read\ the\ handoff*) check 1 "the dry-run never prints the prompt CONTENT";;
               *) check 0 "the dry-run never prints the prompt CONTENT";; esac
out=$("$RS" "$ALIAS" --prompt 'C0SECRETNEVERPRINT' --dry-run 2>&1); rc=$?
check $rc -eq 0 "--prompt + --dry-run completes"
case "$out" in *C0SECRETNEVERPRINT*) check 1 "--prompt content never appears in the dry-run";;
               *) check 0 "--prompt content never appears in the dry-run";; esac

# ---------------------------------------------------------------------------
echo "== C0: the prompt reaches the claude invocation (stub claude records argv) =="
mkdir -p "$T/stubs" "$T/keys"
printf '#!/bin/sh\nif [ "${1:-}" = "--version" ] || [ "${1:-}" = "-v" ]; then echo "2.1.304 (Claude Code)"; exit 0; fi\nprintf %%s "$*" >> "$C0_ARGV_LOG"\nprintf "\\n" >> "$C0_ARGV_LOG"\nexit 0\n' > "$T/stubs/claude"
chmod 755 "$T/stubs/claude"
# Named `litellm` on purpose: the launcher tears down only a process it can identify as ITS proxy
# (_is_our_proxy: "litellm" + proxy-<port>.yaml in argv). A stub_proxy.py name failed that check,
# so every run of this suite leaked one listening stub.
printf '#!/usr/bin/env python3\nimport sys\nport = 4141\nargs = sys.argv[1:]\nfor i, a in enumerate(args):\n    if a == "--port" and i + 1 < len(args):\n        port = int(args[i + 1])\nimport http.server\nhttp.server.test(HandlerClass=http.server.SimpleHTTPRequestHandler, port=port)\n' > "$T/stubs/litellm"
chmod 755 "$T/stubs/litellm"
mkdir -p "$T/keys"
printf 'sk-test-fixture-not-a-real-key\n' > "$T/keys/nvidia"
chmod 600 "$T/keys/nvidia"
printf 'C0INITIALPROMPTMARKER read the handoff and continue\n' > "$T/launch.md"

export C0_ARGV_LOG="$T/argv.log"; : > "$C0_ARGV_LOG"
# TMPDIR sandboxes the launcher's RUNDIR (live sessions keep their proxy pid/owner files in the
# real one) and a dedicated port range keeps this run off the ports live sessions use.
PATH="$T/stubs:$PATH" LA_LITELLM_CMD="$T/stubs/litellm" \
    LA_API_KEYS_DIR="$T/keys" TMPDIR="$T" \
    LA_REMOTE_PROXY_PORT_MIN=4180 LA_REMOTE_PROXY_PORT_MAX=4189 \
    timeout 120 bash "$RS" "$ALIAS" --prompt-file "$T/launch.md" >/dev/null 2>&1
lrc=$?
unset C0_ARGV_LOG
if /usr/bin/grep -q 'C0INITIALPROMPTMARKER' "$T/argv.log" 2>/dev/null; then
    check 0 "the prompt text reaches the claude invocation (stub recorded argv)"
else
    check 1 "the prompt text reaches the claude invocation (launch rc=$lrc, argv log: $(/bin/cat "$T/argv.log" 2>/dev/null | head -c 120))"
fi
if /usr/bin/grep -q -- '--prompt-file' "$T/argv.log" 2>/dev/null; then
    check 1 "the FILE PATH is not passed to claude (its CONTENT is)"
else
    check 0 "the FILE PATH is not passed to claude (its CONTENT is)"
fi

if pgrep -f "$T/stubs/litellm" >/dev/null 2>&1; then
    check 1 "the launch tore down the proxy it started (a stub is still running)"
else
    check 0 "the launch tore down the proxy it started"
fi

# ---------------------------------------------------------------------------
echo "== C0b: handover-verify proves the first prompt ARRIVED, not that a port listens =="
SD="$T/sessions"; PD="$T/projects/-Users-test-fake-cwd"
mkdir -p "$SD" "$PD"
printf '{"pid": 424242, "sessionId": "sid-fresh", "startedAt": 1}\n' > "$SD/424242.json"
# An idle stub: the registry entry exists but NO transcript ever appeared.
"$HV" --expect 'the-handoff-path.md' --pid 424242 --timeout 1 \
      --sessions-dir "$SD" --projects-dir "$T/projects" > "$T/hv1.txt" 2>&1
check $? -eq 1 "an idle stub session (no transcript) => NOT_VERIFIED (exit 1)"
/usr/bin/grep -q 'NOT_VERIFIED' "$T/hv1.txt"; check $? -eq 0 "and it says NOT_VERIFIED"
# The transcript appears (the first prompt landed) -> VERIFIED.
printf '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"Read the-handoff-path.md first and follow it"}]}}\n' > "$PD/sid-fresh.jsonl"
"$HV" --expect 'the-handoff-path.md' --pid 424242 --timeout 3 \
      --sessions-dir "$SD" --projects-dir "$T/projects" > "$T/hv2.txt" 2>&1
check $? -eq 0 "the transcript containing the handoff path => VERIFIED (exit 0)"
/usr/bin/grep -q 'VERIFIED' "$T/hv2.txt" && /usr/bin/grep -q -v 'NOT_VERIFIED' "$T/hv2.txt"
check $? -eq 0 "and it says VERIFIED"
"$HV" --expect 'nope' --pid 424242 --timeout 1 \
      --sessions-dir "$SD" --projects-dir "$T/projects" > "$T/hv3.txt" 2>&1
check $? -eq 1 "an unrelated transcript => NOT_VERIFIED"
"$HV" --expect 'x' --pid 999999 --timeout 1 \
      --sessions-dir "$SD" --projects-dir "$T/projects" > "$T/hv4.txt" 2>&1
check $? -eq 1 "an unknown pid (no registry entry) => NOT_VERIFIED"

echo
echo "$passed passed, $failed failed"
[ "$failed" -eq 0 ]

#!/bin/bash
# la-reboot.sh — restart a CRASHED local model server IN PLACE: same port, same argv.
#
# WHY THIS EXISTS
# A long local session eventually hits Rapid's Metal admission cap and dies mid-turn:
#   API Error: 503 Server is busy (max concurrent requests reached). ... needs
#   approximately 0.0 GB of Metal memory on top of 107.2 GB already in use ... but the
#   current limit is 103.9 GB (D-METAL-CAP).
# The session is still open and still holds its whole conversation, but its backend is
# gone. The only recovery used to be: exit the session, run la-evict.sh, start a NEW
# session, then /resume — which pays for a full model reload AND a context replay, and
# loses the tail of the work.
#
# This script restarts the SAME model on the SAME port with the SAME arguments, so the
# still-open session simply reconnects on its next request and carries on. No exit, no
# /resume.
#
# IT IS A BANDAID, ON PURPOSE.
# Running out of memory is not how local inference is meant to work. The root cause —
# why residency climbs to the cap at all — is tracked separately; this only shortens the
# recovery. If you find yourself running it often, that is data for the root-cause work,
# not a reason to automate this.
#
# WHY IT IS THE ONLY SCRIPT ALLOWED TO KILL A SESSION PORT
# Everything else in this repo refuses to touch ports 8000-8010 because a live session
# may be attached. Here that is the entire point: the server on that port is already
# broken, and the attached session is what we are rescuing. So the guards are strict:
#
#   * It acts on exactly ONE port — never a range, never a scan-and-pick.
#   * It requires POSITIVE EVIDENCE of a crash. Liveness is not the test: an
#     OOM-refusing Rapid server still answers /v1/models perfectly well while failing
#     every real request. Evidence is a failed completion probe, a dead listener, or a
#     D-METAL-CAP / 503 / OOM signature in the log tail.
#   * A server that still completes a request is REFUSED unless you pass --force.
#   * The argv is captured from the live process BEFORE anything is killed. After the
#     kill that information is gone for good, and re-deriving the command from current
#     config would silently launch something else if the config changed since launch.
#
# Usage: la-reboot.sh [--port N] [--force] [--dry-run] [--wait N] [--status]
#
#   --port N     port to rescue. Default: the port of the local session attached to
#                this terminal's ANTHROPIC_BASE_URL, else the single busy session port
#                if exactly one is found.
#   --force      reboot even when the server still answers a completion probe.
#   --dry-run    print the plan (including the exact argv) and change nothing.
#   --wait N     seconds to wait for readiness after relaunch (default 180).
#   --status     report what would be targeted and why, then exit.
#
# Exit: 0 rebooted (or dry-run/status/no-op completed)
#       1 nothing to reboot / readiness never reached
#       2 usage error
#       3 refused — the server looks healthy (use --force)

set -u

CONFIG_DIR="${LA_CONFIG_DIR_LOGS:-$HOME/.claude/logs/local-agents-configs}"
LOG_DIR="${LA_REBOOT_LOG_DIR:-$HOME/.claude/logs}"
TERM_WAIT="${LA_REBOOT_TERM_WAIT:-8}"
READY_WAIT="${LA_REBOOT_READY_WAIT:-180}"
PROBE_TIMEOUT="${LA_REBOOT_PROBE_TIMEOUT:-20}"

PORT=""; FORCE=0; DRY_RUN=0; STATUS_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port)       shift; PORT="${1:-}" ;;
        --force)      FORCE=1 ;;
        --dry-run|-n) DRY_RUN=1 ;;
        --status)     STATUS_ONLY=1 ;;
        --wait)       shift; READY_WAIT="${1:-180}" ;;
        -h|--help)    sed -n '2,52p' "$0"; exit 0 ;;
        *)            echo "unknown argument: $1" >&2
                      echo "usage: $(basename "$0") [--port N] [--force] [--dry-run] [--wait N] [--status]" >&2
                      exit 2 ;;
    esac
    shift
done

case "$PORT" in
    "" ) ;;
    *[!0-9]* ) echo "--port must be a number, got '$PORT'" >&2; exit 2 ;;
esac

# --- which port? --------------------------------------------------------------
# Prefer this shell's own session endpoint: if you are inside the broken session, that
# is unambiguously the server you mean. Otherwise fall back to the attached session
# ports, and refuse to guess when there is more than one.
detect_port() {
    case "${ANTHROPIC_BASE_URL:-}" in
        *localhost:*|*127.0.0.1:*)
            printf '%s\n' "${ANTHROPIC_BASE_URL##*:}" | tr -dc '0-9'
            return ;;
    esac
    for cpid in $(pgrep -x claude 2>/dev/null); do
        ps -Eww -p "$cpid" 2>/dev/null | tr ' ' '\n' \
            | sed -n \
                -e 's|^ANTHROPIC_BASE_URL=http://localhost:\([0-9]*\).*|\1|p' \
                -e 's|^ANTHROPIC_BASE_URL=http://127\.0\.0\.1:\([0-9]*\).*|\1|p'
    done | sort -u
}

if [ -z "$PORT" ]; then
    found="$(detect_port | tr '\n' ' ')"
    n=$(printf '%s' "$found" | wc -w | tr -d ' ')
    if [ "$n" = 1 ]; then
        PORT="$(printf '%s' "$found" | tr -d ' ')"
        echo "Target port $PORT (from the attached local session)."
    elif [ "$n" = 0 ]; then
        echo "No local session port found, and no --port given." >&2
        echo "Pass --port N explicitly (the port your session was served on)." >&2
        exit 2
    else
        echo "Several local session ports found: $found" >&2
        echo "Refusing to guess — pass --port N." >&2
        exit 2
    fi
fi

META="$CONFIG_DIR/server_${PORT}.meta"
meta_get() { awk -F= -v k="$1" '$1==k{print substr($0,index($0,"=")+1)}' "$META" 2>/dev/null; }

PID="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"

# --- evidence: is it actually broken? ----------------------------------------
# Three independent signals, cheapest first. None of them is "does it answer /v1/models",
# because an OOM-refusing server answers that fine.
EVIDENCE=""; HEALTHY=0

if [ -z "${PID:-}" ]; then
    EVIDENCE="no listener on :$PORT — the server is gone"
else
    # A real completion is the only probe that walks the path that actually fails.
    probe_body='{"model":"'"$(meta_get served_id)"'","messages":[{"role":"user","content":"1"}],"max_tokens":1}'
    probe="$(curl -s --max-time "$PROBE_TIMEOUT" -w '\n%{http_code}' \
             -X POST "http://127.0.0.1:$PORT/v1/chat/completions" \
             -H 'Content-Type: application/json' -d "$probe_body" 2>/dev/null)"
    code="$(printf '%s' "$probe" | tail -1)"
    case "$code" in
        200) HEALTHY=1; EVIDENCE="a completion probe SUCCEEDED (HTTP 200) — this server is working" ;;
        503) EVIDENCE="completion probe returned 503 (server busy / admission refused)" ;;
        "")  EVIDENCE="completion probe got no response (wedged or not listening)" ;;
        *)   EVIDENCE="completion probe returned HTTP $code" ;;
    esac
    # Log evidence CORROBORATES; it never overrules the probe. A log is a historical
    # record: a D-METAL-CAP line from an OOM the server already recovered from sits in
    # the tail forever, so treating it as decisive would reboot a perfectly healthy
    # server. Measured on this machine 2026-09-13 — a live probe returned 200 while the
    # tail still held an older Metal failure, and an earlier draft of this script called
    # that "looks broken". A server that completes a request is working, full stop.
    for lf in "$LOG_DIR"/*_"$PORT".log; do
        [ -f "$lf" ] || continue
        if tail -n 200 "$lf" 2>/dev/null | grep -qiE 'D-METAL-CAP|out of memory|kIOGPU|Metal memory'; then
            if [ "$HEALTHY" = 1 ]; then
                EVIDENCE="$EVIDENCE (the log tail also holds an OLDER Metal/OOM failure, which the successful probe overrules)"
            else
                EVIDENCE="$EVIDENCE; the log tail shows a Metal/OOM failure"
            fi
            break
        fi
    done
fi

echo "=== la-reboot :$PORT ==="
if [ -n "${PID:-}" ]; then
    echo "  listener   pid $PID"
else
    echo "  listener   none"
fi
echo "  alias      $(meta_get alias)"
echo "  model      $(meta_get model_dir)"
echo "  served id  $(meta_get served_id)"
echo "  evidence   $EVIDENCE"

if [ "$STATUS_ONLY" = 1 ]; then
    if [ "$HEALTHY" = 1 ]; then echo "RESULT: healthy — a reboot would need --force."
    elif [ -z "${PID:-}" ]; then echo "RESULT: no listener — a reboot needs the recorded argv, see below."
    else echo "RESULT: looks broken — a reboot would proceed."; fi
fi

# --- capture the exact argv BEFORE touching anything -------------------------
# ps joins arguments with spaces, so any argument containing one (Rapid's
# --speculative-config JSON does not today, but a path or prompt could) is corrupted by
# word-splitting. KERN_PROCARGS2 gives the true NUL-separated vector.
ARGV_FILE="$(mktemp -t lareboot)"
cleanup() { rm -f "$ARGV_FILE"; }
trap cleanup EXIT

capture_argv() {
    [ -n "${PID:-}" ] || return 1
    python3 - "$1" "$ARGV_FILE" <<'PY'
import ctypes, ctypes.util, struct, sys
pid = int(sys.argv[1]); out = sys.argv[2]
libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
mib = (ctypes.c_int * 3)(1, 49, pid)   # CTL_KERN, KERN_PROCARGS2
size = ctypes.c_size_t(0)
if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0: sys.exit(1)
buf = ctypes.create_string_buffer(size.value)
if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0: sys.exit(1)
argc = struct.unpack("i", buf.raw[:4])[0]
# layout: argc, exec_path\0, padding\0*, argv[0..argc-1]\0, env...
rest = buf.raw[4:]
chunks = rest.split(b"\0")
i = 0
while i < len(chunks) and chunks[i] == b"": i += 1
i += 1                                  # skip exec_path
while i < len(chunks) and chunks[i] == b"": i += 1
args = chunks[i:i+argc]
if len(args) != argc or not args[0]: sys.exit(1)
with open(out, "wb") as f:
    f.write(b"\0".join(args))
PY
}

if [ -n "${PID:-}" ]; then
    if capture_argv "$PID"; then
        ARGC=$(python3 -c "import sys;d=open(sys.argv[1],'rb').read();print(len(d.split(b'\0')))" "$ARGV_FILE")
        echo "  argv       captured from the live process ($ARGC args)"
        cp -f "$ARGV_FILE" "$CONFIG_DIR/server_${PORT}.argv" 2>/dev/null || true
    else
        echo "  argv       ⚠️  could not read the live argv" >&2
    fi
fi
# A dead listener leaves nothing to read, so fall back to the argv recorded on the last
# successful capture for this port. Without it there is genuinely nothing to relaunch —
# say so rather than inventing a command from current config.
if [ ! -s "$ARGV_FILE" ]; then
    if [ -s "$CONFIG_DIR/server_${PORT}.argv" ]; then
        cp -f "$CONFIG_DIR/server_${PORT}.argv" "$ARGV_FILE"
        echo "  argv       recovered from the recorded launch for :$PORT"
    else
        echo
        echo "Nothing to reboot on :$PORT: no live process to read the argv from, and no"
        echo "recorded argv at $CONFIG_DIR/server_${PORT}.argv."
        echo "RESULT: nothing to reboot — relaunch with local-llm-hotswap.sh instead."
        exit 1
    fi
fi

[ "$STATUS_ONLY" = 1 ] && exit 0

# --- refuse a healthy server --------------------------------------------------
if [ "$HEALTHY" = 1 ] && [ "$FORCE" != 1 ]; then
    echo
    echo "REFUSING: :$PORT answered a real completion, so it is not the crashed server this"
    echo "  script exists to rescue. Rebooting it would cut a working backend out from under"
    echo "  whatever is using it. Pass --force if you truly mean to restart it anyway."
    echo "RESULT: refused — server healthy. Use --force to override."
    exit 3
fi

show_argv() {
    python3 -c "
import sys
d=open(sys.argv[1],'rb').read().split(b'\0')
print('    ' + ' '.join(repr(x.decode()) if b' ' in x else x.decode() for x in d))
" "$ARGV_FILE"
}

if [ "$DRY_RUN" = 1 ]; then
    echo
    echo "  [dry-run] would stop pid ${PID:-<none>} and relaunch on :$PORT with:"
    show_argv
    echo "RESULT: dry run — nothing was stopped or started."
    exit 0
fi

# --- stop -------------------------------------------------------------------
if [ -n "${PID:-}" ]; then
    if ! ps -o user= -p "$PID" 2>/dev/null | grep -qx "$(id -un)"; then
        echo "pid $PID is not owned by $(id -un) — refusing" >&2
        exit 2
    fi
    echo
    echo "  → stopping pid $PID"
    kill -TERM "$PID" 2>/dev/null
    waited=0
    while [ "$waited" -lt "$TERM_WAIT" ]; do
        kill -0 "$PID" 2>/dev/null || break
        sleep 1; waited=$((waited + 1))
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo "    SIGTERM ignored after ${TERM_WAIT}s — SIGKILL"
        kill -9 "$PID" 2>/dev/null
        sleep 1
    fi
    kill -0 "$PID" 2>/dev/null && { echo "    ⚠️  pid $PID still alive — aborting before relaunch" >&2; exit 1; }
    echo "    stopped"
fi

# The port must be genuinely free before relaunching, or the new server races the old
# socket and dies with EADDRINUSE — which would look like a reboot failure.
freed=0
for _ in $(seq 1 20); do
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1 || { freed=1; break; }
    sleep 1
done
[ "$freed" = 1 ] || { echo "  ⚠️  :$PORT still in use after the stop — aborting" >&2; exit 1; }

# --- relaunch with the captured argv ----------------------------------------
LOG_FILE="$LOG_DIR/vllm_${PORT}.log"
echo "  → relaunching on :$PORT with the captured argv"
python3 - "$ARGV_FILE" "$LOG_FILE" <<'PY'
import os, sys
argv = open(sys.argv[1], "rb").read().split(b"\0")
argv = [a.decode() for a in argv if a]
log = open(sys.argv[2], "ab", buffering=0)
pid = os.fork()
if pid == 0:
    os.setsid()
    os.dup2(log.fileno(), 1); os.dup2(log.fileno(), 2)
    devnull = os.open(os.devnull, os.O_RDONLY); os.dup2(devnull, 0)
    env = dict(os.environ); env["RAPID_MLX_TELEMETRY"] = "0"
    try:
        os.execve(argv[0], argv, env)
    except Exception:
        os._exit(127)
print(pid)
PY
NEW_PID=$(tail -1 "$LOG_FILE" >/dev/null 2>&1; :)
sleep 1
NEW_PID="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"

# --- wait for readiness -----------------------------------------------------
# Report by OUTCOME: the port answering /v1/models with the id the session will ask for.
WANT_ID="$(meta_get served_id)"
echo "  → waiting up to ${READY_WAIT}s for :$PORT to serve '${WANT_ID:-any}'"
ready=0
for _ in $(seq 1 "$READY_WAIT"); do
    # Tolerate whitespace after the colon. Rapid emits compact JSON ("id":"x") so the
    # tighter pattern used elsewhere in this repo happens to work, but a server that
    # pretty-prints ("id": "x") would make this loop silently never match and report a
    # working server as "not ready" — measured 2026-09-13 against a test server.
    ids="$(curl -s --max-time 3 "http://127.0.0.1:$PORT/v1/models" 2>/dev/null \
           | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    if [ -n "$ids" ]; then
        if [ -z "$WANT_ID" ] || printf '%s\n' "$ids" | grep -qxF "$WANT_ID"; then ready=1; break; fi
    fi
    sleep 1
done

NEW_PID="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
if [ "$ready" != 1 ]; then
    echo
    echo "  ⚠️  :$PORT did not become ready within ${READY_WAIT}s. Log tail:"
    tail -n 15 "$LOG_FILE" 2>/dev/null | sed 's/^/      /'
    echo "RESULT: relaunched but not ready — see $LOG_FILE"
    exit 1
fi

# Refresh the recorded pid so the reuse checks in hotswap/la-ram-preflight keep matching.
if [ -f "$META" ] && [ -n "${NEW_PID:-}" ]; then
    tmp="${META}.tmp.$$"
    grep -v '^pid=' "$META" > "$tmp" 2>/dev/null
    echo "pid=$NEW_PID" >> "$tmp"
    mv -f "$tmp" "$META"
fi

echo
echo "  ✅ :$PORT is serving '${WANT_ID:-?}' again (pid ${NEW_PID:-?})"
echo "RESULT: rebooted :$PORT — your open session should continue on its next request."
echo "REBOOT_PORT=$PORT"
exit 0

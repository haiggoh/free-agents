#!/usr/bin/env bash
# Framework-free integration tests for bin/la-evict.sh.
#
# Uses controlled listener processes on dynamically selected high ports.
# It never scans or modifies the normal local-agents range 8000–8010.

set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SCRIPT="$REPO/bin/la-evict.sh"

PASS=0
FAIL=0

check() {
    local status="$1"
    local label="$2"

    if [ "$status" -eq 0 ]; then
        PASS=$((PASS + 1))
        printf '  PASS: %s\n' "$label"
    else
        FAIL=$((FAIL + 1))
        printf '  FAIL: %s\n' "$label"
    fi
}

assert_contains() {
    local needle="$1"
    local haystack="$2"
    local label="$3"

    printf '%s' "$haystack" | grep -qF -- "$needle"
    check "$?" "$label"
}

is_running() {
    local pid="$1"
    local stat

    kill -0 "$pid" 2>/dev/null || return 1
    stat="$(/bin/ps -o stat= -p "$pid" 2>/dev/null | tr -d ' ')"
    case "$stat" in
        ""|Z*) return 1 ;;
        *) return 0 ;;
    esac
}

assert_running() {
    local pid="$1"
    local label="$2"

    is_running "$pid"
    check "$?" "$label"
}

wait_for_stopped() {
    local pid="$1"
    local attempt=0

    while [ "$attempt" -lt 50 ]; do
        is_running "$pid" || {
            wait "$pid" 2>/dev/null || true
            return 0
        }
        sleep 0.1
        attempt=$((attempt + 1))
    done

    return 1
}

assert_stopped() {
    local pid="$1"
    local label="$2"

    wait_for_stopped "$pid"
    check "$?" "$label"
}

for command in python3 lsof ps pgrep vm_stat awk grep sed sort seq; do
    command -v "$command" >/dev/null 2>&1 || {
        printf 'Missing required command: %s\n' "$command" >&2
        exit 2
    }
done

[[ -f "$SCRIPT" ]] || {
    printf 'Missing script under test: %s\n' "$SCRIPT" >&2
    exit 2
}

SB="$(mktemp -d "${TMPDIR:-/tmp}/la-evict-test.XXXXXX")"
TEST_HOME="$SB/home"
LOG_DIR="$TEST_HOME/.claude/logs"
MOCK_BIN="$SB/mock-bin"
PYTHON="$(command -v python3)"
REAL_PATH="$PATH"
PIDS=()

mkdir -p "$LOG_DIR" "$MOCK_BIN"

cleanup() {
    local pid

    for pid in "${PIDS[@]:-}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done

    sleep 0.2

    for pid in "${PIDS[@]:-}"; do
        kill -KILL "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done

    rm -rf "$SB"
}
trap cleanup EXIT

# Find three consecutive unused high ports. They are held together during each
# probe to avoid selecting a partially occupied range.
BASE="$(
"$PYTHON" - <<'PY'
import socket

for base in range(20000, 45000):
    sockets = []
    try:
        for port in range(base, base + 3):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", port))
            sockets.append(sock)
    except OSError:
        for sock in sockets:
            sock.close()
        continue

    for sock in sockets:
        sock.close()
    print(base)
    raise SystemExit(0)

raise SystemExit("No three-port test range available")
PY
)" || exit 2

[[ "$BASE" =~ ^[0-9]+$ ]] || {
    printf 'Invalid test-port base: %s\n' "$BASE" >&2
    exit 2
}

cat > "$SB/server.py" <<'PY'
#!/usr/bin/env python3
import signal
import socket
import sys

port = int(sys.argv[1])
mode = sys.argv[2] if len(sys.argv) > 2 else "normal"

if mode == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", port))
sock.listen(8)

while True:
    conn, _ = sock.accept()
    conn.close()
PY

cp "$SB/server.py" "$SB/rapid-mlx-server.py"
cp "$SB/server.py" "$SB/vllm-mlx-server.py"
cp "$SB/server.py" "$SB/unknown-server.py"

LAST_PID=""

wait_for_listener() {
    local port="$1"
    local expected_pid="$2"
    local attempt=0
    local observed

    while [ "$attempt" -lt 50 ]; do
        observed="$(
            lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null |
            head -1
        )"

        if [ "$observed" = "$expected_pid" ]; then
            return 0
        fi

        if [ -n "$observed" ] && [ "$observed" != "$expected_pid" ]; then
            printf 'Port %s belongs to unexpected pid %s\n' \
                "$port" "$observed" >&2
            return 1
        fi

        sleep 0.1
        attempt=$((attempt + 1))
    done

    return 1
}

start_server() {
    local label="$1"
    local port="$2"
    local mode="${3:-normal}"
    local script="$SB/${label}-server.py"
    local pid

    "$PYTHON" "$script" "$port" "$mode" \
        >"$SB/${label}-${port}.log" 2>&1 &
    pid=$!
    PIDS+=("$pid")

    wait_for_listener "$port" "$pid" || {
        printf 'Controlled server failed to listen: %s pid=%s port=%s\n' \
            "$label" "$pid" "$port" >&2
        exit 2
    }

    LAST_PID="$pid"
}

STATE_FILE="$SB/evict.state"
PORT_START="$BASE"
PORT_MAX="$BASE"
TEST_PATH="$REAL_PATH"

run_evict() {
    env \
        HOME="$TEST_HOME" \
        PATH="$TEST_PATH" \
        LA_PORT_START="$PORT_START" \
        LA_PORT_MAX="$PORT_MAX" \
        LA_EVICT_EXTRA_PORTS="" \
        LA_EVICT_STATE="$STATE_FILE" \
        LA_EVICT_LOG_DIR="$LOG_DIR" \
        LA_EVICT_WINDOW=600 \
        LA_EVICT_YOUNG=120 \
        LA_EVICT_TERM_WAIT=1 \
        /bin/bash "$SCRIPT" "$@"
}

printf 'Using controlled test ports %s–%s\n' "$BASE" "$((BASE + 2))"

# ---------------------------------------------------------------------------
printf '\n== status, dry run, ranking, and escalation ==\n'

PORT_START="$BASE"
PORT_MAX="$((BASE + 1))"
STATE_FILE="$SB/ranking.state"
TEST_PATH="$REAL_PATH"

start_server rapid-mlx "$BASE"
RAPID_PID="$LAST_PID"

sleep 2

start_server vllm-mlx "$((BASE + 1))"
VLLM_PID="$LAST_PID"

OUT="$(run_evict --status)"
STATUS=$?
check "$STATUS" "status exits successfully"
assert_contains ":$BASE" "$OUT" "status lists the older Rapid server"
assert_contains ":$((BASE + 1))" "$OUT" "status lists the newer vllm server"
assert_running "$RAPID_PID" "status does not stop Rapid"
assert_running "$VLLM_PID" "status does not stop vllm"

OUT="$(run_evict --dry-run)"
STATUS=$?
check "$STATUS" "dry run exits successfully"
assert_contains \
    "would evict vllm-mlx on :$((BASE + 1))" \
    "$OUT" \
    "dry run lists the newest unattached server"
assert_contains \
    "would evict rapid-mlx on :$BASE" \
    "$OUT" \
    "dry run lists EVERY unattached server, not only the first"
assert_running "$RAPID_PID" "dry run leaves Rapid alive"
assert_running "$VLLM_PID" "dry run leaves vllm alive"

OUT="$(run_evict)"
STATUS=$?
check "$STATUS" "first eviction exits successfully"
assert_contains \
    "evicting vllm-mlx on :$((BASE + 1))" \
    "$OUT" \
    "first invocation evicts the newest unattached server"
assert_contains \
    "evicting rapid-mlx on :$BASE" \
    "$OUT" \
    "tier 0 sweeps EVERY unattached server in one pass"
assert_stopped "$VLLM_PID" "newest unattached server stopped"
assert_stopped "$RAPID_PID" "older unattached server stopped too"

OUT="$(run_evict)"
STATUS=$?
check "$STATUS" "second eviction exits successfully"
assert_contains \
    "nothing to evict" \
    "$OUT" \
    "a second invocation with an empty inventory reports nothing to do"

# ---------------------------------------------------------------------------
printf '\n== attached-session guard and explicit escalation ==\n'

PORT_START="$BASE"
PORT_MAX="$BASE"
STATE_FILE="$SB/attached.state"

start_server rapid-mlx "$BASE"
ATTACHED_SERVER_PID="$LAST_PID"

ATTACHED_FAKE_PID=999999
ATTACHED_PORT="$BASE"

cat > "$MOCK_BIN/pgrep" <<EOF
#!/bin/sh
if [ "\$#" -eq 2 ] && [ "\$1" = "-x" ] && [ "\$2" = "claude" ]; then
    echo "$ATTACHED_FAKE_PID"
    exit 0
fi
exec /usr/bin/pgrep "\$@"
EOF

cat > "$MOCK_BIN/ps" <<EOF
#!/bin/sh
if [ "\$#" -eq 3 ] &&
   [ "\$1" = "-Eww" ] &&
   [ "\$2" = "-p" ] &&
   [ "\$3" = "$ATTACHED_FAKE_PID" ]; then
    echo "$ATTACHED_FAKE_PID claude ANTHROPIC_BASE_URL=http://localhost:$ATTACHED_PORT"
    exit 0
fi

if [ "\$#" -eq 3 ] &&
   [ "\$1" = "-o" ] &&
   [ "\$2" = "command=" ] &&
   [ "\$3" = "$ATTACHED_FAKE_PID" ]; then
    echo "claude --model claude-opus-5"
    exit 0
fi

exec /bin/ps "\$@"
EOF

chmod +x "$MOCK_BIN/pgrep" "$MOCK_BIN/ps"
TEST_PATH="$MOCK_BIN:$REAL_PATH"

OUT="$(run_evict)"
STATUS=$?
check "$STATUS" "tier 0 with only an attached server exits successfully"
assert_contains \
    "still attached" \
    "$OUT" \
    "tier 0 warns which ports are attached"
assert_contains \
    ":$BASE" \
    "$OUT" \
    "the warning names the attached port"
assert_running \
    "$ATTACHED_SERVER_PID" \
    "tier 0 leaves the attached server alive"

# An empty target list must produce no spurious eviction chatter: with only an
# attached server there is nothing unattached to take, so the run reports a warning
# and stops. A stray "not owned by ..." line here means evict_one was called with
# empty fields from a newline left in $targets.
if printf '%s' "$OUT" | grep -qF "not owned"; then
    check 1 "an empty target list emits no spurious eviction line"
else
    check 0 "an empty target list emits no spurious eviction line"
fi

OUT="$(run_evict)"
STATUS=$?
if [ "$STATUS" -eq 3 ]; then
    check 0 "tier 1 holds back when only attached candidates remain"
else
    check 1 "tier 1 holds back when only attached candidates remain"
fi
assert_contains \
    "held back" \
    "$OUT" \
    "tier 1 protects an attached server"
assert_running \
    "$ATTACHED_SERVER_PID" \
    "tier 1 leaves the attached server alive"

OUT="$(run_evict)"
STATUS=$?
check "$STATUS" "tier 2 emergency eviction completes"
assert_contains \
    "escalating to tier 2" \
    "$OUT" \
    "tier 2 says it escalated"
assert_stopped \
    "$ATTACHED_SERVER_PID" \
    "tier 2 evicts the attached server"

# ---------------------------------------------------------------------------
printf '\n== unknown-listener protection ==\n'

PORT_START="$BASE"
PORT_MAX="$BASE"
STATE_FILE="$SB/unknown.state"
TEST_PATH="$REAL_PATH"

start_server unknown "$BASE"
UNKNOWN_PID="$LAST_PID"

OUT="$(run_evict --tier 1)"
STATUS=$?
check "$STATUS" "unknown-listener run completes without shell failure"
assert_contains \
    "unrecognised listener, refusing without --allow-unknown" \
    "$OUT" \
    "unknown listener is refused by default"
assert_running "$UNKNOWN_PID" "unknown listener remains alive"

kill -TERM "$UNKNOWN_PID" 2>/dev/null || true
wait_for_stopped "$UNKNOWN_PID" || true

# ---------------------------------------------------------------------------
printf '\n== TERM-to-KILL fallback ==\n'

PORT_START="$BASE"
PORT_MAX="$BASE"
STATE_FILE="$SB/sigkill.state"
TEST_PATH="$REAL_PATH"

start_server rapid-mlx "$BASE" ignore-term
STUBBORN_PID="$LAST_PID"

OUT="$(run_evict --tier 1)"
STATUS=$?
check "$STATUS" "TERM-to-KILL run exits successfully"
assert_contains \
    "SIGTERM ignored after 1s" \
    "$OUT" \
    "stubborn server triggers SIGKILL fallback"
assert_stopped "$STUBBORN_PID" "stubborn server is ultimately stopped"

# ---------------------------------------------------------------------------
printf '\n== explicit --port targeting ==\n'

# PORT_MAX covers BASE+2 so a request for the still-free middle port is INSIDE
# the configured range: "outside the range" and "nothing listening there" are
# different failures and must never be conflated.
PORT_START="$BASE"
PORT_MAX="$((BASE + 2))"
STATE_FILE="$SB/ported.state"
TEST_PATH="$REAL_PATH"

start_server rapid-mlx "$BASE"
PORT_TARGET_PID="$LAST_PID"

sleep 2

start_server vllm-mlx "$((BASE + 1))"
PORT_OTHER_PID="$LAST_PID"

OUT="$(run_evict --port "$BASE" --dry-run)"
STATUS=$?
check "$STATUS" "port dry run exits successfully"
assert_contains "would evict rapid-mlx on :$BASE" "$OUT" "dry run names the requested port"
assert_running "$PORT_TARGET_PID" "port dry run leaves the requested server alive"
assert_running "$PORT_OTHER_PID" "port dry run leaves the other server alive"

OUT="$(run_evict --port "$BASE")"
STATUS=$?
check "$STATUS" "port eviction exits successfully"
assert_contains "evicting rapid-mlx on :$BASE" "$OUT" "port flag evicts the named port"
assert_stopped "$PORT_TARGET_PID" "named-port server stopped"
assert_running "$PORT_OTHER_PID" "server on another port is untouched"

# Repeated --port is additive: every named port goes, nothing else.
start_server rapid-mlx "$BASE"
PORT_REPEAT_PID="$LAST_PID"

OUT="$(run_evict --port "$((BASE + 1))" --port "$BASE")"
STATUS=$?
check "$STATUS" "repeated port flags exit successfully"
assert_stopped "$PORT_REPEAT_PID" "first repeated port stopped"
assert_stopped "$PORT_OTHER_PID" "second repeated port stopped"

# Usage errors exit 2 BEFORE anything is scanned or touched.
start_server rapid-mlx "$BASE"
PORT_GUARD_PID="$LAST_PID"

OUT="$(run_evict --port 2>&1)"
STATUS=$?
if [ "$STATUS" -eq 2 ]; then check 0 "--port with no value is a usage error"; else check 1 "--port with no value is a usage error"; fi
assert_contains "Usage:" "$OUT" "--port with no value prints a usage line"

OUT="$(run_evict --port 8o2x)"
STATUS=$?
if [ "$STATUS" -eq 2 ]; then check 0 "a non-numeric --port is a usage error"; else check 1 "a non-numeric --port is a usage error"; fi

OUT="$(run_evict --port "$BASE" --all)"
STATUS=$?
if [ "$STATUS" -eq 2 ]; then check 0 "--port combined with --all is a usage error"; else check 1 "--port combined with --all is a usage error"; fi

# THE GUARD THAT KEEPS THE PROXY BAND UNREACHABLE. The LiteLLM band is refused on
# its own, ahead of the range check, so it is protected even if a range override
# happens to include it: an operator's typo must never be able to name a litellm
# port that live sessions depend on.
OUT="$(run_evict --port 4149 2>&1)"
STATUS=$?
if [ "$STATUS" -eq 2 ]; then check 0 "the proxy band is refused outright"; else check 1 "the proxy band is refused outright"; fi
assert_contains "proxy band" "$OUT" "the refusal says it is the proxy band"
assert_running "$PORT_GUARD_PID" "a proxy-band request leaves every server untouched"

# ...and so is the whole band, not just one lucky port.
for _p in 4141 4145 4151; do
    env HOME="$TEST_HOME" PATH="$TEST_PATH" \
        LA_PORT_START="$PORT_START" LA_PORT_MAX="$PORT_MAX" \
        LA_EVICT_EXTRA_PORTS="" LA_EVICT_STATE="$STATE_FILE" \
        LA_EVICT_LOG_DIR="$LOG_DIR" LA_EVICT_TERM_WAIT=1 \
        /bin/bash "$SCRIPT" --port "$_p" >/dev/null 2>&1
    if [ $? -eq 2 ]; then check 0 "proxy-band port $_p is refused"; else check 1 "proxy-band port $_p is refused"; fi
done

OUT="$(run_evict --port 9999 2>&1)"
STATUS=$?
if [ "$STATUS" -eq 2 ]; then check 0 "a port outside the range is refused"; else check 1 "a port outside the range is refused"; fi
assert_contains "outside the local-model port range" "$OUT" "the refusal names the range"
assert_running "$PORT_GUARD_PID" "a refused range leaves every server untouched"

# The range override cannot widen past the proxy band either: pointing the scan range
# straight at it changes nothing, because the scan skips those ports outright.
OUT="$(env HOME="$TEST_HOME" PATH="$TEST_PATH" \
      LA_PORT_START=4141 LA_PORT_MAX=4151 LA_EVICT_EXTRA_PORTS="" \
      LA_EVICT_STATE="$STATE_FILE" LA_EVICT_LOG_DIR="$LOG_DIR" \
      LA_EVICT_TERM_WAIT=1 /bin/bash "$SCRIPT" --status 2>&1)"
assert_contains "nothing to evict" "$OUT" "a range override over the proxy band evicts nothing"

OUT="$(run_evict --port "$((BASE + 2))")"
STATUS=$?
if [ "$STATUS" -eq 4 ]; then check 0 "a listener-less named port is reported"; else check 1 "a listener-less named port is reported"; fi
assert_contains "no model server on :$((BASE + 2))" "$OUT" "the empty request names its port"
assert_running "$PORT_GUARD_PID" "a listener-less request stops nothing"

kill -TERM "$PORT_GUARD_PID" 2>/dev/null || true
wait_for_stopped "$PORT_GUARD_PID" || true

# A --port run names a KNOWN target, so it must not consume an escalation step:
# the next plain run still starts fresh at tier 0 rather than tier 1.
start_server rapid-mlx "$BASE"
PORT_LADDER_A="$LAST_PID"

sleep 2

start_server vllm-mlx "$((BASE + 1))"
PORT_LADDER_B="$LAST_PID"

OUT="$(run_evict --port "$BASE")"
STATUS=$?
check "$STATUS" "port run before the ladder exits successfully"
assert_stopped "$PORT_LADDER_A" "port run stopped its own target"

OUT="$(run_evict)"
STATUS=$?
check "$STATUS" "the run after a --port run exits successfully"
assert_contains \
    "evicting vllm-mlx on :$((BASE + 1))" \
    "$OUT" \
    "the run after --port still sweeps orphans (still tier 0)"
if printf '%s' "$OUT" | grep -qF "escalating to tier"; then
    check 1 "a --port run does not advance the escalation ladder"
else
    check 0 "a --port run does not advance the escalation ladder"
fi
assert_stopped "$PORT_LADDER_B" "the run after --port swept the remaining orphan"

# And that holds MID-ladder: any number of --port runs leave the tier exactly
# where the plain runs left it. Without this, a stray --port would silently push
# the next evacuation into emergency, which is the one tier that cuts a live
# session's backend out from under it.
STATE_FILE="$SB/mid-ladder.state"

start_server rapid-mlx "$BASE"
MID_A="$LAST_PID"

sleep 2

OUT="$(run_evict)"
assert_stopped "$MID_A" "tier 0 swept the first plain-run orphan"

start_server vllm-mlx "$BASE"
MID_B="$LAST_PID"

sleep 2

OUT="$(run_evict)"
assert_contains "escalating to tier 1" "$OUT" "the second plain run is tier 1"
assert_stopped "$MID_B" "tier 1 evicted one server"

start_server rapid-mlx "$BASE"
MID_C="$LAST_PID"

sleep 2

OUT="$(run_evict --port "$BASE")"
STATUS=$?
check "$STATUS" "mid-ladder port run exits successfully"
assert_stopped "$MID_C" "the intervening --port run stopped its target"

start_server vllm-mlx "$BASE"
MID_D="$LAST_PID"

sleep 2

OUT="$(run_evict)"
assert_contains "escalating to tier 2" "$OUT" "the --port run left the ladder exactly where it was"
assert_stopped "$MID_D" "tier 2 still evicts exactly one server"

# ---------------------------------------------------------------------------
printf '\n== emergency tier also removes unrecognised listeners ==\n'

# An unrecognised backend (today: omlx) is refused at every non-emergency tier,
# so a stale one can outlive its model. The emergency tier is the one place it
# goes, because leaving it resident is exactly the OOM risk eviction exists for.
PORT_START="$BASE"
PORT_MAX="$BASE"
STATE_FILE="$SB/unknown-tier.state"
TEST_PATH="$REAL_PATH"

start_server unknown "$BASE"
UNKNOWN_TIER_PID="$LAST_PID"

OUT="$(run_evict)"
STATUS=$?
assert_contains "unrecognised listener" "$OUT" "tier 0 refuses an unrecognised listener"
assert_running "$UNKNOWN_TIER_PID" "tier 0 leaves the unrecognised listener alive"

OUT="$(run_evict)"
STATUS=$?
assert_contains "unrecognised listener" "$OUT" "tier 1 still refuses an unrecognised listener"
assert_running "$UNKNOWN_TIER_PID" "tier 1 leaves the unrecognised listener alive"

OUT="$(run_evict)"
STATUS=$?
check "$STATUS" "emergency tier exits successfully"
assert_contains "EMERGENCY" "$OUT" "the emergency tier labels itself"
assert_contains "evicting" "$OUT" "the emergency tier removes an unrecognised listener"
assert_stopped "$UNKNOWN_TIER_PID" "the emergency tier stops the unrecognised listener"

# ---------------------------------------------------------------------------
printf '\n== tier 1 takes exactly ONE server, by ranking ==\n'

# Tier 0 sweeps orphans; from tier 1 on the OLD ranking rule applies — one server
# per invocation. Without two servers this case is untestable: with one, picking
# row N and picking the last row are the same thing, so a mutation that clamps
# wrong survives green. (Measured: that mutation passed 81/81 until this existed.)
STATE_FILE="$SB/one-at-a-time.state"
PORT_START="$BASE"
PORT_MAX="$((BASE + 1))"
TEST_PATH="$REAL_PATH"

start_server rapid-mlx "$BASE"
ONE_OLD_PID="$LAST_PID"

sleep 2

start_server vllm-mlx "$((BASE + 1))"
ONE_NEW_PID="$LAST_PID"

OUT="$(run_evict --tier 1)"
STATUS=$?
check "$STATUS" "tier 1 exits successfully"
assert_contains \
    "evicting vllm-mlx on :$((BASE + 1))" \
    "$OUT" \
    "tier 1 takes the top-ranked (newest) server"
assert_stopped "$ONE_NEW_PID" "tier 1 stopped exactly one server"
assert_running "$ONE_OLD_PID" "tier 1 left the other server alone"

OUT="$(run_evict --tier 2)"
STATUS=$?
check "$STATUS" "tier 2 exits successfully"
assert_contains \
    "evicting rapid-mlx on :$BASE" \
    "$OUT" \
    "tier 2 takes the next-ranked server"
assert_stopped "$ONE_OLD_PID" "tier 2 stopped the remaining server"

# ---------------------------------------------------------------------------
printf '\n== usage errors and the natural ladder ==\n'

# This section starts servers on BASE and BASE+1, so the scan range must cover both.
PORT_START="$BASE"
PORT_MAX="$((BASE + 2))"
TEST_PATH="$REAL_PATH"

# Usage errors must be decided before any state is touched.
STATE_FILE="$SB/usage.state"
printf '1 1 guard\n' > "$STATE_FILE"

OUT="$(run_evict --port "$BASE" --tier 2>&1)"
STATUS=$?
if [ "$STATUS" -eq 2 ]; then check 0 "--tier without a value is a usage error"; else check 1 "--tier without a value is a usage error"; fi

OUT="$(run_evict --tier bogus 2>&1)"
STATUS=$?
if [ "$STATUS" -eq 2 ]; then check 0 "a non-numeric --tier is a usage error"; else check 1 "a non-numeric --tier is a usage error"; fi

OUT="$(run_evict --reset --bogus 2>&1)"
STATUS=$?
if [ "$STATUS" -eq 2 ]; then check 0 "--reset after a bad flag is a usage error"; else check 1 "--reset after a bad flag is a usage error"; fi
if [ -e "$STATE_FILE" ]; then check 0 "a usage error never clears the escalation state"; else check 1 "a usage error never clears the escalation state"; fi

OUT="$(run_evict --port "$BASE" --tier 2>&1)"
if [ -e "$STATE_FILE" ]; then check 0 "--port with --tier is refused before any work"; else check 1 "--port with --tier is refused before any work"; fi

# A --port request when NOTHING is listening at all is exit 4, not the generic
# "nothing to evict" — the caller asked about one specific port.
OUT="$(run_evict --port "$BASE")"
STATUS=$?
if [ "$STATUS" -eq 4 ]; then check 0 "a --port with an empty inventory exits 4"; else check 1 "a --port with an empty inventory exits 4"; fi
assert_contains "no model server on :$BASE" "$OUT" "the empty-inventory request names its port"

# Multi-port: the ports that DO exist are evicted; the missing one is reported and
# the run still exits 4. Balking before evicting anything would leave RAM on the
# table during an OOM, which is the one moment this tool is for.
start_server rapid-mlx "$BASE"
PARTIAL_PID="$LAST_PID"

sleep 2

start_server vllm-mlx "$((BASE + 1))"
PARTIAL2_PID="$LAST_PID"

OUT="$(run_evict --port "$BASE" --port "$((BASE + 2))")"
STATUS=$?
if [ "$STATUS" -eq 4 ]; then check 0 "a partially-available multi-port exits 4"; else check 1 "a partially-available multi-port exits 4"; fi
assert_stopped "$PARTIAL_PID" "the available port in a multi-port run is still evicted"
assert_running "$PARTIAL2_PID" "the unnamed server survives a multi-port run"

kill -TERM "$PARTIAL2_PID" 2>/dev/null || true
wait_for_stopped "$PARTIAL2_PID" || true

# --- forced tiers: --tier 1 obeys the guard, and prefers an unattached candidate --
# (the NATURAL ladder — bare runs escalating tier 0 -> 1 -> 2 — is covered above; this
# section pins what --tier does, which is never the ladder's business)
ATTACHED_FAKE_PID=999997
ATTACHED_PORT="$BASE"

cat > "$MOCK_BIN/pgrep" <<EOF
#!/bin/sh
if [ "\$#" -eq 2 ] && [ "\$1" = "-x" ] && [ "\$2" = "claude" ]; then
    echo "$ATTACHED_FAKE_PID"
    exit 0
fi
exec /usr/bin/pgrep "\$@"
EOF

cat > "$MOCK_BIN/ps" <<EOF
#!/bin/sh
if [ "\$#" -eq 3 ] && [ "\$1" = "-Eww" ] && [ "\$2" = "-p" ] && [ "\$3" = "$ATTACHED_FAKE_PID" ]; then
    echo "$ATTACHED_FAKE_PID claude ANTHROPIC_BASE_URL=http://localhost:$ATTACHED_PORT"
    exit 0
fi
exec /bin/ps "\$@"
EOF

chmod +x "$MOCK_BIN/pgrep" "$MOCK_BIN/ps"
TEST_PATH="$MOCK_BIN:$REAL_PATH"
STATE_FILE="$SB/natural.state"

# The guard is about the BEST candidate, and attached sorts LAST in the ranking (see
# the ranking comment in bin/la-evict.sh): any unattached candidate outranks an
# attached one. So the hold-back fires only when the attached server is the ONLY
# candidate — and when an unattached one exists, tier 1 takes THAT one instead. The
# two halves of that contract need two scenarios, which is what this section pins.

# --- A: only an attached candidate -> tier 1 holds back, tier 2 overrides ---------
STATE_FILE="$SB/onlyattached.state"

start_server rapid-mlx "$BASE"
ONLY_ATTACHED="$LAST_PID"

sleep 2

# Tier 1 must obey the guard even when forced: `--tier 1` means tier 1, not
# "do whatever you like". A forced tier used to bypass this guard, which made
# `--tier 1` mean something different from tier 1.
OUT="$(run_evict --tier 1)"
STATUS=$?
if [ "$STATUS" -eq 3 ]; then check 0 "a forced --tier 1 still holds back"; else check 1 "a forced --tier 1 still holds back"; fi
assert_contains "held back" "$OUT" "the forced hold-back says so"
assert_contains "Run again to override" "$OUT" "the forced hold-back names the override"
assert_running "$ONLY_ATTACHED" "a forced --tier 1 leaves the attached server alive"

# Tier 2 is the emergency tier: it takes the best candidate whatever it is attached
# to — the one the operator was just told to override, not the second row.
OUT="$(run_evict --tier 2)"
STATUS=$?
check "$STATUS" "forced tier 2 exits successfully"
assert_contains "evicting rapid-mlx on :$BASE" "$OUT" "tier 2 takes the top-ranked candidate"
assert_stopped "$ONLY_ATTACHED" "tier 2 stops the attached server"

# --- B: an unattached candidate exists -> tier 1 takes THAT one, not the attached --
STATE_FILE="$SB/preferunattached.state"

start_server rapid-mlx "$BASE"
P_ATTACHED="$LAST_PID"

sleep 2

start_server vllm-mlx "$((BASE + 1))"
P_OLD="$LAST_PID"

OUT="$(run_evict --tier 1)"
STATUS=$?
check "$STATUS" "a forced --tier 1 succeeds when an unattached candidate exists"
assert_contains "evicting vllm-mlx on :$((BASE + 1))" "$OUT" "a forced --tier 1 takes the unattached candidate"
assert_stopped "$P_OLD" "the unattached candidate is the one taken"
assert_running "$P_ATTACHED" "a forced --tier 1 leaves the attached server alone"

kill -TERM "$P_ATTACHED" 2>/dev/null || true
wait_for_stopped "$P_ATTACHED" || true
TEST_PATH="$REAL_PATH"

# Non-bare runs must not advance the ladder: --all and --tier leave the tier exactly
# where the last bare run left it.
STATE_FILE="$SB/nonbare.state"

start_server rapid-mlx "$BASE"
NB_A="$LAST_PID"
sleep 2

OUT="$(run_evict)"
assert_stopped "$NB_A" "the bare tier-0 run swept the orphan"
sleep 1

start_server vllm-mlx "$BASE"
NB_B="$LAST_PID"
sleep 2

OUT="$(run_evict --allow-unknown)"
assert_contains "evicting vllm-mlx on :$BASE" "$OUT" "a --allow-unknown run still sweeps at tier 0"
assert_stopped "$NB_B" "the --allow-unknown sweep actually stopped that server"

start_server rapid-mlx "$BASE"
NB_C="$LAST_PID"
sleep 2

OUT="$(run_evict --tier 1)"
assert_contains "evicting (tier 1)" "$OUT" "a --tier 1 run forces its own tier"
assert_stopped "$NB_C" "the forced tier-1 run stopped exactly that one server"

start_server vllm-mlx "$BASE"
NB_D="$LAST_PID"
sleep 2

OUT="$(run_evict)"
assert_contains "escalating to tier 1" "$OUT" "non-bare runs did not advance the ladder past tier 1"

kill -TERM "$NB_D" 2>/dev/null || true
wait_for_stopped "$NB_D" || true

# ---------------------------------------------------------------------------
printf '\n== state reset and empty inventory ==\n'

STATE_FILE="$SB/reset.state"
printf '1 1 test\n' > "$STATE_FILE"

OUT="$(run_evict --reset)"
STATUS=$?
check "$STATUS" "state reset exits successfully"
assert_contains \
    "escalation state cleared" \
    "$OUT" \
    "state reset reports completion"
if [ -e "$STATE_FILE" ]; then
    check 1 "state file removed"
else
    check 0 "state file removed"
fi

OUT="$(run_evict --status)"
STATUS=$?
check "$STATUS" "empty status exits successfully"
assert_contains \
    "No local model servers are listening" \
    "$OUT" \
    "empty inventory is reported"
assert_contains \
    "nothing to evict" \
    "$OUT" \
    "empty result is explicit"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"

[ "$FAIL" -eq 0 ]

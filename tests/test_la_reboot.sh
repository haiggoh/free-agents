#!/usr/bin/env bash
# test_la_reboot.sh — la-reboot.sh must restore a CRASHED server in place, and must refuse to
# touch a healthy one.
#
# WHY this exists: a local session that hits the Rapid D-METAL-CAP OOM ("503 Server is busy …
# 107.2 GB already in use … limit is 103.9 GB") loses its backend, and the only recovery was
# exit -> la-evict -> new session -> /resume, which costs a full model reload AND a context replay.
# la-reboot restarts the SAME model on the SAME port with the SAME argv so the still-open session
# reconnects and continues.
#
# THE SHARP EDGE: this is the one script allowed to kill a server on the session port range, so
# every guard below is load-bearing. A healthy server must survive; an unrelated port must never be
# touched; and the argv must be captured BEFORE the kill, because after it the evidence is gone.
case "${1:-}" in
  -h|--help) printf '%s\n' 'Test la-reboot with isolated local HTTP fixtures.' 'Usage: test_la_reboot.sh [--help]' 'Environment: PATH selects bash, python3, curl and lsof.'; exit 0 ;;
  '') ;;
  *) echo 'Usage: test_la_reboot.sh [--help]' >&2; exit 2 ;;
esac
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO="$PWD"
RB="$REPO/bin/la-reboot.sh"

pass=0 fail=0
ok()  { printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  ✗ %s\n' "$1"; fail=$((fail+1)); }

WORK="$(mktemp -d)"
FIXTURE_PIDS=""
cleanup() {
  for fixture_pid in $FIXTURE_PIDS; do kill "$fixture_pid" 2>/dev/null || true; done
  rm -rf "$WORK"
}
trap cleanup EXIT
mkdir -p "$WORK/config" "$WORK/logs"
export LA_CONFIG_DIR_LOGS="$WORK/config" LA_REBOOT_LOG_DIR="$WORK/logs"
export LA_REBOOT_TERM_WAIT=2 LA_REBOOT_PROBE_TIMEOUT=2
# Ask the OS for scratch ports, and verify fixture ownership before any real reboot.
read -r EMPTY_PORT STUB_PORT E2E_PORT <<EOF
$(python3 -c 'import socket; ss=[socket.socket() for _ in range(3)]; [s.bind(("127.0.0.1",0)) for s in ss]; print(*(s.getsockname()[1] for s in ss))')
EOF

echo "la-reboot: contract"

[ -f "$RB" ] || { bad "bin/la-reboot.sh does not exist"; printf '\n%d passed, %d failed\n' "$pass" "$fail"; exit 1; }
ok "bin/la-reboot.sh exists"
bash -n "$RB" 2>/dev/null && ok "whole script parses before it can stop a server" || bad "shell syntax error can interrupt a reboot after the stop"
[ -x "$RB" ] && ok "is executable" || bad "is not executable"

# --help must PRINT and must not act (the repo rule: a probe must never trigger the work).
out=$("$RB" --help 2>&1); rc=$?
if [ $rc -eq 0 ] && printf '%s' "$out" | grep -qiE 'usage'; then
  ok "--help prints usage and exits 0"
else
  bad "--help did not print usage (rc=$rc)"
fi
if printf '%s' "$out" | grep -qiE 'la-reboot'; then ok "--help names the script"; else bad "--help lacks a description"; fi
# An unknown flag must fail loudly rather than proceeding as if nothing was passed.
"$RB" --definitely-not-a-flag >/dev/null 2>&1
[ $? -ne 0 ] && ok "an unrecognised flag exits non-zero" || bad "an unrecognised flag was ignored and it RAN"

echo
echo "la-reboot: safety guards (static)"

# The argv must be read from the live process BEFORE the kill — after it, it is unrecoverable.
if grep -qE 'KERN_PROCARGS2|sysctl' "$RB"; then
  ok "captures exact argv from the live process (sysctl KERN_PROCARGS2, not ps word-splitting)"
else
  bad "does not capture exact argv — ps space-splitting corrupts any argument containing a space"
fi
kill_line=$(grep -nE '\bkill -' "$RB" | head -1 | cut -d: -f1)
argv_line=$(grep -nE 'KERN_PROCARGS2|capture_argv|read_argv' "$RB" | head -1 | cut -d: -f1)
if [ -n "$kill_line" ] && [ -n "$argv_line" ] && [ "$argv_line" -lt "$kill_line" ]; then
  ok "argv capture precedes the kill in source order"
else
  bad "argv capture does not precede the kill (argv=$argv_line kill=$kill_line)"
fi
# Refuse a healthy server unless forced.
if grep -qiE 'force' "$RB"; then ok "has an explicit --force override"; else bad "no --force override"; fi
if grep -qiE 'D-METAL-CAP|503|metal' "$RB"; then
  ok "looks for positive crash evidence (D-METAL-CAP / 503), not mere liveness"
else
  bad "no crash-evidence check — an OOM-refusing server still answers /v1/models"
fi
# It must operate on ONE port only.
if grep -qE 'PORT_START|seq .*PORT' "$RB"; then
  bad "scans a port RANGE — it must act on exactly one derived/named port"
else
  ok "acts on a single port, never a range"
fi

echo
echo "la-reboot: behaviour (no real server harmed)"

# --dry-run against a port with NOTHING on it: must report nothing to do, exit non-zero-or-clean,
# and above all must not attempt a kill.
out=$("$RB" --port "$EMPTY_PORT" --dry-run 2>&1); rc=$?
if printf '%s' "$out" | grep -qiE 'no (listener|server)|nothing|not listening|free'; then
  ok "an empty port is reported as nothing to reboot"
else
  bad "an empty port was not reported clearly (rc=$rc): $(printf '%s' "$out" | head -1)"
fi

# A HEALTHY stub server on a scratch port must be REFUSED without --force.
python3 - "$WORK/stub.pid" "$STUB_PORT" <<'PY' &
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"data":[{"id":"claude-opus-5"}]}).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        body = json.dumps({"choices":[{"finish_reason":"stop"}]}).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*a): pass
open(sys.argv[1],"w").write(str(__import__("os").getpid()))
HTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
PY
STUB_BG=$!
FIXTURE_PIDS="$FIXTURE_PIDS $STUB_BG"
for _ in $(seq 1 40); do
  curl -s --max-time 1 "http://127.0.0.1:$STUB_PORT/v1/models" >/dev/null 2>&1 && break
  sleep 0.25
done

out=$("$RB" --port "$STUB_PORT" 2>&1); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qiE 'healthy|refus|no crash|--force'; then
  ok "a HEALTHY server is refused without --force (rc=$rc)"
else
  bad "a healthy server was not refused (rc=$rc): $(printf '%s' "$out" | tail -1)"
fi
if curl -s --max-time 2 "http://127.0.0.1:$STUB_PORT/v1/models" >/dev/null 2>&1; then
  ok "the healthy server is STILL ALIVE — the refusal was real, not cosmetic"
else
  bad "the healthy server was killed despite the refusal"
fi

# --dry-run with --force must still not kill it.
out=$("$RB" --port "$STUB_PORT" --force --dry-run 2>&1)
if curl -s --max-time 2 "http://127.0.0.1:$STUB_PORT/v1/models" >/dev/null 2>&1; then
  ok "--force --dry-run does not kill"
else
  bad "--force --dry-run KILLED the server"
fi
if printf '%s' "$out" | grep -qiE 'dry.run|would'; then
  ok "--dry-run says what it would do"
else
  bad "--dry-run gave no plan"
fi

# REGRESSION (found 2026-09-13 by running --status against the real server): a STALE
# D-METAL-CAP line in the log tail must not overrule a SUCCESSFUL completion probe. A log is a
# historical record — an OOM the server already recovered from stays in the tail forever — so
# treating it as decisive would reboot a healthy server, which is the exact opposite of this
# script's purpose. A server that completes a request is working.
LOGD="$WORK/logs"; mkdir -p "$LOGD"
printf 'some line\nERROR: needs 0.0 GB on top of 107.2 GB, limit is 103.9 GB (D-METAL-CAP)\nlater line\n' \
  > "$LOGD/vllm_${STUB_PORT}.log"
out=$(LA_REBOOT_LOG_DIR="$LOGD" "$RB" --port "$STUB_PORT" --status 2>&1)
if printf '%s' "$out" | grep -qiE 'healthy'; then
  ok "a stale OOM log line does NOT overrule a successful probe"
else
  bad "a stale OOM log line overruled a working server: $(printf '%s' "$out" | grep -i result | head -1)"
fi
if printf '%s' "$out" | grep -qiE 'older|overrule'; then
  ok "the stale log evidence is still REPORTED, not silently dropped"
else
  bad "the stale log line vanished from the report — evidence should be surfaced, just not decisive"
fi
if LA_REBOOT_LOG_DIR="$LOGD" "$RB" --port "$STUB_PORT" >/dev/null 2>&1; then
  bad "with a stale OOM line present, a healthy server was NOT refused"
else
  ok "still refused without --force despite the stale OOM line"
fi

echo
echo "la-reboot: end-to-end on a scratch port (real stop -> real relaunch)"

# The whole point of the script, proven by OUTCOME rather than by absence of error: a server that
# REFUSES completions (503, the D-METAL-CAP shape) is stopped and relaunched from its captured argv,
# comes back serving the same id, and gets a NEW pid. The fixture carries an argument containing a
# SPACE, which ps word-splitting would corrupt — that is the case KERN_PROCARGS2 exists for.
FAKE="$WORK/fake-server.py"
cat > "$FAKE" <<'FAKEPY'
import sys, json, os, argparse, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
p = argparse.ArgumentParser(description="Isolated reboot fixture. Environment: VLLM_MLX_ENABLE_THINKING must match --thinking.")
p.add_argument('--port', type=int, required=True)
p.add_argument('--served-model-name', required=True)
p.add_argument('--mode', choices=['recover', 'oom', 'healthy', 'unauthorized', 'broken-anthropic', 'takeover'], default='recover')
p.add_argument('--state', required=True)
p.add_argument('--marker', required=True)
p.add_argument('--thinking', default='true')
p.add_argument('--host', default='127.0.0.1')
a = p.parse_args()
port, mid, mode = a.port, a.served_model_name, a.mode
first_start = not Path(a.state).exists()
Path(a.state).write_text('started')
if mode=='takeover' and not first_start:
    Path(a.state+'.replacement-pid').write_text(str(os.getpid()))
    Path(a.state+'.takeover-ready').touch()
    time.sleep(8)
    sys.exit(0)
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        b=json.dumps({"data":[{"id":mid},{"id":"extra-id"}]}).encode()
        self.send_response(200); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        request=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
        if request.get('model') != mid:
            b=b'{"error":"wrong model"}'; self.send_response(400)
        elif mode=='unauthorized':
            b=b'{"error":"authentication required"}'; self.send_response(401)
        elif mode=="oom" or (mode in ('recover','takeover') and first_start) or (mode=='broken-anthropic' and self.path=='/v1/messages'):
            b=json.dumps({"error":"503 (D-METAL-CAP)"}).encode(); self.send_response(503)
        elif os.environ.get('VLLM_MLX_ENABLE_THINKING') != a.thinking or not Path('cwd-marker').exists() or a.marker != 'arg with spaces':
            b=b'{"error":"restart lost runtime context"}'; self.send_response(500)
        elif self.path=='/v1/messages':
            b=json.dumps({"type":"message","role":"assistant","model":mid,"content":[{"type":"text","text":"continued"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}).encode(); self.send_response(200)
        else:
            b=json.dumps({"choices":[{"message":{"role":"assistant","content":"continued"},"finish_reason":"stop"}]}).encode(); self.send_response(200)
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self,*a): pass
HTTPServer(("127.0.0.1",port),H).serve_forever()
FAKEPY

CFGD="$WORK/cfg"; mkdir -p "$CFGD"
printf 'backend=rapid\nalias=fake-e2e\nmodel_dir=/tmp/FakeModel\nserved_id=claude-opus-5\npid=0\n' \
  > "$CFGD/server_${E2E_PORT}.meta"
chmod 600 "$CFGD/server_${E2E_PORT}.meta"
mkdir -p "$WORK/server cwd"
touch "$WORK/server cwd/cwd-marker"

(cd "$WORK/server cwd" && VLLM_MLX_ENABLE_THINKING=true exec python3 "$FAKE" --mode recover --state "$WORK/started" --served-model-name claude-opus-5 --host 127.0.0.1 \
      --port "$E2E_PORT" --marker "arg with spaces") > "$WORK/e2e.log" 2>&1 &
E2E_BG=$!
FIXTURE_PIDS="$FIXTURE_PIDS $E2E_BG"
for _ in $(seq 1 40); do
  curl -s --max-time 1 "http://127.0.0.1:$E2E_PORT/v1/models" >/dev/null 2>&1 && break
  sleep 0.25
done
OLD_PID="$(lsof -nP -iTCP:"$E2E_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"

if [ -n "$OLD_PID" ] && [ "$OLD_PID" = "$E2E_BG" ]; then
  ok "fixture server is up on :$E2E_PORT (pid $OLD_PID, refusing completions)"
  out=$(VLLM_MLX_ENABLE_THINKING=false LA_CONFIG_DIR_LOGS="$CFGD" LA_REBOOT_LOG_DIR="$WORK" LA_REBOOT_READY_WAIT=5 \
        "$RB" --port "$E2E_PORT" 2>&1); rc=$?
  NEW_PID="$(lsof -nP -iTCP:"$E2E_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  FIXTURE_PIDS="$FIXTURE_PIDS $NEW_PID"

  [ "$rc" -ne 0 ] && bad "reboot exited $rc — a crash with a recorded argv must proceed" || ok "reboot exited 0"
  printf '%s' "$out" | grep -q "REBOOT_PORT=$E2E_PORT" && ok "prints REBOOT_PORT=$E2E_PORT" || bad "no REBOOT_PORT line (rc=$rc)"
  if [ -n "$NEW_PID" ] && [ "$NEW_PID" != "$OLD_PID" ]; then
    ok "a NEW process serves the port ($OLD_PID -> $NEW_PID)"
  else
    bad "port not served by a new process (old=$OLD_PID new=${NEW_PID:-none})"
  fi
  if curl -s --max-time 3 "http://127.0.0.1:$E2E_PORT/v1/models" | grep -q 'claude-opus-5'; then
    ok "it serves the SAME id after the reboot"
  else
    bad "the rebooted server does not serve the expected id"
  fi
  body=$(curl -fsS --max-time 3 "http://127.0.0.1:$E2E_PORT/v1/messages" -H 'Content-Type: application/json' -H 'Authorization: Bearer local' -H 'anthropic-version: 2023-06-01' -d '{"model":"claude-opus-5","messages":[{"role":"user","content":"previous request"},{"role":"assistant","content":"previous answer"},{"role":"user","content":"continue"}],"max_tokens":1}' 2>/dev/null)
  if printf '%s' "$body" | grep -q 'continued'; then
    ok "same session endpoint accepts an Anthropic continuation after reboot exits (argv, cwd and original runtime environment preserved)"
  else
    bad "model listing recovered but the open-session continuation is unusable"
  fi
  [ "$(stat -f '%Lp' "$CFGD/server_${E2E_PORT}.meta")" = 600 ] && ok "metadata permissions are preserved" || bad "metadata permissions widened"
  # argv fidelity: the space-containing argument must have survived verbatim.
  if [ -n "$NEW_PID" ] && python3 - "$NEW_PID" <<'ARGVPY'
import ctypes, ctypes.util, struct, sys
pid=int(sys.argv[1]); libc=ctypes.CDLL(ctypes.util.find_library("c"))
mib=(ctypes.c_int*3)(1,49,pid); size=ctypes.c_size_t(0)
if libc.sysctl(mib,3,None,ctypes.byref(size),None,0)!=0: sys.exit(1)
buf=ctypes.create_string_buffer(size.value)
if libc.sysctl(mib,3,buf,ctypes.byref(size),None,0)!=0: sys.exit(1)
argc=struct.unpack("i",buf.raw[:4])[0]
c=buf.raw[4:].split(b"\0"); i=0
while i<len(c) and c[i]==b"": i+=1
i+=1
while i<len(c) and c[i]==b"": i+=1
args=[a.decode() for a in c[i:i+argc]]
sys.exit(0 if "arg with spaces" in args else 1)
ARGVPY
  then
    ok "the space-containing argument survived verbatim (ps splitting would have broken it)"
  else
    bad "argv fidelity lost — the space-containing argument did not survive"
  fi
  # Readiness must be judged from the served id, tolerating pretty-printed JSON.
  printf '%s' "$out" | grep -qi 'not become ready' && bad "reported not-ready while actually serving" \
    || ok "readiness was detected (no false not-ready)"
  [ -n "$NEW_PID" ] && kill "$NEW_PID" 2>/dev/null
else
  bad "could not start the e2e fixture server — e2e checks skipped, not passed"
fi

# Model listings alone must never certify recovery. The old fixture permanently
# returned 503 yet the old test called its model listing a successful reboot.
start_fixture() {
  fixture_mode="$1"
  FIXTURE_PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
  printf 'backend=rapid\nserved_id=claude-opus-5\npid=0\n' > "$CFGD/server_${FIXTURE_PORT}.meta"
  (cd "$WORK/server cwd" && VLLM_MLX_ENABLE_THINKING=true exec python3 "$FAKE" --mode "$fixture_mode" --state "$WORK/state-$FIXTURE_PORT" --port "$FIXTURE_PORT" --served-model-name claude-opus-5 --marker 'arg with spaces') > "$WORK/fixture-$FIXTURE_PORT.log" 2>&1 &
  FIXTURE_PID=$!
  FIXTURE_PIDS="$FIXTURE_PIDS $FIXTURE_PID"
  for _ in $(seq 1 40); do
    curl -fsS --max-time 1 "http://127.0.0.1:$FIXTURE_PORT/v1/models" >/dev/null 2>&1 && break
    sleep 0.1
  done
  [ "$(lsof -nP -iTCP:"$FIXTURE_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)" = "$FIXTURE_PID" ]
}

for failure_mode in oom broken-anthropic legacy-anthropic; do
fixture_mode="$failure_mode"
[ "$fixture_mode" = legacy-anthropic ] && fixture_mode=broken-anthropic
if start_fixture "$fixture_mode"; then
  if [ "$failure_mode" = legacy-anthropic ]; then
    printf 'served_id=claude-opus-5\npid=0\n' > "$CFGD/server_${FIXTURE_PORT}.meta"
  fi
  out=$(LA_CONFIG_DIR_LOGS="$CFGD" "$RB" --port "$FIXTURE_PORT" --wait 2 2>&1); rc=$?
  restarted_pid="$(lsof -nP -iTCP:"$FIXTURE_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  FIXTURE_PIDS="$FIXTURE_PIDS $restarted_pid"
  if [ "$rc" = 1 ] && printf '%s' "$out" | grep -q 'not ready for completions' && ! printf '%s' "$out" | grep -q '^REBOOT_PORT='; then
    ok "a relaunched server with $failure_mode is explicitly not ready"
  else
    bad "a model listing falsely certified recovery while inference still fails (rc=$rc)"
  fi
else
  bad "could not own the persistent OOM fixture"
fi
done

if start_fixture unauthorized; then
  out=$(LA_CONFIG_DIR_LOGS="$CFGD" "$RB" --port "$FIXTURE_PORT" --wait 2 2>&1); rc=$?
  restarted_pid="$(lsof -nP -iTCP:"$FIXTURE_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
  FIXTURE_PIDS="$FIXTURE_PIDS $restarted_pid"
  if [ "$rc" = 3 ] && [ "$restarted_pid" = "$FIXTURE_PID" ]; then
    ok "HTTP 401 is not crash evidence and leaves the original server running"
  else
    bad "an authentication failure killed or attempted to reboot a live server (rc=$rc)"
  fi
else
  bad "could not own the authentication fixture"
fi

# A different process can win the freed port. It may serve the right model and
# complete requests, but must not be recorded as the replacement we launched.
if start_fixture takeover; then
  (
    for _ in $(seq 1 100); do
      [ -f "$WORK/state-$FIXTURE_PORT.takeover-ready" ] && break
      sleep 0.05
    done
    [ -f "$WORK/state-$FIXTURE_PORT.takeover-ready" ] || exit 1
    cd "$WORK/server cwd" || exit 1
    VLLM_MLX_ENABLE_THINKING=true exec python3 "$FAKE" --mode healthy --state "$WORK/outsider-$FIXTURE_PORT" --port "$FIXTURE_PORT" --served-model-name claude-opus-5 --marker 'arg with spaces'
  ) > "$WORK/outsider.log" 2>&1 &
  OUTSIDER_PID=$!
  FIXTURE_PIDS="$FIXTURE_PIDS $OUTSIDER_PID"
  out=$(LA_CONFIG_DIR_LOGS="$CFGD" "$RB" --port "$FIXTURE_PORT" --wait 4 2>&1); rc=$?
  replacement_pid=$(cat "$WORK/state-$FIXTURE_PORT.replacement-pid" 2>/dev/null)
  FIXTURE_PIDS="$FIXTURE_PIDS $replacement_pid"
  if [ "$rc" = 1 ] && printf '%s' "$out" | grep -q 'unrelated listener' && grep -qx 'pid=0' "$CFGD/server_${FIXTURE_PORT}.meta" && ! printf '%s' "$out" | grep -q '^REBOOT_PORT='; then
    ok "an unrelated port-taker cannot be reported or cached as the replacement"
  else
    bad "a successful response from an unrelated port-taker was accepted (rc=$rc)"
  fi
else
  bad "could not own the port-takeover fixture"
fi

kill "$STUB_BG" 2>/dev/null
[ -f "$WORK/stub.pid" ] && kill "$(cat "$WORK/stub.pid")" 2>/dev/null
wait "$STUB_BG" 2>/dev/null

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

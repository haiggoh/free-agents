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
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
REPO="$PWD"
RB="$REPO/bin/la-reboot.sh"

pass=0 fail=0
ok()  { printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  ✗ %s\n' "$1"; fail=$((fail+1)); }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

echo "la-reboot: contract"

[ -f "$RB" ] || { bad "bin/la-reboot.sh does not exist"; printf '\n%d passed, %d failed\n' "$pass" "$fail"; exit 1; }
ok "bin/la-reboot.sh exists"
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
out=$("$RB" --port 9993 --dry-run 2>&1); rc=$?
if printf '%s' "$out" | grep -qiE 'no (listener|server)|nothing|not listening|free'; then
  ok "an empty port is reported as nothing to reboot"
else
  bad "an empty port was not reported clearly (rc=$rc): $(printf '%s' "$out" | head -1)"
fi

# A HEALTHY stub server on a scratch port must be REFUSED without --force.
python3 - "$WORK/stub.pid" 9994 <<'PY' &
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
for _ in $(seq 1 40); do
  curl -s --max-time 1 http://127.0.0.1:9994/v1/models >/dev/null 2>&1 && break
  sleep 0.25
done

out=$("$RB" --port 9994 2>&1); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -qiE 'healthy|refus|no crash|--force'; then
  ok "a HEALTHY server is refused without --force (rc=$rc)"
else
  bad "a healthy server was not refused (rc=$rc): $(printf '%s' "$out" | tail -1)"
fi
if curl -s --max-time 2 http://127.0.0.1:9994/v1/models >/dev/null 2>&1; then
  ok "the healthy server is STILL ALIVE — the refusal was real, not cosmetic"
else
  bad "the healthy server was killed despite the refusal"
fi

# --dry-run with --force must still not kill it.
out=$("$RB" --port 9994 --force --dry-run 2>&1)
if curl -s --max-time 2 http://127.0.0.1:9994/v1/models >/dev/null 2>&1; then
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
  > "$LOGD/vllm_9994.log"
out=$(LA_REBOOT_LOG_DIR="$LOGD" "$RB" --port 9994 --status 2>&1)
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
if LA_REBOOT_LOG_DIR="$LOGD" "$RB" --port 9994 >/dev/null 2>&1; then
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
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[sys.argv.index("--port")+1])
mid  = sys.argv[sys.argv.index("--served-model-name")+1]
mode = sys.argv[sys.argv.index("--mode")+1] if "--mode" in sys.argv else "healthy"
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        b=json.dumps({"data":[{"id":mid}]}).encode()
        self.send_response(200); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        if mode=="oom":
            b=json.dumps({"error":"503 (D-METAL-CAP)"}).encode(); self.send_response(503)
        else:
            b=json.dumps({"choices":[{"finish_reason":"stop"}]}).encode(); self.send_response(200)
        self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self,*a): pass
HTTPServer(("127.0.0.1",port),H).serve_forever()
FAKEPY

E2E_PORT=9996
CFGD="$WORK/cfg"; mkdir -p "$CFGD"
printf 'backend=rapid\nalias=fake-e2e\nmodel_dir=/tmp/FakeModel\nserved_id=claude-opus-5\npid=0\n' \
  > "$CFGD/server_${E2E_PORT}.meta"

nohup python3 "$FAKE" --mode oom --served-model-name claude-opus-5 --host 127.0.0.1 \
      --port "$E2E_PORT" --marker "arg with spaces" > "$WORK/e2e.log" 2>&1 &
for _ in $(seq 1 40); do
  curl -s --max-time 1 "http://127.0.0.1:$E2E_PORT/v1/models" >/dev/null 2>&1 && break
  sleep 0.25
done
OLD_PID="$(lsof -nP -iTCP:"$E2E_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"

if [ -n "$OLD_PID" ]; then
  ok "fixture server is up on :$E2E_PORT (pid $OLD_PID, refusing completions)"
  out=$(LA_CONFIG_DIR_LOGS="$CFGD" LA_REBOOT_LOG_DIR="$WORK" LA_REBOOT_READY_WAIT=30 \
        "$RB" --port "$E2E_PORT" 2>&1); rc=$?
  NEW_PID="$(lsof -nP -iTCP:"$E2E_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"

  [ "$rc" -eq 0 ] && ok "reboot exited 0" || bad "reboot exited $rc"
  printf '%s' "$out" | grep -q "REBOOT_PORT=$E2E_PORT" && ok "prints REBOOT_PORT=$E2E_PORT" || bad "no REBOOT_PORT line"
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

kill "$STUB_BG" 2>/dev/null
[ -f "$WORK/stub.pid" ] && kill "$(cat "$WORK/stub.pid")" 2>/dev/null
wait "$STUB_BG" 2>/dev/null

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

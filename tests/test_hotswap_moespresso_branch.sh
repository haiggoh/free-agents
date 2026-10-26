#!/usr/bin/env bash
# test_hotswap_moespresso_branch.sh — hotswap must launch a serve=moespresso registration and
# REUSE a healthy one, against a stub that behaves exactly like the measured MoEspresso server.
#
# WHY THIS EXISTS (measured 2026-10-25 against a real `moespresso@3 3.0.0` serve on :8097):
#   1. the spoof id is irrelevant — POST /v1/chat/completions accepts and ECHOES any model name,
#      so no oMLX-style symlink farm is built; the real package dir is served directly;
#   2. GET /v1/models returns {"error": {"message": "not found"}} — there is no model-list route.
#      That breaks hotswap's reuse detection, which requires the advertised id to match the spoof
#      (local-llm-hotswap.sh:320 in the 0.44.x tree): a healthy MoEspresso server would NEVER be
#      reused, and every launch would start a fresh 57 GB server.
# Everything else (wait_ready needs only SOME http response; _preflight_warmup posts a real
# completion and reads finish_reason) works unchanged against that same shape.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
REPO="$PWD"
HOTSWAP="$REPO/bin/local-llm-hotswap.sh"

# --help prints this header and exits BEFORE anything runs.
case "${1:-}" in -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;; esac

pass=0 fail=0
ok()  { printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  ✗ %s\n' "$1"; fail=$((fail+1)); }

PORT=$(( 9900 + ($$ % 80) ))
WORK="$(mktemp -d)"
# The stub is long-lived across BOTH invocations of this test, so it lives in a sibling dir
# that nothing here deletes: only the scratch repo is disposable.
FIX="$(mktemp -d)"
# Take the port before AND after: a stub left listening would make the next test's port scan
# skip its own port and fail with 'All ports saturated' — an error that names the wrong thing.
# Kill the port and delete the fixtures are SEPARATE jobs: calling a combined cleanup at startup
# would rm -rf the dirs this test then writes its stub into.
_port_only() { for p in $(/usr/sbin/lsof -t -i :$PORT -sTCP:LISTEN 2>/dev/null); do kill -9 "$p" 2>/dev/null; done; return 0; }
_cleanup() { _port_only; rm -rf "$WORK" "$FIX"; }
trap _cleanup EXIT
_port_only

echo "hotswap moespresso branch:"

# --- the stub: records argv, behaves like the measured server -------------------------------
STUB_PY="$FIX/stub-server.py"
cat > "$STUB_PY" <<'PY'
import sys, http.server
port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8080
class H(http.server.BaseHTTPRequestHandler):
    def _json(self, code, body):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        # str(len(b)), never str(b): bytes' repr is `b'...'`, which curl rejects as an invalid
        # Content-Length — the body then never arrives and every probe fails for a reason that
        # lives in the fixture, not in the code under test.
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        # The measured shape: no model-list route at all.
        self._json(404, '{"error": {"message": "not found"}}')
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self._json(200, '{"choices":[{"finish_reason":"length"}],"model":"whatever-was-sent"}')
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
PY
MOE_BIN="$FIX/moespresso"
mkdir -p "$(dirname "$MOE_BIN")"
cat > "$MOE_BIN" <<STUB
#!/bin/bash
printf '%s\n' "\$*" >> "\$MOE_ARGV"
if [ "\$1" = "--version" ]; then echo "moespresso 3.0.0"; exit 0; fi
if [ "\$1" = "serve" ]; then exec /usr/bin/python3 "\$STUB_PY" "\$@"; fi
exit 0
STUB
chmod +x "$MOE_BIN"

# --- the fixture package, like the real artifact ---------------------------------------------
MODELS="$WORK/models"; PKG="$MODELS/deepseek-v4-flash-0731-coder"; mkdir -p "$PKG"
printf '%s' '{"model_type":"deepseek_v4","weight_format":"mxtq","mxtq_bits":2,"expert_dtype":"fp4"}' \
  > "$PKG/config.json"
printf '%s' '{"required_features":["calibration","deepseek_v4_per_layer_experts"],"artifact_id":"pkg:test"}' \
  > "$PKG/package_manifest.json"
/usr/bin/python3 - "$PKG/model-00001-of-00001.safetensors" <<'PY'
import json, struct, sys
h = {"model.layers.0.ffn.experts.tq_bundle": {"dtype": "F16", "shape": [1]}}
h["__metadata__"] = {"format": "pt"}
b = json.dumps(h).encode()
with open(sys.argv[1], "wb") as fh:
    fh.write(struct.pack("<Q", len(b)) + b + b"\0" * (2 << 20))
PY

# --- the scratch tree: ports the stub can own, no real binary anywhere ------------------------
SCRATCH="$WORK/repo"; mkdir -p "$SCRATCH"
cp -RL "$REPO/bin" "$REPO/config" "$SCRATCH/" 2>/dev/null
rm -f "$SCRATCH/config/config.local.sh"
cat > "$SCRATCH/config/config.local.sh" <<CFG
LA_MODELS_DIR=$MODELS
LA_PORT_START=$PORT
LA_PORT_MAX=$PORT
LA_RAPID_BIN=/nonexistent/rapid-mlx
LA_OMLX_BIN=/nonexistent/omlx
LA_VENV=/nonexistent/venv/bin
la_register ds-coder deepseek-v4-flash-0731-coder moespresso "" "" false "" high "" "" ""
CFG

run() { env -u LA_LIVE_CONFIG_DIR LA_MOESPRESSO_BIN="$MOE_BIN" MOE_ARGV="$FIX/argv.log" \
        STUB_PY="$STUB_PY" LA_CONFIG_DIR="$SCRATCH/config" \
        HOTSWAP_READY_TIMEOUT="${MOE_TEST_READY_TIMEOUT:-25}" LA_HOTSWAP_PREFLIGHT=1 \
        "$SCRATCH/bin/local-llm-hotswap.sh" "$@" 2>&1; }

# 1. STATIC: the branch exists and the farm is NOT built (the measured spoof is irrelevant).
if grep -q '"$SERVE" = "moespresso"' "$HOTSWAP"; then
  ok "hotswap has a moespresso branch"
else
  bad "hotswap has no moespresso branch"
fi

# 2. BEHAVIOURAL: a fresh launch serves the real package dir, on the requested port.
: > "$FIX/argv.log"
out=$(run ds-coder); rc=$?
if printf '%s' "$out" | grep -q "SUCCESS_PORT=$PORT"; then
  ok "a moespresso registration launches and prints SUCCESS_PORT"
else
  bad "no SUCCESS_PORT from a moespresso launch: $(printf '%s' "$out" | /usr/bin/tail -3 | /usr/bin/tr '\n' ' ')"
fi
if [ -s "$FIX/argv.log" ]; then
  argv="$(/usr/bin/head -1 "$FIX/argv.log")"
  case "$argv" in
    *" $PKG "*) ok "the REAL package dir is served (no farm — the spoof id is irrelevant)" ;;
    *) bad "the served directory is not the package dir: $argv" ;;
  esac
  case "$argv" in
    *" --host 127.0.0.1 "*|*"--host 127.0.0.1"*) ok "binds to 127.0.0.1" ;;
    *) bad "no --host 127.0.0.1 in: $argv" ;;
  esac
  case "$argv" in
    *" --port $PORT"*) ok "listens on the scanned port" ;;
    *) bad "no --port 9990 in: $argv" ;;
  esac
  case "$argv" in
    *" --thinking"*) ok "thinking is passed through" ;;
    *) bad "no --thinking in: $argv" ;;
  esac
else
  bad "the stub binary was never invoked"
fi

# 3. REUSE: the second invocation must NOT start a fresh 57 GB server.
: > "$FIX/argv.log"
out2=$(run ds-coder); rc2=$?
if printf '%s' "$out2" | grep -q 'already healthy'; then
  ok "a second invocation REUSES the healthy server"
else
  bad "no reuse on the second invocation: $(printf '%s' "$out2" | /usr/bin/tail -3 | /usr/bin/tr '\n' ' ')"
fi
if [ -s "$FIX/argv.log" ]; then
  bad "the binary was invoked again — a fresh 57 GB server would start"
else
  ok "the stub was not invoked again"
fi

# 3b. When reuse is DECLINED, the "busy" message must name the occupant.
#
# It read "Port N busy serving ''" for this backend: CURRENT_MODEL comes from /v1/models, which
# MoEspresso does not implement, so it was always empty. The meta file already holds the alias, so
# the answer was free. The decline is forced by perturbing the saved KV precision in the real meta
# (hotswap's meta dir is $HOME/.claude/logs/... and is not overridable), with a copy taken first
# and restored unconditionally.
_meta="$HOME/.claude/logs/local-agents-configs/server_${PORT}.meta"
if [ -f "$_meta" ] && printf '%s' "$out2" | /usr/bin/grep -q 'already healthy'; then
  _backup="$_meta.test-backup"
  /bin/cp -p "$_meta" "$_backup"
  /usr/bin/sed -i.bak 's/^kv_dtype=.*$/kv_dtype=bf16/' "$_meta"
  out3=$(run ds-coder)
  /bin/cp -p "$_backup" "$_meta"; rm -f "$_backup" "${_meta}.bak"
  if printf '%s' "$out3" | /usr/bin/grep -q "busy serving ''"; then
    bad "the occupied-port message names nothing: $(printf '%s' "$out3" | /usr/bin/grep 'busy serving' | /usr/bin/head -1)"
  elif printf '%s' "$out3" | /usr/bin/grep -q "busy serving 'ds-coder"; then
    ok "an occupied port is named from its metadata, not a blank"
  else
    bad "no occupied-port message at all: $(printf '%s' "$out3" | /usr/bin/tail -2 | /usr/bin/tr '\n' ' ')"
  fi
fi

# 4. The refusal path refuses without killing anything (the 0.44.2 rule).
gate_line=$(grep -n 'la-detect-backend.py --gate' "$HOTSWAP" | head -1 | cut -d: -f1)
branch_line=$(grep -n '"$SERVE" = "moespresso"' "$HOTSWAP" | head -1 | cut -d: -f1)
if [ -z "$branch_line" ]; then
  bad "no branch to inspect (skipped, not passed)"
else
  block=$(sed -n "${branch_line},$((branch_line+30))p" "$HOTSWAP")
  if printf '%s' "$block" | grep -qE '\b(kill|pkill|la-evict)\b'; then
    bad "the moespresso branch kills something — it must only launch or refuse"
  else
    ok "the branch never kills"
  fi
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

#!/usr/bin/env bash
# tests/test_omlx_backend.sh — serve=omlx: an oMLX server for an ordinary local session.
#
# Usage: bash tests/test_omlx_backend.sh [--help]
#
# Same method as tests/test_rapid_backend.sh: the real bin/ + config/ are copied into a mktemp
# sandbox, HOME points into it, ports are 8110-8114 (never 8000-8010), and LA_OMLX_BIN is a STUB.
# The stub reproduces the one oMLX behaviour this backend depends on (measured on 0.7.0, 2026-10-15):
# the served model id is the NAME OF THE SUBFOLDER under --model-dir AFTER RESOLVING SYMLINKS. A view
# folder that is itself a symlink to the weights is therefore served under the weights' real name and
# Claude Code's spoofed id 404s; a REAL folder named after the spoof id, holding per-file links, works.
#
# COVERED: vocabulary (serve=omlx resolves, is never accepted as LA_DEFAULT_MLX_BACKEND, GGUF warning),
# hotswap's oMLX branch (argv, view layout, meta record, served id, DISPATCH_MODEL, reuse), and the
# csl inventory listing an omlx model as session-capable.
set -uo pipefail
case "${1:-}" in -h|--help) sed -n 2,15p "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; "") ;; *) echo "usage: $0 [--help]" >&2; exit 2 ;; esac

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$2"; else FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$2"; fi; }
assert_grep() { printf '%s' "$2" | grep -qF -- "$1"; check $? "$3"; }
expect() {  # expect <label> <command...>: PASS when the command succeeds
  local label="$1"; shift
  if "$@"; then check 0 "$label"; else check 1 "$label"; fi
}

reap_stubs() {
  local pid
  for pid in $(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | awk '$9 ~ /:811[0-9]$/ {print $2}' | sort -u); do
    ps -o command= -p "$pid" 2>/dev/null | grep -q 'la-omlx-test' && kill "$pid" 2>/dev/null
  done
}
reap_stubs

SB="$(mktemp -d "${TMPDIR:-/tmp}/la-omlx-test.XXXXXX")"
trap 'reap_stubs; rm -rf "$SB"' EXIT
mkdir -p "$SB/home/.models/laguna-weights" "$SB/home/.stub" "$SB/home/.claude/logs/local-agents-configs"
cp -RL "$REPO/bin" "$SB/bin"; cp -RL "$REPO/config" "$SB/config"
[ -L "$SB/config/config.local.sh" ] && { echo "FATAL: sandbox config is a symlink" >&2; exit 1; }
head -c 2097152 /dev/zero > "$SB/home/.models/laguna-weights/model.safetensors"
echo '{"model_type":"laguna"}' > "$SB/home/.models/laguna-weights/config.json"

STUB="$SB/home/.stub/omlx"
cat > "$STUB" <<'PY'
#!/usr/bin/env python3
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
argv = sys.argv[1:]
open(os.environ["STUB_ARGV_FILE"], "w").write(json.dumps(argv))
def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None
if argv[:1] != ["serve"]:
    print("stub omlx: only `serve` is implemented", file=sys.stderr); sys.exit(2)
mdir = opt("--model-dir")
# oMLX's rule: one model per SUBFOLDER, id = basename of the folder's RESOLVED path.
ids = sorted(os.path.basename(os.path.realpath(os.path.join(mdir, e))) for e in os.listdir(mdir)
             if os.path.isdir(os.path.join(mdir, e)))
class H(BaseHTTPRequestHandler):
    def _send(self, obj):
        b = json.dumps(obj, separators=(",", ":")).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/v1/models":
            self._send({"object": "list", "data": [{"id": i} for i in ids]})
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); self.rfile.read(n)
        self._send({"choices": [{"finish_reason": "stop"}]})
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(opt("--port"))), H).serve_forever()
PY
chmod +x "$STUB"

cat > "$SB/config/config.local.sh" <<CFG
LA_MODELS_DIR="\$HOME/.models"
LA_PORT_START=8110
LA_PORT_MAX=8114
LA_OMLX_BIN="\$HOME/.stub/omlx"
la_register laguna-omlx laguna-weights omlx "" "" true claude-opus-5-5 high "" "" 64
CFG

run() {  # $1=script, rest=args
  local s="$1"; shift
  ( trap - EXIT; cd "$SB" && HOME="$SB/home" STUB_ARGV_FILE="$ARGV_FILE" HOTSWAP_READY_TIMEOUT=15 \
      LA_SKIP_RAM_PREFLIGHT=1 bash "$SB/bin/$s" "$@" )
}
cfg_eval() {  # evaluate a snippet with the sandbox config loaded
  ( cd "$SB" && HOME="$SB/home" bash -c '. "$1/config/config-lib.sh"; la_load_config >/dev/null 2>&1 || exit 9; eval "$2"' _ "$SB" "$1" )
}

echo "== vocabulary =="
cfg_eval 'la_lookup laguna-omlx && echo "serve=$LA_CUR_SERVE"' | grep -qx 'serve=omlx'; rc=$?
check "$rc" "serve=omlx is a valid pin and resolves to itself"
expect "spelling oMLX normalises to omlx" [ "$(cfg_eval 'la_resolve_serve oMLX')" = "omlx" ]
if ( cd "$SB" && HOME="$SB/home" LA_DEFAULT_MLX_BACKEND=omlx bash -c '. config/config-lib.sh; la_load_config' >/dev/null 2>&1 ); then rc=1; else rc=0; fi
check "$rc" "LA_DEFAULT_MLX_BACKEND=omlx is REJECTED (oMLX is a per-model pin, never the machine default)"
W=$(cd "$SB" && HOME="$SB/home" bash -c '. config/config-lib.sh; la_register g gemma-GGUF omlx "" "" false "" high; la_finalize_serve' 2>&1)
assert_grep "GGUF" "$W" "GGUF artifact pinned to omlx is warned about"

echo "== hotswap: oMLX launch =="
ARGV_FILE="$SB/argv1.json"
OUT=$(run local-llm-hotswap.sh laguna-omlx)
assert_grep "SUCCESS_PORT=8110" "$OUT" "prints SUCCESS_PORT on the first test port"
assert_grep "DISPATCH_MODEL=claude-opus-5-5" "$OUT" "DISPATCH_MODEL is the spoof id"
ARGS=$(cat "$ARGV_FILE" 2>/dev/null)
assert_grep '"serve"' "$ARGS" "stub received the serve subcommand"
assert_grep '"--no-hf-cache"' "$ARGS" "never consults the HF cache"
assert_grep '"--headless"' "$ARGS" "no web UI"
assert_grep '"--paged-ssd-cache-dir"' "$ARGS" "paged SSD cache dir passed"
assert_grep '"--max-concurrent-requests"' "$ARGS" "concurrency passed explicitly"
VIEW=$(python3 -c 'import json,sys;a=json.load(open(sys.argv[1]));print(a[a.index("--model-dir")+1])' "$ARGV_FILE" 2>/dev/null)
real_dir() { [ -d "$1" ] && [ ! -L "$1" ]; }
expect "view holds a REAL folder named after the spoof id" real_dir "$VIEW/claude-opus-5-5"
expect "weights are linked file by file" test -L "$VIEW/claude-opus-5-5/model.safetensors"
case "$VIEW" in "$SB/home/"*) check 0 "view lives under HOME (sandboxed)";; *) check 1 "view lives under HOME (sandboxed): $VIEW";; esac
M="$SB/home/.claude/logs/local-agents-configs/server_8110.meta"
grep -qx 'backend=omlx' "$M" 2>/dev/null; check $? "meta records backend=omlx"
grep -qx 'served_id=claude-opus-5-5' "$M" 2>/dev/null; check $? "meta records the served id"
IDS=$(curl -s --max-time 3 http://127.0.0.1:8110/v1/models)
assert_grep '"id":"claude-opus-5-5"' "$IDS" "server answers to the spoof id"

echo "== hotswap: reuse =="
ARGV_FILE="$SB/argv2.json"; rm -f "$ARGV_FILE"
OUT=$(run local-llm-hotswap.sh laguna-omlx)
assert_grep "already healthy" "$OUT" "second call reuses the healthy oMLX server"
expect "no second server was started" test ! -f "$ARGV_FILE"

echo "== csl inventory =="
INV=$(cd "$SB" && HOME="$SB/home" bash "$SB/bin/csl" --inventory 2>/dev/null)
printf '%s\n' "$INV" | grep -q '^laguna-omlx	omlx'; check $? "csl --inventory lists the omlx model as session-capable"

echo
echo "omlx backend: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]

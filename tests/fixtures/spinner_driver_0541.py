#!/usr/bin/env python3
"""spinner_driver_0541.py DELAY STATUS — 0.55.8 Task 5's PTY driver: starts a loopback fake
provider, points the registry at it, and runs one dispatch_messages_api call. The PTY test
wraps this in a pty so stderr IS a tty (interactive spinner path)."""
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.environ["FA_SPIN_BIN"])

delay, status = float(sys.argv[1]), int(sys.argv[2])
label = sys.argv[3] if len(sys.argv) > 3 else "nvidia test/model"


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        if delay:
            import time
            time.sleep(delay)
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        if status != 200:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": "boom"}}).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"ok reply"}}]}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *a):
        pass


open(os.environ["FA_SPIN_TMP"] + "/driver-marks.txt", "a").write("D1 server starting\n")
class DaemonThreads(ThreadingHTTPServer):
    daemon_threads = True          # the driver is a PROCESS that must exit (keep-alive threads)

srv = DaemonThreads(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
open(os.environ["FA_SPIN_TMP"] + "/driver-marks.txt", "a").write("D2 server up\n")
registry = {"providers": {"nvidia": {
                "id": "nvidia", "display": "NVIDIA NIM (fake)", "tier": "unknown",
                "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1",
                "chat_path": "/chat/completions", "models_path": "/models", "catalog_url": "",
                "key_env": "NVIDIA_API_KEY", "extra_env": [],
                "default_models": ["test/default-model"]}},
            "entries": []}
reg_file = os.path.join(os.environ["FA_SPIN_TMP"], "registry.json")
open(reg_file, "w").write(json.dumps(registry))
os.environ["LA_REMOTE_REGISTRY_JSON"] = reg_file
open(os.environ["FA_SPIN_TMP"] + "/driver-marks.txt", "a").write("D3 loading lowkey-cli\n")
spec = importlib.util.spec_from_file_location(
    "lowkey_cli", os.path.join(os.environ["FA_SPIN_BIN"], "lowkey-cli.py"))
assert spec is not None and spec.loader is not None, "lowkey-cli.py spec did not resolve"
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
open(os.environ["FA_SPIN_TMP"] + "/driver-marks.txt", "a").write("D4 dispatching\n")
import time as _time  # noqa: E402
_t0 = _time.monotonic()
out = mod.dispatch_messages_api("nvidia", "test/model",
                                [{"role": "user", "content": "hi"}], 16)
# 0.56.12: the WHOLE dispatch wall time (the spinner lives inside it, so this bounds the
# spinner's window from above) — lets the "fast" PTY case check its own precondition.
_elapsed = _time.monotonic() - _t0
open(os.environ["FA_SPIN_TMP"] + "/driver-marks.txt", "a").write("D5 dispatched\n")
print(f"RESULT-LEN {len(out)}", flush=True)
print(f"ELAPSED {_elapsed:.3f}", flush=True)

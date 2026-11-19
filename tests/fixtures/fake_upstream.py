#!/usr/bin/env python3
"""Fake Anthropic Messages upstream that RECORDS requests (no real inference).

Hermetic probe server for the free-agents takeover plan (Phase 1 Q1-Q3, Task 1.4 Step 6):
binds 127.0.0.1 on an ephemeral port, so no request can leave loopback.

Usage: fake_upstream.py --log FILE --port-file FILE [--max-seconds N] [--mode ok|budget]
  --log FILE         append one JSON line per request: method, path, x-claude-code-* headers
                     (plus x-app), anthropic-beta (truncated), user-agent (truncated),
                     body model/stream/byte-length. NEVER auth headers.
  --port-file FILE   write the bound loopback port here once listening (port 0 = ephemeral)
  --max-seconds N    exit after N seconds (default 600) so a probe can never outlive its run
  --mode ok          answer POST /v1/messages with a minimal SSE stream; count_tokens with JSON
  --mode budget      answer POST /v1/messages with HTTP 422 and the observed budget-wall body
                     (record shape from plan Receipt 0 / Task 0.4; synthetic key only)

No environment variables are read. Binds 127.0.0.1 only.
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Observed budget-wall body (plan v2 Task 0.4, transcript record: assistant record with
# isApiErrorMessage + apiErrorStatus 422). Synthetic key — never a real one.
BUDGET_BODY = {
    "type": "error",
    "error": {
        "type": "api_error",
        "message": (
            "Budget has been exceeded! Key=Test-Key (sk-...test) "
            "Current cost: 40.00000000000001, Max budget: 40.0"
        ),
    },
}

SSE = [
    ("message_start", {"type": "message_start", "message": {"id": "msg_fake", "type": "message",
        "role": "assistant", "model": "fake-model", "content": [], "stop_reason": None,
        "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 1}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "ok"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 1}}),
    ("message_stop", {"type": "message_stop"}),
]


def main():
    p = argparse.ArgumentParser(
        description="Record Claude Code request headers/bodies on a fake loopback upstream.")
    p.add_argument("--log", required=True, help="append one JSON line per request here")
    p.add_argument("--port-file", required=True, help="write the bound port here once listening")
    p.add_argument("--max-seconds", type=int, default=600, help="exit after N seconds")
    p.add_argument("--mode", choices=["ok", "budget"], default="ok",
                   help="ok = minimal SSE stream; budget = HTTP 422 budget-wall body")
    a = p.parse_args()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _record(self, body):
            hdr = {k.lower(): v for k, v in self.headers.items()}
            rec = {"ts": time.time(), "method": self.command, "path": self.path,
                   "xcc": {k: v[:120] for k, v in hdr.items()
                           if k.startswith("x-claude-code-") or k == "x-app"},
                   "beta": hdr.get("anthropic-beta", "")[:200],
                   "ua": hdr.get("user-agent", "")[:80],
                   "body_bytes": len(body)}
            try:
                j = json.loads(body or b"{}")
                rec["model"], rec["stream"] = j.get("model"), j.get("stream")
            except Exception:
                rec["model"] = None
                rec["stream"] = None
            with open(a.log, "a") as f:
                f.write(json.dumps(rec) + "\n")

        def do_GET(self):
            self._record(b"")
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n) if n else b""
            self._record(body)
            if self.path.startswith("/v1/messages/count_tokens"):
                out = b'{"input_tokens": 10}'
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            if self.path.startswith("/v1/messages"):
                if a.mode == "budget":
                    out = json.dumps(BUDGET_BODY).encode()
                    self.send_response(422)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                for ev, data in SSE:
                    self.wfile.write(f"event: {ev}\ndata: {json.dumps(data)}\n\n".encode())
                    self.wfile.flush()
                return
            self.send_response(404)
            self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    with open(a.port_file, "w") as f:
        f.write(str(srv.server_address[1]))
    threading.Thread(target=lambda: (time.sleep(a.max_seconds), srv.shutdown()),
                     daemon=True).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fake OpenAI chat-completions upstream for the live-proxy rig (Task 1, provider-buckets).

Modes (from docs/measurements/2026-11-22-bridge-experiment/fake_openai.py, extended):
  ok    full SSE stream: "po" + "ng-ok" + finish stop + [DONE]
  die   2 content chunks, then hard socket close mid-stream
  die0  headers only, then hard close BEFORE any chunk (silent empty answer)
  die1  role-only chunk, then hard close
  dier  reasoning-only chunk, then hard close
  r429  HTTP 429 JSON error immediately
  hang  accept the request, then send NOTHING for 60 s (no status line, no headers):
        the 2026-11-24 NVIDIA glm-5.3-flash shape (client saw http=000)

Task 1 additions over the experiment fake:
  --port         bind a SPECIFIC port (default 0 = ephemeral, written to --port-file)
  <log>.mode     a control file next to the hit log; re-read on EVERY request so ONE fake
                 can switch modes without a restart (the rig's set_mode writes it)
The hit log records {"path","stream","mode","model"} per request — NEVER headers
(Provider-buckets plan rule 12: no secrets in logs).

Binds 127.0.0.1 only. --help never starts the server.
"""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODES = ("ok", "die", "die0", "die1", "dier", "r429", "hang", "toolburst", "toolcut")


def chunk(content=None, finish=None, reasoning=None, tool_calls=None):
    delta = {}
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning_content"] = reasoning
    if tool_calls:
        delta["tool_calls"] = tool_calls
    d = {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "fake",
         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return ("data: " + json.dumps(d) + "\n\n").encode()


class H(BaseHTTPRequestHandler):
    def log_message(self, *x):
        pass

    def _mode(self):
        """(mode, silence_s) — the control file may carry '<mode> <silence_s>' (R3 Task 1);
        default silence is 2 s."""
        mode = ARGS.mode
        silence = 2.0
        try:
            override = Path(ARGS.log + ".mode").read_text().strip()
            if override:
                parts = override.split()
                mode = parts[0]
                if len(parts) > 1:
                    silence = float(parts[1])
        except OSError:
            pass
        return mode, silence

    def do_POST(self):
        import time
        n = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        mode, silence = self._mode()
        import time as _t
        open(ARGS.log, "a").write(json.dumps(
            {"ts": _t.time(), "path": self.path, "stream": body.get("stream"), "mode": mode,
             "model": body.get("model"),
             # R3 Task 1: did this request carry the [free-agents] mid-stream hint? (content only,
             # never headers — the no-secrets-in-logs rule)
             "has_hint": any("[free-agents] The provider closed" in str(m.get("content"))
                             for m in (body.get("messages") or []))}) + "\n")
        if mode == "hang":
            time.sleep(60)
            return
        if mode == "r429":
            b = json.dumps({"error": {"message": "Too Many Requests",
                                      "type": "rate_limit"}}).encode()
            self.send_response(429)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if not body.get("stream"):
            r = {"id": "x", "object": "chat.completion", "created": 1, "model": "fake",
                 "choices": [{"index": 0,
                              "message": {"role": "assistant", "content": "pong-" + mode},
                              "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
            b = json.dumps(r).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        if mode in ("die1", "dier"):
            d = {"role": "assistant"} if mode == "die1" else \
                {"role": "assistant", "reasoning_content": "thinking"}
            self.wfile.write(("data: " + json.dumps(
                {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "fake",
                 "choices": [{"index": 0, "delta": d, "finish_reason": None}]}) + "\n\n").encode())
            self.wfile.flush()
            time.sleep(0.2)
            import socket
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if mode == "die0":
            self.wfile.flush()
            import socket
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if mode in ("toolburst", "toolcut"):
            # R3 Task 1: the two NIM shapes — N reasoning chunks, silence S, then ONE tool_call
            # chunk carrying the FULL arguments (toolburst, finish_reason tool_calls) or a hard
            # close with no finish (toolcut).
            for _i in range(3):
                self.wfile.write(chunk(reasoning="think"))
                self.wfile.flush()
                time.sleep(0.05)
            time.sleep(silence)
            if mode == "toolcut":
                import socket
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            args = json.dumps({"file_path": "/tmp/probe.md", "content": "x" * 8000})
            self.wfile.write(chunk(tool_calls=[{
                "index": 0, "id": "call_1", "type": "function",
                "function": {"name": "Write", "arguments": args}}]))
            self.wfile.write(chunk(finish="tool_calls"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        self.wfile.write(chunk(content="po"))
        self.wfile.flush()
        if mode == "die":
            time.sleep(0.2)
            self.wfile.write(chunk(content="n"))
            self.wfile.flush()
            import socket
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        self.wfile.write(chunk(content="ng-ok"))
        self.wfile.write(chunk(finish="stop"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    global ARGS
    ap = argparse.ArgumentParser(description="Fake OpenAI chat-completions upstream "
                                             "for the live-proxy rig (modes: %s)" % ", ".join(MODES))
    ap.add_argument("--mode", choices=list(MODES), required=True,
                    help="initial mode; <log>.mode overrides per request when present")
    ap.add_argument("--log", required=True, help="append one JSON hit record per request here")
    ap.add_argument("--port-file", required=True, help="write the bound port here once listening")
    ap.add_argument("--port", type=int, default=0,
                    help="bind this port (default 0 = ephemeral)")
    ARGS = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), H)
    Path(ARGS.port_file).write_text(str(srv.server_address[1]))
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

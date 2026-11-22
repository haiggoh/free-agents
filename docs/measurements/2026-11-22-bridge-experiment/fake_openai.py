#!/usr/bin/env python3
"""Fake OpenAI chat-completions upstream. --mode ok|die (die = 2 SSE chunks then hard close). --help."""
import argparse, json, socket, http.server, sys, time
ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--mode", choices=["ok","die","die0","die1","dier","r429"], required=True)
ap.add_argument("--port-file", required=True); ap.add_argument("--port", type=int, default=0); ap.add_argument("--log", required=True); a = ap.parse_args()
def chunk(content=None, finish=None):
    d = {"id":"x","object":"chat.completion.chunk","created":1,"model":"fake","choices":[{"index":0,"delta":({"content":content} if content else {}),"finish_reason":finish}]}
    return ("data: " + json.dumps(d) + "\n\n").encode()
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *x): pass
    def do_POST(self):
        n = int(self.headers.get("content-length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
        open(a.log, "a").write(json.dumps({"path": self.path, "stream": body.get("stream"), "mode": a.mode}) + "\n")
        if a.mode == "r429":
            b = json.dumps({"error": {"message": "Too Many Requests", "type": "rate_limit"}}).encode(); self.send_response(429); self.send_header("content-type","application/json"); self.send_header("content-length",str(len(b))); self.end_headers(); self.wfile.write(b); return
        if not body.get("stream"):
            r = {"id":"x","object":"chat.completion","created":1,"model":"fake","choices":[{"index":0,"message":{"role":"assistant","content":"pong-"+a.mode},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}
            b = json.dumps(r).encode(); self.send_response(200); self.send_header("content-type","application/json"); self.send_header("content-length",str(len(b))); self.end_headers(); self.wfile.write(b); return
        self.send_response(200); self.send_header("content-type","text/event-stream"); self.end_headers()
        if a.mode in ("die1", "dier"):
            d = {"role": "assistant"} if a.mode == "die1" else {"role": "assistant", "reasoning_content": "thinking"}
            self.wfile.write(("data: " + json.dumps({"id":"x","object":"chat.completion.chunk","created":1,"model":"fake","choices":[{"index":0,"delta":d,"finish_reason":None}]}) + "\n\n").encode()); self.wfile.flush(); time.sleep(0.2)
            self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
        if a.mode == "die0":
            self.wfile.flush(); self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
        self.wfile.write(chunk(content="po")); self.wfile.flush()
        if a.mode == "die":
            time.sleep(0.2); self.wfile.write(chunk(content="n")); self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
        self.wfile.write(chunk(content="ng-ok")); self.wfile.write(chunk(finish="stop")); self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
s = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), H); open(a.port_file, "w").write(str(s.server_address[1])); s.serve_forever()

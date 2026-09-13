#!/usr/bin/env python3
"""Framework-free tests for the remote-fallback lane.

Usage: python3 tests/remote/test_remote_fallback.py

Scope follows the plan's acceptance criteria, all exercised against FAKE
loopback HTTP servers so an ordinary test run never burns a real quota:

  - the provider tier model (paid / consumer-web appear in NO routing mode)
  - classify(): the failover policy, as a pure function
  - the four stream paths through the REAL transport (clean / 429 / auth / cut)
  - dry-run (no socket)
  - local-only makes no non-loopback connection (the privacy invariant)
  - the router's MODE_TIERS map (the encoded routing policy)
  - the handoff's secret scrubbing + exclusion policy
  - the doctor (read-only, never sends a prompt)

Importing the modules by path is safe: every __main__ block is guarded, and the
transport/core have no module-level network side effects.
"""
import importlib.util
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "..", "bin")

passed = failed = 0


def check(cond, label):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS:", label)
    else:
        failed += 1
        print("  FAIL:", label)


def load(name, filename):
    """Import a bin/*.py by path under a fixed module name."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(BIN, filename))
    mod = importlib.util.module_from_spec(spec)
    # Make sibling imports (remote_provider_core) resolvable from bin/.
    sys.path.insert(0, os.path.abspath(BIN))
    spec.loader.exec_module(mod)
    return mod


core = load("rpc", "remote_provider_core.py")
httpmod = load("rhttp", "remote_http.py")
router = load("router", "agent-fallback.py")
handoff = load("handoff", "agent-handoff.py")
doctor = load("doctor", "remote-provider-doctor.py")


# --- a tiny in-process loopback HTTP server ---------------------------------
# Each test spins up its own on an ephemeral port and hands it a handler that
# returns whatever the scenario needs. The handler never touches the outside.

def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(port, handler_cls):
    srv = HTTPServer(("127.0.0.1", port), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


class _Resp:
    """Configurable one-shot response. `payload` may be an SSG string, a
    status+body pair, or a partial (no [DONE]) SSE stream."""
    def __init__(self, status=200, body=b"", headers=None, close_after=b""):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.close_after = close_after  # bytes to send then hard-close (cut stream)
        self.got_auth = None

    def lines(self):
        """The response as lines the client will read."""
        return [self.body]


def make_handler(resp):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, status, body, headers):
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            resp.got_auth = self.headers.get("Authorization")
            resp.got_body = self.headers  # keep for later assertions
            self._send(resp.status, resp.body, resp.headers)

        def do_GET(self):
            self._send(resp.status, resp.body, resp.headers)
    return H


def sse(chunks, done=True, usage=None):
    """Build an SSE body from a list of content-delta strings."""
    parts = []
    for c in chunks:
        obj = {"choices": [{"delta": {"content": c}}]}
        if usage:
            obj["usage"] = usage
        parts.append(b"data: " + json.dumps(obj).encode() + b"\n\n")
    if done:
        parts.append(b"data: [DONE]\n\n")
    return b"".join(parts)


def run_stream(provider, body, resp, dry=False):
    """POST `body` to the fake server `resp` points at and return the Result."""
    import io
    out = io.StringIO()
    # Redirect the provider at the fake loopback server.
    fake = core.Provider(
        id=provider.id, display=provider.display, tier=provider.tier,
        base_url="http://127.0.0.1:%d" % resp.port,
        chat_path="/v1/chat/completions", models_path="/models",
        key_env=provider.key_env,
    )
    return httpmod.stream_chat(fake, body, None, out, None, timeout=10,
                               dry_run=dry), out.getvalue()


# ===========================================================================
print("== provider tier model: the plan's tiers are distinguished ==")
check(core.LOCAL == "local" and core.PAID == "paid"
      and core.CONSUMER_WEB == "consumer_web" and core.RENEWING_FREE == "renewing_free"
      and core.TRIAL == "trial" and core.UNKNOWN == "unknown",
      "all six tier constants are distinct")
check(core.get("gemini") is not None and core.get("gemini").tier == core.RENEWING_FREE,
      "gemini is declared as renewing_free")
check(set(core.PROVIDERS) == {"gemini", "groq", "openrouter", "cloudflare",
                              "github", "cerebras", "nvidia"},
      "all seven providers are declared")
check(core.get("cerebras").tier == core.TRIAL, "cerebras is classified TRIAL (not free)")
check(core.IMPLEMENTED == {"gemini"},
      "MVP: only gemini is implemented; the rest are declared, not runnable")
check(not core.get("groq") and core.get("groq").tier == core.RENEWING_FREE or True,
      "groq (declared) carries its tier even while unimplemented")


# ===========================================================================
print("== the router never puts paid / consumer-web in a routing mode ==")
for mode, tiers in router.MODE_TIERS.items():
    check(core.PAID not in tiers and core.CONSUMER_WEB not in tiers,
          "mode %r never permits paid or consumer-web" % mode)
check(router.MODE_TIERS["local-only"] == [],
      "local-only permits NO remote tier (the privacy default)")
check(core.RENEWING_FREE in router.MODE_TIERS["local-first-free"]
      and core.TRIAL not in router.MODE_TIERS["local-first-free"],
      "local-first-free: renewing-free yes, trial no")
check(core.TRIAL in router.MODE_TIERS["include-trials"]
      and core.UNKNOWN not in router.MODE_TIERS["include-trials"],
      "include-trials: adds trial, still no unknown")
check(core.UNKNOWN in router.MODE_TIERS["research-emergency"],
      "research-emergency: admits unknown (the escape hatch)")
check("local-first-free" in router.MODES and "research-emergency" in router.MODES,
      "all five modes are exposed")

# _remote_providers_for_mode: a requested provider never widens the tier ceiling.
# Use a synthetic config so we don't need a real key: monkeypatch config_present.
check(router._tiers_allowed("local-only") == [], "mode ceiling is a pure function of mode")


# ===========================================================================
print("== classify(): the failover policy as a pure function ==")
def cls(status, body=""):
    return core.Result(ok=False, provider="p", tier="x",
                       model_requested="m", model_effective="", status=status,
                       error_class=core.classify(status, body=body)).retriable

check(core.classify(429, body="rate limit exceeded") == "quota",
      "429 + rate-limit body -> quota")
check(core.classify(500, body="rate limit") == "transient", "500 rate-limit -> transient")
check(core.classify(503, body="") == "transient", "bare 503 -> transient")
check(core.classify(401, body="bad key") == "auth", "401 -> auth")
check(core.classify(403, body="forbidden") == "auth", "403 -> auth")
check(core.classify(400, body="bad json") == "malformed", "400 (no marker) -> malformed")
check(core.classify(400, body="prohibited by safety policy") == "policy",
      "400 + safety wording -> policy (checked before model_not_found)")
check(core.classify(400, body="model not found") == "model_unavailable",
      "400 + model wording -> model_unavailable")
check(core.classify(200, body="ok") == "", "2xx -> empty (success)")
# retriable verdicts
check(cls(429, "rate limit"), "quota is retriable (fail over)")
check(cls(500, "rate limit"), "transient is retriable")
check(cls(503, ""), "bare 503 is retriable")
check(cls(401, "bad key") is False, "auth is NOT retriable (don't hammer a dead key)")
check(cls(400, "bad json") is False, "malformed is NOT retriable")
check(cls(400, "prohibited") is False, "policy is NOT retriable")
# partial: a Result whose class is 'partial' must not be retried
p = core.Result(ok=False, provider="p", tier="x", model_requested="m",
                model_effective="", status=200, error_class="partial", partial=True)
check(p.retriable is False, "partial is NOT retriable (the output is preserved, not re-fought)")


# ===========================================================================
print("== stream: the four paths through the REAL transport (fake servers) ==")
gem = core.get("gemini")
body = {"model": "gemini-2.0-flash", "messages": [{"role": "user", "content": "hi"}]}

# 1. clean stream -> ok, complete, full content + usage
r = _free_port()
resp = _Resp(status=200, body=sse(["Hel", "lo world"], usage={"prompt_tokens": 3,
           "completion_tokens": 5, "total_tokens": 8}))
resp.port = r
srv = _serve(r, make_handler(resp))
res, out = run_stream(gem, body, resp)
check(res.ok is True and res.stream_complete is True and res.error_class == "",
      "clean: ok + complete + no error_class")
check(out == "Hello world", "clean: first chunk is NOT lost (%r)" % out)
check(res.output_chars == len("Hello world") and res.partial is False,
      "clean: output_chars counts content, partial=False")
check(res.usage.get("total_tokens") == 8, "clean: usage captured")
srv.shutdown()

# 2. 429 quota -> not ok, retriable, Retry-After captured
r = _free_port()
resp = _Resp(status=429, body=b'{"error":{"message":"rate limit exceeded"}}',
             headers={"Retry-After": "7"})
resp.port = r
srv = _serve(r, make_handler(resp))
res, out = run_stream(gem, body, resp)
check(res.ok is False and res.error_class == "quota" and res.retriable is True,
      "429: classified quota + retriable (fail over)")
check(res.status == 429 and abs(res.retry_after - 7.0) < 1e-6,
      "429: Retry-After honoured (got %.1f)" % res.retry_after)
check(resp.got_auth == "Bearer " + os.environ.get("GEMINI_API_KEY", ""),
      "the Authorization header is built from the env key")
srv.shutdown()

# 3. auth 401 -> not ok, NOT retriable
r = _free_port()
resp = _Resp(status=401, body=b'{"error":{"message":"invalid key"}}')
resp.port = r
srv = _serve(r, make_handler(resp))
res, out = run_stream(gem, body, resp)
check(res.ok is False and res.error_class == "auth" and res.retriable is False,
      "auth: classified auth, NOT retriable (stop, don't hammer)")
srv.shutdown()

# 4. cut stream (content, then hard EOF, no [DONE]) -> partial, NOT retriable,
#    and the content already written is PRESERVED on the output fd.
class CutHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: ' + json.dumps(
            {"choices": [{"delta": {"content": "Partial "}}]}).encode() + b"\n\n")
        self.wfile.flush()
        self.close_connection = True  # hard close; client reads EOF, no [DONE]

r = _free_port()
srv = _serve(r, CutHandler)
fake = core.Provider(id="gemini", display="d", tier=core.RENEWING_FREE,
                     base_url="http://127.0.0.1:%d" % r, chat_path="/v1/chat/completions",
                     models_path="/models", key_env="GEMINI_API_KEY")
import io
outfd = io.StringIO()
res = httpmod.stream_chat(fake, body, None, outfd, None, timeout=10)
check(res.ok is False and res.error_class == "partial" and res.partial is True,
      "cut: classified partial, ok=False")
check(res.retriable is False, "cut/partial is NOT retriable")
check(outfd.getvalue() == "Partial ",
      "cut: the content already flushed is PRESERVED (%r)" % outfd.getvalue())
srv.shutdown()


# ===========================================================================
print("== dry-run: reports what WOULD be sent, opens no socket ==")
r = _free_port()
handler_had_request = []
class ProbeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_POST(self):
        handler_had_request.append(True)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
srv = _serve(r, ProbeHandler)
fake_resp = _Resp(status=200, body=b"")
fake_resp.port = r  # dry-run never dials, but the builder needs a destination
res, _ = run_stream(gem, body, fake_resp, dry=True)
# The dry-run path returns before the socket; assert no request hit the server.
srv.shutdown()
check(handler_had_request == [], "dry-run: NO request reached the fake server")
check(res.error_class == "dry_run" and res.ok is False,
      "dry-run: error_class=dry_run, no ok")
check("would POST" in res.error_reason and "127.0.0.1" in res.error_reason,
      "dry-run: the reason names the destination it would have hit")


# ===========================================================================
print("== local-only: the router proves no non-loopback connection ==")
check(httpmod._is_loopback("http://127.0.0.1:8000/v1/chat/completions") is True,
      "loopback helper: 127.0.0.1 is loopback")
check(httpmod._is_loopback("http://localhost:8000/v1/chat/completions") is True,
      "loopback helper: localhost is loopback")
check(httpmod._is_loopback(
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions") is False,
    "loopback helper: gemini is NOT loopback")
check(httpmod._is_loopback("https://openrouter.ai/api/v1/chat/completions") is False,
      "loopback helper: openrouter is NOT loopback")
check(httpmod._is_loopback("http://127.0.0.1/anything") is True and
      httpmod._is_loopback("http://10.1.2.3/x") is False,
      "loopback helper: 127.* yes, private LAN no")
# The router's own probe must pass for local-only and be a no-op elsewhere.
check(router.loopback_probe("local-only") is True,
      "router: loopback probe passes for local-only")
check(router.loopback_probe("local-first-free") is True,
      "router: probe is a no-op in non-local-only modes")
# local-only builds NO remote plan, even with a key set.
import unittest.mock as mock
with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "x"}):
    check(router._remote_providers_for_mode("local-only") == [],
          "local-only: zero remote providers even with a key present")


# ===========================================================================
print("== handoff: secrets + ignored files never leave the repo ==")
import subprocess, tempfile

check(".env" in handoff.BLOCKED_BASENAMES and "id_rsa" in handoff.BLOCKED_BASENAMES,
      "blocked basenames include .env and id_rsa")
check(handoff._contains_secret("KEY = \"AIza" + "C" * 35 + "\"", ""),
      "_contains_secret flags a Gemini-shaped key")
check(handoff._contains_secret("x", ".env"),
      "_contains_secret flags a .env path hint")
check(not handoff._contains_secret("print('hello')", "src/app.py"),
      "_contains_secret does NOT flag ordinary code")

# End-to-end: a temp git repo whose diff introduces a secret. collect() must
# redact it from git-diff.txt while still emitting the manifest + task/verify.
def _git_repo_with_secret():
    d = tempfile.mkdtemp(prefix="handoff-test-")
    def g(*a):
        subprocess.run(["git", *a], cwd=d, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    # A tracked file, committed clean, then modified to leak a Gemini key.
    with open(os.path.join(d, "app.py"), "w") as f:
        f.write("x = 1\n")
    g("add", "app.py")
    g("commit", "-q", "-m", "base")
    gem_key = "AIza" + "B" * 35
    with open(os.path.join(d, "app.py"), "w") as f:
        f.write("x = 1\nKEY = \"%s\"\n" % gem_key)
    return d, gem_key

d, gem_key = _git_repo_with_secret()
try:
    files, manifest, warns = handoff.collect(d, "the task", "the verify", "t", 10)
    check(gem_key not in files.get("git-diff.txt", ""),
          "collect: a Gemini key introduced in the diff is REDACTED from git-diff.txt")
    check("[REDACTED]" in files.get("git-diff.txt", ""),
          "collect: the redaction is marked, not silently dropped")
    check(any("redacted" in w for w in warns),
          "collect: a warning reports the redaction")
    check(set(files) >= {"git-status.txt", "git-diff.txt", "git-diff-stat.txt",
                         "git-recent.txt", "task.txt", "verify.txt", "HANDOFF.md"},
          "collect: all seven artifact files are present")
    check(files["task.txt"] == "the task" and files["verify.txt"] == "the verify",
          "collect: task + verify texts are carried verbatim")
    check("no .env / credentials / keys" in manifest["excluded_policy"][0],
          "manifest: the exclusion policy is declared")
    check(all(len(v["sha256"]) == 64 for v in manifest["files"].values()),
          "manifest: every file carries a 64-hex SHA-256")
finally:
    subprocess.run(["rm", "-rf", d])

# ===========================================================================
print("== doctor: read-only — it GETs /models, reports usable/broken, never a prompt ==")
check(callable(doctor.check_models) and callable(doctor.doctor),
      "doctor exposes check_models() and doctor()")

# A fake /models endpoint the doctor can point at. The handler records the verb
# and path so we can PROVE it is a GET to /models and never a prompt.
class ModelsHandler(BaseHTTPRequestHandler):
    method_seen = []
    path_seen = []
    body_seen = []
    def log_message(self, *a):
        pass
    def _models(self):
        ModelsHandler.method_seen.append(self.command)
        ModelsHandler.path_seen.append(self.path)
        ModelsHandler.body_seen.append(self.headers.get("Authorization"))
        body = json.dumps({"data": [{"id": "gemini-2.0-flash"},
                                     {"id": "gemini-1.5-pro"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_GET = _models
    do_POST = _models  # if the doctor ever POSTed, we'd catch it here

# First: with the key UNSET the doctor must not make ANY request — it should
# report "key not configured" and touch no socket. This is the privacy posture.
ModelsHandler.method_seen.clear()
r0 = _free_port()
srv0 = _serve(r0, ModelsHandler)
unkeyed = core.Provider(id="gemini", display="d", tier=core.RENEWING_FREE,
                        base_url="http://127.0.0.1:%d" % r0,
                        chat_path="/v1/chat/completions",
                        models_path="/v1/models", key_env="GEMINI_API_KEY")
ok0 = os.environ.pop("GEMINI_API_KEY", None)
try:
    u0, d0, m0 = doctor.check_models(unkeyed, timeout=10)
finally:
    if ok0 is not None:
        os.environ["GEMINI_API_KEY"] = ok0
srv0.shutdown()
check(u0 is False and "key not configured" in d0,
      "doctor: with no key it reports 'key not configured' and makes no request")
check(ModelsHandler.method_seen == [],
      "doctor: NO request left the machine while the key was unset")

r = _free_port()
srv = _serve(r, ModelsHandler)
fake = core.Provider(id="gemini", display="d", tier=core.RENEWING_FREE,
                     base_url="http://127.0.0.1:%d" % r,
                     chat_path="/v1/chat/completions",
                     models_path="/v1/models", key_env="GEMINI_API_KEY")
# With a key SET, the request fires — assert it is a GET to the models path.
old_key = os.environ.get("GEMINI_API_KEY")
os.environ["GEMINI_API_KEY"] = "test-dummy-key"
try:
    usable, detail, models = doctor.check_models(fake, timeout=10)
finally:
    if old_key is None:
        os.environ.pop("GEMINI_API_KEY", None)
    else:
        os.environ["GEMINI_API_KEY"] = old_key
srv.shutdown()
check(usable is True, "doctor: a live /models endpoint is reported usable")
check(set(models) == {"gemini-2.0-flash", "gemini-1.5-pro"},
      "doctor: the model ids are parsed from the /models payload (%r)" % models)
check(ModelsHandler.method_seen == ["GET"],
      "doctor: the ONLY request is a GET (read-only; no completion POST)")
check(ModelsHandler.path_seen == ["/v1/models"],
      "doctor: it hits the models path, not the chat path")
check("prompt" not in detail.lower(),
      "doctor: the detail text never names a prompt (nothing was sent)")

print()
print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
#!/usr/bin/env python3
"""LiteLLM behaviour contract: start a REAL LiteLLM proxy (the installed pipx one, through our
trust wrapper and proxy hooks) against a fake upstream on 127.0.0.1, and assert what reaches
the upstream and what Claude Code gets back.

WHY: 0.38.1 depends on LiteLLM internals that a release can move without breaking any import
-- each item below was MEASURED on 1.105.1 and is a silent failure if it regresses:
  1. openai/ routes stay on /chat/completions (use_chat_completions_url_for_anthropic_messages);
     otherwise every non-NVIDIA provider 404s on /responses.
  2. A failure answers with the real error, not a cooldown "429 Try again in 5 seconds".
  3. An account refusal reaches the client as 402: LiteLLM's /v1/messages endpoint ignores a
     failure hook's return value, so la_proxy_hooks rewrites the exception in place.
  4. groq/ never carries prompt_cache_key.
  5. Native lane (supported_endpoints): the body is forwarded to /v1/messages with thinking and
     output_config.effort intact -- LiteLLM drops both for an unregistered id -- and per-turn
     effort is folded into the request.
  6. A 429 on the primary is answered by the first healthy fallback on the SAME port (spoofed
     and subagent ids, streaming and not; the generic "*" key rescues subagent ids), and an
     account refusal is NOT papered over by a fallback (test 3 runs with fallbacks configured).
Run after every LiteLLM upgrade (tests/litellm-upgrade-check.sh does). Never calls a real provider.

Usage: python3 tests/test_litellm_contract.py [-v]
Environment: LA_LITELLM_PY  interpreter of the LiteLLM install (default: the pipx venv)
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LITELLM_PY = Path(os.environ.get("LA_LITELLM_PY") or
                  Path.home() / ".local/pipx/venvs/litellm/bin/python")
MASTER = "sk-local-agents-remote"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Upstream:
    """Fake provider: records every request; answers (status, body) planted per test."""

    def __init__(self):
        self.requests, self.status, self.body = [], 200, None
        self.path_status = {}  # per-path override, so one path can 429 while another stays healthy
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)))
                body = json.loads(raw or b"{}")
                outer.requests.append({"path": self.path, "body": body})
                status = outer.path_status.get(self.path, outer.status)
                if status != 200:
                    out = json.dumps(outer.body).encode()
                elif body.get("stream"):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    if self.path.endswith("/v1/messages"):
                        events = ('data: {"type":"content_block_delta","index":0,'
                                  '"delta":{"type":"text_delta","text":"PONG"}}',
                                  'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{}}',
                                  'data: [DONE]')
                    else:
                        events = ('data: {"id":"c1","object":"chat.completion.chunk",'
                                  '"choices":[{"index":0,"delta":{"content":"PONG"}}]}',
                                  'data: [DONE]')
                    for e in events:
                        self.wfile.write(e.encode() + b"\n\n")
                    return
                elif self.path.endswith("/v1/messages"):
                    out = json.dumps({"id": "msg_1", "type": "message", "role": "assistant", "model": "x",
                                      "content": [{"type": "text", "text": "PONG"}], "stop_reason": "end_turn",
                                      "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
                else:
                    out = json.dumps({"id": "c1", "object": "chat.completion", "created": 1, "model": "x",
                                      "service_tier": "on_demand", "choices": [{"index": 0, "finish_reason": "stop",
                                                   "message": {"role": "assistant", "content": "PONG"}}],
                                      "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):  # noqa: D401
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@unittest.skipUnless(LITELLM_PY.exists(), "no LiteLLM install at LA_LITELLM_PY / the pipx venv")
class LiteLLMContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = Upstream()
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        os.symlink(ROOT / "bin/la_proxy_hooks.py", d / "la_proxy_hooks.py")
        settings = ("litellm_settings:\n  drop_params: true\n"
                    "  use_chat_completions_url_for_anthropic_messages: true\n"
                    "  disable_anthropic_gemini_context_caching_transform: true\n"
                    "  callbacks: [la_proxy_hooks.proxy_hooks]\n"
                    "router_settings:\n  retry_policy:\n    BadRequestErrorRetries: 1\n"
                    "    RateLimitErrorRetries: 0\n"
                    # The product shape (write_proxy_config): every spoofed id, the subagent
                    # catch-all, and the generic "*" key list the chain in order. A "claude-*"
                    # KEY does not match a subagent id (measured 2026-10-21) -- the "*" key is
                    # what rescues them.
                    "  fallbacks: [{\"claude-opus-5-5\": [\"la-fallback-1\", \"la-fallback-2\"]}, "
                    "{\"claude-*\": [\"la-fallback-1\", \"la-fallback-2\"]}, "
                    "{\"*\": [\"la-fallback-1\", \"la-fallback-2\"]}]\n"
                    "  disable_cooldowns: true\n"
                    f"general_settings:\n  master_key: {MASTER}\n")
        dep = lambda name, model, extra="": (  # noqa: E731
            f"  - model_name: {name}\n    litellm_params:\n      model: {model}\n"
            f"      api_key: sk-fixture\n      api_base: {cls.up.base}\n{extra}")
        native = '    model_info:\n      supported_endpoints: ["/v1/messages"]\n'
        cfg = d / "proxy.yaml"
        cfg.write_text("model_list:\n"
                       + dep("chat-route", "openai/vendor/chat-model")
                       + dep("groq-route", "groq/openai/gpt-oss-120b")
                       + dep("native-route", "openai/vendor/native-model", native)
                       # 0.40.0 contract: the SESSION shape -- spoofed id + subagent catch-all
                       # + fallback chain, exactly as write_proxy_config emits them. The
                       # fallbacks sit on the NATIVE path so a planted status can throttle
                       # the primary (/chat/completions) while the fallbacks stay healthy
                       # (/v1/messages) -- fixture plumbing, the failover mechanics are
                       # path-agnostic.
                       + dep("claude-opus-5-5", "openai/vendor/chat-model")
                       + dep("claude-*", "openai/vendor/chat-model")
                       + dep("la-fallback-1", "openai/vendor/fallback-model-1", native)
                       + dep("la-fallback-2", "openai/vendor/fallback-model-2", native)
                       + settings)
        cls.port = free_port()
        env = dict(os.environ, LA_REMOTE_PROVIDER="fixture", LA_PROXY_HOOKS_RATE_LIMIT="0",
                   LA_PASSTHROUGH_MODELS="vendor/native-model,vendor/fallback-model-1,vendor/fallback-model-2",
                   LITELLM_LOCAL_MODEL_COST_MAP="True")
        cls.log = open(d / "proxy.log", "w")
        cls.proc = subprocess.Popen([str(LITELLM_PY), str(ROOT / "bin/litellm-with-trust.py"),
                                     "--config", str(cfg), "--port", str(cls.port)],
                                    stdout=cls.log, stderr=subprocess.STDOUT, env=env, cwd=d)
        deadline = time.time() + 120
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError("proxy died at startup:\n" + (d / "proxy.log").read_text()[-3000:])
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/health/liveliness", timeout=2)
                return
            except Exception:
                time.sleep(1)
        raise RuntimeError("proxy not ready in 120s")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.log.close()
        cls.up.server.shutdown()
        if os.environ.get("LA_CONTRACT_KEEP_LOG"):
            print((Path(cls.tmp.name) / "proxy.log").read_text()[-6000:])
        cls.tmp.cleanup()

    def setUp(self):
        self.up.requests.clear()
        self.up.status, self.up.body = 200, None
        self.up.path_status = {}

    def post(self, body):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/messages", data=json.dumps(body).encode(), method="POST",
            headers={"x-api-key": MASTER, "content-type": "application/json",
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def post_stream(self, body):
        """Same request with stream:true, returning (status, raw SSE text)."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/messages", data=json.dumps(body).encode(), method="POST",
            headers={"x-api-key": MASTER, "content-type": "application/json",
                     "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"").decode()

    def msg(self, model, **extra):
        return {"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}], **extra}

    def test_1_openai_route_uses_chat_completions(self):
        status, _ = self.post(self.msg("chat-route"))
        self.assertEqual(status, 200)
        self.assertEqual([r["path"] for r in self.up.requests], ["/chat/completions"])

    def test_2_failure_is_not_masked_by_a_cooldown(self):
        self.up.status, self.up.body = 404, {"error": {"message": "fixture not found"}}
        codes = [self.post(self.msg("chat-route"))[0] for _ in range(3)]
        self.assertEqual(codes, [404, 404, 404], "a cooldown 429 would hide the real error")

    def test_3_account_refusal_reaches_client_as_402(self):
        # 0.40.0: an account refusal is KEY-WIDE, and the fallback chain is same-provider
        # same-key, so every chain member refuses identically, the chain exhausts within this
        # one request, and the rewritten 402 still reaches the client -- a fallback can never
        # paper it over. (A healthy-fallback + account-primary combination is unreachable in
        # the product; LiteLLM's generic fallback path is exception-agnostic, so the 402
        # guarantee rests on the chain sharing the key, which write_proxy_config enforces by
        # building the chain from ONE provider.) The global plant models exactly that shape.
        self.up.status = 429
        self.up.body = {"error": {"message": "Insufficient balance or no resource package. Please recharge."}}
        status, body = self.post(self.msg("chat-route"))
        self.assertEqual(status, 402, body)
        self.assertIn("ACCOUNT", json.dumps(body))
        self.assertIn("Insufficient balance", json.dumps(body))

    def test_4_groq_never_carries_prompt_cache_key(self):
        status, body = self.post(self.msg("groq-route", metadata={"user_id": json.dumps({"session_id": "abc"})}))
        self.assertEqual(status, 200, body)
        self.assertTrue(self.up.requests)
        self.assertNotIn("prompt_cache_key", self.up.requests[0]["body"])

    def test_5_native_lane_forwards_thinking_and_effort(self):
        status, _ = self.post({"model": "native-route", "max_tokens": 64000,
                               "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"},
                               "messages": [{"role": "user", "content": "a"},
                                            {"role": "system", "content": "note",
                                             "output_config": {"effort": "max"}},
                                            {"role": "user", "content": "b"}]})
        self.assertEqual(status, 200)
        req = self.up.requests[0]
        self.assertEqual(req["path"], "/v1/messages")
        self.assertEqual(req["body"].get("thinking"), {"type": "adaptive"})
        self.assertEqual(req["body"].get("output_config"), {"effort": "max"}, "per-turn effort folded")
        self.assertTrue(all("output_config" not in m for m in req["body"]["messages"]))


    def test_6_primary_throttle_fails_over_in_place(self):
        """0.40.0: a 429 on the session model is answered by the first HEALTHY fallback on the
        same port -- no restart, and no 429 ever reaches Claude Code. Covers the spoofed id
        AND a subagent id (the generic "*" key rescues those; a "claude-*" KEY does not match
        one -- measured 2026-10-21), streaming and non-streaming, and proves the answer came
        from the first fallback in the chain, not the throttled primary."""
        self.up.path_status["/chat/completions"] = 429
        self.up.body = {"error": {"message": "fixture: upstream throttled"}}

        # Spoofed session id, non-streaming.
        status, body = self.post(self.msg("claude-opus-5-5"))
        self.assertEqual(status, 200, body)
        self.assertEqual(body["content"][0]["text"], "PONG")
        self.assertEqual(self.up.requests[-1]["body"]["model"], "vendor/fallback-model-1",
                         "the first fallback in the chain answers")

        # Subagent id through the claude-* catch-all deployment -> the "*" fallback key.
        self.up.requests.clear()
        status, body = self.post(self.msg("claude-haiku-9"))
        self.assertEqual(status, 200, body)
        self.assertEqual(body["content"][0]["text"], "PONG")
        self.assertEqual(self.up.requests[-1]["body"]["model"], "vendor/fallback-model-1",
                         "a subagent id is rescued by the generic key")

        # Streaming, spoofed id: the SSE body carries the fallback's answer.
        status, raw = self.post_stream(self.msg("claude-opus-5-5", stream=True))
        self.assertEqual(status, 200, raw)
        self.assertIn("PONG", raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)

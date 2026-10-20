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
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)))
                outer.requests.append({"path": self.path, "body": json.loads(raw or b"{}")})
                if outer.status != 200:
                    out = json.dumps(outer.body).encode()
                elif self.path.endswith("/v1/messages"):
                    out = json.dumps({"id": "msg_1", "type": "message", "role": "assistant", "model": "x",
                                      "content": [{"type": "text", "text": "PONG"}], "stop_reason": "end_turn",
                                      "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
                else:
                    out = json.dumps({"id": "c1", "object": "chat.completion", "created": 1, "model": "x",
                                      "service_tier": "on_demand", "choices": [{"index": 0, "finish_reason": "stop",
                                                   "message": {"role": "assistant", "content": "PONG"}}],
                                      "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
                self.send_response(outer.status)
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
                    "  disable_cooldowns: true\n"
                    f"general_settings:\n  master_key: {MASTER}\n")
        dep = lambda name, model, extra="": (  # noqa: E731
            f"  - model_name: {name}\n    litellm_params:\n      model: {model}\n"
            f"      api_key: sk-fixture\n      api_base: {cls.up.base}\n{extra}")
        cfg = d / "proxy.yaml"
        cfg.write_text("model_list:\n"
                       + dep("chat-route", "openai/vendor/chat-model")
                       + dep("groq-route", "groq/openai/gpt-oss-120b")
                       + dep("native-route", "openai/vendor/native-model",
                             '    model_info:\n      supported_endpoints: ["/v1/messages"]\n')
                       + settings)
        cls.port = free_port()
        env = dict(os.environ, LA_REMOTE_PROVIDER="fixture", LA_PROXY_HOOKS_RATE_LIMIT="0",
                   LA_PASSTHROUGH_MODELS="vendor/native-model", LITELLM_LOCAL_MODEL_COST_MAP="True")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

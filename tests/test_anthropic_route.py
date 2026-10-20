#!/usr/bin/env python3
"""Offline tests for bin/anthropic-route.py: per-model choice between a provider's native
Anthropic /v1/messages endpoint and the chat/completions translation. Never calls a real
provider: the "provider" is a local HTTP server that answers what each test plants.

Usage: python3 tests/test_anthropic_route.py [-v]
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "bin" / "anthropic-route.py"


class FakeProvider:
    """Answers POST /v1/messages with a planted (status, body); records each request."""

    def __init__(self, status=200, body=None):
        self.status, self.requests = status, []
        self.body = body if body is not None else {"type": "message", "content": [{"type": "text", "text": "ok"}]}
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)))
                outer.requests.append({"path": self.path, "headers": dict(self.headers),
                                       "body": json.loads(raw or b"{}")})
                out = json.dumps(outer.body).encode()
                self.send_response(outer.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


class AnthropicRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "anthropic-route.json"

    def run_tool(self, *args, key="fixture-key", mode=None):
        env = dict(os.environ, LA_ANTHROPIC_ROUTE_CACHE=str(self.cache), LA_ANTHROPIC_KEY=key)
        env.pop("LA_REMOTE_ANTHROPIC", None)
        if mode is not None:
            env["LA_REMOTE_ANTHROPIC"] = mode
        return subprocess.run([sys.executable, str(TOOL), *args], env=env, text=True,
                              capture_output=True, timeout=60)

    def provider(self, status=200, body=None):
        p = FakeProvider(status, body)
        self.addCleanup(p.close)
        return p

    def test_help_does_not_probe(self):
        r = self.run_tool("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("LA_REMOTE_ANTHROPIC", r.stdout)
        self.assertFalse(self.cache.exists())

    def test_unknown_flag_exits_nonzero(self):
        r = self.run_tool("--bogus")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usage", r.stderr.lower())

    def test_200_chooses_anthropic_and_sends_effort_with_bearer_key(self):
        p = self.provider(200)
        r = self.run_tool("decide", "kilo", "kilo-auto/free", p.base)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "anthropic"), r.stderr)
        req = p.requests[0]
        self.assertEqual(req["path"], "/v1/messages")
        self.assertEqual(req["headers"].get("Authorization"), "Bearer fixture-key")
        self.assertEqual(req["body"]["model"], "kilo-auto/free")
        # The probe checks the thing we route for: native effort must be ACCEPTED, not just a 200.
        self.assertEqual(req["body"]["output_config"], {"effort": "low"})
        self.assertLessEqual(req["body"]["max_tokens"], 32, "the probe must stay tiny")

    def test_verdict_is_cached_per_provider_and_model(self):
        p = self.provider(200)
        self.run_tool("decide", "kilo", "m1", p.base)
        r = self.run_tool("decide", "kilo", "m1", p.base)
        self.assertEqual(r.stdout.strip(), "anthropic")
        self.assertEqual(len(p.requests), 1, "a cached verdict spends no second probe")
        self.run_tool("decide", "kilo", "m2", p.base)
        self.assertEqual(len(p.requests), 2, "another model is probed on its own")
        data = json.loads(self.cache.read_text())
        self.assertEqual(data["kilo|m1"]["route"], "anthropic")

    def test_schema_refusal_chooses_chat_and_is_cached(self):
        # LLM7's real reply for GLM-5.3-Flash on its Anthropic endpoint (2026-10-20).
        p = self.provider(400, {"error": {"message": "Model 'GLM-5.3-Flash' does not support the requested schema.",
                                          "code": "unsupported_model_schema"}})
        r = self.run_tool("decide", "llm7", "GLM-5.3-Flash", p.base)
        self.assertEqual(r.stdout.strip(), "chat")
        self.run_tool("decide", "llm7", "GLM-5.3-Flash", p.base)
        self.assertEqual(len(p.requests), 1)

    def test_account_or_rate_errors_are_undecided_and_not_cached(self):
        # Z.ai's real answer on 2026-10-20: the endpoint exists, the BALANCE is the problem. That
        # says nothing about the route, so it must not be remembered -- a top-up must be able to win.
        for status, body in ((429, {"error": {"message": "Insufficient balance or no resource package."}}),
                             (402, {"error": {"message": "Insufficient balance"}}),
                             (401, {"error": {"message": "invalid key"}}),
                             (503, {"error": {"message": "overloaded"}})):
            with self.subTest(status=status):
                self.cache.unlink(missing_ok=True)
                p = self.provider(status, body)
                r = self.run_tool("decide", "zai", "glm-5.3", p.base)
                self.assertEqual(r.stdout.strip(), "chat")
                self.assertIn("undecided", r.stderr)
                self.run_tool("decide", "zai", "glm-5.3", p.base)
                self.assertEqual(len(p.requests), 2, "an undecided verdict is retried next launch")

    def test_unreachable_endpoint_falls_back_to_chat(self):
        r = self.run_tool("decide", "kilo", "m", "http://127.0.0.1:1")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "chat"))

    def test_stale_verdict_is_reprobed(self):
        p = self.provider(200)
        self.cache.write_text(json.dumps({"kilo|m": {"route": "chat", "at": time.time() - 40 * 86400}}))
        r = self.run_tool("decide", "kilo", "m", p.base)
        self.assertEqual(r.stdout.strip(), "anthropic")
        self.assertEqual(len(p.requests), 1)

    def test_mode_overrides(self):
        p = self.provider(200)
        self.assertEqual(self.run_tool("decide", "kilo", "m", p.base, mode="off").stdout.strip(), "chat")
        bad = self.provider(400, {"error": {"message": "schema"}})
        self.assertEqual(self.run_tool("decide", "kilo", "m", bad.base, mode="on").stdout.strip(), "anthropic")
        self.assertEqual(p.requests + bad.requests, [], "forced modes never probe")

    def test_corrupt_cache_is_ignored_not_fatal(self):
        self.cache.write_text("{not json")
        p = self.provider(200)
        r = self.run_tool("decide", "kilo", "m", p.base)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "anthropic"))

    def test_key_never_reaches_argv_or_cache(self):
        p = self.provider(200)
        self.run_tool("decide", "kilo", "m", p.base, key="sk-secret-fixture-123")
        self.assertNotIn("sk-secret-fixture-123", self.cache.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)

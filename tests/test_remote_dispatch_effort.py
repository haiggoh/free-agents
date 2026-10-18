#!/usr/bin/env python3
"""--reasoning-effort on bin/remote-agent-dispatch.py reaches the request body.

Usage: python3 tests/test_remote_dispatch_effort.py [-v]
Environment: none required; the provider call is replaced, nothing is sent.
"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
_spec = importlib.util.spec_from_file_location("rad", ROOT / "bin/remote-agent-dispatch.py")
rad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rad)


class EffortTests(unittest.TestCase):
    def sent_body(self, *extra):
        seen = {}

        def fake_stream(provider, body, *a, **k):
            seen["body"] = body
            return rad.remote_http.Result(ok=True, provider=provider.id, tier=provider.tier,
                              model_requested=body["model"], model_effective=body["model"])

        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict("os.environ", {"NVIDIA_API_KEY": "k"}), \
                mock.patch.object(rad.remote_http, "stream_chat", side_effect=fake_stream):
            argv = ["--provider", "nvidia", "--model", "z-ai/glm-5.3", "--prompt", "hi",
                    "--max-tokens", "128000", "--outdir", td, *extra]
            with mock.patch.object(sys, "argv", ["remote-agent-dispatch.py", *argv]):
                rad.main()
        return seen["body"]

    def test_effort_is_sent(self):
        body = self.sent_body("--reasoning-effort", "high")
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertEqual(body["max_tokens"], 128000)

    def test_no_flag_sends_no_field(self):
        # Providers that reject the field (Nemotron reads enable_thinking) must not get it by default.
        self.assertNotIn("reasoning_effort", self.sent_body())

    def test_reasoning_only_run_is_a_failure(self):
        # Measured 2026-10-18: GLM spent all 32000 tokens on reasoning, [DONE] arrived, the
        # dispatcher printed a green tick for 0 chars. It must exit non-zero and name the cause.
        def fake_stream(provider, body, outdir, out_fd, rfd=None, **k):
            rfd.write("thinking...")
            return rad.remote_http.Result(
                ok=True, provider=provider.id, tier=provider.tier, model_requested="m",
                model_effective="m", output_chars=0, stream_complete=True,
                usage={"completion_tokens": 32000,
                       "completion_tokens_details": {"reasoning_tokens": 32000}})

        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict("os.environ", {"NVIDIA_API_KEY": "k"}), \
                mock.patch.object(rad.remote_http, "stream_chat", side_effect=fake_stream), \
                mock.patch.object(sys, "argv", ["x", "--provider", "nvidia", "--prompt", "hi",
                                                "--outdir", td]), \
                mock.patch.object(sys, "stderr", new_callable=__import__("io").StringIO) as err:
            rc = rad.main()
            self.assertEqual(Path(td, "reasoning.txt").read_text(), "thinking...")
        self.assertEqual(rc, 2)
        self.assertIn("NO answer text", err.getvalue())
        self.assertIn("reasoning_tokens=32000", err.getvalue())

    def test_max_is_rejected(self):
        # OpenAI's spelling stops at high; the launcher folds xhigh/max to high the same way.
        with mock.patch.object(sys, "argv", ["x", "--provider", "nvidia", "--prompt", "hi",
                                             "--reasoning-effort", "max"]), \
                self.assertRaises(SystemExit) as cm:
            rad.main()
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()

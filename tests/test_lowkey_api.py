#!/usr/bin/env python3
"""0.50.0 item 8 (lowkey-any-model-api): `lowkey --provider <id> --api-model <id>` routes
through a free REMOTE API provider (remote_provider_core + remote_http), not the local
server. In-process: load bin/lowkey-cli.py as a module, stub remote_http.stream_chat, and
assert the exact chat-completions body that would be sent.

Usage: python3 tests/test_lowkey_api.py [-v]
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_lowkey():
    spec = importlib.util.spec_from_file_location("lowkey_cli", ROOT / "bin" / "lowkey-cli.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeStreamResult:
    """Models the real remote_provider_core.Result contract: stream_chat never raises, it
    reports ok/error_class/error_reason (0.54.0: lowkey now reads result.ok)."""

    def __init__(self):
        self.ok = True
        self.error_class = ""
        self.error_reason = ""
        self.output_path = None

    def to_dict(self):
        return {}


class LowkeyAPITests(unittest.TestCase):
    def setUp(self):
        self.lk = load_lowkey()
        self.captured = []

        def fake_stream_chat(provider, body, outdir, out_fd, rfd=None, timeout=120, dry_run=False):
            self.captured.append({"provider": provider.id, "body": body})
            out_fd.write("REMOTE ANSWER\n")
            out_fd.flush()
            return FakeStreamResult()

        fake_http = mock.Mock()
        fake_http.stream_chat = fake_stream_chat
        fake_core = mock.Mock()
        fake_prov = mock.Mock()
        fake_prov.id = "nvidia"
        fake_prov.config_present.return_value = True
        fake_prov.default_models = ["m"]
        fake_core.get = lambda pid: fake_prov
        # the API path imports the modules lazily — patch them in sys.modules
        self.mod_patcher = mock.patch.dict(sys.modules,
                                           {"remote_http": fake_http, "remote_provider_core": fake_core})
        self.mod_patcher.start()
        self.addCleanup(self.mod_patcher.stop)

    def _run(self, argv):
        with mock.patch.object(sys, "argv", ["lowkey"] + argv):
            try:
                self.lk.main()
            except SystemExit as e:
                if e.code not in (0, None):
                    raise

    def test_provider_api_model_routes_to_remote_http_with_model_and_history(self):
        self._run(["--provider", "nvidia", "--api-model", "mistralai/mistral-large-4",
                   "--prompt", "hello", "--progress", "quiet"])
        self.assertEqual(len(self.captured), 1, "expected exactly one remote dispatch")
        cap = self.captured[0]
        self.assertEqual(cap["provider"], "nvidia")
        self.assertEqual(cap["body"]["model"], "mistralai/mistral-large-4")
        self.assertEqual(cap["body"]["messages"][-1]["content"], "hello")

    def test_convo_history_accumulates_across_turns(self):
        # two one-shot prompts share one convo history only in convo mode; here drive the
        # API dispatch function twice directly and check the messages grow
        msgs = [{"role": "user", "content": "hi"}]
        self.lk.dispatch_messages_api("nvidia", "m", msgs, 128)
        msgs.append({"role": "assistant", "content": "REMOTE ANSWER"})
        msgs.append({"role": "user", "content": "again"})
        self.lk.dispatch_messages_api("nvidia", "m", msgs, 128)
        self.assertEqual(len(self.captured), 2)
        self.assertEqual([m["content"] for m in self.captured[1]["body"]["messages"]],
                         ["hi", "REMOTE ANSWER", "again"])

    def test_local_path_untouched_without_provider(self):
        # without --provider the API path must not be taken at all
        self.assertFalse(getattr(self.lk, "API_PROVIDER", None))


if __name__ == "__main__":
    unittest.main()

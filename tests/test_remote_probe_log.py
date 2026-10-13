#!/usr/bin/env python3
"""Offline tests for remote-probe-log.py; never call real providers.

Usage: python3 tests/test_remote_probe_log.py [-v] [test names]
Environment: LA_API_KEYS_DIR is isolated to a fixture; urllib is stubbed so no network
is touched; the machine-wide limiter is faked so the shared 40 RPM bucket is not spent.
"""
import email.message
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "remote_probe_log_under_test", ROOT / "bin" / "remote-probe-log.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROW = {"alias": "fixture-glm", "provider": "nvidia",
       "model": "z-ai/glm-5.3-flash", "tier": "unknown"}


class _Resp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _RecordingUrlopen:
    def __init__(self, payload):
        self.payload = payload
        self.bodies = []

    def __call__(self, req, timeout=None):
        self.bodies.append(json.loads(req.data.decode()))
        return _Resp(self.payload)


class _FakeLimiter:
    def __init__(self):
        self.acquired = 0
        self.noted_429 = []

    def acquire(self):
        self.acquired += 1
        return 0.0

    def note_429(self, retry_after=None):
        self.noted_429.append(retry_after)


class ProbeLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        keys = Path(self.tmp.name) / "keys"
        keys.mkdir()
        (keys / "nvidia").write_text("fixture-not-a-real-key\n")
        self.env = dict(os.environ, LA_API_KEYS_DIR=str(keys))
        # A fake machine-wide limiter wins over the real bin/rate_limiter.py because
        # sys.modules is consulted before the filesystem on import.
        self.limiter = _FakeLimiter()
        # A fake module wins over the real bin/rate_limiter.py because sys.modules is
        # consulted before the filesystem. get_limiter goes in via __dict__: plain
        # attribute assignment trips Pyright on ModuleType, setattr trips bugbear B010.
        fake = types.ModuleType("rate_limiter")
        fake.__dict__["get_limiter"] = lambda: self.limiter
        sys.modules["rate_limiter"] = fake
        self.addCleanup(sys.modules.pop, "rate_limiter", None)
        self.probe = _load_probe()

    def _probe(self, urlopen, env_extra=None):
        env = dict(self.env, **(env_extra or {}))
        with mock.patch.object(urllib.request, "urlopen", urlopen), \
                mock.patch.dict(os.environ, env, clear=False):
            return self.probe.probe_one(dict(ROW))

    @staticmethod
    def _reasoning_payload(content=None, finish="length", reasoning_tokens=24):
        message = {"role": "assistant", "reasoning_content": "x" * 100}
        if content is not None:
            message["content"] = content
        return {"choices": [{"message": message, "finish_reason": finish}],
                "usage": {"completion_tokens": 24,
                          "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}}}

    def test_empty_content_with_reasoning_is_explained_not_just_empty(self):
        # The 2026-10-13 confound, as a regression test: a thinking model that spent its
        # whole budget on reasoning must NOT be recorded as a bare, unexplained "empty".
        rec = self._probe(_RecordingUrlopen(self._reasoning_payload(content=None)))
        self.assertEqual(rec["status"], "empty")
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["finish"], "length")
        self.assertEqual(rec["reasoning_len"], 100)
        self.assertEqual(rec["reasoning_tokens"], 24)
        self.assertIn("elapsed", rec)

    def test_ok_content_records_fields(self):
        rec = self._probe(_RecordingUrlopen(
            self._reasoning_payload(content="OK", finish="stop", reasoning_tokens=3)))
        self.assertEqual(rec["status"], "ok")
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["finish"], "stop")
        self.assertIn("elapsed", rec)

    def test_max_tokens_spans_reasoning_budget(self):
        urlopen = _RecordingUrlopen(self._reasoning_payload(content="OK", finish="stop"))
        self._probe(urlopen)
        self.assertEqual(urlopen.bodies[0]["max_tokens"], 512)

    def test_nvidia_probe_passes_through_machine_limiter(self):
        self._probe(_RecordingUrlopen(
            self._reasoning_payload(content="OK", finish="stop")))
        self.assertEqual(self.limiter.acquired, 1)

    def test_limiter_opt_out_is_respected(self):
        self._probe(_RecordingUrlopen(
            self._reasoning_payload(content="OK", finish="stop")),
            env_extra={"LA_REMOTE_RATE_LIMIT": "0"})
        self.assertEqual(self.limiter.acquired, 0)

    def test_http_429_is_tagged_and_notifies_limiter(self):
        def raise_429(req, timeout=None):
            hdrs = email.message.Message()
            hdrs["Retry-After"] = "12"
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests",
                                         hdrs, None)
        rec = self._probe(raise_429)
        self.assertEqual(rec["status"], "rate_limited")
        self.assertEqual(self.limiter.noted_429, [12.0])
        self.assertIn("elapsed", rec)


if __name__ == "__main__":
    unittest.main()

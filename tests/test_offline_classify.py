#!/usr/bin/env python3
"""0.50.1 (wifi-cutoff plan): connect-level failures classify as "offline" — the network
is down, the model is fine — and must not read as a model/rate-limit error.

Usage: python3 tests/test_offline_classify.py [-v]
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


core = _load("remote_provider_core", "bin/remote_provider_core.py")
registry = _load("remote_provider_registry_mod", "bin/remote_provider_registry.py")


class OfflineClassifyTests(unittest.TestCase):
    def test_both_classify_copies_agree_offline(self):
        # The two modules carry a copy each (core re-exports); both must classify alike.
        cases = [
            (0, "", "offline"),                                   # no HTTP response at all
            (500, "APIConnectionError: Failed to connect", "offline"),
            (500, "Error code: 500 - Connection refused", "offline"),
            (500, "Network is unreachable", "offline"),
            (500, "[Errno -3] Name or service not known", "offline"),
            (500, "[Errno 101] Network is unreachable", "offline"),
        ]
        for status, body, want in cases:
            with self.subTest(status=status, body=body[:40]):
                self.assertEqual(core.classify(status, body=body), want)
                self.assertEqual(registry.classify(status, body=body), want)

    def test_existing_classes_unchanged(self):
        self.assertEqual(core.classify(410, body="end of life on 2026-10-28"), "model_eol")
        self.assertEqual(core.classify(429, body="Too Many Requests"), "quota")
        self.assertEqual(core.classify(500, body="connection reset by peer"), "transient")
        self.assertEqual(core.classify(500, body="request timed out"), "transient")
        self.assertEqual(core.classify(401, body="unauthorized"), "auth")
        self.assertEqual(core.classify(200, body=""), "")

    def test_offline_is_not_retriable(self):
        r = core.Result(ok=False, provider="nvidia", tier="renewing_free",
                        model_requested="m", model_effective="m", status=0,
                        error_class=core.classify(0, body="Failed to connect"))
        self.assertFalse(r.retriable, "offline must not burn the fallback chain")


if __name__ == "__main__":
    unittest.main()

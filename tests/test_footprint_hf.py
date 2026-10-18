#!/usr/bin/env python3
"""bin/estimate-model-footprint.py Hugging Face fallback: the parameter count when the id has none.

Usage: python3 tests/test_footprint_hf.py [-v]
Environment: none required; the HF API is replaced by fixtures shaped like the real responses
(2026-10-18) and the cache lives in a temp dir.
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("efp_hf", ROOT / "bin/estimate-model-footprint.py")
efp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(efp)

# Search results for "laguna-xs-2.1", trimmed from the live response: the base repo plus the
# derivative uploads that share its name (a different INT4 count, a tiny DFlash head).
LAGUNA_SEARCH = [
    {"id": "mudler/Laguna-XS-2.1-APEX-GGUF"},
    {"id": "poolside/Laguna-XS-2.1", "safetensors": {"total": 33442617088}},
    {"id": "poolside/Laguna-XS-2.1-INT4", "safetensors": {"total": 40690374480}},
    {"id": "poolside/Laguna-XS-2.1-DFlash", "safetensors": {"total": 462064896}},
    {"id": "mlx-community/Laguna-XS-2.1-4bit", "safetensors": {"total": 33442617088}},
]


class HFFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "hf-params.json"
        os.environ["LA_HF_PARAMS_CACHE"] = str(self.cache)
        self.addCleanup(os.environ.pop, "LA_HF_PARAMS_CACHE", None)
        self.calls = []
        self.responses = {}
        real = efp._hf_get
        self.addCleanup(setattr, efp, "_hf_get", real)

        def fake(url, timeout):
            self.calls.append(url)
            for needle, resp in self.responses.items():
                if needle in url:
                    return resp
            return None
        efp._hf_get = fake

    def test_laguna_lowercase_id_resolves_via_search(self):
        # The roster spells it lowercase; the repo GET is case-sensitive and fails, search finds it.
        self.responses = {"search=laguna-xs-2.1": LAGUNA_SEARCH}
        r = efp.estimate("poolside/laguna-xs-2.1", use_hf=True)
        self.assertEqual(r["total_b"], 33.4)
        self.assertEqual(r["suggested"], "local-capable")
        self.assertIn("poolside/Laguna-XS-2.1", r["note"])

    def test_exact_repo_wins_without_search(self):
        self.responses = {"/zai-org/GLM-5.3-Flash?": {"id": "zai-org/GLM-5.3-Flash",
                                                      "safetensors": {"total": 321323031390}}}
        r = efp.estimate("zai-org/GLM-5.3-Flash", use_hf=True)
        self.assertEqual(r["suggested"], "remote-preferred")
        self.assertFalse(any("search=" in c for c in self.calls))

    def test_two_different_base_repos_is_unknown(self):
        self.responses = {"search=kat-coder": [
            {"id": "a/kat-coder", "safetensors": {"total": 34_000_000_000}},
            {"id": "b/kat-coder", "safetensors": {"total": 72_000_000_000}}]}
        r = efp.estimate("kat-coder", use_hf=True)
        self.assertEqual(r["suggested"], "unknown")      # ambiguous → stays visible

    def test_derivatives_alone_do_not_match(self):
        self.responses = {"search=x-model": [{"id": "q/x-model-GGUF"}, {"id": "q/x-model-NVFP4",
                                                                       "safetensors": {"total": 9e9}}]}
        self.assertEqual(efp.estimate("x-model", use_hf=True)["suggested"], "unknown")

    def test_cache_hit_skips_network_and_offline_uses_cache_only(self):
        self.responses = {"search=laguna-xs-2.1": LAGUNA_SEARCH}
        efp.estimate("poolside/laguna-xs-2.1", use_hf=True)
        n = len(self.calls)
        self.assertEqual(efp.estimate("poolside/laguna-xs-2.1", use_hf=True)["total_b"], 33.4)
        self.assertEqual(len(self.calls), n)
        self.assertEqual(efp.estimate("other/never-seen", use_hf=True, hf_offline=True)["suggested"],
                         "unknown")
        self.assertEqual(len(self.calls), n)

    def test_cache_expires(self):
        self.cache.write_text(json.dumps({"x/y": {"total": 1e9, "repo": "x/y",
                                                  "at": time.time() - efp.HF_CACHE_TTL_S - 5}}))
        efp.hf_total_params("x/y")
        self.assertTrue(self.calls)

    def test_without_hf_flag_nothing_is_fetched(self):
        efp.estimate("poolside/laguna-xs-2.1")
        self.assertEqual(self.calls, [])

    def test_network_failure_is_unknown_not_an_error(self):
        r = efp.estimate("poolside/laguna-xs-2.1", use_hf=True)   # every fake call returns None
        self.assertEqual(r["suggested"], "unknown")


if __name__ == "__main__":
    unittest.main()


class ApplyAppendTests(unittest.TestCase):
    def test_append_after_file_without_trailing_newline(self):
        # Measured 2026-10-18: the real PSV ends without "\n"; the first appended row was glued
        # onto the last line ("...classificationnvidia|z-ai/glm-5.3|...") and both were lost.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "p.psv"
            p.write_text("# h\nkilo|SELECT|unknown||kilo||note")
            efp.apply_to_policy(str(p), [{"provider": "nvidia", "model_id": "big/model",
                                          "suggested": "remote-preferred", "weights_gb": 300,
                                          "total_gb": 308, "reason": "too big"}])
            lines = p.read_text().splitlines()
        self.assertEqual(lines[1], "kilo|SELECT|unknown||kilo||note")
        self.assertTrue(lines[2].startswith("nvidia|big/model|remote-preferred|"))

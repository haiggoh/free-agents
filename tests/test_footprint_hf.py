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


class CreatedAtTests(unittest.TestCase):
    """The createdAt date rides the same GET and cache entry as the count (T6 age filter).

    The picker's old-model filter reads hf_created() offline at render time, so the cache
    entry must carry the date, a pre-created entry is refetched exactly once to gain it,
    and a failed refetch must not erase the count the local-capable filter depends on."""

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

    def entry(self, key):
        return json.loads(self.cache.read_text())[key]

    def test_exact_repo_response_caches_created_at(self):
        self.responses = {"/zai-org/GLM-5.3-Flash?": {"id": "zai-org/GLM-5.3-Flash",
                                                      "createdAt": "2025-08-04T22:33:06.000Z",
                                                      "safetensors": {"total": 321323031390}}}
        efp.hf_total_params("zai-org/GLM-5.3-Flash")
        e = self.entry("zai-org/glm-5.3-flash")
        self.assertEqual(e["created"], "2025-08-04T22:33:06.000Z")
        self.assertEqual(e["total"], 321323031390)

    def test_search_branch_also_asks_for_created_at(self):
        self.responses = {"search=laguna-xs-2.1": LAGUNA_SEARCH}
        efp.hf_total_params("poolside/laguna-xs-2.1")
        self.assertTrue(any("expand[]=createdAt" in c for c in self.calls), self.calls)
        self.assertEqual(self.entry("poolside/laguna-xs-2.1")["created"], None)  # fixture has no date

    def test_hf_created_reads_cache_only(self):
        self.responses = {"/x/y?": {"id": "x/y", "createdAt": "2024-01-02T03:04:05.000Z",
                                    "safetensors": {"total": 7e9}}}
        efp.hf_total_params("x/y")
        n = len(self.calls)
        self.assertEqual(efp.hf_created("x/y"), "2024-01-02T03:04:05.000Z")
        self.assertEqual(len(self.calls), n)          # render-time read: never a network wait
        self.assertIsNone(efp.hf_created("never/warmed"))

    def test_entry_without_created_is_refetched_once_and_failure_keeps_total(self):
        # A cache written before the created field existed gains the date on the next
        # warm-up; a failed refetch keeps the count and remembers the miss (no storm).
        self.cache.write_text(json.dumps({"a/b": {"total": 12e9, "repo": "A/B", "at": time.time()}}))
        efp.hf_total_params("a/b")                    # every fake call fails
        e = self.entry("a/b")
        self.assertEqual(e["total"], 12e9)
        self.assertIsNone(e["created"])
        self.assertEqual(e["repo"], "A/B")
        n = len(self.calls)
        efp.hf_total_params("a/b")                    # fresh entry WITH the created key: no refetch
        self.assertEqual(len(self.calls), n)

    def test_successful_refetch_gains_created_at(self):
        self.responses = {"/a/b?": {"id": "a/b", "createdAt": "2023-05-06T00:00:00.000Z",
                                    "safetensors": {"total": 5e9}}}
        self.cache.write_text(json.dumps({"a/b": {"total": 5e9, "repo": "a/b", "at": time.time()}}))
        efp.hf_total_params("a/b")
        e = self.entry("a/b")
        self.assertEqual(e["created"], "2023-05-06T00:00:00.000Z")
        self.assertEqual(e["total"], 5e9)

    def test_offline_read_of_pre_created_entry_keeps_the_count(self):
        # The local-capable filter reads offline: an entry without a date must still
        # answer with its cached total until the warm-up fills the date in.
        self.cache.write_text(json.dumps({"a/b": {"total": 12e9, "repo": "A/B", "at": time.time()}}))
        self.assertEqual(efp.hf_total_params("a/b", offline=True), (12e9, "A/B"))


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

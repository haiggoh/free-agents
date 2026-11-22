"""HF context window, cached beside the parameter count (hotfix 2026-11-22, user request).

The context comes from the resolved repo's config.json (e.g. zai-org/GLM-5.3-Flash ->
text_config.max_position_embeddings = 1048576), is cached per model id in the SAME HF cache entry
as the parameter count, and is read offline at render time. Provider-independent: any provider's id
that resolves to the same HF repo gets the same context. Deliberately NOT load_sensitive (no
Textual, no network): the main gate must run it.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
_spec = importlib.util.spec_from_file_location("efp_ctx", ROOT / "bin" / "estimate-model-footprint.py")
assert _spec is not None and _spec.loader is not None
fp = importlib.util.module_from_spec(_spec)
sys.modules["efp_ctx"] = fp
_spec.loader.exec_module(fp)
_FP: "object" = fp  # attribute assignment target for the _hf_get stub (dynamic module)

GLM_CONFIG = {"text_config": {"max_position_embeddings": 1048576}, "model_type": "glm"}


class HFContextCacheTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("LA_HF_PARAMS_CACHE")
        self.tmp = Path(tempfile.mkdtemp(prefix="fa-hf-ctx-"))
        os.environ["LA_HF_PARAMS_CACHE"] = str(self.tmp / "hf-params.json")
        self.addCleanup(self._restore)
        self.calls = []
        self._orig_get = fp._hf_get
        self.addCleanup(setattr, fp, "_hf_get", self._orig_get)

    def _restore(self):
        if self._old is None:
            os.environ.pop("LA_HF_PARAMS_CACHE", None)
        else:
            os.environ["LA_HF_PARAMS_CACHE"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cache(self):
        p = Path(os.environ["LA_HF_PARAMS_CACHE"])
        return json.loads(p.read_text()) if p.exists() else {}

    def _stub(self, api=None, search=None, config=None):
        """URL-dispatching _hf_get stub; every call is recorded in self.calls."""
        def get(url, timeout):  # noqa: ARG001 — signature of the real _hf_get
            del timeout
            self.calls.append(url)
            if url.endswith("/resolve/main/config.json"):
                return config if config is not None else fp.HFError("http", 404)
            if "?search=" in url:
                return search if search is not None else []
            return api if api is not None else fp.HFError("http", 404)
        setattr(_FP, "_hf_get", get)

    def test_success_caches_context_beside_the_count(self):
        self._stub(api={"safetensors": {"total": 123}, "id": "org/m", "createdAt": "2026-01-01"},
                   config=GLM_CONFIG)
        total, repo = fp.hf_total_params("org/m")
        self.assertEqual((total, repo), (123, "org/m"))
        entry = self._cache()["org/m"]
        self.assertEqual(entry["ctx"], 1048576)
        self.assertTrue(entry["ctx_done"])
        self.assertEqual(entry["total"], 123)
        self.assertTrue(any(u.endswith("/org/m/resolve/main/config.json") for u in self.calls),
                        f"config.json of the RESOLVED repo must be fetched: {self.calls}")
        self.assertEqual(fp.hf_context("org/m"), 1048576)

    def test_gated_config_records_null_and_keeps_the_count(self):
        self._stub(api={"safetensors": {"total": 8_000_000_000}, "id": "meta/llama", "createdAt": "2025"},
                   config=fp.HFError("http", 401))
        fp.hf_total_params("meta/llama")
        entry = self._cache()["meta/llama"]
        self.assertEqual(entry["total"], 8_000_000_000)
        self.assertIsNone(entry["ctx"])
        self.assertTrue(entry["ctx_done"], "a 401 is an answer: do not refetch every warm-up")
        self.assertIsNone(fp.hf_context("meta/llama"))

    def test_network_error_on_config_writes_no_ctx_and_retries_later(self):
        self._stub(api={"safetensors": {"total": 5}, "id": "org/n", "createdAt": "2026"},
                   config=fp.HFError("network"))
        fp.hf_total_params("org/n")
        entry = self._cache()["org/n"]
        self.assertEqual(entry["total"], 5, "the count still caches")
        self.assertNotIn("ctx_done", entry, "a network failure is never an answer")
        # next warm-up: only config.json is fetched (the count is fresh), and it lands
        self.calls.clear()
        self._stub(api=fp.HFError("network"), config={"max_position_embeddings": 32768})
        fp.hf_total_params("org/n")
        self.assertEqual(self._cache()["org/n"]["ctx"], 32768)
        self.assertTrue(all(u.endswith("/resolve/main/config.json") for u in self.calls),
                        f"a fresh count must not be refetched, only the missing ctx: {self.calls}")

    def test_existing_fresh_entry_gains_ctx_once(self):
        Path(os.environ["LA_HF_PARAMS_CACHE"]).write_text(json.dumps({
            "org/old": {"v": 2, "total": 7, "repo": "org/old", "created": "2025-01-01", "at": time.time()}}))
        self._stub(config={"n_positions": 2048})
        self.assertEqual(fp.hf_total_params("org/old"), (7, "org/old"))
        self.assertEqual(self._cache()["org/old"]["ctx"], 2048)
        self.calls.clear()
        fp.hf_total_params("org/old")
        self.assertEqual(self.calls, [], "once ctx_done is set, a fresh entry answers with no network")

    def test_offline_never_fetches(self):
        self._stub(api={"safetensors": {"total": 1}, "id": "o/x"}, config=GLM_CONFIG)
        self.assertEqual(fp.hf_total_params("o/x", offline=True), (None, None))
        self.assertIsNone(fp.hf_context("o/x"))
        self.assertEqual(self.calls, [], "render-time reads never touch the network")

    def test_cross_provider_id_resolves_to_the_same_repo(self):
        # NVIDIA's id z-ai/glm-5.3-flash is not an HF repo; the name search finds zai-org/GLM-5.3-Flash
        self._stub(api=fp.HFError("http", 404),
                   search=[{"id": "zai-org/GLM-5.3-Flash", "safetensors": {"total": 31_000_000_000},
                            "createdAt": "2026-09-01"}],
                   config=GLM_CONFIG)
        fp.hf_total_params("z-ai/glm-5.3-flash")
        self.assertEqual(fp.hf_context("z-ai/glm-5.3-flash"), 1048576)
        self.assertTrue(any("/zai-org/GLM-5.3-Flash/resolve/main/config.json" in u for u in self.calls),
                        f"config.json must come from the RESOLVED repo, not the provider id: {self.calls}")

    def test_unknown_model_has_no_context(self):
        self.assertIsNone(fp.hf_context("never/seen"))

    def test_expired_entry_reads_as_unknown(self):
        Path(os.environ["LA_HF_PARAMS_CACHE"]).write_text(json.dumps({
            "org/e": {"v": 2, "total": 1, "repo": "org/e", "ctx": 4096, "ctx_done": True,
                      "at": time.time() - fp.HF_CACHE_TTL_S - 10}}))
        self.assertIsNone(fp.hf_context("org/e"))


if __name__ == "__main__":
    unittest.main()

"""Remote-lane HF resolution + context from card/router/config (fix/runtime-model-facts, 2026-11-23).

Measured defects:
  * remote id nvidia/nemotron-3-ultra-550b-a55b resolved to mlx-community/Nemotron-3-Ultra-550B-A55B
    (the only EXACT-tail match; NVIDIA's own repos carry an NVIDIA- prefix and -BF16/-NVFP4
    suffixes). mlx-community builds are right for the LOCAL lane (Mac) and the local-capable
    filter, wrong for a remote API model (user, 2026-11-23).
  * context came from config.json max_position_embeddings alone: 262144 for Nemotron 3 Ultra,
    whose card states "Context Length | Up to 1M tokens" (NVIDIA's own configs say 262144 too).
  * mistralai/mistral-large-4-1t-a52b has no resolvable safetensors repo, yet the HF router
    catalog serves it at 1048576.
Fixtures are REAL excerpts (tests/fixtures/hf_context/). No network: _hf_get/_hf_get_text stubbed.
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
FIX = ROOT / "tests" / "fixtures" / "hf_context"
sys.path.insert(0, str(ROOT / "bin"))
_spec = importlib.util.spec_from_file_location("efp_lane", ROOT / "bin" / "estimate-model-footprint.py")
assert _spec is not None and _spec.loader is not None
fp = importlib.util.module_from_spec(_spec)
sys.modules["efp_lane"] = fp
_spec.loader.exec_module(fp)
_FP: "object" = fp

NEMO = "nvidia/nemotron-3-ultra-550b-a55b"
CARD = (FIX / "nemotron-ultra-card-excerpt.md").read_text()
ROUTER = json.loads((FIX / "router-excerpt.json").read_text())
SEARCH_NEMO = [
    {"id": "mlx-community/Nemotron-3-Ultra-550B-A55B", "safetensors": {"total": 549308993536}, "createdAt": "2026-10-01"},
    {"id": "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16", "safetensors": {"total": 550000000000}, "createdAt": "2026-09-20"},
    {"id": "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-NVFP4", "safetensors": {"total": 310000000000}, "createdAt": "2026-09-20"},
    {"id": "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-Base-BF16", "safetensors": {"total": 550000000001}, "createdAt": "2026-09-20"},
]


class LaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fa-lane-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._old = os.environ.get("LA_HF_PARAMS_CACHE")
        os.environ["LA_HF_PARAMS_CACHE"] = str(self.tmp / "hf.json")
        self.addCleanup(self._restore)
        self.calls = []
        for name in ("_hf_get", "_hf_get_text"):
            self.addCleanup(setattr, fp, name, getattr(fp, name))
        self.config = {"max_position_embeddings": 262144}
        self.search = SEARCH_NEMO
        self.card = CARD

        def get(url, timeout):
            del timeout
            self.calls.append(url)
            if url.startswith(fp._hf_router_url()):
                return ROUTER
            if url.endswith("/resolve/main/config.json"):
                return self.config
            if "?search=" in url:
                return self.search
            return fp.HFError("http", 401)                  # the exact remote id is no HF repo

        def get_text(url, timeout):
            del timeout
            self.calls.append(url)
            return self.card if url.endswith("/raw/main/README.md") else fp.HFError("http", 404)
        setattr(_FP, "_hf_get", get)
        setattr(_FP, "_hf_get_text", get_text)

    def _restore(self):
        if self._old is None:
            os.environ.pop("LA_HF_PARAMS_CACHE", None)
        else:
            os.environ["LA_HF_PARAMS_CACHE"] = self._old

    def _cache(self):
        return json.loads((self.tmp / "hf.json").read_text())

    # --- repo resolution by lane ----------------------------------------------------------
    def test_remote_lane_prefers_the_upstream_full_precision_repo(self):
        total, repo = fp.hf_total_params(NEMO, lane="remote")
        self.assertEqual(repo, "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16")
        self.assertEqual(total, 550000000000)

    def test_own_org_beats_a_suffixless_re_upload(self):
        # live HF 2026-11-23: unsloth/NVIDIA-Nemotron-3-Ultra-550B-A55B (no suffix) won over
        # nvidia/...-BF16 when the precision tier ran before the owner preference
        self.search = SEARCH_NEMO + [{"id": "unsloth/NVIDIA-Nemotron-3-Ultra-550B-A55B",
                                      "safetensors": {"total": 560524578816}, "createdAt": "2026-10-02"}]
        _, repo = fp.hf_total_params(NEMO, lane="remote")
        self.assertEqual(repo, "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16")

    def test_any_lane_keeps_todays_mlx_resolution(self):
        # local lane / local-capable filter: unchanged behaviour (exact-tail match)
        total, repo = fp.hf_total_params(NEMO)
        self.assertEqual(repo, "mlx-community/Nemotron-3-Ultra-550B-A55B")

    def test_base_variant_is_a_different_model(self):
        self.search = [m for m in SEARCH_NEMO if "Base" in m["id"]]
        total, repo = fp.hf_total_params(NEMO, lane="remote")
        self.assertIsNone(total, "a -Base checkpoint must not stand in for the instruct model")

    def test_remote_lane_re_resolves_an_mlx_entry_once(self):
        (self.tmp / "hf.json").write_text(json.dumps({NEMO: {
            "v": 2, "total": 549308993536, "repo": "mlx-community/Nemotron-3-Ultra-550B-A55B",
            "created": "2026-10-01", "at": time.time(), "ctx": 262144, "ctx_done": True}}))
        _, repo = fp.hf_total_params(NEMO, lane="remote")
        self.assertEqual(repo, "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16")
        self.calls.clear()
        fp.hf_total_params(NEMO, lane="remote")
        self.assertEqual(self.calls, [], "after one remote re-resolution the entry answers offline")

    def test_lane_is_validated(self):
        with self.assertRaises(ValueError):
            fp.hf_total_params(NEMO, lane="cloud")

    # --- context: max of card, router, config ---------------------------------------------
    def test_nemotron_context_is_the_cards_1m_not_config_262k(self):
        fp.hf_total_params(NEMO, lane="remote")
        self.assertEqual(fp.hf_context(NEMO), 1_000_000)
        self.assertEqual(self._cache()[NEMO]["ctx_v"], 2)

    def test_context_from_card_phrasings(self):
        self.assertEqual(fp.context_from_card(CARD), 1_000_000)
        self.assertEqual(fp.context_from_card("- **Large Context Window**: supports a 1M context window."), 1_000_000)
        self.assertEqual(fp.context_from_card("- **1M context length**, for long-context workloads."), 1_000_000)
        self.assertEqual(fp.context_from_card("trained at 4K context, extended to a 128K context window"), 128_000)
        self.assertIsNone(fp.context_from_card("max_new_tokens=64k, temperature 1.0"))
        self.assertIsNone(fp.context_from_card(""))

    def test_router_answers_without_any_resolvable_repo(self):
        self.search = []                                     # mistral-large-4: no safetensors repo
        fp.hf_total_params("mistralai/mistral-large-4-1t-a52b", lane="remote")
        self.assertEqual(fp.hf_context("mistralai/mistral-large-4-1t-a52b"), 1048576)

    def test_router_matches_across_owner_spellings(self):
        # NVIDIA's id z-ai/... vs the router's zai-org/...
        self.assertEqual(fp.hf_router_context("z-ai/glm-5.3-flash"), 1048576)
        self.assertEqual(fp.hf_router_context(NEMO), 262144)
        self.assertIsNone(fp.hf_router_context("nobody/never-served"))

    def test_old_context_entries_refetch_once(self):
        (self.tmp / "hf.json").write_text(json.dumps({"z-ai/glm-5.3-flash": {
            "v": 2, "total": 321323031390, "repo": "zai-org/GLM-5.3-Flash", "created": "2026-08-26",
            "at": time.time(), "ctx": 1048576, "ctx_done": True, "lane": "remote"}}))
        self.config = {"text_config": {"max_position_embeddings": 1048576}}
        fp.hf_total_params("z-ai/glm-5.3-flash", lane="remote")
        self.assertEqual(self._cache()["z-ai/glm-5.3-flash"].get("ctx_v"), 2)


if __name__ == "__main__":
    unittest.main()

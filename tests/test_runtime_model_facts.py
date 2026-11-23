"""Runtime model facts at the ONE choke point Limits.model() (fix/runtime-model-facts, 2026-11-23).

User rule (2026-11-22): model facts are fetched by the code at runtime, never by a manual snapshot,
so a model introduced later works unattended. Measured defect on fix/limiter-provider-buckets @
6c9b61e: the committed snapshot had context None for nvidia:mistralai/mistral-large-4-1t-a52b,
nemotron-3-ultra and step-5-preview, so the session ladder skipped every NVIDIA workhorse but GLM.

Context precedence (CONTEXT_PRECEDENCE): hand-pinned entry > the provider's own catalog value >
runtime HF cache (hf_context, warmed by code) > roster window (autocompact_default) > unknown.
Pure tests: temp HF cache, temp registry, temp limits file; the network is stubbed.
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
import provider_limits as pl  # noqa: E402

_spec = importlib.util.spec_from_file_location("efp_rt", ROOT / "bin" / "estimate-model-footprint.py")
assert _spec is not None and _spec.loader is not None
fp = importlib.util.module_from_spec(_spec)
sys.modules["efp_rt"] = fp
_spec.loader.exec_module(fp)

MISTRAL = "mistralai/mistral-large-4-1t-a52b"
NEMOTRON = "nvidia/nemotron-3-ultra-550b-a55b"
NEWMODEL = "acme/brand-new-model-2027"


class RuntimeFactsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fa-rtfacts-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._env = {k: os.environ.get(k) for k in
                     ("LA_HF_PARAMS_CACHE", "LA_REMOTE_REGISTRY_JSON", "FA_PROVIDER_LIMITS")}
        self.addCleanup(self._restore)
        os.environ["LA_HF_PARAMS_CACHE"] = str(self.tmp / "hf.json")
        self.registry = {"providers": {}, "entries": [
            {"alias": "nvidia-mistral-large4", "provider": "nvidia", "model_id": MISTRAL, "autocompact_default": "1m"},
            {"alias": "nvidia-nemotron-ultra", "provider": "nvidia", "model_id": NEMOTRON, "autocompact_default": "1m"},
            {"alias": "nvidia-glm53", "provider": "nvidia", "model_id": "z-ai/glm-5.3-flash", "autocompact_default": "-"}]}
        (self.tmp / "registry.json").write_text(json.dumps(self.registry))
        os.environ["LA_REMOTE_REGISTRY_JSON"] = str(self.tmp / "registry.json")
        self.hf = {}
        self._write_hf()
        pl._reset_runtime_caches()

    def _restore(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        pl._reset_runtime_caches()

    def _write_hf(self):
        (self.tmp / "hf.json").write_text(json.dumps(self.hf))

    def _hf_ctx(self, mid, ctx):
        self.hf[mid.lower()] = {"v": 2, "total": 1, "repo": mid, "ctx": ctx, "ctx_done": True, "at": time.time()}
        self._write_hf()

    def _limits(self, models):
        doc = {"schema_version": 1, "session_min_context": 262145, "large_context_threshold": 262144,
               "providers": {}, "models": models}
        return pl._parse(doc, "test")

    def _fact(self, ctx, source, reason="r"):
        return {"context": ctx, "max_output": None, "released": None, "broken": None,
                "source": source, "reason": reason}

    def test_snapshot_null_context_is_filled_from_the_runtime_hf_cache(self):
        self._hf_ctx(MISTRAL, 1048576)
        lim = self._limits({f"nvidia:{MISTRAL}": self._fact(None, "catalog https://x 2026-11-22")})
        f = lim.model("nvidia", MISTRAL)
        self.assertEqual(f.context, 1048576)
        self.assertTrue(f.source.startswith("runtime hf"), f.source)

    def test_roster_window_answers_when_hf_cannot(self):
        # the measured case: HF has no repo for mistral-large-4 on NVIDIA; the roster pins 1m
        lim = self._limits({f"nvidia:{MISTRAL}": self._fact(None, "catalog https://x 2026-11-22")})
        f = lim.model("nvidia", MISTRAL)
        self.assertEqual(f.context, 1_000_000)
        self.assertTrue(f.source.startswith("roster"), f.source)

    def test_hand_pinned_value_wins_over_runtime(self):
        self._hf_ctx(MISTRAL, 1048576)
        lim = self._limits({f"nvidia:{MISTRAL}": self._fact(500_000, "docs/measurements/x.md")})
        self.assertEqual(lim.model("nvidia", MISTRAL).context, 500_000)

    def test_provider_catalog_value_wins_over_hf(self):
        # the provider may serve a smaller window than the model's HF maximum
        self._hf_ctx("acme/m", 1048576)
        lim = self._limits({"openrouter:acme/m": self._fact(131072, "catalog https://openrouter.ai 2026-11-22")})
        self.assertEqual(lim.model("openrouter", "acme/m").context, 131072)

    def test_hf_wins_over_roster_window(self):
        # Nemotron 3 Ultra: HF config.json says 262144, the roster's autocompact proxy says 1m
        self._hf_ctx(NEMOTRON, 262144)
        lim = self._limits({})
        self.assertEqual(lim.model("nvidia", NEMOTRON).context, 262144)

    def test_a_model_absent_from_the_snapshot_still_gets_facts(self):
        # future-proof: a model released after the snapshot was committed
        self._hf_ctx(NEWMODEL, 2097152)
        f = self._limits({}).model("nvidia", NEWMODEL)
        self.assertIsNotNone(f, "a model unknown to the snapshot must still resolve from runtime facts")
        self.assertEqual(f.context, 2097152)

    def test_unknown_everywhere_stays_unknown(self):
        self.assertIsNone(self._limits({}).model("nvidia", "nobody/knows"))
        lim = self._limits({"nvidia:nobody/knows": self._fact(None, "catalog https://x")})
        self.assertIsNone(lim.model("nvidia", "nobody/knows").context)

    def test_offline_default_never_touches_the_network(self):
        calls = []
        orig = fp._hf_get
        self.addCleanup(setattr, fp, "_hf_get", orig)
        setattr(fp, "_hf_get", lambda url, timeout: calls.append(url) or fp.HFError("network"))
        pl._set_footprint_module_for_tests(fp)
        self._limits({}).model("nvidia", NEWMODEL)
        self.assertEqual(calls, [], "Limits.model() default is cache-only: render paths must never wait")

    def test_online_fetches_once_bounded_and_caches(self):
        calls = []
        orig = fp._hf_get
        self.addCleanup(setattr, fp, "_hf_get", orig)

        def get(url, timeout):
            calls.append((url, timeout))
            if url.endswith("/resolve/main/config.json"):
                return {"max_position_embeddings": 524288}
            return {"safetensors": {"total": 7}, "id": NEWMODEL, "createdAt": "2027-01-01"}
        setattr(fp, "_hf_get", get)
        pl._set_footprint_module_for_tests(fp)
        f = self._limits({}).model("nvidia", NEWMODEL, online=True)
        self.assertEqual(f.context, 524288)
        self.assertTrue(calls and all(t <= 5.0 for _, t in calls), f"bounded timeout: {calls}")
        n = len(calls)
        self._limits({}).model("nvidia", NEWMODEL, online=True)
        self.assertEqual(len(calls), n, "a second online lookup answers from the cache")


class LadderRuntimeFactsTests(RuntimeFactsTests):
    """Delivery: the SESSION LADDER itself picks a model only runtime facts make eligible."""

    def _pick(self, lim, order, roster):
        import session_ladder
        return session_ladder.pick(order, need_context=0, limits=lim, headroom=lambda p: 1.0,
                                   predicted_wait=lambda a: 0.0, timeout=lambda a: 600.0,
                                   roster=roster, keyed=lambda p: True)

    def test_ladder_picks_le_chonk_from_the_roster_window(self):
        lim = self._limits({f"nvidia:{MISTRAL}": self._fact(None, "catalog https://x 2026-11-22")})
        alias, reasons = self._pick(lim, ["nvidia-mistral-large4"], {"nvidia-mistral-large4": ("nvidia", MISTRAL)})
        self.assertEqual(alias, "nvidia-mistral-large4", reasons)

    def test_ladder_online_lookup_makes_a_brand_new_model_eligible(self):
        orig = fp._hf_get
        self.addCleanup(setattr, fp, "_hf_get", orig)

        def get(url, timeout):
            del timeout
            if url.endswith("/resolve/main/config.json"):
                return {"max_position_embeddings": 1048576}
            return {"safetensors": {"total": 7}, "id": NEWMODEL, "createdAt": "2027-01-01"}
        setattr(fp, "_hf_get", get)
        pl._set_footprint_module_for_tests(fp)
        alias, reasons = self._pick(self._limits({}), ["acme-new"], {"acme-new": ("nvidia", NEWMODEL)})
        self.assertEqual(alias, "acme-new", f"never-seen model must be judged on fetched facts: {reasons}")

    def test_ladder_still_skips_a_256k_model(self):
        self._hf_ctx(NEMOTRON, 262144)
        alias, reasons = self._pick(self._limits({}), ["nvidia-nemotron-ultra"],
                                    {"nvidia-nemotron-ultra": ("nvidia", NEMOTRON)})
        self.assertIsNone(alias)
        self.assertIn("262144", reasons[0][1])


if __name__ == "__main__":
    unittest.main()

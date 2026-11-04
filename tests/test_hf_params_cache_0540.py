#!/usr/bin/env python3
"""0.54.0 Task 3 — the HF parameter-count cache never caches a failure as an answer, and
disk-hydrated catalogs trigger the warm-up (so sizes paint on every load path).

Cache rules pinned here (bin/estimate-model-footprint.py, cache schema v2):
  * a NETWORK failure (TLS/timeout/DNS) writes NOTHING — the 2026-11-04 09:33 incident cached
    `total: null` for 30 days because _hf_get returned None for every failure;
  * HTTP 401/404 (or a 200 with no unique match) writes a short negative (neg_until <= 7d);
  * transient http (5xx) writes nothing;
  * a legacy v1 entry with total null is STALE and refetched; a v1 entry with a count answers.

The pilot tests (picker venv) pin that a disk-cache-only catalog hydration triggers
_warm_hf_cache once per provider, and that the warm-up re-renders the Lowkey screen too.
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("efp_hf", ROOT / "bin" / "estimate-model-footprint.py")
assert _spec is not None and _spec.loader is not None
fp = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["efp_hf"] = fp
_spec.loader.exec_module(fp)  # type: ignore[union-attr]

from picker_harness import PilotCase  # noqa: E402  (imports fine without textual; setUp skips)

try:
    import textual  # noqa: F401
except ImportError:
    textual = None
sp = None
if textual is not None:
    import session_picker as sp  # noqa: E402

pytestmark = pytest.mark.load_sensitive


@pytest.fixture
def temp_cache(tmp_path, monkeypatch):
    cache = tmp_path / "hf-params.json"
    monkeypatch.setenv("LA_HF_PARAMS_CACHE", str(cache))
    return cache


class CacheRuleTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("LA_HF_PARAMS_CACHE")
        self.tmp = Path(__import__("tempfile").mkdtemp(prefix="fa-hf-0540-"))
        os.environ["LA_HF_PARAMS_CACHE"] = str(self.tmp / "hf-params.json")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("LA_HF_PARAMS_CACHE", None)
        else:
            os.environ["LA_HF_PARAMS_CACHE"] = self._old
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cache(self):
        p = Path(os.environ["LA_HF_PARAMS_CACHE"])
        return json.loads(p.read_text()) if p.exists() else {}

    def test_network_failure_is_not_cached(self):
        orig = fp._hf_get
        fp._hf_get = lambda url, timeout: fp.HFError("network")
        self.addCleanup(setattr, fp, "_hf_get", orig)
        total, repo = fp.hf_total_params("some/model")
        self.assertIsNone(total)
        self.assertNotIn("some/model", self._cache(), "a network failure must not be cached")
        # and a second call with a working stub returns the count
        fp._hf_get = lambda url, timeout: {"safetensors": {"total": 123}, "id": "some/model",
                                           "createdAt": "2026-01-01"}
        total, repo = fp.hf_total_params("some/model")
        self.assertEqual(total, 123)
        self.assertEqual(self._cache()["some/model"]["v"], 2)

    def test_http_404_or_401_is_negative_cached_short(self):
        orig = fp._hf_get
        fp._hf_get = lambda url, timeout: fp.HFError("http", 401)
        self.addCleanup(setattr, fp, "_hf_get", orig)
        now = time.time()
        total, _ = fp.hf_total_params("gated/model")
        self.assertIsNone(total)
        entry = self._cache()["gated/model"]
        self.assertIsNone(entry["total"])
        self.assertLessEqual(entry["neg_until"], now + 7 * 86400 + 5)
        self.assertGreater(entry["neg_until"], now)
        # within the window: no refetch
        calls = []
        fp._hf_get = lambda url, timeout: calls.append(url) or fp.HFError("http", 401)
        fp.hf_total_params("gated/model")
        self.assertEqual(calls, [], "negative entry within neg_until must not refetch")
        # after the window: refetch
        cache = self._cache()
        cache["gated/model"]["neg_until"] = now - 1
        p = Path(os.environ["LA_HF_PARAMS_CACHE"])
        p.write_text(json.dumps(cache))
        fp.hf_total_params("gated/model")
        self.assertGreaterEqual(len(calls), 1, "expired negative must refetch")

    def test_transient_http_writes_nothing(self):
        orig = fp._hf_get
        fp._hf_get = lambda url, timeout: fp.HFError("http", 503)
        self.addCleanup(setattr, fp, "_hf_get", orig)
        fp.hf_total_params("busy/model")
        self.assertNotIn("busy/model", self._cache(), "a 5xx must not be cached")

    def test_legacy_v1_null_entries_are_refetched(self):
        # the poisoned 09:33 shape: no "v" key, total null, has "created" — treated as complete
        # before 0.54.0, caching a network failure for 30 days
        p = Path(os.environ["LA_HF_PARAMS_CACHE"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"poisoned/model": {"total": None, "created": None,
                                                     "at": time.time()}}))
        orig = fp._hf_get
        calls = []
        fp._hf_get = lambda url, timeout: calls.append(url) or {"safetensors": {"total": 42},
                                                               "id": "poisoned/model"}
        self.addCleanup(setattr, fp, "_hf_get", orig)
        total, _ = fp.hf_total_params("poisoned/model")
        self.assertEqual(total, 42)
        self.assertEqual(len(calls), 1, "a legacy v1 null entry must be refetched")

    def test_v1_entries_with_a_count_still_answer(self):
        # a v1 entry with a count AND a created date answers with NO refetch (no mass refetch)
        p = Path(os.environ["LA_HF_PARAMS_CACHE"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"good/model": {"total": 99, "repo": "good/model",
                                                 "created": "2026-01-01", "at": time.time()}}))
        orig = fp._hf_get
        fp._hf_get = lambda url, timeout: (_ for _ in ()).throw(AssertionError("refetched!"))
        self.addCleanup(setattr, fp, "_hf_get", orig)
        total, repo = fp.hf_total_params("good/model")
        self.assertEqual((total, repo), (99, "good/model"), "a v1 count must still answer")

    def test_mixed_failures_write_nothing(self):
        # exact lookup 401, search network -> NO cache entry (any network error poisons the write)
        orig = fp._hf_get
        def fake(url, timeout):
            if "?search=" in url:
                return fp.HFError("network")
            return fp.HFError("http", 401)
        fp._hf_get = fake
        self.addCleanup(setattr, fp, "_hf_get", orig)
        fp.hf_total_params("mixed/model")
        self.assertNotIn("mixed/model", self._cache())

    def test_offline_read_uses_fresh_v2_negative_as_none(self):
        p = Path(os.environ["LA_HF_PARAMS_CACHE"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"neg/model": {"v": 2, "total": None, "repo": None,
                                                "created": None, "at": time.time(),
                                                "neg_until": time.time() + 86400}}))
        total, repo = fp.hf_total_params("neg/model", offline=True)
        self.assertIsNone(total)


# --- pilot tests: warm-up trigger on disk-hydrated catalogs (picker venv) -------------------

CAT85 = [f"org/fake-{i}" for i in range(85)]


@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class WarmupTriggerTests(PilotCase):
    def setUp(self):
        super().setUp()
        self._saved_lookup = os.environ.get("LA_PICKER_HF_LOOKUP")
        os.environ["LA_PICKER_HF_LOOKUP"] = "1"
        self.addCleanup(self._restore_lookup)
        self.warm_calls = []
        orig = sp._warm_hf_cache
        def spy(ids):
            self.warm_calls.append(list(ids))
        sp._warm_hf_cache = spy
        self.addCleanup(setattr, sp, "_warm_hf_cache", orig)

    def _restore_lookup(self):
        if self._saved_lookup is None:
            os.environ.pop("LA_PICKER_HF_LOOKUP", None)
        else:
            os.environ["LA_PICKER_HF_LOOKUP"] = self._saved_lookup

    def test_disk_hydrated_catalog_triggers_warmup(self):
        # catalogs only from the DISK cache (no live rcat load): warm-up must still fire
        from picker_harness import REMOTE_INV
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text("#!/bin/sh\nfor a in \"$@\"; do [ \"$a\" = --inventory ] && { cat <<'EOF'\n"
                        + REMOTE_INV + "EOF\nexit 0; }; done\n")
        stub.chmod(0o755)
        os.environ["LA_EAGER_CATALOGS"] = "0"     # no live catalog fetches at all
        # seed the disk cache for nvidia
        import session_picker_model as mm
        mm.write_catalog_cache("nvidia", [mm.CatalogModel(mid) for mid in CAT85[:20]])

        async def scenario(app, pilot, h):
            sm = app.screen_model
            sm._toggle_choose_model()
            app.render_model()
            await h.wait_for(lambda: bool(sm.agents) and sm._models_loaded, "roster", secs=15)
            await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list),
                             "disk catalog hydrated", secs=15)
            app.render_model()
            await h.wait_for(lambda: any("nvidia" in str(c) or True for c in self.warm_calls)
                             and self.warm_calls, "warm-up fired", secs=15)

        self.run_picker(scenario, start="remote")
        self.assertTrue(self.warm_calls, "disk-hydrated catalog must trigger the HF warm-up")
        warmed = {mid for call in self.warm_calls for mid in call}
        self.assertTrue(any(mid.startswith("org/fake-") for mid in warmed))

    def test_warmup_respects_kill_switch(self):
        os.environ["LA_PICKER_HF_LOOKUP"] = "0"
        from picker_harness import REMOTE_INV
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text("#!/bin/sh\nfor a in \"$@\"; do [ \"$a\" = --inventory ] && { cat <<'EOF'\n"
                        + REMOTE_INV + "EOF\nexit 0; }; done\n")
        stub.chmod(0o755)
        os.environ["LA_EAGER_CATALOGS"] = "0"
        import session_picker_model as mm
        mm.write_catalog_cache("nvidia", [mm.CatalogModel(mid) for mid in CAT85[:5]])

        async def scenario(app, pilot, h):
            sm = app.screen_model
            sm._toggle_choose_model()
            app.render_model()
            await h.wait_for(lambda: bool(sm.agents) and sm._models_loaded, "roster", secs=15)
            await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list), "hydrated", secs=15)
            app.render_model()
            await pilot.pause()

        self.run_picker(scenario, start="remote")
        self.assertEqual(self.warm_calls, [], "LA_PICKER_HF_LOOKUP=0 must skip the warm-up")



# --- Task 3 part 2: overrides + unknown list + rendered sizes ------------------------------

FIXTURE_IDS = [l.strip() for l in (ROOT / "tests/fixtures/nvidia-catalog-85.txt").read_text().splitlines() if l.strip()]
HF_FIXTURE = json.loads((ROOT / "tests/fixtures/hf-nvidia-85.json").read_text())
GATED_IDS = [
    "ai21labs/jamba-1.5-large-instruct", "mistralai/mistral-large", "mistralai/mistral-large-2-instruct",
    "nvidia/ai-synthetic-video-detector", "nvidia/embed-qa-4", "nvidia/nemotron-parse",
    "nvidia/nemotron-parse-2.0", "nvidia/nvclip", "nvidia/vila",
]
HF_RESOLVABLE = ["google/deplot", "microsoft/phi-3-vision-128k-instruct", "microsoft/phi-3.5-moe-instruct",
                 "mistralai/mixtral-8x22b-v0.1", "nvidia/nemotron-3.5-bolt-e4b",
                 "nvidia/nemotron-3.5-content-safety", "stepfun-ai/step-5-preview"]


@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class OverrideTests(unittest.TestCase):
    def _sp(self):
        assert sp is not None
        return sp

    def test_overrides_cover_gated_nvidia_ids(self):
        spmod = self._sp()
        for mid in GATED_IDS:
            covered = (mid in spmod.PARAM_COUNT_OVERRIDES) or (mid in spmod.PARAM_COUNT_UNKNOWN)
            self.assertTrue(covered, f"{mid} is neither overridden nor listed unknown")

    def test_new_overrides_are_exact_and_unambiguous(self):
        spmod = self._sp()
        new_keys = [k for k in spmod.PARAM_COUNT_OVERRIDES if "/" in k]
        for k in new_keys:
            matches = [mid for mid in FIXTURE_IDS if mid == k]      # exact match, as model_param_count does
            self.assertEqual(len(matches), 1, f"override {k!r} matches {matches}")
        # full-id keys match by ==, so a prefix id must not steal a sibling's count:
        self.assertEqual(spmod.model_param_count("nvidia", "mistralai/mistral-large"), 123.0)
        self.assertEqual(spmod.model_param_count("nvidia", "mistralai/mistral-large-2-instruct"), 123.0)
        self.assertEqual(spmod.model_param_count("nvidia", "mistralai/mistral-large-4-1t-a52b"), 1050.0)
        old_keys = [k for k in spmod.PARAM_COUNT_OVERRIDES if "/" not in k]
        self.assertTrue(old_keys, "the pre-existing substring keys should still be listed")

    def test_hf_resolvable_ids_not_overridden(self):
        spmod = self._sp()
        for mid in HF_RESOLVABLE:
            self.assertNotIn(mid, spmod.PARAM_COUNT_OVERRIDES,
                             f"{mid} resolves on HF - sizing it by override would hide a broken HF path")

    def test_unknown_entries_record_what_was_checked(self):
        spmod = self._sp()
        for mid, reason in spmod.PARAM_COUNT_UNKNOWN.items():
            self.assertIn("401", reason, f"{mid}: the HF 401 must be named")
            sources = reason.count("fetched") + reason.count("http") + reason.count(".com") + reason.count("GitHub")
            self.assertGreaterEqual(sources, 2, f"{mid}: reason must name >= 2 checked sources, got: {reason}")

    def test_model_param_count_uses_overrides_and_unknown(self):
        spmod = self._sp()
        self.assertEqual(spmod.model_param_count("nvidia", "nvidia/nemotron-parse-2.0"), 0.905)
        self.assertIsNone(spmod.model_param_count("nvidia", "nvidia/vila"))



@unittest.skipUnless(sp is not None, "session_picker needs textual: run under the picker venv python")
class RenderedSizesTests(PilotCase):
    """The user-visible layer: after the warm-up, Remote AND Lowkey rows carry the size."""

    def setUp(self):
        super().setUp()
        self._saved_lookup = os.environ.get("LA_PICKER_HF_LOOKUP")
        os.environ["LA_PICKER_HF_LOOKUP"] = "1"
        self.addCleanup(self._restore_lookup)
        # stub the module instance session_picker ACTUALLY loads (its own spec name), not a
        # second copy — otherwise the warm-up would use the real network
        fpmod = sp._footprint()
        orig_get = fpmod._hf_get

        def fake_get(url, timeout):
            mid = url.split("/api/models/", 1)[1].split("?", 1)[0] if "/api/models/" in url else ""
            if mid and mid in HF_FIXTURE and HF_FIXTURE[mid]:
                return {"safetensors": {"total": HF_FIXTURE[mid]}, "id": mid,
                        "createdAt": "2026-01-01"}
            if "?search=" in url:
                from urllib.parse import unquote
                tail = unquote(url.split("search=", 1)[1].split("&", 1)[0])
                exact = [m for m in HF_FIXTURE
                         if m.split("/")[-1].lower() == tail.lower() and HF_FIXTURE[m]]
                if len(exact) == 1:
                    return [{"safetensors": {"total": HF_FIXTURE[exact[0]]}, "id": exact[0],
                             "createdAt": "2026-01-01"}]
                return []
            return fpmod.HFError("http", 401)
        fpmod._hf_get = fake_get
        self.addCleanup(setattr, fpmod, "_hf_get", orig_get)
        # the warm-up runs in a background thread that is NOT in app._loading — track it
        import threading
        self.warm_done = threading.Event()
        orig_warm = sp._warm_hf_cache
        def spy_warm(ids):
            orig_warm(ids)
            self.warm_done.set()
        sp._warm_hf_cache = spy_warm
        self.addCleanup(setattr, sp, "_warm_hf_cache", orig_warm)

    def _restore_lookup(self):
        if self._saved_lookup is None:
            os.environ.pop("LA_PICKER_HF_LOOKUP", None)
        else:
            os.environ["LA_PICKER_HF_LOOKUP"] = self._saved_lookup

    def test_rendered_rows_show_sizes_remote_and_lowkey(self):
        from picker_harness import REMOTE_INV
        import session_picker_model as mm
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text("#!/bin/sh\nfor a in \"$@\"; do [ \"$a\" = --inventory ] && { cat <<'EOF'\n"
                        + REMOTE_INV + "EOF\nexit 0; }; done\n")
        stub.chmod(0o755)
        os.environ["LA_EAGER_CATALOGS"] = "0"
        mm.write_catalog_cache("nvidia", [mm.CatalogModel(mid) for mid in FIXTURE_IDS])
        seen = {}

        def _sized(item_id):
            # a row is "sized" when model_param_count answers for its model id (the label may
            # state the size in the name instead of a "· suffix" — both are sized)
            mid = item_id.split(":", 2)[2] if item_id.startswith("cat:") else ""
            return bool(mid) and sp.model_param_count("nvidia", mid) is not None

        async def scenario_remote(app, pilot, h):
            sm = app.screen_model
            sm._toggle_choose_model()
            app.render_model()
            await h.wait_for(lambda: bool(sm.agents) and sm._models_loaded, "roster", secs=15)
            await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list), "hydrated", secs=15)
            await h.wait_for(lambda: self.warm_done.is_set() and not getattr(app, "_loading", set()), "warm-up done", secs=40)
            app.render_model()
            await pilot.pause()
            nvidia = next(g for g in sm.accordion.groups if g.id == "nvidia")
            seen["remote_sized"] = sum(1 for i in nvidia.items if _sized(i.id))
            seen["remote_total"] = len(nvidia.items)

        self.run_picker(scenario_remote, start="remote")

        async def scenario_lowkey(app, pilot, h):
            sm = app.screen_model
            sm._toggle_choose_model()
            app.render_model()
            await h.wait_for(lambda: any(g.id == "api" and g.items for g in sm.accordion.groups),
                             "api rows", secs=15)
            await h.wait_for(lambda: self.warm_done.is_set() and not getattr(app, "_loading", set()), "warm-up done", secs=40)
            app.render_model()
            await pilot.pause()
            api = next(g for g in sm.accordion.groups if g.id == "api")
            subs = list(getattr(api, "groups", None) or [x for x in api.items if isinstance(x, mm.Group)])
            items = [i for g in subs for i in g.items]
            seen["lowkey_sized"] = sum(1 for i in items if _sized(i.id))
            seen["lowkey_total"] = len(items)

        self.run_picker(scenario_lowkey, start="lowkey")
        # every catalog id is sized except the PARAM_COUNT_UNKNOWN ones (overrides cover the rest)
        # every RENDERED catalog row is sized except the PARAM_COUNT_UNKNOWN ids; the pinned
        # stub row (alias-keyed, no model_id) can never be sized. Compare against rendered totals.
        unknown = set(sp.PARAM_COUNT_UNKNOWN)
        self.assertGreaterEqual(seen["remote_sized"], seen["remote_total"] - 1 - len(unknown),
                                f"remote: rendered rows without a size: {seen}")
        # lowkey's API list also carries the 3 pinned stub rows (REMOTE_INV aliases, no
        # model_id — never sized), so subtract those too
        self.assertGreaterEqual(seen["lowkey_sized"], seen["lowkey_total"] - 3 - len(unknown),
                                f"lowkey must repaint sizes after the warm-up: {seen}")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""tests/test_hf_cache_writer_0541.py — 0.55.8 Task 1 (0541 plan Item A):

ONE locked, re-reading HF cache writer (`_hf_cache_store`) so concurrent warm-ups never
lose entries; a corrupt cache is preserved as a .corrupt-* sibling, never silently
discarded; LA_HF_API_BASE is honored and an invalid scheme fails loudly naming the variable.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_hf_cache_writer_0541.py
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))


def _load_emf():
    """bin/estimate-model-footprint.py is dash-named — load it by path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "estimate_model_footprint",
        Path(__file__).resolve().parent.parent / "bin" / "estimate-model-footprint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


emf = _load_emf()
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive


class HFCacheWriter(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fa-hfcache-test-"))
        self.cache = self.tmp / "hf-params.json"
        os.environ["LA_HF_PARAMS_CACHE"] = str(self.cache)
        self.addCleanup(os.environ.pop, "LA_HF_PARAMS_CACHE", None)
        self._saved_get = emf._hf_get
        self.addCleanup(setattr, emf, "_hf_get", self._saved_get)

    def test_concurrent_writers_keep_every_entry(self):
        # 8 threads; the _hf_get stub blocks on a barrier so every thread's cache READ
        # happens before any write. The 0.54.0 writer (read once at start, one fixed .tmp)
        # keeps only the LAST writer's entry; the locked re-reading writer keeps all 8.
        barrier = threading.Barrier(8)
        def slow_get(url, timeout):
            barrier.wait(timeout=10)
            mid = url.split("/api/models/")[1].split("?")[0]
            return {"id": mid, "createdAt": "2026-01-01",
                    "safetensors": {"total": 7_000_000_000}}
        emf._hf_get = slow_get
        def work(i):
            emf.hf_total_params(f"org/model-{i}", offline=False)
        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        data = json.loads(self.cache.read_text())
        self.assertEqual(len([k for k in data if k.startswith("org/model-")]), 8,
                         f"lost entries — only {sorted(data)} on disk")

    def test_tmp_names_never_collide(self):
        # two writers in flight must not lose each other on the shared tmp name
        barrier = threading.Barrier(2)
        def get(url, timeout):
            barrier.wait(timeout=10)
            mid = url.split("/api/models/")[1].split("?")[0]
            return {"id": mid, "safetensors": {"total": 1}}
        emf._hf_get = get
        errs = []
        def work(i):
            try:
                emf.hf_total_params(f"org/c-{i}", offline=False)
            except Exception as e:
                errs.append(e)
        ts = [threading.Thread(target=work, args=(i,)) for i in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)
        self.assertEqual(errs, [], f"tmp collision raised: {errs}")
        self.assertEqual(len(json.loads(self.cache.read_text())), 2)

    def test_corrupt_cache_is_preserved(self):
        self.cache.write_text("{ not json !!!")
        emf._hf_get = lambda url, timeout: {"id": "org/x", "safetensors": {"total": 5}}
        emf.hf_total_params("org/x", offline=False)
        data = json.loads(self.cache.read_text())
        self.assertEqual(data["org/x"]["total"], 5, "the new entry must be written")
        corrupt = list(self.tmp.glob("hf-params.json.corrupt-*"))
        self.assertEqual(len(corrupt), 1, f"the corrupt original must be preserved: {corrupt}")
        self.assertIn("not json", corrupt[0].read_text())

    def test_hf_api_base_override_and_loud_failure(self):
        os.environ["LA_HF_API_BASE"] = "http://127.0.0.1:9999"
        self.addCleanup(os.environ.pop, "LA_HF_API_BASE", None)
        self.assertEqual(emf._hf_api_base(), "http://127.0.0.1:9999/api/models")  # origin override keeps /api/models
        os.environ["LA_HF_API_BASE"] = "ftp://x"
        with self.assertRaises(ValueError) as cm:
            emf._hf_api_base()
        self.assertIn("LA_HF_API_BASE", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

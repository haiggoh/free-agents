#!/usr/bin/env python3
"""tests/test_model_facts_created_sentinel.py — bogus catalog `created` dates (R1; waypoint
model-facts-py-nvidia-catalog).

NVIDIA's catalog stamps EVERY model with the same `created` value (a constant sentinel), which
model-facts.py turned into released "1993-04-26" for GLM 5.3 Flash in config/provider-limits.json.
Rule: a provider `created` identical across ALL its models, or any value before 2015, is
"unknown" (released None).
"""
import datetime
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
spec = importlib.util.spec_from_file_location("model_facts", ROOT / "bin" / "model-facts.py")
mf = importlib.util.module_from_spec(spec)
sys.modules["model_facts"] = mf
spec.loader.exec_module(mf)

EPOCH_1993_04_26 = int(datetime.datetime(1993, 4, 26, tzinfo=datetime.timezone.utc).timestamp())
EPOCH_2024_06_01 = int(datetime.datetime(2024, 6, 1, tzinfo=datetime.timezone.utc).timestamp())
EPOCH_2025_01_15 = int(datetime.datetime(2025, 1, 15, tzinfo=datetime.timezone.utc).timestamp())


def _catalog_doc(created_by_id):
    """The real NVIDIA catalog shape: {"data": [{"id": ..., "created": ...}, ...]}."""
    return {"data": [{"id": mid, "created": c, "context_window": 1048576}
                     for mid, c in created_by_id.items()]}


class _FakeProvider:
    catalog_url = "https://catalog.invalid/v1/models"
    models_path = None

    def base(self):
        return "https://catalog.invalid"

    def auth_headers(self):
        return {}


def _fetch_with_doc(doc, tmpdir):
    out = Path(tmpdir) / "facts.json"
    resp = mock.MagicMock()
    resp.status = 200
    resp.read.return_value = json.dumps(doc).encode()
    cm = mock.MagicMock()
    cm.__enter__.return_value = resp          # `with urlopen(...) as r` binds __enter__()
    with mock.patch.object(mf, "_providers", return_value={"nvidia": _FakeProvider()}), \
         mock.patch("urllib.request.urlopen", return_value=cm), \
         mock.patch("fa_tls.client_context", return_value=mock.MagicMock()):
        return mf.fetch(["nvidia"], out)


class CreatedSentinelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _models(self, doc):
        result = _fetch_with_doc(doc, self.tmp.name)
        return result["providers"]["nvidia"]["models"]

    def test_constant_created_across_all_models_is_unknown(self):
        # the real NVIDIA shape: every model carries the SAME sentinel timestamp
        models = self._models(_catalog_doc({"z-ai/glm-5.3-flash": EPOCH_1993_04_26,
                                            "mistralai/mistral-large-4-1t-a52b": EPOCH_1993_04_26}))
        self.assertIsNone(models["z-ai/glm-5.3-flash"]["released"])
        self.assertIsNone(models["mistralai/mistral-large-4-1t-a52b"]["released"])

    def test_glm53_flash_is_not_dated_1993_04_26(self):
        models = self._models(_catalog_doc({"z-ai/glm-5.3-flash": EPOCH_1993_04_26,
                                            "other/model": EPOCH_1993_04_26}))
        released = models["z-ai/glm-5.3-flash"]["released"]
        self.assertNotEqual(released, "1993-04-26")
        self.assertIsNone(released)

    def test_pre_2015_created_is_unknown_even_when_values_differ(self):
        models = self._models(_catalog_doc({"z-ai/glm-5.3-flash": EPOCH_1993_04_26,
                                            "other/model": EPOCH_2024_06_01}))
        self.assertIsNone(models["z-ai/glm-5.3-flash"]["released"])
        self.assertEqual(models["other/model"]["released"], "2024-06-01")

    def test_distinct_real_dates_are_kept(self):
        models = self._models(_catalog_doc({"a/one": EPOCH_2024_06_01,
                                            "b/two": EPOCH_2025_01_15}))
        self.assertEqual(models["a/one"]["released"], "2024-06-01")
        self.assertEqual(models["b/two"]["released"], "2025-01-15")

    def test_provider_limits_json_has_no_pre_2015_released(self):
        doc = json.loads((ROOT / "config" / "provider-limits.json").read_text(encoding="utf-8"))
        models = doc.get("models", {})
        self.assertTrue(models, "provider-limits.json must carry a models table")
        for key, entry in models.items():
            if isinstance(entry, dict) and entry.get("released"):
                year = int(str(entry["released"])[:4])
                self.assertGreaterEqual(year, 2015, f"{key} released {entry['released']}")

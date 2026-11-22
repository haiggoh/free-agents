#!/usr/bin/env python3
"""Task 4 Step 1 — provider_limits loader: strict validation, loud overrides.

The limits file is the single structured source (spec C1): unknown keys, wrong types and
missing keys are schema ERRORS (never a silent fallback to "no limits"), and an invalid
FA_PROVIDER_LIMITS override aborts NAMING the var. null = no known limit, 0 = forbidden.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
_spec = importlib.util.spec_from_file_location("provider_limits", REPO / "bin" / "provider_limits.py")
pl = importlib.util.module_from_spec(_spec)
sys.modules["provider_limits"] = pl          # dataclasses need the module registered
_spec.loader.exec_module(pl)


def valid_doc():
    return {
        "schema_version": 1,
        "session_min_context": 262145,
        "large_context_threshold": 262144,
        "providers": {
            "nvidia": {"rpm": 40, "tpm": None, "rpd": None, "rpw": None, "tpd": None,
                       "dispatch_only": False, "reserves_on_request": False,
                       "measured_at": "2026-11-20", "source": "docs/measurements/x.md"},
        },
        "models": {
            "nvidia:z-ai/glm-5.3-flash": {"context": 1000000, "max_output": 16384,
                                          "released": None, "broken": False,
                                          "source": "hf config.json z-ai/GLM-5.3-Flash",
                                          "reason": "released: no release date in catalog"},
        },
    }


def write(tmp, doc):
    p = Path(tmp) / "provider-limits.json"
    p.write_text(json.dumps(doc))
    return p


class LoaderValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def load(self, doc):
        return pl.load(write(self.tmp.name, doc))

    def test_valid_document_loads(self):
        limits = self.load(valid_doc())
        self.assertEqual(limits.provider("nvidia").rpm, 40)
        self.assertIsNone(limits.provider("nvidia").tpm)
        m = limits.model("nvidia", "z-ai/glm-5.3-flash")
        self.assertEqual(m.context, 1000000)
        self.assertEqual(m.reason, "released: no release date in catalog")

    def test_unknown_top_level_key_rejected(self):
        doc = valid_doc(); doc["surprise"] = 1
        with self.assertRaises(pl.LimitsError) as cm:
            self.load(doc)
        self.assertIn("surprise", str(cm.exception))

    def test_unknown_provider_key_rejected(self):
        doc = valid_doc()
        doc["providers"]["nvidia"]["rpmm"] = 40
        with self.assertRaises(pl.LimitsError) as cm:
            self.load(doc)
        self.assertIn("rpmm", str(cm.exception))

    def test_wrong_type_rejected(self):
        doc = valid_doc(); doc["providers"]["nvidia"]["rpm"] = "40"
        with self.assertRaises(pl.LimitsError) as cm:
            self.load(doc)
        self.assertIn("rpm", str(cm.exception))

    def test_missing_key_is_schema_error(self):
        doc = valid_doc(); del doc["providers"]["nvidia"]["tpm"]
        with self.assertRaises(pl.LimitsError) as cm:
            self.load(doc)
        self.assertIn("tpm", str(cm.exception))

    def test_model_fact_null_field_needs_reason(self):
        doc = valid_doc()
        doc["models"]["nvidia:z-ai/glm-5.3-flash"]["context"] = None
        doc["models"]["nvidia:z-ai/glm-5.3-flash"]["reason"] = None
        with self.assertRaises(pl.LimitsError) as cm:
            self.load(doc)
        self.assertIn("reason", str(cm.exception))

    def test_thresholds(self):
        limits = self.load(valid_doc())
        self.assertEqual(limits.large_context_threshold, 262144)
        self.assertEqual(limits.session_min_context, 262145)

    def test_unknown_provider_returns_none(self):
        limits = self.load(valid_doc())
        self.assertIsNone(limits.provider("nope"))
        self.assertIsNone(limits.model("nope", "x"))

    def test_fa_provider_limits_missing_path_aborts_naming_var(self):
        with unittest.mock.patch.dict(os.environ, {"FA_PROVIDER_LIMITS": "/nonexistent/x.json"}):
            with self.assertRaises(pl.LimitsError) as cm:
                pl.load()
        self.assertIn("FA_PROVIDER_LIMITS", str(cm.exception))
        self.assertIn("/nonexistent/x.json", str(cm.exception))

    def test_fa_provider_limits_invalid_file_aborts_naming_var(self):
        bad = write(self.tmp.name, valid_doc())
        bad.write_text("{not json")
        with unittest.mock.patch.dict(os.environ, {"FA_PROVIDER_LIMITS": str(bad)}):
            with self.assertRaises(pl.LimitsError) as cm:
                pl.load()
        self.assertIn("FA_PROVIDER_LIMITS", str(cm.exception))

    def test_repo_file_is_valid(self):
        # the shipped config/provider-limits.json must always validate
        limits = pl.load(REPO / "config" / "provider-limits.json")
        self.assertIsNotNone(limits.provider("nvidia"))


if __name__ == "__main__":
    unittest.main()

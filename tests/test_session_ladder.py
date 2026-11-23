#!/usr/bin/env python3
"""Task 9 Step 1 — the session ladder's ordered + spill selection (C7). RED first."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))
_spec = importlib.util.spec_from_file_location("provider_limits", REPO / "bin" / "provider_limits.py")
_pl = importlib.util.module_from_spec(_spec)
sys.modules["provider_limits"] = _pl
_spec.loader.exec_module(_pl)
import session_ladder as sl  # noqa: E402

LIMITS_DOC = {
    "schema_version": 1, "session_min_context": 262145, "large_context_threshold": 262144,
    "providers": {
        "nvidia": {"rpm": 40, "tpm": None, "rpd": None, "rpw": None, "tpd": None,
                   "dispatch_only": False, "reserves_on_request": False,
                   "measured_at": "2026-11-20", "source": "test"},
        "mistral": {"rpm": 2, "tpm": 40000, "rpd": None, "rpw": None, "tpd": None,
                    "dispatch_only": False, "reserves_on_request": False,
                    "measured_at": "2026-11-20", "source": "test"},
        "groq": {"rpm": 1000, "tpm": 8000, "rpd": None, "rpw": None, "tpd": None,
                 "dispatch_only": False, "reserves_on_request": False,
                 "measured_at": "2026-11-20", "source": "test"},
        "gemini": {"rpm": None, "tpm": None, "rpd": None, "rpw": None, "tpd": None,
                   "dispatch_only": True, "reserves_on_request": False,
                   "measured_at": "2026-11-20", "source": "test"},
    },
    "models": {
        "nvidia:z-ai/glm-5.3-flash": {"context": 1000000, "max_output": None,
                                      "released": None, "broken": False,
                                      "source": "hf config.json", "reason": "max_output/released unknown"},
        "mistral:mistral-large-latest": {"context": 1000000, "max_output": None,
                                     "released": None, "broken": False,
                                     "source": "catalog", "reason": "max_output/released unknown"},
        "groq:qwen/qwen3-32b": {"context": 131072, "max_output": None,
                                "released": None, "broken": False,
                                "source": "catalog", "reason": "max_output/released unknown"},
        "gemini:gemini-3.8-flash": {"context": 1048576, "max_output": None,
                                    "released": None, "broken": None,
                                    "source": "catalog", "reason": "released: unknown"},
        "nvidia:z-ai/glm-5.3": {"context": 200000, "max_output": None,
                                "released": None, "broken": False,
                                "source": "catalog", "reason": "max_output/released unknown"},
    },
}

ROSTER = {
    "nvidia-glm53": ("nvidia", "z-ai/glm-5.3-flash"),
    "mistral-large": ("mistral", "mistral-large-latest"),
    "groq-qwen32": ("groq", "qwen/qwen3-32b"),
    "gemini-flash": ("gemini", "gemini-3.8-flash"),
    "nvidia-glm53-full": ("nvidia", "z-ai/glm-5.3"),
}


class LadderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = Path(self.tmp.name) / "provider-limits.json"
        p.write_text(json.dumps(LIMITS_DOC))
        self.limits = _pl.load(p)

    def pick(self, order, **kw):
        base = dict(need_context=0,
                    headroom=lambda prov: 1.0,
                    predicted_wait=lambda alias: 0.0,
                    timeout=lambda alias: 600.0,
                    roster=ROSTER)
        base.update(kw)
        return sl.pick(order, limits=self.limits, **base)

    def test_nvidia_first_when_it_has_room(self):
        alias, reasons = self.pick(["nvidia-glm53", "mistral-large"])
        self.assertEqual(alias, "nvidia-glm53")

    def test_gemini_never_picked_for_a_session(self):
        alias, reasons = self.pick(["gemini-flash", "nvidia-glm53"])
        self.assertEqual(alias, "nvidia-glm53", "dispatch_only must not lead a session")
        self.assertIn(("gemini-flash", "skipped: gemini is dispatch_only (never a full session)"),
                      reasons)

    def test_small_context_model_never_picked(self):
        alias, reasons = self.pick(["groq-qwen32", "mistral-large"])
        self.assertEqual(alias, "mistral-large", "131072 < session_min_context")
        why = dict(reasons)["groq-qwen32"]
        self.assertIn("context 131072", why)

    def test_context_unknown_is_skipped_not_gambled(self):
        alias, reasons = self.pick(["nvidia-glm53-full", "mistral-large"])
        # 200000 < 262145: a known-but-small context
        self.assertEqual(alias, "mistral-large")

    def test_unknown_context_skips_with_reason(self):
        doc = json.loads(json.dumps(LIMITS_DOC))
        doc["models"]["nvidia:z-ai/glm-5.3-flash"]["context"] = None
        self.limits = _pl.load(_write(self.tmp.name, doc))
        alias, reasons = self.pick(["nvidia-glm53", "mistral-large"])
        self.assertEqual(alias, "mistral-large")
        self.assertEqual(dict(reasons)["nvidia-glm53"],
                         "skipped: context unknown (sessions never gamble)")

    def test_spills_past_a_saturated_provider_with_the_reason(self):
        alias, reasons = self.pick(
            ["nvidia-glm53", "mistral-large"],
            predicted_wait=lambda a: 900.0 if a == "nvidia-glm53" else 0.0,
            timeout=lambda a: 600.0 if a == "nvidia-glm53" else 600.0)
        self.assertEqual(alias, "mistral-large")
        self.assertEqual(dict(reasons)["nvidia-glm53"],
                         "skipped: nvidia predicted wait 900s > 600s timeout")

    def test_exhausted_provider_is_skipped(self):
        alias, reasons = self.pick(["nvidia-glm53", "mistral-large"],
                                   headroom=lambda prov: 0.0 if prov == "nvidia" else 1.0)
        self.assertEqual(alias, "mistral-large")
        self.assertIn("exhausted", dict(reasons)["nvidia-glm53"])

    def test_missing_key_skips_the_provider(self):
        alias, reasons = self.pick(["nvidia-glm53", "mistral-large"],
                                   keyed=lambda prov: prov != "nvidia")
        self.assertEqual(alias, "mistral-large")
        self.assertIn("no key", dict(reasons)["nvidia-glm53"])

    def test_broken_model_is_skipped(self):
        doc = json.loads(json.dumps(LIMITS_DOC))
        doc["models"]["nvidia:z-ai/glm-5.3-flash"]["broken"] = True
        self.limits = _pl.load(_write(self.tmp.name, doc))
        alias, reasons = self.pick(["nvidia-glm53", "mistral-large"])
        self.assertEqual(alias, "mistral-large")
        self.assertIn("broken", dict(reasons)["nvidia-glm53"])

    def test_nothing_eligible_returns_none_and_all_reasons(self):
        alias, reasons = self.pick(["gemini-flash", "groq-qwen32"])
        self.assertIsNone(alias)
        self.assertEqual(len(reasons), 2)


def _write(tmp, doc):
    p = Path(tmp) / "pl.json"
    p.write_text(json.dumps(doc))
    return p


if __name__ == "__main__":
    unittest.main()
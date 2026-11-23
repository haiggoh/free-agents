#!/usr/bin/env python3
"""Task 11 Step 1 — the limits/quota infoboxes (C8): one shared renderer (limits_lines) on
both the model selector (Remote rows + provider group headers) and the API-keys screen,
rendered from provider-limits.json; unknown provider -> "Limits: unknown"; NVIDIA group,
Mistral model row and API-keys screen all show the expected lines.

Run under the picker venv: ~/.local/share/free-agents/picker-venv/bin/python -m pytest …
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import ROOT, PilotCase, REMOTE_INV  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.load_sensitive

INV = ("nvidia-big\tnvidia\tNVIDIA Big\tunknown\t0\t1\t0\tnvidia/big-1m\n"
       "mistral-c\tmistral\tMistral C\tunknown\t0\t1\t0\tmistral/c-256k\n")

LIMITS = {
    "schema_version": 1, "session_min_context": 262145, "large_context_threshold": 262144,
    "providers": {
        "nvidia": {"rpm": 40, "tpm": None, "rpd": None, "rpw": None, "tpd": None,
                   "dispatch_only": False, "reserves_on_request": False,
                   "measured_at": "2026-11-20",
                   "source": "docs/measurements/2026-11-20-remote-quota-probe.md"},
        "mistral": {"rpm": 2, "tpm": 40000, "rpd": None, "rpw": None, "tpd": None,
                    "dispatch_only": False, "reserves_on_request": False,
                    "measured_at": "2026-11-20", "source": "test-probe.md"},
    },
    "models": {
        "nvidia:nvidia/big-1m": {"context": 1000000, "max_output": 16384,
                                 "released": None, "broken": False,
                                 "source": "hf config.json", "reason": "released unknown"},
        "mistral:mistral/c-256k": {"context": 262144, "max_output": 8192,
                                   "released": None, "broken": False,
                                   "source": "catalog", "reason": "released unknown"},
    },
}


class _Case(PilotCase):
    def setUp(self):
        super().setUp()
        stub = Path(self.env["CSL_REMOTE_LAUNCHER"])
        stub.write_text(stub.read_text().replace(REMOTE_INV, INV))
        stub.chmod(0o755)
        self.limits = Path(self.tmp.name) / "provider-limits.json"
        self.limits.write_text(json.dumps(LIMITS))
        os.environ["FA_PROVIDER_LIMITS"] = str(self.limits)
        self.addCleanup(os.environ.pop, "FA_PROVIDER_LIMITS", None)


class LimitsInfoboxTests(_Case):
    def test_limits_lines_unit(self):
        lines = self.m.limits_lines("nvidia", "nvidia/big-1m", None)
        self.assertIn("Requests/min: 40", lines)
        self.assertIn("Tokens/min: —", lines)
        self.assertIn("Daily quota: none known", lines)
        self.assertIn("Max output: 16,384", lines)
        self.assertIn("Context: 1,000,000", lines)
        self.assertIn("Full sessions: yes", lines)
        self.assertTrue(any(ln.startswith("Measured 2026-11-20") for ln in lines))
        small = self.m.limits_lines("mistral", "mistral/c-256k", None)
        self.assertIn("no — context 262,144 ≤ 256k", small)
        self.assertEqual(self.m.limits_lines("nope"), ["Limits: unknown (no entry in provider-limits.json)"])

    def test_remote_infobox_shows_limits(self):
        async def s(app, pilot, h):
            await h.press("c")                    # expand the model list
            await h.wait_text("NVIDIA Big")
            await h.goto_row("item", "nvidia-big")
            await h.settle()
            info = app.query_one("#info")
            text = str(info.renderable)
            self.assertIn("Requests/min: 40", text, text)
            self.assertIn("Context: 1,000,000", text, text)
        self.run_picker(s, start="remote")


if __name__ == "__main__":
    unittest.main()

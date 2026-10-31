#!/usr/bin/env python3
"""tests/test_fallback_eol_0493.py — 0.49.7 (H5): end-of-life ids never enter a session's
fallback chain, even via an explicit LA_REMOTE_FALLBACKS override (z-ai/glm-5.3 retired by
NVIDIA, 410 Gone, EOL 2026-10-28T08:00:00Z). The 410+429 LiteLLM blob misled the user because
the chain still contained the EOL id; dropping it is the launch-side fix."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("session_fallbacks", ROOT / "bin" / "session-fallbacks.py")
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

BROKEN = ROOT / "config" / "broken-nvidia-models.json"


class EolChainTests(unittest.TestCase):
    def test_shipped_chain_leads_with_le_chonk_and_has_no_glm_full(self):
        chain = sf.chain("nvidia", "mistralai/mistral-large-4-1t-a52b")  # own model skipped
        self.assertEqual(chain, ["nvidia:z-ai/glm-5.3-flash",
                                 "nvidia:deepseek-ai/deepseek-v4.1-flash",
                                 "nvidia:stepfun-ai/step-5-preview",
                                 "nvidia:nvidia/nemotron-3-ultra-550b-a55b"])
        self.assertNotIn("nvidia:z-ai/glm-5.3", sf.chain("nvidia", "nvidia:x"))

    def test_eol_id_dropped_even_from_explicit_override(self):
        with mock.patch.dict(os.environ, {
                "LA_REMOTE_FALLBACKS": "nvidia:z-ai/glm-5.3,nvidia:deepseek-ai/deepseek-v4.1-flash",
                "LA_BROKEN_MODELS_FILE": str(BROKEN)}):
            self.assertEqual(sf.chain("nvidia", "nvidia:x"),
                             ["nvidia:deepseek-ai/deepseek-v4.1-flash"],
                             "the retired id must be filtered from the override too")

    def test_missing_broken_file_fails_open(self):
        # planted negative: with no broken-list file the filter must not fire (fail-open),
        # proving the override test above is driven by the file, not a hardcoded id
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nope.json"
            with mock.patch.dict(os.environ, {
                    "LA_REMOTE_FALLBACKS": "nvidia:z-ai/glm-5.3",
                    "LA_BROKEN_MODELS_FILE": str(missing)}):
                self.assertEqual(sf.chain("nvidia", "nvidia:x"), ["nvidia:z-ai/glm-5.3"])

    def test_help_exits_zero(self):
        import subprocess
        r = subprocess.run([sys.executable, str(ROOT / "bin/session-fallbacks.py"), "--help"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("LA_BROKEN_MODELS_FILE", r.stdout, "the new env var must be documented")


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
    unittest.main()

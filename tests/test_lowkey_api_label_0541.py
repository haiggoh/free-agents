#!/usr/bin/env python3
"""tests/test_lowkey_api_label_0541.py — 0.55.8 Task 4 (0541 plan Item C):

The lowkey API assistant's label is the model's READABLE name (model_names.readable — the
same source the picker rows use), not the local-alias shortener that truncated
'mistralai/mistral-large-4-1t-a52b' to 'mistralai'. Local labels keep the shortener.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_lowkey_api_label_0541.py
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import pytest  # noqa: E402

LOWKEY = ROOT / "bin" / "lowkey-cli.py"


def dry_run(*extra):
    return subprocess.run([sys.executable, str(LOWKEY), "--convo", "--dry-run", *extra],
                          capture_output=True, text=True, timeout=30,
                          env={**os.environ, "LA_HF_PARAMS_CACHE": tempfile_mkstemp()})
def tempfile_mkstemp():
    import tempfile
    return tempfile.mkdtemp() + "/hf-params.json"


class LowkeyApiLabel(unittest.TestCase):
    def label_of(self, *extra):
        r = dry_run(*extra)
        line = next((x for x in r.stdout.splitlines() if x.startswith("Display label")), "")
        return r, line

    def test_api_label_is_readable_model_name(self):
        r, line = self.label_of("--provider", "nvidia",
                                "--api-model", "mistralai/mistral-large-4-1t-a52b")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Display label  : Mistral Large 4 1T A52B", r.stdout,
                      f"label line: {line!r}")

    def test_api_label_matches_picker_row_name(self):
        import model_names
        for mid in ("mistralai/mistral-large-4-1t-a52b", "zai-org/glm-5.3-flash",
                    "stepfun-ai/step-5-preview"):
            r, line = self.label_of("--provider", "nvidia", "--api-model", mid)
            self.assertIn(f"Display label  : {model_names.readable(mid)}", r.stdout,
                          f"{mid}: {line!r}")

    def test_empty_api_model_labels_provider(self):
        # unit level: the CLI dry-run fills a provider-default model, so the empty case only
        # exists at the function level (the DECISION: provider name when the id is empty)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "lowkey_cli", ROOT / "bin" / "lowkey-cli.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.api_display_label("nvidia", ""), "nvidia")

    def test_local_label_unchanged(self):
        r, line = self.label_of("--model", "qwen-3.8-operator")
        self.assertIn("Display label  : qwen", r.stdout, line)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Offline tests for local-classifier-default.sh; never serve a model.

Usage: python3 tests/test_local_classifier_default.py [-v] [test names]
Environment: LA_MODELS_DIR is isolated to a fixture per test; "weights" are empty
config.json files, so no network or GPU is touched.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bin" / "local-classifier-default.sh"


def make_model(dirpath: Path):
    dirpath.mkdir(parents=True)
    (dirpath / "config.json").write_text("{}")


class ClassifierDefaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.models = Path(self.tmp.name) / "models"
        self.models.mkdir()
        self.env = dict(os.environ, LA_MODELS_DIR=str(self.models))

    def run_helper(self, *args):
        return subprocess.run(["bash", str(HELPER), *args], env=self.env,
                              capture_output=True, text=True)

    def test_granite_selected_when_present(self):
        make_model(self.models / "granite-4.2-30b-q4-mlx")
        r = self.run_helper()
        self.assertEqual(r.returncode, 0)
        self.assertIn("granite-4.2-30b-q4-mlx", r.stdout)
        self.assertEqual(self.run_helper("--alias").stdout.strip(), "granite-4.2-30b")

    def test_falls_back_to_first_installed(self):
        make_model(self.models / "KAT-Coder-V2.5-Dev-OptiQ-4bit")
        r = self.run_helper()
        self.assertEqual(r.returncode, 0)
        self.assertIn("KAT-Coder", r.stdout)
        self.assertEqual(self.run_helper("--alias").stdout.strip(), "kat-coder-optiq")

    def test_fallback_order_respected(self):
        make_model(self.models / "qwen-agentworld-4bit")            # last fallback
        make_model(self.models / "Ornith-1.5-35B-A3B-MLX-4bit")     # earlier fallback wins
        self.assertEqual(self.run_helper("--alias").stdout.strip(), "ornith-1.5-35b")

    def test_none_installed_warns_with_install_hint(self):
        r = self.run_helper()
        self.assertEqual(r.returncode, 2)
        self.assertIn("no classifier-capable local model", r.stderr)
        self.assertIn("--select granite-4.2-30b", r.stderr)

    def test_dir_only_silent_on_failure(self):
        r = self.run_helper("--dir-only")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertEqual(r.stderr, "")

    def test_env_override_primary(self):
        make_model(self.models / "qwen-agentworld-4bit")
        self.env["LA_LOCAL_CLASSIFIER_ALIAS"] = "agentworld-35b"
        self.assertEqual(self.run_helper("--alias").stdout.strip(), "agentworld-35b")


if __name__ == "__main__":
    unittest.main()

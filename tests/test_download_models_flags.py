#!/usr/bin/env python3
"""install/download-models.sh flag contract (QC finding 4a): the inert --json flag was
removed in 0.59.3 — it must now fail loudly with exit 2 and name the machine-readable
route (--inventory), instead of being silently accepted and ignored."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install" / "download-models.sh"


class JsonFlagRejected(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"PATH": os.environ["PATH"], "HOME": self.tmp.name, "LC_ALL": "C"}

    def test_json_flag_exits_2_and_names_inventory(self):
        r = subprocess.run(["bash", str(SCRIPT), "--json"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a supported flag", r.stderr)
        self.assertIn("--inventory", r.stderr)

    def test_help_still_works(self):
        r = subprocess.run(["bash", str(SCRIPT), "--help"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Usage", r.stdout)


if __name__ == "__main__":
    unittest.main()

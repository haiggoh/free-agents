#!/usr/bin/env python3
"""0.50.0 item 6 (free-agents-repo-wide-help-sweep remainder): every remaining script
answers --help with a usage line and exit 0 WITHOUT doing its real work, and rejects an
unknown flag with a non-zero exit. The .rtc/*.sh hooks are run with cwd in a temp dir so a
regression fails fast instead of packaging the repo.

Usage: python3 tests/test_help_sweep_0500.py [-v]
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RTC_SCRIPTS = [".rtc/pre-package.sh", ".rtc/pre-flight.sh", ".rtc/post-release.sh"]
PY_SCRIPTS = ["bin/queue_marker.py", "hooks/offload-nudge.py", "hooks/transcript-identity.py"]


class HelpSweepTests(unittest.TestCase):
    def test_rtc_scripts_help_without_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rel in RTC_SCRIPTS:
                with self.subTest(script=rel):
                    r = subprocess.run(["bash", str(ROOT / rel), "--help"],
                                       capture_output=True, text=True, timeout=30, cwd=tmp)
                    self.assertEqual(r.returncode, 0, r.stderr[-400:])
                    self.assertIn("Usage", r.stdout, "no usage line printed")
                    # unknown flag: non-zero + usage on stderr
                    r2 = subprocess.run(["bash", str(ROOT / rel), "--bogus"],
                                        capture_output=True, text=True, timeout=30, cwd=tmp)
                    self.assertNotEqual(r2.returncode, 0)
                    self.assertIn("Usage", r2.stderr)

    def test_python_files_help_without_running(self):
        for rel in PY_SCRIPTS:
            with self.subTest(script=rel):
                r = subprocess.run([sys.executable, str(ROOT / rel), "--help"],
                                   capture_output=True, text=True, timeout=30,
                                   stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 0, r.stderr[-400:])
                self.assertTrue(r.stdout.strip(), "--help printed nothing")
                # a hook must not emit its hook JSON when asked for help
                self.assertNotIn("hookSpecificOutput", r.stdout)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""config-lib.sh bash-4 guard (waypoint config-lib-sh-requires-bash-4).

Under macOS /bin/bash 3.2 the lib used to crash cryptically (declare -A invalid option,
then an arithmetic syntax error in la_register). Now it fails loudly and names the
requirement. Under a modern bash it loads normally. The 3.2 case is skipped when
/bin/bash is already 4+ (e.g. non-macOS CI).
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "config" / "config-lib.sh"


def bash_version(bash):
    r = subprocess.run([bash, "--version"], capture_output=True, text=True)
    m = re.search(r"version (\d+)\.(\d+)", r.stdout)
    return (int(m.group(1)), int(m.group(2))) if m else (99, 0)


class BashGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"PATH": os.environ["PATH"], "HOME": self.tmp.name, "LC_ALL": "C"}

    def _run(self, bash):
        return subprocess.run(
            [bash, "-c", f'source "{LIB}" && echo LIB-LOADED'],
            env=self.env, capture_output=True, text=True, timeout=60)

    def test_bash32_fails_loudly_not_cryptically(self):
        v = bash_version("/bin/bash")
        if v[0] >= 4:
            self.skipTest("/bin/bash is 4+ on this host")
        r = self._run("/bin/bash")
        self.assertEqual(r.returncode, 1)
        self.assertIn("requires bash 4+", r.stderr)
        self.assertNotIn("invalid option", r.stderr)  # no more cryptic declare error
        self.assertNotIn("LIB-LOADED", r.stdout)

    def test_modern_bash_loads(self):
        r = self._run("bash")  # PATH bash (brew/bash5 on this repo's machines)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("LIB-LOADED", r.stdout)


if __name__ == "__main__":
    unittest.main()

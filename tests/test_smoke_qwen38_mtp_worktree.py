#!/usr/bin/env python3
"""tests/smoke_qwen38_mtp.sh in an agent worktree (waypoint smoke-qwen38-mtp-sh-cannot).

config.local.sh is git-ignored, so a fresh worktree has only config.example.sh and
la_load_config silently falls back to it (LA_FALLBACK_CONFIG=1), ending in a confusing
"alias lookup failed". The script must fail EARLY and clearly in that case, naming
LA_LIVE_CONFIG_DIR — and never get as far as the port/runtime preflight.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "smoke_qwen38_mtp.sh"


class SmokeWorktreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()

    def _env(self, extra=None):
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "LC_ALL": "C"}
        if extra:
            env.update(extra)
        return env

    def _run(self, extra=None):
        return subprocess.run(["bash", str(SCRIPT)], env=self._env(extra),
                              capture_output=True, text=True, timeout=60)

    def test_worktree_without_config_local_fails_early_and_clearly(self):
        # this checkout is a worktree: config/config.local.sh is git-ignored and absent
        self.assertFalse((ROOT / "config" / "config.local.sh").exists(),
                         "test premise: worktree lacks the git-ignored config.local.sh")
        r = self._run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("no config.local.sh", r.stderr)
        self.assertIn("LA_LIVE_CONFIG_DIR", r.stderr)
        # the failure must come BEFORE the runtime/port preflight (no side effects)
        self.assertNotIn("FRESH-RUNTIME PREFLIGHT", r.stdout)

    def test_la_live_config_dir_fixture_gets_past_the_config_check(self):
        live = Path(self.tmp.name) / "live"
        live.mkdir()
        (live / "config.local.sh").write_text("# no pins\n")
        r = self._run({"LA_LIVE_CONFIG_DIR": str(live)})
        self.assertNotIn("no config.local.sh", r.stderr)
        # delivery-shaped (QC 2b): prove the script RAN PAST the config check into the
        # preflight, not merely that the config message is absent
        self.assertIn("FRESH-RUNTIME PREFLIGHT", r.stdout)


if __name__ == "__main__":
    unittest.main()

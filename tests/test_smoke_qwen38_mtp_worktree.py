#!/usr/bin/env python3
"""tests/smoke_qwen38_mtp.sh in an agent worktree (waypoint smoke-qwen38-mtp-sh-cannot).

config.local.sh is git-ignored, so a fresh worktree has only config.example.sh and
la_load_config silently falls back to it (LA_FALLBACK_CONFIG=1), ending in a confusing
"alias lookup failed". The script must fail EARLY and clearly in that case, naming
LA_LIVE_CONFIG_DIR — and never get as far as the port/runtime preflight.

The script computes REPO from its own location, so the test builds a minimal isolated
git repo in tmp (the script + config-lib + example config) — never the shared checkout,
whose cleanliness the full parallel gate cannot guarantee.
"""
import os
import shutil
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
        t = Path(self.tmp.name)
        self.home = t / "home"
        self.home.mkdir()
        # minimal isolated "worktree": a git repo with the script and config, but NO
        # config.local.sh (git-ignored in real checkouts)
        self.repo = t / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "config").mkdir()
        shutil.copy2(SCRIPT, self.repo / "tests" / "smoke_qwen38_mtp.sh")
        shutil.copy2(ROOT / "config" / "config-lib.sh", self.repo / "config" / "config-lib.sh")
        shutil.copy2(ROOT / "config" / "config.example.sh", self.repo / "config" / "config.example.sh")
        subprocess.run(["git", "-c", "user.email=t@local", "-c", "user.name=t", "init", "-q", str(self.repo)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@local", "-c", "user.name=t",
                        "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.email=t@local", "-c", "user.name=t",
                        "commit", "-q", "-m", "init", "--allow-empty"], check=True, capture_output=True)
        self.assertFalse((self.repo / "config" / "config.local.sh").exists())

    def _env(self, extra=None):
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "LC_ALL": "C"}
        if extra:
            env.update(extra)
        return env

    def _run(self, extra=None):
        return subprocess.run(["bash", str(self.repo / "tests" / "smoke_qwen38_mtp.sh")],
                              env=self._env(extra), capture_output=True, text=True, timeout=60)

    def test_worktree_without_config_local_fails_early_and_clearly(self):
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
        # delivery-shaped: prove the script RAN PAST the config check into the preflight
        self.assertIn("FRESH-RUNTIME PREFLIGHT", r.stdout)


if __name__ == "__main__":
    unittest.main()

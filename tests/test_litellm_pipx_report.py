#!/usr/bin/env python3
"""LiteLLM has ONE owner: the pipx install the remote sessions run (user decision 2026-10-30).

remote-session.sh resolves `command -v litellm` (the pipx install the nightly job upgrades); the
manager's own ~/.venvs/litellm-* were never used. So health reports the pipx install read-only
and marks the manager's venvs as leftovers to remove — never "repair" them.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "install" / "manage-backend.py"


def build_venv(target: Path, binary: str) -> None:
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / binary
    script.write_text(f"#!{target}/bin/python\n", encoding="utf-8")
    script.chmod(0o755)


class LitellmPipxReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        pipx_venv = self.home / ".local" / "pipx" / "venvs" / "litellm"
        build_venv(pipx_venv, "litellm")
        site = next((pipx_venv / "lib").glob("python*")) / "site-packages"
        (site / "litellm-1.82.0.dist-info").mkdir(parents=True)
        (self.home / ".local" / "bin").mkdir(parents=True)
        (self.home / ".local" / "bin" / "litellm").symlink_to(pipx_venv / "bin" / "litellm")
        leftover = self.home / ".venvs" / "litellm-1.102.1"
        (leftover / "bin").mkdir(parents=True)          # the real leftover: no binary at all
        self.env = {**os.environ, "HOME": str(self.home), "LA_LIVE_CONFIG_DIR": str(self.home),
                    "PATH": f"{self.home / '.local' / 'bin'}:/usr/bin:/bin"}
        self.env.pop("_LA_LIVE_FOR", None)

    def tearDown(self):
        self.tmp.cleanup()

    def health(self, *extra):
        return subprocess.run([sys.executable, str(CLI), "--backend", "litellm", *extra, "health"],
                              capture_output=True, text=True, env=self.env)

    def test_pipx_install_is_reported_with_its_owner_and_version(self):
        rows = json.loads(self.health("--json").stdout)
        pipx = [r for r in rows if r.get("owner") == "pipx"]
        self.assertEqual(len(pipx), 1, rows)
        self.assertEqual(pipx[0]["version"], "1.82.0")
        self.assertEqual(pipx[0]["state"], "complete")

    def test_manager_venvs_are_leftovers_to_remove_not_repair(self):
        out = self.health().stdout
        self.assertIn("maintained by pipx", out)
        self.assertIn("leftover", out)
        self.assertIn("remove 1.102.1", out)
        self.assertNotIn("repair 1.102.1", out)

    def test_a_leftover_does_not_fail_health(self):
        self.assertEqual(self.health().returncode, 0)


if __name__ == "__main__":
    unittest.main()

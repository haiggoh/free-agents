#!/usr/bin/env python3
"""Two-root venv readers (0.58.0 plan Task 1).

0.50.0 moved venv WRITES to $XDG_DATA_HOME/free-agents/backends (default
~/.local/share/free-agents/backends) and the manager's Python already scans both
roots (install/managers/base.py discovery_roots, new root first). The SHELL
readers still scanned only ~/.venvs: la_discover_rapid_bin never saw a new-root
install and la_backend_repair_hint called a new-root binary "not ours". These
cases pin both readers across BOTH roots, plus the la_backend_roots helper that
Tasks 1-2 share. Only managed names count: <prefix>-<X.Y.Z> for rapid-mlx /
vllm-mlx / litellm — the user's own ~/.venvs/* entries are never listed.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

XDG_REL = Path(".local") / "share" / "free-agents" / "backends"


def make_fake_venv(root: Path, dirname: str, binname: str) -> Path:
    """Layout la_backend_bin_health accepts: an executable bin plus a bin/python
    that imports encodings (symlink to the running interpreter). The health
    check's own behaviour is pinned by tests/test_backend_bin_health.sh."""
    vdir = root / dirname
    (vdir / "bin").mkdir(parents=True)
    (vdir / "bin" / "python").symlink_to(sys.executable)
    script = vdir / "bin" / binname
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return vdir


class TwoRootReadersTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        self.home.mkdir()
        self.new_root = self.home / XDG_REL
        self.legacy = self.home / ".venvs"
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_DATA_HOME", "LA_USER_STATE_DIR", "LA_RAPID_BIN",
                            "LA_VENV", "_LA_RAPID_SYSTEM_CANDIDATES")}
        env["HOME"] = str(self.home)
        env["PATH"] = "/usr/bin:/bin"  # no rapid-mlx on PATH
        env["_LA_RAPID_SYSTEM_CANDIDATES"] = ""  # skip the fixed brew candidates
        self.env = env

    def run_lib(self, snippet: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c",
             'source config/config-lib.sh >/dev/null 2>&1; ' + snippet],
            capture_output=True, text=True, cwd=REPO, env=self.env)

    def test_backend_roots_prints_new_root_first(self):
        out = self.run_lib("la_backend_roots").stdout.splitlines()
        self.assertEqual(out, [str(self.new_root), str(self.legacy)])

    def test_discovery_finds_a_new_root_venv(self):
        make_fake_venv(self.new_root, "rapid-mlx-1.0.3", "rapid-mlx")
        r = self.run_lib("la_discover_rapid_bin")
        self.assertEqual(r.stdout.strip(),
                         str(self.new_root / "rapid-mlx-1.0.3" / "bin" / "rapid-mlx"))
        self.assertEqual(r.returncode, 0)

    def test_discovery_still_finds_a_legacy_root_venv(self):
        make_fake_venv(self.legacy, "rapid-mlx-1.0.0", "rapid-mlx")
        r = self.run_lib("la_discover_rapid_bin")
        self.assertEqual(r.stdout.strip(),
                         str(self.legacy / "rapid-mlx-1.0.0" / "bin" / "rapid-mlx"))

    def test_discovery_prefers_newest_across_roots(self):
        make_fake_venv(self.legacy, "rapid-mlx-1.0.0", "rapid-mlx")
        make_fake_venv(self.new_root, "rapid-mlx-1.0.3", "rapid-mlx")
        self.assertEqual(self.run_lib("la_discover_rapid_bin").stdout.strip(),
                         str(self.new_root / "rapid-mlx-1.0.3" / "bin" / "rapid-mlx"))

    def test_discovery_same_version_both_roots_prefers_new_root(self):
        make_fake_venv(self.legacy, "rapid-mlx-1.0.3", "rapid-mlx")
        make_fake_venv(self.new_root, "rapid-mlx-1.0.3", "rapid-mlx")
        self.assertEqual(self.run_lib("la_discover_rapid_bin").stdout.strip(),
                         str(self.new_root / "rapid-mlx-1.0.3" / "bin" / "rapid-mlx"))

    def test_discovery_ignores_unmanaged_dirs(self):
        make_fake_venv(self.legacy, "rapid-mlx-foo", "rapid-mlx")
        make_fake_venv(self.new_root, "rapid-mlx-foo", "rapid-mlx")
        r = self.run_lib("la_discover_rapid_bin")
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(r.returncode, 1)

    def test_repair_hint_names_repair_for_a_new_root_binary(self):
        r = self.run_lib('la_backend_repair_hint '
                         '"$HOME/.local/share/free-agents/backends/vllm-mlx-0.6.0/bin/vllm-mlx"')
        self.assertIn("--backend vllm-mlx repair 0.6.0", r.stdout)

    def test_repair_hint_still_names_repair_for_a_legacy_binary(self):
        r = self.run_lib('la_backend_repair_hint "$HOME/.venvs/rapid-mlx-0.16.1/bin/rapid-mlx"')
        self.assertIn("--backend rapid-mlx repair 0.16.1", r.stdout)


if __name__ == "__main__":
    unittest.main()

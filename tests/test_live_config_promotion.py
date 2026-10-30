#!/usr/bin/env python3
"""A backend update writes ONLY the machine's live config — it never dirties the repo.

User 2026-10-30: "anytime I ran a backend update on rapid mlx from inside the backend manager I
had a dirty worktree afterwards … an update [should not] dirty the worktree of my local repo".
And measured the same day: promote stamped four tracked files but never reached the live pin in
~/.local/share/free-agents/config/config.local.sh (it looked for <repo>/config/config.local.sh,
which does not exist), so the live machine stayed on a dead 0.16.1 through three promotions.

Every case runs in a temp HOME with a temp repo made from this checkout's config/ (the manager
asks config-lib.sh itself where the live config is — one implementation), and real stdlib venvs.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "install"))

import managers.rapid_mlx  # noqa: E402,F401  (registers the manager)
from managers.base import ManagerError, live_config_file  # noqa: E402
from managers.rapid_mlx import RapidMLXManager  # noqa: E402

PIN_LINE = 'LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-{v}/bin/rapid-mlx"\n'


def make_rapid_venv(home: Path, version: str) -> Path:
    target = home / ".venvs" / f"rapid-mlx-{version}"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / "rapid-mlx"
    script.write_text(f'#!{target}/bin/python\nprint("rapid-mlx {version}")\n', encoding="utf-8")
    script.chmod(0o755)
    return target


class LiveConfigPromotionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.repo = root / "repo"
        self.live_dir = self.home / ".local" / "share" / "free-agents" / "config"
        self.live_dir.mkdir(parents=True)
        shutil.copytree(REPO / "config", self.repo / "config",
                        ignore=shutil.ignore_patterns("config.local.sh", "backups"))
        (self.repo / "bin").mkdir()
        (self.repo / "bin" / "README").write_text("tracked placeholder\n", encoding="utf-8")
        self.live = self.live_dir / "config.local.sh"
        self.live.write_text("# live machine config\n" + PIN_LINE.format(v="0.1.0"), encoding="utf-8")
        self.live.chmod(0o600)
        for v in ("0.1.0", "0.2.0"):
            make_rapid_venv(self.home, v)
        env = {k: v for k, v in os.environ.items()
               if k not in ("LA_LIVE_CONFIG_DIR", "_LA_LIVE_FOR", "LA_CONFIG_DIR", "LA_RAPID_BIN")}
        env["HOME"] = str(self.home)
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def manager(self):
        mgr = RapidMLXManager(home=self.home, repo=self.repo)
        mgr.validate_environment = lambda version: None  # pip check needs pip; not under test here
        return mgr

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True)

    def test_live_config_file_is_resolved_by_config_lib(self):
        self.assertEqual(live_config_file(self.repo), self.live)

    def test_set_but_missing_live_config_dir_aborts_naming_it(self):
        with mock.patch.dict(os.environ, {"LA_LIVE_CONFIG_DIR": str(self.home / "nope")}):
            with self.assertRaisesRegex(ManagerError, "LA_LIVE_CONFIG_DIR"):
                live_config_file(self.repo)

    def test_promote_leaves_a_real_checkout_clean_and_moves_the_live_pin(self):
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "fixture")
        self.manager().promote_pins("0.2.0")
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        self.assertIn("rapid-mlx-0.2.0/bin/rapid-mlx", self.live.read_text(encoding="utf-8"))
        self.assertNotIn("rapid-mlx-0.1.0", self.live.read_text(encoding="utf-8"))
        self.assertEqual(self.live.stat().st_mode & 0o777, 0o600)

    def test_promote_works_on_a_copy_without_git(self):
        # The installed plugin copy is not a checkout; promotion must not refuse it any more.
        result = self.manager().promote_pins("0.2.0")
        self.assertEqual(result["result"], "promoted")
        self.assertIn("rapid-mlx-0.2.0", self.live.read_text(encoding="utf-8"))

    def test_promote_never_touches_tracked_files(self):
        before = {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        self.manager().promote_pins("0.2.0")
        after = {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_live_config_without_a_pin_line_is_left_byte_identical(self):
        self.live.write_text("# no explicit pin: auto-discovery\n", encoding="utf-8")
        before = self.live.read_bytes()
        result = self.manager().promote_pins("0.2.0")
        self.assertEqual(result["result"], "no_explicit_pin")
        self.assertEqual(self.live.read_bytes(), before)

    def test_promotion_is_validated_on_what_the_launcher_resolves(self):
        # The outcome check sources config-lib like a launcher does: promoting to a version whose
        # venv is DEAD must fail and roll the live file back.
        dead = self.home / ".venvs" / "rapid-mlx-0.2.0" / "bin" / "python"
        dead.unlink()
        dead.symlink_to(self.home / "Cellar" / "gone" / "python3.14")
        before = self.live.read_bytes()
        with self.assertRaisesRegex(ManagerError, "rolled back"):
            self.manager().promote_pins("0.2.0")
        self.assertEqual(self.live.read_bytes(), before)

    def test_remove_refuses_the_version_the_live_config_pins(self):
        refs = self.manager()._referenced_paths(self.repo, self.home / ".venvs" / "rapid-mlx-0.1.0")
        self.assertTrue(any(str(self.live) in r for r in refs), refs)


class NoShippedBackendPinTest(unittest.TestCase):
    """A guard: no shipped file may carry a machine's backend venv path again."""

    def test_no_versioned_backend_venv_path_in_shipped_files(self):
        out = subprocess.run(
            ["git", "-C", str(REPO), "grep", "-nE", r"(rapid-mlx|vllm-mlx|litellm)-[0-9]+\.[0-9]+\.[0-9]+/bin",
             "--", ".", ":!CHANGELOG.md", ":!docs", ":!tests"],
            capture_output=True, text=True)
        self.assertIn(out.returncode, (0, 1), out.stderr)
        self.assertEqual(out.stdout, "", "a backend venv path is committed in a shipped file:\n" + out.stdout)


if __name__ == "__main__":
    unittest.main()

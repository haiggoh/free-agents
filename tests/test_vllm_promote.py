#!/usr/bin/env python3
"""vllm-mlx `promote` points the lane at a manager-installed venv (user decision 2026-10-30).

The vllm-mlx lane launches "$LA_VENV/vllm-mlx", and LA_VENV defaulted to the legacy ~/.local-llm
venv — so every manager install/update of vllm-mlx (~/.venvs/vllm-mlx-*) never took effect.
promote now writes LA_VENV into the LIVE config (only there; the repo is never touched) and is
validated on what a launcher resolves, with rollback.
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

import managers.vllm_mlx  # noqa: E402,F401
from managers import get_manager  # noqa: E402
from managers.base import ManagerError  # noqa: E402

LEGACY = 'LA_VENV="$HOME/.local-llm/bin"         # venv with `vllm-mlx` and `python -m mlx_lm` (LEGACY lane)\n'


def build_venv(target: Path) -> None:
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / "vllm-mlx"
    script.write_text(f"#!{target}/bin/python\n", encoding="utf-8")
    script.chmod(0o755)


class VllmPromoteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.repo = root / "repo"
        shutil.copytree(REPO / "config", self.repo / "config",
                        ignore=shutil.ignore_patterns("config.local.sh", "backups"))
        self.live_dir = self.home / ".local" / "share" / "free-agents" / "config"
        self.live_dir.mkdir(parents=True)
        self.live = self.live_dir / "config.local.sh"
        self.live.write_text("# live\n" + LEGACY, encoding="utf-8")
        self.live.chmod(0o600)
        build_venv(self.home / ".venvs" / "vllm-mlx-0.6.0")
        env = {k: v for k, v in os.environ.items() if k not in ("LA_LIVE_CONFIG_DIR", "_LA_LIVE_FOR", "LA_VENV")}
        env["HOME"] = str(self.home)
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def mgr(self):
        m = get_manager("vllm-mlx")(home=self.home)
        m._repo = self.repo
        m.validate_environment = lambda version: None
        return m

    def resolved_la_venv(self) -> str:
        out = subprocess.run(["bash", "-c", 'source config/config-lib.sh >/dev/null 2>&1; la_load_config >/dev/null 2>&1; '
                              'printf %s "$LA_VENV"'], capture_output=True, text=True, cwd=self.repo,
                             env={**os.environ})
        return out.stdout

    def test_promote_points_the_lane_at_the_manager_venv(self):
        result = self.mgr().promote_pins("0.6.0")
        self.assertEqual(result["result"], "promoted")
        self.assertEqual(self.resolved_la_venv(), f"{self.home}/.venvs/vllm-mlx-0.6.0/bin")
        self.assertEqual(self.live.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(".local-llm", "".join(line for line in self.live.read_text().splitlines(True) if line.startswith("LA_VENV=")))
        self.assertTrue(list((self.live_dir / "backups").glob("config.local.sh.pre-promote-vllm-mlx-0.6.0-*")))

    def test_promote_appends_when_the_live_config_has_no_la_venv_line(self):
        self.live.write_text("# live, no LA_VENV\n", encoding="utf-8")
        self.mgr().promote_pins("0.6.0")
        self.assertEqual(self.resolved_la_venv(), f"{self.home}/.venvs/vllm-mlx-0.6.0/bin")

    def test_promote_to_a_dead_venv_is_rolled_back(self):
        target = self.home / ".venvs" / "vllm-mlx-0.6.0" / "bin" / "python"
        target.unlink()
        target.symlink_to(self.home / "gone" / "python3.14")
        before = self.live.read_bytes()
        with self.assertRaisesRegex(ManagerError, "rolled back"):
            self.mgr().promote_pins("0.6.0")
        self.assertEqual(self.live.read_bytes(), before)

    def test_promote_never_touches_the_repo(self):
        before = {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file()}
        self.mgr().promote_pins("0.6.0")
        self.assertEqual(before, {p: p.read_bytes() for p in self.repo.rglob("*") if p.is_file()})

    def test_dry_run_writes_nothing(self):
        before = self.live.read_bytes()
        result = self.mgr().promote_pins("0.6.0", dry_run=True)
        self.assertEqual(result["result"], "dry_run")
        self.assertEqual(self.live.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

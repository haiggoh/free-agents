#!/usr/bin/env python3
"""Promotion pins the venv WHERE IT IS INSTALLED — across the 0.50.0 XDG move (user 2026-11-13).

Measured on a real machine: the live pin was LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-1.0.0/...",
1.0.1-1.0.3 were installed (0.50.0+) under ~/.local/share/free-agents/backends, and every
promotion failed with "promoted binary cannot run: missing … ~/.venvs/rapid-mlx-1.0.3/bin/rapid-mlx".
The rapid pin writer swapped ONLY the version digits and kept the old directory; the vllm-mlx
writer hardcoded $HOME/.venvs. The existing promotion tests built every venv under ~/.venvs, so
they never saw the layout every new install produces. These cases do: OLD pin in the legacy
root, the promoted version in the XDG root (and the reverse, so a legacy venv still promotes).
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

import managers.rapid_mlx  # noqa: E402,F401
import managers.vllm_mlx  # noqa: E402,F401
from managers import get_manager  # noqa: E402
from managers.rapid_mlx import RapidMLXManager  # noqa: E402

XDG_REL = Path(".local") / "share" / "free-agents" / "backends"


def make_venv(target: Path, binary: str, version_line: str) -> Path:
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / binary
    script.write_text(f'#!{target}/bin/python\nprint("{version_line}")\n', encoding="utf-8")
    script.chmod(0o755)
    return target


class _LiveHome(unittest.TestCase):
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
        env = {k: v for k, v in os.environ.items()
               if k not in ("LA_LIVE_CONFIG_DIR", "_LA_LIVE_FOR", "LA_CONFIG_DIR", "LA_RAPID_BIN",
                            "LA_VENV", "XDG_DATA_HOME")}
        env["HOME"] = str(self.home)
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write_live(self, text: str) -> None:
        self.live.write_text(text, encoding="utf-8")
        self.live.chmod(0o600)

    def resolved(self, var: str) -> str:
        out = subprocess.run(["bash", "-c", 'source config/config-lib.sh >/dev/null 2>&1; '
                              f'la_load_config >/dev/null 2>&1; printf %s "${var}"'],
                             capture_output=True, text=True, cwd=self.repo, env={**os.environ})
        return out.stdout

    def pin_lines(self, var: str) -> list[str]:
        return [ln for ln in self.live.read_text(encoding="utf-8").splitlines()
                if ln.lstrip().startswith(f"{var}=")]


class RapidPinAcrossRootsTest(_LiveHome):
    COMMENT = "# history: was $HOME/.venvs/rapid-mlx-0.9.0/bin/rapid-mlx before\n"

    def setUp(self):
        super().setUp()
        make_venv(self.home / ".venvs" / "rapid-mlx-1.0.0", "rapid-mlx", "rapid-mlx 1.0.0")
        make_venv(self.home / XDG_REL / "rapid-mlx-1.0.3", "rapid-mlx", "rapid-mlx 1.0.3")
        self.write_live("# live\n" + self.COMMENT
                        + 'LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-1.0.0/bin/rapid-mlx"\n')

    def mgr(self):
        m = RapidMLXManager(home=self.home, repo=self.repo)
        m.validate_environment = lambda version: None  # pip check needs pip; not under test
        return m

    def test_legacy_pin_promotes_to_a_version_in_the_xdg_root(self):
        result = self.mgr().promote_pins("1.0.3")
        self.assertEqual(result["result"], "promoted")
        self.assertEqual(self.resolved("LA_RAPID_BIN"),
                         str(self.home / XDG_REL / "rapid-mlx-1.0.3" / "bin" / "rapid-mlx"))

    def test_written_pin_is_home_relative_never_the_expanded_home(self):
        self.mgr().promote_pins("1.0.3")
        (line,) = self.pin_lines("LA_RAPID_BIN")
        self.assertIn('"$HOME/.local/share/free-agents/backends/rapid-mlx-1.0.3/bin/rapid-mlx"', line)
        self.assertNotIn(str(self.home), line)

    def test_a_legacy_root_version_still_promotes_in_place(self):
        make_venv(self.home / ".venvs" / "rapid-mlx-1.0.1", "rapid-mlx", "rapid-mlx 1.0.1")
        self.mgr().promote_pins("1.0.1")
        self.assertEqual(self.resolved("LA_RAPID_BIN"),
                         str(self.home / ".venvs" / "rapid-mlx-1.0.1" / "bin" / "rapid-mlx"))

    def test_comment_lines_are_left_untouched(self):
        self.mgr().promote_pins("1.0.3")
        self.assertIn(self.COMMENT, self.live.read_text(encoding="utf-8"))


class VllmPinAcrossRootsTest(_LiveHome):
    def setUp(self):
        super().setUp()
        make_venv(self.home / XDG_REL / "vllm-mlx-0.6.0", "vllm-mlx", "")
        self.write_live('# live\nLA_VENV="$HOME/.venvs/vllm-mlx-0.5.0/bin"\n')

    def mgr(self):
        m = get_manager("vllm-mlx")(home=self.home)
        m._repo = self.repo
        m.validate_environment = lambda version: None
        return m

    def test_promote_points_la_venv_at_the_xdg_root_install(self):
        result = self.mgr().promote_pins("0.6.0")
        self.assertEqual(result["result"], "promoted")
        self.assertEqual(self.resolved("LA_VENV"), str(self.home / XDG_REL / "vllm-mlx-0.6.0" / "bin"))
        (line,) = self.pin_lines("LA_VENV")
        self.assertIn('"$HOME/.local/share/free-agents/backends/vllm-mlx-0.6.0/bin"', line)


if __name__ == "__main__":
    unittest.main()

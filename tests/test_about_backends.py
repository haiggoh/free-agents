#!/usr/bin/env python3
"""tests/test_about_backends.py — About lists ONLY registered backend families (plan Task 9b).

The old gather globbed every dir in ~/.venvs, so the user's own unrelated venvs (hf-py312)
showed up in About, and it read the pin from backend-pin.txt — a file NO code writes — so About
could never show a pin. about_backend_lines() drives the registered managers' real
installed_versions() (health = run the interpreter) and reads the pin from the LIVE config via
live_config_file(repo). Fixtures build REAL venvs in a temp HOME and break them the way a
Homebrew patch upgrade does.

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_about_backends.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "install"))

import session_picker_model as m  # noqa: E402


def make_venv(venvs: Path, prefix: str, version: str) -> Path:
    """A real stdlib venv (no pip: fast) plus a console-script-shaped binary like pip writes."""
    target = venvs / f"{prefix}-{version}"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / prefix
    script.write_text(f"#!{target}/bin/python\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    script.chmod(0o755)
    return target


def break_venv(target: Path) -> None:
    """What a deleted Homebrew keg does to a venv: bin/python* now name a path that is gone."""
    dead = target / "bin" / "python-dead-keg"
    for name in os.listdir(target / "bin"):
        link = target / "bin" / name
        if name.startswith("python") and link.is_symlink():
            link.unlink()
            link.symlink_to(dead)


class AboutBackendLinesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        venvs = self.home / ".venvs"
        venvs.mkdir(parents=True)
        (venvs / "hf-py312").mkdir()  # the user's own venv — NOT a registered backend family
        make_venv(venvs, "rapid-mlx", "0.2.0")
        make_venv(venvs, "rapid-mlx", "0.3.0")
        make_venv(venvs, "vllm-mlx", "0.6.0")
        # the LIVE config pins rapid-mlx 0.2.0 (LA_LIVE_CONFIG_DIR is the manager's own override)
        self.live = Path(self.tmp.name) / "live"
        self.live.mkdir()
        (self.live / "config.local.sh").write_text(
            '# live config\nLA_RAPID_BIN="$HOME/.venvs/rapid-mlx-0.2.0/bin/rapid-mlx"\n',
            encoding="utf-8")
        os.environ["LA_LIVE_CONFIG_DIR"] = str(self.live)
        self.addCleanup(os.environ.pop, "LA_LIVE_CONFIG_DIR", None)

    def tearDown(self):
        self.tmp.cleanup()

    def lines(self):
        return m.about_backend_lines(self.home, ROOT)

    def test_only_registered_families_and_pin_comes_from_the_live_config(self):
        lines = self.lines()
        text = "\n".join(lines)
        self.assertNotIn("hf-py312", text)                      # never a bare glob of ~/.venvs
        self.assertEqual(len(lines), 2)
        rapid = next(line for line in lines if line.startswith("rapid-mlx"))
        self.assertIn("pinned 0.2.0", rapid)                    # from config.local.sh, not backend-pin.txt
        self.assertIn("runs", rapid)                            # real health, not file presence
        vllm = next(line for line in lines if line.startswith("vllm-mlx"))
        self.assertIn("1 installed, not pinned (auto-discovery: 0.6.0)", vllm)

    def test_broken_version_is_flagged_with_the_warning_emoji(self):
        break_venv(self.home / ".venvs" / "rapid-mlx-0.3.0")
        rapid = next(line for line in self.lines() if line.startswith("rapid-mlx"))
        self.assertIn(m.ec.EMOJI_WARNING_STR, rapid)
        self.assertIn("0.3.0", rapid)
        self.assertIn("broken", rapid)
        # the pin line still reports the PINNED version's health, not the broken one's
        self.assertIn("pinned 0.2.0 (runs)", rapid)

    def test_commented_out_pin_line_is_not_a_pin(self):
        # a commented-out LA_RAPID_BIN must not show as the live pin (comment lines are not pins)
        (self.live / "config.local.sh").write_text(
            '# LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-0.3.0/bin/rapid-mlx"\n'
            'LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-0.2.0/bin/rapid-mlx"\n', encoding="utf-8")
        rapid = next(line for line in self.lines() if line.startswith("rapid-mlx"))
        self.assertIn("pinned 0.2.0", rapid)
        self.assertNotIn("pinned 0.3.0", rapid)  # the commented line must not become the pin

    def test_no_live_config_means_no_pin_but_still_lists_families(self):
        os.environ.pop("LA_LIVE_CONFIG_DIR", None)
        (self.live / "config.local.sh").unlink()  # override points at a dir without the file
        lines = self.lines()
        rapid = next(line for line in lines if line.startswith("rapid-mlx"))
        self.assertIn("2 installed, not pinned (auto-discovery: 0.3.0)", rapid)


if __name__ == "__main__":
    unittest.main()

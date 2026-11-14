#!/usr/bin/env python3
"""Repair re-pins a pin it relocated (0.58.0 plan Task 3).

0.50.0 relocates a legacy (~/.venvs) venv into the XDG root on repair, but until now the
live pin was left pointing at the deleted original ("a repair never promotes"), gating
the lane on a path that no longer exists. A repair now re-pins ONLY a pin that references
the relocated venv (result gains "repinned": bool); an unpinned venv's live config stays
byte-identical, and a failed re-pin restores both the venv and the pin byte-for-byte.
Fixture pattern: tests/test_pin_promotion_xdg_root.py + tests/test_backend_repair.py
(temp HOME, LA_LIVE_CONFIG_DIR, real stdlib venvs, stubbed install/validate).
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "install"))

import managers.rapid_mlx  # noqa: E402,F401
from managers.base import ManagerError  # noqa: E402
from managers.rapid_mlx import RapidMLXManager  # noqa: E402

XDG_REL = Path(".local") / "share" / "free-agents" / "backends"


def build_venv(target: Path, binary: str) -> None:
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / binary
    script.write_text(f"#!{target}/bin/python\nprint('{binary}')\n", encoding="utf-8")
    script.chmod(0o755)


class RepairRepinsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.live_dir = root / "live"
        self.live_dir.mkdir()
        self.live = self.live_dir / "config.local.sh"
        env = {k: v for k, v in os.environ.items()
               if k not in ("LA_LIVE_CONFIG_DIR", "_LA_LIVE_FOR", "LA_CONFIG_DIR",
                            "LA_RAPID_BIN", "LA_VENV", "XDG_DATA_HOME",
                            "LA_USER_STATE_DIR")}
        env["HOME"] = str(self.home)
        env["LA_LIVE_CONFIG_DIR"] = str(self.live_dir)
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def write_live(self, text: str) -> None:
        self.live.write_text(text, encoding="utf-8")
        self.live.chmod(0o600)

    def pin_lines(self) -> list[str]:
        return [ln for ln in self.live.read_text(encoding="utf-8").splitlines()
                if ln.lstrip().startswith("LA_RAPID_BIN=")]

    def legacy_venv(self, version="1.0.0") -> Path:
        target = self.home / ".venvs" / f"rapid-mlx-{version}"
        build_venv(target, "rapid-mlx")
        (target / "MARKER").write_text("original", encoding="utf-8")
        return target

    def mgr(self) -> RapidMLXManager:
        m = RapidMLXManager(home=self.home, repo=REPO)
        m.validate_environment = lambda version: None  # pip check needs pip; not under test
        m.install_version = lambda version, **kw: build_venv(m.target_for(version), "rapid-mlx")
        return m

    def test_repair_of_the_pinned_legacy_venv_moves_the_pin(self):
        target = self.legacy_venv()
        self.write_live('LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-1.0.0/bin/rapid-mlx"\n')
        m = self.mgr()
        result = m.repair_version("1.0.0")
        self.assertEqual(result["result"], "repaired")
        self.assertIs(result["repinned"], True)
        (line,) = self.pin_lines()
        self.assertIn('"$HOME/.local/share/free-agents/backends/rapid-mlx-1.0.0/bin/rapid-mlx"', line)
        self.assertNotIn(str(self.home), line)  # pins are written home-relative
        self.assertFalse(target.exists(), "the legacy venv was relocated after success")

    def test_repair_of_an_unpinned_venv_leaves_the_live_config_byte_identical(self):
        self.legacy_venv()
        self.write_live("# no pins\n")
        before = self.live.read_bytes()
        result = self.mgr().repair_version("1.0.0")
        self.assertEqual(result["result"], "repaired")
        self.assertIs(result["repinned"], False)
        self.assertEqual(self.live.read_bytes(), before)

    def test_failed_repin_restores_venv_and_pin(self):
        target = self.legacy_venv()
        self.write_live('LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-1.0.0/bin/rapid-mlx"\n')
        before = self.live.read_bytes()
        m = self.mgr()

        def boom(version, **kw):
            raise ManagerError("pin write failed")
        m.promote_pins = boom
        with self.assertRaisesRegex(ManagerError, "original was restored"):
            m.repair_version("1.0.0")
        self.assertEqual((target / "MARKER").read_text(encoding="utf-8"), "original",
                         "the set-aside original venv is back at the legacy path")
        self.assertEqual(self.live.read_bytes(), before, "the live pin is byte-identical")
        self.assertFalse(m.target_for("1.0.0").exists(), "the half-built XDG venv is deleted")


if __name__ == "__main__":
    unittest.main()

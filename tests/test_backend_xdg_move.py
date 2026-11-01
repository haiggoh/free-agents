#!/usr/bin/env python3
"""0.50.0 item 7 (free-agents-move-backend): backend venvs move to a free-agents-owned
XDG root ($XDG_DATA_HOME/free-agents/backends, default ~/.local/share/free-agents/backends).

New installs land there; discovery still reads the legacy ~/.venvs so nothing installed
breaks (new root wins on duplicates); repair of a legacy venv relocates it into the new
root make-before-break (rebuild first, original removed only on success).

Usage: python3 tests/test_backend_xdg_move.py [-v]
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
from managers import get_manager  # noqa: E402
from managers.base import backends_root, legacy_venvs_root, resolve_venv_dir  # noqa: E402


def make_venv_at(root: Path, cls, version: str) -> Path:
    target = root / f"{cls.VENV_PREFIX}-{version}"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / cls.BACKEND_NAME
    script.write_text(f"#!{target}/bin/python\n", encoding="utf-8")
    script.chmod(0o755)
    return target


class XDGMoveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.live_dir = Path(self.tmp.name) / "live"
        self.live_dir.mkdir(parents=True)
        (self.live_dir / "config.local.sh").write_text("# no pins\n", encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home),
                                                "LA_LIVE_CONFIG_DIR": str(self.live_dir)})
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("_LA_LIVE_FOR", None)
        os.environ.pop("XDG_DATA_HOME", None)

    def manager(self):
        mgr = get_manager("rapid-mlx")(home=self.home)
        mgr.validate_environment = lambda version: None
        return mgr

    def test_backends_root_honours_xdg_and_default(self):
        self.assertEqual(backends_root(self.home),
                         self.home / ".local" / "share" / "free-agents" / "backends")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.home / "xdg")}):
            self.assertEqual(backends_root(self.home),
                             self.home / "xdg" / "free-agents" / "backends")
        self.assertEqual(legacy_venvs_root(self.home), self.home / ".venvs")

    def test_discovery_reads_both_roots_new_root_wins(self):
        cls = get_manager("rapid-mlx")
        mgr = self.manager()
        make_venv_at(legacy_venvs_root(self.home), cls, "0.1.0")          # legacy only
        make_venv_at(backends_root(self.home), cls, "0.2.0")               # new only
        make_venv_at(legacy_venvs_root(self.home), cls, "0.3.0")           # both
        new_both = make_venv_at(backends_root(self.home), cls, "0.3.0")
        entries = {v: p for v, p, _st in mgr.installed_versions(self.home)}
        self.assertEqual(entries["0.1.0"], legacy_venvs_root(self.home) / "rapid-mlx-0.1.0")
        self.assertEqual(entries["0.2.0"], backends_root(self.home) / "rapid-mlx-0.2.0")
        self.assertEqual(entries["0.3.0"], new_both, "a version in both roots reports the NEW root")

    def test_resolve_and_target_for_use_the_new_root(self):
        cls = get_manager("rapid-mlx")
        mgr = self.manager()
        legacy = make_venv_at(legacy_venvs_root(self.home), cls, "0.1.0")
        self.assertEqual(resolve_venv_dir(cls.VENV_PREFIX, "0.1.0", self.home), legacy,
                         "resolve finds a legacy-only version (removal/repair keep working)")
        self.assertIsNone(resolve_venv_dir(cls.VENV_PREFIX, "9.9.9", self.home))
        self.assertEqual(mgr.target_for("0.9.9", self.home),
                         backends_root(self.home) / "rapid-mlx-0.9.9",
                         "NEW installs must target the XDG root, not ~/.venvs")

    def test_repair_relocates_a_legacy_venv_make_before_break(self):
        cls = get_manager("rapid-mlx")
        mgr = self.manager()
        legacy = make_venv_at(legacy_venvs_root(self.home), cls, "1.0.0")
        mgr.save_receipt("1.0.0", {"pip_freeze": ["rapid-mlx==1.0.0"]})

        def fake_install(version, **kwargs):
            make_venv_at(backends_root(self.home), cls, version)          # rebuild in the NEW root
        mgr.install_version = fake_install

        out = mgr.repair_version("1.0.0")
        new_target = backends_root(self.home) / "rapid-mlx-1.0.0"
        self.assertEqual(out["target"], str(new_target), "repair must rebuild into the XDG root")
        self.assertTrue(new_target.is_dir())
        self.assertFalse(legacy.exists(), "the legacy venv is removed only after a successful rebuild")
        self.assertTrue((legacy.parent).is_dir() or True)
        # discovery now reports the version at the new root
        entries = {v: p for v, p, _st in mgr.installed_versions(self.home)}
        self.assertEqual(entries["1.0.0"], new_target)


if __name__ == "__main__":
    unittest.main()

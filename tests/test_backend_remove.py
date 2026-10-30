#!/usr/bin/env python3
"""`remove` actually removes — for every venv backend — and refuses only for a stated reason.

Until 0.49.0 the suite held refusal tests only, which is how a remove that could NEVER succeed
shipped green (the receipt reader required a key the writer never wrote). And remove existed for
rapid-mlx alone: the dead ~/.venvs/litellm-1.102.1 could not be removed through the manager at all.
Every case uses a temp HOME, real stdlib venvs and a temp live config; nothing touches ~/.venvs.
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

import managers.litellm  # noqa: E402,F401
import managers.rapid_mlx  # noqa: E402,F401
import managers.vllm_mlx  # noqa: E402,F401
from managers import get_manager  # noqa: E402
from managers.base import ManagerError  # noqa: E402

VENV_BACKENDS = ("rapid-mlx", "vllm-mlx", "litellm")


def make_venv(home: Path, cls, version: str) -> Path:
    target = home / ".venvs" / f"{cls.VENV_PREFIX}-{version}"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / cls.BACKEND_NAME
    script.write_text(f"#!{target}/bin/python\n", encoding="utf-8")
    script.chmod(0o755)
    return target


def kill_base(target: Path, home: Path) -> None:
    for link in (target / "bin").iterdir():
        if link.name.startswith("python") and link.is_symlink():
            link.unlink()
            link.symlink_to(home / "Cellar" / "gone" / "python3.14")


class RemoveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.live_dir = Path(self.tmp.name) / "live"
        self.live_dir.mkdir(parents=True)
        (self.live_dir / "config.local.sh").write_text("# no pins\n", encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "LA_LIVE_CONFIG_DIR": str(self.live_dir)})
        self.env.start()
        os.environ.pop("_LA_LIVE_FOR", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def manager(self, name):
        mgr = get_manager(name)(home=self.home)
        mgr.validate_environment = lambda version: None  # pip check needs pip; not under test
        return mgr

    def seed(self, name, *versions):
        cls = get_manager(name)
        mgr = self.manager(name)
        for v in versions:
            make_venv(self.home, cls, v)
            mgr.save_receipt(v, {"pip_freeze": [f"{cls.PACKAGE_NAME}=={v}"]})
        return cls, mgr

    def test_removes_a_healthy_unpinned_version_for_every_venv_backend(self):
        for name in VENV_BACKENDS:
            with self.subTest(backend=name):
                cls, mgr = self.seed(name, "1.0.0", "1.1.0")
                mgr.remove_version("1.0.0", yes=True, repo=REPO)
                self.assertFalse((self.home / ".venvs" / f"{cls.VENV_PREFIX}-1.0.0").exists())
                self.assertTrue((self.home / ".venvs" / f"{cls.VENV_PREFIX}-1.1.0").exists())

    def test_a_broken_venv_is_removable_without_validation_or_receipt(self):
        cls, mgr = self.seed("rapid-mlx", "1.1.0")
        dead = make_venv(self.home, cls, "1.0.0")
        kill_base(dead, self.home)

        def must_not_validate(version):
            raise AssertionError("validate_environment called on a broken venv")
        mgr.validate_environment = must_not_validate
        mgr.remove_version("1.0.0", yes=True, repo=REPO)
        self.assertFalse(dead.exists())

    def test_the_live_pinned_version_is_refused(self):
        cls, mgr = self.seed("rapid-mlx", "1.0.0", "1.1.0")
        (self.live_dir / "config.local.sh").write_text(
            'LA_RAPID_BIN="$HOME/.venvs/rapid-mlx-1.0.0/bin/rapid-mlx"\n', encoding="utf-8")
        with self.assertRaisesRegex(ManagerError, "pinned"):
            mgr.remove_version("1.0.0", yes=True, repo=REPO)
        self.assertTrue((self.home / ".venvs" / "rapid-mlx-1.0.0").exists())

    def test_the_last_complete_rapid_version_is_kept(self):
        cls, mgr = self.seed("rapid-mlx", "1.0.0")
        with self.assertRaisesRegex(ManagerError, "only installed"):
            mgr.remove_version("1.0.0", yes=True, repo=REPO)

    def test_litellm_venvs_have_no_keep_one_rule(self):
        # remote sessions run the pipx litellm; the manager's venvs are leftovers (user 2026-10-30)
        cls, mgr = self.seed("litellm", "1.0.0")
        mgr.remove_version("1.0.0", yes=True, repo=REPO)
        self.assertFalse((self.home / ".venvs" / "litellm-1.0.0").exists())

    def test_dry_run_deletes_nothing(self):
        cls, mgr = self.seed("vllm-mlx", "1.0.0", "1.1.0")
        mgr.remove_version("1.0.0", yes=True, repo=REPO, dry_run=True)
        self.assertTrue((self.home / ".venvs" / "vllm-mlx-1.0.0").exists())


class SnapshotTest(RemoveTest):
    def test_snapshot_then_remove_works_for_vllm_mlx(self):
        # The loop that made deletion impossible: remove demands a receipt -> snapshot must make one
        # that remove then accepts.
        cls = get_manager("vllm-mlx")
        for v in ("1.0.0", "1.1.0"):
            make_venv(self.home, cls, v)
        mgr = get_manager("vllm-mlx")(home=self.home)
        mgr.validate_environment = lambda version: type("R", (), {"metadata": {"pip_freeze": [f"vllm-mlx=={version}"]}})()
        self.assertIsNone(mgr.locked_requirements("1.0.0"))
        with self.assertRaisesRegex(ManagerError, "run snapshot"):
            mgr.remove_version("1.0.0", yes=True, repo=REPO)
        mgr.snapshot_environment("1.0.0")
        mgr.remove_version("1.0.0", yes=True, repo=REPO)
        self.assertFalse((self.home / ".venvs" / "vllm-mlx-1.0.0").exists())


if __name__ == "__main__":
    unittest.main()

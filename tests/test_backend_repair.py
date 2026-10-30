#!/usr/bin/env python3
"""`repair` rebuilds a broken (or keg-fragile) venv in place and NEVER leaves the user with less.

The old venv is moved aside, the version is rebuilt at its real path (pip writes absolute
shebangs, so a venv built elsewhere and renamed would point at the wrong path), the result is
RUN, and only then is the old one deleted. Any failure puts the original back. install_version is
stubbed with one that builds a real stdlib venv, so no network and no pip are involved.
"""

import json
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


def build_venv(target: Path, binary: str) -> None:
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    script = target / "bin" / binary
    script.write_text(f"#!{target}/bin/python\n", encoding="utf-8")
    script.chmod(0o755)


def kill_base(target: Path) -> None:
    for link in (target / "bin").iterdir():
        if link.name.startswith("python") and link.is_symlink():
            link.unlink()
            link.symlink_to(target.parent / "gone" / "python3.14")


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        live = Path(self.tmp.name) / "live"
        live.mkdir(parents=True)
        (live / "config.local.sh").write_text("# no pins\n", encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"HOME": str(self.home), "LA_LIVE_CONFIG_DIR": str(live)})
        self.env.start()
        os.environ.pop("_LA_LIVE_FOR", None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def broken(self, name="rapid-mlx", version="1.0.0", receipt=True):
        cls = get_manager(name)
        mgr = cls(home=self.home)
        mgr.validate_environment = lambda version: None
        target = self.home / ".venvs" / f"{cls.VENV_PREFIX}-{version}"
        build_venv(target, cls.BACKEND_NAME)
        (target / "MARKER").write_text("original", encoding="utf-8")
        kill_base(target)
        if receipt:
            mgr.save_receipt(version, {"pip_freeze": [f"{cls.PACKAGE_NAME}=={version}"]})
        return cls, mgr, target

    def test_repairs_a_broken_venv_in_place_for_every_venv_backend(self):
        for name in ("rapid-mlx", "vllm-mlx", "litellm"):
            with self.subTest(backend=name):
                cls, mgr, target = self.broken(name)
                calls = []

                def install(version, **kw):
                    calls.append(kw)
                    build_venv(target, cls.BACKEND_NAME)
                mgr.install_version = install
                result = mgr.repair_version("1.0.0")
                self.assertEqual(result["result"], "repaired")
                self.assertEqual(mgr.installed_versions()[0][2], "complete")
                self.assertFalse((target / "MARKER").exists(), "the old venv was replaced")
                self.assertEqual([p.name for p in target.parent.iterdir() if ".repair-" in p.name], [],
                                 "the set-aside original is deleted after success")
                if name == "rapid-mlx":
                    self.assertTrue(calls[0].get("skip_pin_update"), "repair never promotes")

    def test_a_failed_rebuild_restores_the_original(self):
        cls, mgr, target = self.broken()

        def install(version, **kw):
            target.mkdir(parents=True, exist_ok=True)
            raise ManagerError("pip exploded")
        mgr.install_version = install
        with self.assertRaisesRegex(ManagerError, "original was restored"):
            mgr.repair_version("1.0.0")
        self.assertEqual((target / "MARKER").read_text(encoding="utf-8"), "original")

    def test_a_rebuild_that_does_not_run_is_rolled_back(self):
        cls, mgr, target = self.broken()

        def install(version, **kw):
            build_venv(target, cls.BACKEND_NAME)
            kill_base(target)                       # still dead: must not be accepted
        mgr.install_version = install
        with self.assertRaisesRegex(ManagerError, "original was restored"):
            mgr.repair_version("1.0.0")
        self.assertEqual((target / "MARKER").read_text(encoding="utf-8"), "original")

    def test_a_version_used_by_a_running_process_is_refused(self):
        cls, mgr, target = self.broken()
        mgr._running_references = lambda t: [f"PID 1: {t}/bin/rapid-mlx serve"]
        with self.assertRaisesRegex(ManagerError, "running"):
            mgr.repair_version("1.0.0")
        self.assertEqual((target / "MARKER").read_text(encoding="utf-8"), "original")

    def test_mode_reports_locked_recreation_or_fresh_resolution(self):
        _, mgr, _ = self.broken(receipt=True)
        self.assertEqual(mgr.repair_version("1.0.0", dry_run=True)["mode"], "locked_recreation")
        _, mgr2, _ = self.broken(name="vllm-mlx", receipt=False)
        self.assertEqual(mgr2.repair_version("1.0.0", dry_run=True)["mode"], "fresh_resolution")

    def test_health_report_flags_broken_and_live_pinned(self):
        cls, mgr, target = self.broken()
        report = {r["version"]: r for r in mgr.health_report()}
        self.assertEqual(report["1.0.0"]["state"], "broken")
        self.assertIn("reason", report["1.0.0"])


class CliTest(unittest.TestCase):
    def run_cli(self, *args, env=None):
        return subprocess.run([sys.executable, str(REPO / "install" / "manage-backend.py"), *args],
                              capture_output=True, text=True, env=env)

    def test_repair_and_health_have_help(self):
        for cmd in ("repair", "health"):
            with self.subTest(cmd=cmd):
                r = self.run_cli("--backend", "rapid-mlx", cmd, "--help")
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_health_exits_1_when_a_venv_is_broken(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = home / ".venvs" / "rapid-mlx-1.0.0"
            build_venv(target, "rapid-mlx")
            env = {**os.environ, "HOME": str(home), "LA_LIVE_CONFIG_DIR": str(home)}
            env.pop("_LA_LIVE_FOR", None)
            ok = self.run_cli("--backend", "rapid-mlx", "--json", "health", env=env)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(json.loads(ok.stdout)[0]["state"], "complete")
            kill_base(target)
            bad = self.run_cli("--backend", "rapid-mlx", "health", env=env)
            self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
            self.assertIn("repair 1.0.0", bad.stdout)


if __name__ == "__main__":
    unittest.main()

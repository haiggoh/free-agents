#!/usr/bin/env python3
"""RapidMLXManager.remove_version guards (0.34.3).

0.34.x shipped `remove` with NO test: `_running_references` imported psutil, which the
python3 running the manager does not have, so every removal ended in a traceback. These
tests run the real method against a temp venv root — never a real ~/.venvs entry.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "install"))
from managers.base import ManagerError  # noqa: E402
from managers.rapid_mlx import RapidMLXManager  # noqa: E402


def fake_venv(root: Path, version: str, complete: bool = True) -> Path:
    path = root / f"{RapidMLXManager.VENV_PREFIX}-{version}"
    (path / "bin").mkdir(parents=True)
    if complete:
        exe = path / "bin" / "rapid-mlx"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    return path


class RemoveGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        self.mgr = RapidMLXManager(home=home)
        self.root = self.mgr._venv_root(home)      # the manager's own layout, not a guess
        self.root.mkdir(parents=True)

    def test_only_complete_version_is_refused_as_core_dependency(self):
        fake_venv(self.root, "0.16.1")
        fake_venv(self.root, "0.15.0", complete=False)        # an incomplete one does not count
        with self.assertRaises(ManagerError) as cm:
            self.mgr.remove_version("0.16.1", yes=True)
        self.assertIn("core dependency of free-agents", str(cm.exception))
        self.assertTrue((self.root / "rapid-mlx-0.16.1").is_dir())

    def test_running_references_needs_no_psutil(self):
        # The manager must work on a python without psutil: block the import outright.
        target = fake_venv(self.root, "0.15.3")
        with mock.patch.dict(sys.modules, {"psutil": None}):
            self.assertEqual(self.mgr._running_references(target), [])

    def test_running_references_finds_a_process_using_the_venv(self):
        import subprocess
        target = fake_venv(self.root, "0.15.3")
        # argv[0] carries the venv path, as a real `rapid-mlx serve` would.
        proc = subprocess.Popen(["/usr/bin/perl", "-e", "sleep 30", str(target / "bin" / "rapid-mlx")])
        self.addCleanup(proc.kill)
        found = self.mgr._running_references(target)
        self.assertTrue(any(str(proc.pid) in f for f in found), found)

    def test_running_references_does_not_match_a_longer_version_prefix(self):
        # rapid-mlx-0.16.10 running must NOT block repairing/removing rapid-mlx-0.16.1
        # (measured false refusal 2026-10-30: plain `in` matched the prefix)
        import subprocess
        target = fake_venv(self.root, "0.16.1")
        other = fake_venv(self.root, "0.16.10")
        proc = subprocess.Popen(["/usr/bin/perl", "-e", "sleep 30", str(other / "bin" / "rapid-mlx")])
        self.addCleanup(proc.kill)
        self.assertEqual(self.mgr._running_references(target), [])
        found = self.mgr._running_references(other)
        self.assertTrue(any(str(proc.pid) in f for f in found), found)

    def test_failed_ps_refuses_rather_than_allowing(self):
        target = fake_venv(self.root, "0.15.3")
        with mock.patch("managers.rapid_mlx.subprocess.run", side_effect=OSError("no ps")):
            with self.assertRaises(ManagerError):
                self.mgr._running_references(target)


if __name__ == "__main__":
    unittest.main()

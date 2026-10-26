#!/usr/bin/env python3
"""tests/test_choose_python_ceiling.py — a venv is never built on a Python its backend cannot use.

Measured 2026-10-23 and 2026-10-26: Homebrew moved `python3` to 3.15; the picker runs
manage-backend.py as `python3`, choose_python() took sys.executable first, and the rapid-mlx venv
was built on 3.15 — where torch publishes no wheels (`No matching distribution found for
torch>=2.3.0`). Every upgrade failed. Stub interpreters stand in for real ones, so the verdict does
not depend on which Pythons this machine happens to have.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))
from managers import get_manager_instance  # noqa: E402
import managers.rapid_mlx  # noqa: E402,F401  (registers the manager)
import managers.vllm_mlx  # noqa: E402,F401
from managers.base import ManagerError  # noqa: E402


class Ceiling(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LA_BASE_PYTHON", None)

    def stub(self, version: str) -> str:
        """An executable that answers the venv/version probe like a real Python of `version`."""
        p = Path(self.tmp.name) / f"python{version}"
        p.write_text(f"#!/bin/sh\necho {version}\n")
        p.chmod(0o755)
        return str(p)

    def pick(self, name, candidates, explicit=None):
        mgr = get_manager_instance(name)
        with mock.patch.object(type(mgr), "python_candidates", lambda self: candidates):
            return mgr.choose_python(explicit)

    def test_a_too_new_interpreter_is_skipped_for_torch_backends(self):
        new, ok = self.stub("3.15"), self.stub("3.14")
        for name in ("rapid-mlx", "vllm-mlx"):
            with self.subTest(name):
                self.assertEqual(self.pick(name, [new, ok]), Path(ok))

    def test_only_too_new_interpreters_is_a_named_error_not_a_doomed_venv(self):
        with self.assertRaises(ManagerError) as cm:
            self.pick("rapid-mlx", [self.stub("3.15")])
        self.assertIn("3.14", str(cm.exception))

    def test_an_explicit_choice_still_wins(self):
        # The operator who passes --python or LA_BASE_PYTHON has decided; the ceiling guards the
        # implicit default only.
        new = self.stub("3.15")
        self.assertEqual(self.pick("rapid-mlx", [self.stub("3.14")], explicit=new), Path(new))
        os.environ["LA_BASE_PYTHON"] = new
        self.assertEqual(self.pick("vllm-mlx", [self.stub("3.14")]), Path(new))

    def test_a_stable_symlink_is_returned_as_the_symlink_not_its_versioned_target(self):
        # A venv records its base interpreter's path. Resolving /opt/homebrew/bin/python3.14 to
        # Cellar/python@3.14/3.14.8_2/... ties the venv to one patch keg, which `brew cleanup`
        # deletes after the next patch upgrade (measured 2026-10-26: rapid-mlx-0.16.1 survives
        # only because cleanup has not run yet).
        real = self.stub("3.14")
        link = Path(self.tmp.name) / "stable-python3.14"
        link.symlink_to(real)
        self.assertEqual(self.pick("rapid-mlx", [str(link)]), link)

    def test_the_default_candidates_reach_past_a_too_new_python3(self):
        mgr = get_manager_instance("rapid-mlx")
        names = [Path(c).name for c in mgr.python_candidates()]
        for v in ("python3.14", "python3.13", "python3.12"):
            self.assertIn(v, names)


if __name__ == "__main__":
    unittest.main()

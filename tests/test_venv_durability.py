#!/usr/bin/env python3
"""A venv built by the manager survives a Homebrew Python PATCH upgrade.

Measured 2026-10-30: venvs whose pyvenv.cfg `home` and bin/python3.X link name a patch keg
(…/Cellar/python@3.14/3.14.8_2/…) die when `brew upgrade` installs 3.14.10 and deletes the old
keg (rapid-mlx-0.16.1). Venvs whose base is …/opt/python@3.14/bin survived the same upgrade
(rapid-mlx-0.16.9), because Homebrew repoints `opt/` at the new keg. The cause of keg-based homes
was not reproducible (four probes), so the guard is outcome-based: normalize after creation.

These tests build a FAKE Homebrew prefix whose interpreter links resolve to this test's own
Python, then perform the real event — new keg, `opt` relinked, old keg deleted — and require the
venv to still run.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))

from managers import durability  # noqa: E402

MM = "%d.%d" % sys.version_info[:2]
EXE = f"python{MM}"


class FakeBrew:
    """<root>/Cellar/python@MM/<patch>/{bin,Frameworks/.../bin}/pythonMM -> sys.executable, and
    <root>/opt/python@MM -> ../Cellar/python@MM/<patch>, exactly Homebrew's shape."""

    def __init__(self, root: Path):
        self.root = root
        self.opt = root / "opt" / f"python@{MM}"

    def keg(self, patch: str) -> Path:
        return self.root / "Cellar" / f"python@{MM}" / patch

    def install(self, patch: str) -> None:
        keg = self.keg(patch)
        for d in (keg / "bin", keg / "Frameworks" / "Python.framework" / "Versions" / MM / "bin"):
            d.mkdir(parents=True, exist_ok=True)
            (d / EXE).symlink_to(os.path.realpath(sys.executable))
        self.opt.parent.mkdir(parents=True, exist_ok=True)
        if self.opt.is_symlink():
            self.opt.unlink()
        self.opt.symlink_to(Path("..") / "Cellar" / f"python@{MM}" / patch)

    def framework_bin(self, patch: str) -> Path:
        return self.keg(patch) / "Frameworks" / "Python.framework" / "Versions" / MM / "bin"

    def uninstall(self, patch: str) -> None:
        import shutil
        shutil.rmtree(self.keg(patch))


def keg_based_venv(target: Path, brew: FakeBrew, patch: str) -> None:
    """A venv shaped like the dead 0.16.1 / the fragile 1.0.0: home + link on the patch keg."""
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    fw = brew.framework_bin(patch)
    for name in os.listdir(target / "bin"):
        link = target / "bin" / name
        if link.is_symlink() and name == EXE or (link.is_symlink() and not os.readlink(link).startswith("python")):
            link.unlink()
            link.symlink_to(fw / EXE)
    cfg = target / "pyvenv.cfg"
    lines = [ln for ln in cfg.read_text().splitlines() if not ln.startswith(("home =", "executable ="))]
    cfg.write_text("\n".join([f"home = {fw}", f"executable = {fw / EXE}", *lines]) + "\n")


def runs(target: Path) -> bool:
    try:
        return subprocess.run([str(target / "bin" / "python"), "-c", "import encodings"],
                              capture_output=True).returncode == 0
    except OSError:  # a dangling bin/python cannot even be exec'd — that IS "does not run"
        return False


class DurabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.brew = FakeBrew(root / "brew")
        self.brew.install("3.0.1")
        self.venv = root / "venv"
        self.env = mock.patch.dict(os.environ, {"HOMEBREW_PREFIX": str(self.brew.root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_the_fragile_shape_dies_on_a_patch_upgrade(self):
        # The instrument check: WITHOUT normalisation the fixture reproduces the 0.16.1 death.
        keg_based_venv(self.venv, self.brew, "3.0.1")
        self.assertTrue(runs(self.venv))
        self.brew.install("3.0.2")
        self.brew.uninstall("3.0.1")
        self.assertFalse(runs(self.venv))

    def test_a_normalized_venv_survives_the_same_upgrade(self):
        keg_based_venv(self.venv, self.brew, "3.0.1")
        self.assertEqual(durability.venv_durability(self.venv), "pinned-keg")
        self.assertEqual(durability.normalize_venv_base(self.venv), "normalized")
        self.assertEqual(durability.venv_durability(self.venv), "durable")
        self.assertIn(f"home = {self.brew.opt}/bin", (self.venv / "pyvenv.cfg").read_text())
        self.brew.install("3.0.2")
        self.brew.uninstall("3.0.1")
        self.assertTrue(runs(self.venv), "a normalized venv must survive a patch upgrade")

    def test_an_already_durable_venv_is_left_byte_identical(self):
        keg_based_venv(self.venv, self.brew, "3.0.1")
        durability.normalize_venv_base(self.venv)
        before = (self.venv / "pyvenv.cfg").read_bytes()
        self.assertEqual(durability.normalize_venv_base(self.venv), "durable")
        self.assertEqual((self.venv / "pyvenv.cfg").read_bytes(), before)

    def test_a_normalisation_that_would_not_run_is_rolled_back(self):
        keg_based_venv(self.venv, self.brew, "3.0.1")
        before = (self.venv / "pyvenv.cfg").read_bytes()

        def failing(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 1, "", "boom")
        self.assertEqual(durability.normalize_venv_base(self.venv, runner=failing), "pinned-keg")
        self.assertEqual((self.venv / "pyvenv.cfg").read_bytes(), before)
        self.assertTrue(runs(self.venv), "the rollback restored a working venv")

    def test_durable_python_maps_a_keg_or_bin_path_to_opt(self):
        keg_exe = self.brew.framework_bin("3.0.1") / EXE
        self.assertEqual(durability.durable_python(keg_exe), self.brew.opt / "bin" / EXE)
        (self.brew.root / "bin").mkdir()
        (self.brew.root / "bin" / EXE).symlink_to(keg_exe)
        self.assertEqual(durability.durable_python(self.brew.root / "bin" / EXE), self.brew.opt / "bin" / EXE)

    def test_a_non_homebrew_python_passes_through(self):
        other = Path("/usr/bin/python3")
        self.assertEqual(durability.durable_python(other), other)

    def test_choose_python_returns_the_patch_stable_spelling(self):
        import managers.rapid_mlx  # noqa: F401
        from managers import get_manager
        keg_exe = self.brew.framework_bin("3.0.1") / EXE
        chosen = get_manager("rapid-mlx")(home=Path(self.tmp.name)).choose_python(str(keg_exe))
        self.assertEqual(chosen, self.brew.opt / "bin" / EXE)

    def test_cli_has_help_and_rejects_unknown_flags(self):
        script = Path(durability.__file__)
        ok = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0)
        self.assertIn("normalize", ok.stdout)
        bad = subprocess.run([sys.executable, str(script), "--nope"], capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)


if __name__ == "__main__":
    unittest.main()

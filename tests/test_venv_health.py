#!/usr/bin/env python3
"""venv_health runs the interpreter — a venv whose base Python is gone is "broken", not "complete".

Measured 2026-10-30: ~/.venvs/rapid-mlx-0.16.1 kept an executable bin/rapid-mlx while its base
interpreter (a Homebrew patch keg) had been deleted by an upgrade. Every check in the manager and
the launcher looked at file presence / the executable bit, so the dead venv was listed "complete",
auto-discovered, pinned, and every Rapid launch stalled. These tests build REAL venvs in a temp
dir and break them the way Homebrew does.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))

from managers._common import VenvHealth, venv_health  # noqa: E402


def make_venv(root: Path, binary: str = "rapid-mlx") -> Path:
    """A real stdlib venv (no pip: fast) plus a console-script-shaped binary like pip writes."""
    target = root / f"{binary}-0.1.0"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)], check=True)
    if binary.startswith("python"):
        # bin/python already exists (a symlink to the base interpreter) and IS the probe
        # binary — writing the console script here would follow the symlink and clobber the
        # REAL base interpreter (measured damage 2026-10-30: the python@3.12 framework binary).
        return target
    script = target / "bin" / binary
    assert not script.is_symlink(), f"refusing to write through symlink {script}"
    script.write_text(f"#!{target}/bin/python\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    script.chmod(0o755)
    return target


def repoint_python(target: Path, dead: Path) -> None:
    """What a deleted Homebrew keg does to a venv: bin/python* now name a path that is gone."""
    for name in os.listdir(target / "bin"):
        link = target / "bin" / name
        if name.startswith("python") and link.is_symlink():
            link.unlink()
            link.symlink_to(dead)


class VenvHealthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_healthy_venv_is_complete(self):
        target = make_venv(self.root)
        self.assertEqual(venv_health(target, "rapid-mlx"), VenvHealth("complete", ""))

    def test_missing_binary_is_incomplete(self):
        target = make_venv(self.root)
        (target / "bin" / "rapid-mlx").unlink()
        self.assertEqual(venv_health(target, "rapid-mlx").state, "incomplete")

    def test_dead_bin_python_symlink_is_broken_not_incomplete(self):
        # the venv exists but its bin/python names a deleted keg — "broken", never "incomplete"
        target = make_venv(self.root)
        repoint_python(target, self.root / "Cellar" / "gone" / "python3.14")
        self.assertEqual(venv_health(target, "python").state, "broken")

    def test_deleted_base_interpreter_is_broken_and_names_the_path(self):
        target = make_venv(self.root)
        dead = self.root / "Cellar" / "python@3.14" / "3.14.8_2" / "bin" / "python3.14"
        repoint_python(target, dead)
        health = venv_health(target, "rapid-mlx")
        self.assertEqual(health.state, "broken")
        self.assertIn(str(target / "bin" / "python"), health.reason)

    def test_binary_shebang_naming_a_deleted_interpreter_is_broken(self):
        target = make_venv(self.root)
        gone = self.root / "gone" / "python3.14"
        (target / "bin" / "rapid-mlx").write_text(f"#!{gone}\n", encoding="utf-8")
        health = venv_health(target, "rapid-mlx")
        self.assertEqual(health.state, "broken")
        self.assertIn(str(gone), health.reason)

    def test_interpreter_that_cannot_import_its_stdlib_is_broken(self):
        # Half-dead: bin/python resolves, but running it fails (stdlib/prefix gone).
        target = make_venv(self.root)

        def failing_runner(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 1, "", "ModuleNotFoundError: encodings")

        health = venv_health(target, "rapid-mlx", runner=failing_runner)
        self.assertEqual(health.state, "broken")
        self.assertIn("encodings", health.reason)

    def test_binary_without_a_venv_interpreter_is_judged_on_the_binary_alone(self):
        # Binary-install backends (llama.cpp) have no bin/python: present + executable = complete.
        target = self.root / "llama-cpp-b1"
        (target / "bin").mkdir(parents=True)
        binary = target / "bin" / "llama-server"
        binary.write_bytes(b"\xcf\xfa\xed\xfe")  # Mach-O magic, not a script
        binary.chmod(0o755)
        self.assertEqual(venv_health(target, "llama-server").state, "complete")


def all_managers():
    """Every registered manager, found by importing every module in the package (never a typed
    list: a backend added later must be covered without anyone remembering to add it here)."""
    import importlib
    import pkgutil

    import managers

    for info in pkgutil.iter_modules(managers.__path__):
        if not info.name.startswith("_") and info.name not in ("base", "mixins"):
            importlib.import_module(f"managers.{info.name}")
    return [managers.get_manager(name) for name in managers.list_managers()]


class InstalledVersionsReportBrokenTest(unittest.TestCase):
    """The choke point: installed_versions() feeds the picker, remove's "another complete version
    remains" guard and discovery. A dead venv must come back "broken" from EVERY manager."""

    def test_registry_is_not_empty(self):
        # An empty registry would make the loop below pass over nothing.
        self.assertGreaterEqual(len(all_managers()), 6)

    def test_every_manager_reports_a_dead_venv_as_broken(self):
        saved = os.environ.pop("LA_USER_STATE_DIR", None)  # CUDA managers root elsewhere by design
        self.addCleanup(lambda: saved is not None and os.environ.__setitem__("LA_USER_STATE_DIR", saved))
        for cls in all_managers():
            with self.subTest(manager=cls.BACKEND_NAME), tempfile.TemporaryDirectory() as tmp:
                home = Path(tmp)
                version = next(v for v in ("9.9.9", "b9999", "v9.9.9") if cls.VERSION_RE.fullmatch(v))
                mgr = cls(home=home)
                root = mgr.venv_root          # ~/.venvs for the Mac managers, the CUDA state dir for cuda-*
                root.mkdir(parents=True)
                target = make_venv(root, cls.BACKEND_NAME)
                target = target.rename(root / f"{cls.VENV_PREFIX}-{version}")
                binary = getattr(cls, "BINARY", cls.BACKEND_NAME)   # cuda managers probe bin/vllm etc.
                script = target / "bin" / binary
                if not binary.startswith("python"):   # never write through the bin/python symlink
                    assert not script.is_symlink(), f"refusing to write through symlink {script}"
                    script.write_text(f"#!{target}/bin/python\n", encoding="utf-8")
                    script.chmod(0o755)               # venv_health requires X_OK, not just presence
                repoint_python(target, home / "Cellar" / "gone" / "python3.14")
                states = {v: st for v, _, st in mgr.installed_versions()}
                self.assertEqual(states.get(version), "broken", states)


if __name__ == "__main__":
    unittest.main()

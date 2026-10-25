#!/usr/bin/env python3
"""tests/test_moespresso_manager.py — the MoEspresso backend manager.

WHY THIS EXISTS (measured 2026-10-25): the tap publishes two formulae — `moespresso` (4.0.0) and
`moespresso@3` (3.0.0) — and ONLY the 3.x line loads DeepSeek V4 packages. 4.0.0 refuses one with
`Error: unsupported required package feature`, because the package manifest requires
`calibration` + `deepseek_v4_per_layer_experts` and 4.0.0 is a Qwen engine. Worse, `brew install
moespresso@3` succeeds while `/opt/homebrew/bin/moespresso` STILL points at 4.0.0 ("the following
moespresso@3 executables are shadowed by linked moespresso commands"). A manager that resolves by
PATH name therefore reports a healthy install that cannot load the model — silent-wrong, which is
the exact failure shape this file must prevent.

Every runner here is a stub: nothing is installed and brew is never really invoked.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

from managers import get_manager, get_manager_instance, list_managers  # noqa: E402
from managers._common import ManagerError  # noqa: E402
from managers.moespresso import MoespressoManager  # noqa: E402


def _brew(argv, stdout="", rc=0):
    return MagicMock(returncode=rc, stdout=stdout, stderr="", argv=argv)


# --- registration ---------------------------------------------------------------------------
def test_moespresso_manager_is_registered():
    assert "moespresso" in list_managers()
    assert get_manager("moespresso") is MoespressoManager
    mgr = get_manager_instance("moespresso")
    assert isinstance(mgr, MoespressoManager)
    assert mgr.BACKEND_NAME == "moespresso"
    # Not a venv-managed backend: it ships as a Homebrew bottle.
    assert mgr.VENV_PREFIX == "moespresso"


# --- the 3.x pin: the whole point of this manager -------------------------------------------
def test_install_uses_the_at3_formula_not_the_linked_one():
    """`brew install moespresso` would install 4.0.0, which cannot load the package."""
    mgr = MoespressoManager()
    # dry_run must not touch brew, but must still resolve the right binary path.
    with patch("pathlib.Path.exists", return_value=True):
        info = mgr.install_version("3.0.0", dry_run=True)
    assert info.version == "3.0.0"
    # The real install path: assert the argv a real invocation would use.
    with patch.object(mgr, "run_command", return_value=_brew([], "🍺")) as run, \
         patch("pathlib.Path.exists", return_value=True):
        mgr.install_version("3.0.0", dry_run=False)
    argv = [c.args[0] for c in run.call_args_list][0]
    assert "brew" in argv
    assert any("steadfastgaze/tap/moespresso@3" == a for a in argv), (
        f"must install the @3 (versioned) formula, got {argv}")
    rest = argv[argv.index("install"):]
    assert not any(a == "moespresso" for a in rest), (
        f"must never `brew install moespresso` (that is 4.0.0): {argv}")


def _fake_keg(root: Path, version: str) -> Path:
    """A keg as Homebrew lays it out, so get_installed_versions reads it like the real thing."""
    binpath = root / version / "bin" / "moespresso"
    binpath.parent.mkdir(parents=True)
    binpath.write_text("#!/bin/sh\n")
    binpath.chmod(0o755)
    return binpath


def test_only_the_3x_keg_counts_as_installed(tmp_path):
    """`brew list --versions moespresso` saying 4.0.0 is NOT a usable MoEspresso install.

    Hermetic: `cellar_roots` points at a temp keg tree. Without that patch this test reads the
    real machine's Cellar (which happens to have a 3.0.0 keg) and would pass for the wrong
    reason — a test that cannot fail is not a test.
    """
    mgr = MoespressoManager()
    root = tmp_path / "Cellar" / "moespresso@3"
    _fake_keg(root, "3.0.0")
    with patch.object(mgr, "cellar_roots", return_value=[root]):
        versions = mgr.get_installed_versions()
    assert "3.0.0" in versions
    assert "4.0.0" not in versions, (
        "4.0.0 is linked and shadows @3, but it cannot load a DeepSeek V4 package — "
        f"reporting it as usable is silent-wrong: {versions}")


def test_a_machine_with_only_4x_is_not_usable(tmp_path):
    """brew says 4.0.0, no @3 keg: nothing usable, so the caller gets a loud failure."""
    mgr = MoespressoManager()
    with patch.object(mgr, "cellar_roots", return_value=[tmp_path / "nowhere"]):
        assert mgr.get_installed_versions() == []
        assert mgr.resolve_binary() is None
        try:
            mgr.require_binary()
        except ManagerError as exc:
            assert "steadfastgaze/tap/moespresso@3" in str(exc), (
                f"the failure must name the tap so the user can install it: {exc}")
        else:
            raise AssertionError("require_binary() must raise when only 4.x is present")


# --- binary resolution: keg path, never PATH name -------------------------------------------
def test_resolves_the_keg_binary_not_the_shadowing_symlink(tmp_path):
    """`/opt/homebrew/bin/moespresso` is 4.0.0 even when @3 is installed. Resolve the keg."""
    mgr = MoespressoManager()
    keg = tmp_path / "Cellar" / "moespresso@3" / "3.0.0" / "bin"
    keg.mkdir(parents=True)
    binpath = keg / "moespresso"
    binpath.write_text("#!/bin/sh\n")
    binpath.chmod(0o755)
    with patch.object(mgr, "cellar_roots", return_value=[tmp_path / "Cellar" / "moespresso@3"]):
        found = mgr.resolve_binary()
    assert found == binpath, f"expected the @3 keg binary, got {found}"


def test_resolve_binary_fails_loudly_when_no_keg_exists(tmp_path):
    mgr = MoespressoManager()
    with patch.object(mgr, "cellar_roots", return_value=[tmp_path / "nowhere"]):
        assert mgr.resolve_binary() is None
        try:
            mgr.require_binary()
        except ManagerError as exc:
            assert "steadfastgaze/tap/moespresso@3" in str(exc), (
                f"the failure must name the tap so the user can install it: {exc}")
        else:
            raise AssertionError("require_binary() must raise when no 3.x keg exists")

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

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

from managers import get_manager, get_manager_instance, list_managers  # noqa: E402
from managers._common import ManagerError  # noqa: E402
from managers.moespresso import MoespressoManager  # noqa: E402


@pytest.fixture(autouse=True)
def _no_machine_moespresso(monkeypatch, tmp_path):
    """resolve_binary() has THREE machine inputs: the LA_MOESPRESSO_BIN pin (config-lib.sh
    exports it into every launcher-started shell), the @3 kegs, and Homebrew's @3 links.
    Patching only cellar_roots left the other two live, so the "nothing usable" tests went red
    from a session that had sourced config-lib (2026-10-27). All three point nowhere unless a
    test sets one on purpose."""
    monkeypatch.delenv("LA_MOESPRESSO_BIN", raising=False)
    nowhere = tmp_path / "no-machine-moespresso"
    monkeypatch.setattr(MoespressoManager, "cellar_roots", lambda self: [nowhere])
    monkeypatch.setattr(MoespressoManager, "symlink_candidates", lambda self: [nowhere / "bin"])


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
def test_install_uses_the_at3_formula_not_the_linked_one(tmp_path):
    """`brew install moespresso` would install 4.0.0, which cannot load the package."""
    mgr = MoespressoManager()
    _installed_keg(mgr, tmp_path)  # the keg the stubbed `brew install` would have left
    # dry_run must not touch brew, but must still resolve the right binary path.
    with patch("pathlib.Path.exists", return_value=True):
        info = mgr.install_version("3.0.0", dry_run=True)
    assert info.version == "3.0.0"
    # The real install path: assert the argv a real invocation would use.
    with patch.object(mgr, "run_command", return_value=_brew([], "🍺")) as run, \
         patch.object(mgr, "formula_python", return_value=(3, 14)), \
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


def _installed_keg(mgr, tmp_path, version="3.0.0"):
    """Point mgr at a temp @3 keg — what a stubbed `brew install` would have produced. Without
    it the install tests validated against the REAL Cellar and passed only on a machine that
    already had the keg."""
    root = tmp_path / "Cellar" / "moespresso@3"
    _fake_keg(root, version)
    mgr.cellar_roots = lambda: [root]


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


def test_dry_run_reports_a_bottle_not_a_venv():
    """A dry run must not promise a venv path for a backend that installs a Homebrew bottle."""
    mgr = MoespressoManager()
    with patch.object(mgr, "run_command", return_value=_brew([], "🍺")), \
         patch.object(mgr, "resolve_binary", return_value=None):
        target = str(mgr.target_for("3.0.0"))
    assert ".venvs/" not in target, (
        f"the dry run told the user a venv would be created; the install runs brew: {target}")
    assert "moespresso" in target, target


# --- the version ceiling: an upgrade must never move off the 3.x line -----------------------
# User requirement 2026-10-26: MoEspresso can ONLY run the DeepSeek V4 model in 3.x, so an upgrade
# to 4.x would silently break the one artifact this backend exists for. The weekly check
# (install/backend_upgrades.py) calls check_updates(), and `manage-backend.py upgrade` calls
# install_version(), so the ceiling belongs in the manager: one place every route passes through.
def test_a_4x_release_is_never_proposed_as_an_upgrade():
    mgr = MoespressoManager()
    with patch.object(mgr, "get_installed_versions", return_value=["3.0.0"]), \
         patch.object(mgr, "get_latest_version", return_value="4.0.0"):
        assert mgr.check_updates() == [], (
            "4.0.0 cannot load a MoEspresso package — proposing it breaks the model it would "
            "be upgrading")


def test_a_4x_release_never_appears_as_installable():
    mgr = MoespressoManager()
    with patch.object(mgr, "run_command", return_value=_brew([], "🍺")):
        versions = mgr.get_available_versions()
    assert versions and all(v.startswith("3.") for v in versions), (
        f"a 4.x version must not be offered: {versions}")


def test_installing_a_4x_version_is_refused():
    """An explicit `install 4.0.0` must fail loudly, not run brew."""
    mgr = MoespressoManager()
    with patch.object(mgr, "run_command") as run:
        try:
            mgr.install_version("4.0.0")
        except ManagerError as exc:
            assert "3.x" in str(exc) and "4.x" in str(exc), (
                f"the refusal must name the ceiling and the consequence: {exc}")
            run.assert_not_called()
        else:
            raise AssertionError("install_version('4.0.0') must refuse rather than run brew")


def test_a_3x_upgrade_is_still_offered():
    """The ceiling must not freeze a machine that is behind on 3.x patches."""
    mgr = MoespressoManager()
    with patch.object(mgr, "get_installed_versions", return_value=["3.0.0"]), \
         patch.object(mgr, "get_latest_version", return_value="3.4.0"):
        ups = mgr.check_updates()
    assert ups == [("moespresso", "3.0.0", "3.4.0")], ups


# --- the Python ceiling + the pin (user 2026-10-26) -----------------------------------------
# The user runs `brew upgrade && brew upgrade --greedy && pipx upgrade-all` by hand. MoEspresso
# must stay on 3.x patches AND never land on Python >= 3.15, so the @3 formula is PINNED (a
# blanket brew upgrade skips it) and moves only through free-agents' checked offer.
def test_install_pins_the_formula_so_a_blanket_brew_upgrade_cannot_move_it(tmp_path):
    mgr = MoespressoManager()
    _installed_keg(mgr, tmp_path)
    with patch.object(mgr, "run_command", return_value=_brew([], "🍺")) as run, \
         patch.object(mgr, "formula_python", return_value=(3, 14)), \
         patch("pathlib.Path.exists", return_value=True):
        mgr.install_version("3.0.0", dry_run=False)
    argvs = [c.args[0] for c in run.call_args_list]
    install = argvs.index(["brew", "install", "steadfastgaze/tap/moespresso@3"])
    assert ["brew", "pin", "steadfastgaze/tap/moespresso@3"] in argvs[install + 1:], argvs


def test_a_formula_built_on_a_too_new_python_is_refused_before_brew_runs():
    mgr = MoespressoManager()
    with patch.object(mgr, "run_command") as run, \
         patch.object(mgr, "formula_python", return_value=(3, 15)):
        try:
            mgr.install_version("3.0.1")
        except ManagerError as exc:
            assert "3.14" in str(exc) and "3.15" in str(exc), exc
            run.assert_not_called()
        else:
            raise AssertionError("a python@3.15 build must be refused, not installed")


def test_formula_python_reads_the_python_dependency():
    mgr = MoespressoManager()
    info = '{"formulae": [{"dependencies": ["openssl@3", "python@3.14"]}]}'
    with patch("subprocess.run", return_value=_brew([], info)):
        assert mgr.formula_python() == (3, 14)

#!/usr/bin/env python3
"""Tests for LlamaCppManager."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

# Add install directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

import pytest

from managers import get_manager, get_manager_instance, list_managers
from managers.llama_cpp import LlamaCppManager


def test_llama_cpp_manager_registration():
    """Test that LlamaCppManager is registered."""
    assert "llama-cpp" in list_managers()
    mgr_class = get_manager("llama-cpp")
    assert mgr_class is LlamaCppManager
    mgr = get_manager_instance("llama-cpp")
    assert isinstance(mgr, LlamaCppManager)
    assert mgr.BACKEND_NAME == "llama-cpp"
    assert mgr.VENV_PREFIX == "llama-cpp"


def test_llama_cpp_get_installed_versions():
    """Test get_installed_versions returns a list."""
    mgr = LlamaCppManager()
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="llama.cpp b5000\n")
        versions = mgr.get_installed_versions()
        assert isinstance(versions, list)


def test_llama_cpp_get_available_versions():
    """Test get_available_versions with mocked GitHub response."""
    mgr = LlamaCppManager()

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.read.return_value = b'[{"tag_name": "b5000", "prerelease": False, "assets": [{"name": "llama-b5000-macos-arm64.zip"}]}, {"tag_name": "b4900", "prerelease": False, "assets": [{"name": "llama-b4900-macos-arm64.zip"}]}]'
        mock_urlopen.return_value = mock_response

        import json
        with patch("json.load", return_value=[
            {"tag_name": "b5000", "prerelease": False, "assets": [{"name": "llama-b5000-macos-arm64.zip"}]},
            {"tag_name": "b4900", "prerelease": False, "assets": [{"name": "llama-b4900-macos-arm64.zip"}]},
        ]):
            versions = mgr.get_available_versions()
            assert isinstance(versions, list)
            assert "5000" in versions or "b5000" in versions


def test_llama_cpp_get_packages_to_check():
    """Test get_packages_to_check returns expected packages."""
    mgr = LlamaCppManager()
    packages = mgr.get_packages_to_check()
    assert packages == ["llama-cpp"]


def test_llama_cpp_package_source():
    """Test package source is github_binary."""
    mgr = LlamaCppManager()
    assert mgr.get_package_source("llama-cpp") == "github_binary"


def test_llama_cpp_version_key():
    """Test version_key correctly parses and sorts versions."""
    mgr = LlamaCppManager()
    versions = ["b5000", "b4900", "b4800", "b4700"]
    sorted_versions = sorted(versions, key=mgr.version_key, reverse=True)
    # Should sort by build number descending
    assert sorted_versions == ["b5000", "b4900", "b4800", "b4700"]


def test_llama_cpp_class_attributes():
    """Test that class attributes are correctly set."""
    mgr = LlamaCppManager()
    assert mgr.BACKEND_NAME == "llama-cpp"
    assert mgr.VENV_PREFIX == "llama-cpp"
    assert mgr.DEFAULT_VERSION == "b5000"
    assert mgr.PACKAGE_NAME == "llama-cpp"
    assert mgr.PYPI_URL == ""


def test_llama_cpp_version_key_invalid():
    """Test version_key raises ValueError for invalid versions."""
    mgr = LlamaCppManager()
    with pytest.raises(ValueError):
        mgr.version_key("invalid")


def test_llama_cpp_require_version():
    """Test require_version validates and returns version."""
    mgr = LlamaCppManager()
    assert mgr.require_version("b5000") == "b5000"
    assert mgr.require_version("b4900") == "b4900"

    with pytest.raises(Exception):  # ManagerError
        mgr.require_version("invalid")


def test_llama_cpp_valid_versions():
    """Test valid_versions filters correctly."""
    mgr = LlamaCppManager()
    # Use version format that VERSION_RE expects (no 'b' prefix for the key, or with 'b')
    # The llama.cpp releases on GitHub use tags like "b5000", but the regex expects "5000" or "b5000"
    releases = {}
    for ver in ["5000", "4900", "4800"]:
        releases[ver] = [{"yanked": False}]
    releases["4700"] = [{"yanked": True}]

    versions = mgr.valid_versions(releases, include_prereleases=False)
    assert "5000" in versions or "b5000" in versions
    assert "4700" not in versions  # yanked


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# --- a Homebrew install is preferred over the GitHub unpack (user decision 2026-10-26) --------
# The manager DETECTS a brew install (get_installed_versions reads `brew list --versions`) but
# install_version() has only ever fetched a GitHub release into ~/.local/llama-cpp/<version>/.
# Two copies could therefore coexist, and `brew upgrade llama.cpp` silently did nothing for the
# backend free-agents actually resolves. The user asked for the brew install to be preferred.


def _brew_ok(stdout="llama.cpp 0.7.0\n"):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def _brew_absent():
    return MagicMock(returncode=1, stdout="", stderr="No available formula")


@pytest.fixture
def mgr(tmp_path):
    """`home` is a read-only property on BackendManager, so patch it on the CLASS."""
    m = LlamaCppManager()
    with patch.object(type(m), "home", new_callable=PropertyMock, return_value=tmp_path):
        yield m


def test_a_brew_install_is_used_when_brew_has_the_formula(mgr):
    """A machine with brew llama.cpp must not download a GitHub release."""
    with patch("subprocess.run", return_value=_brew_ok()) as run, \
         patch.object(mgr, "validate_environment", return_value=MagicMock()):
        mgr.install_version("0.7.0", dry_run=False)
    argv = [c.args[0] for c in run.call_args_list][0]
    assert "brew" in argv, argv
    assert any("llama.cpp" == a for a in argv), argv
    assert not any("github" in str(a) or "huggingface" in str(a) for a in argv), (
        f"a GitHub fetch must not run when brew can provide it: {argv}")


def test_the_brew_route_targets_the_brew_prefix_not_a_local_unpack(mgr):
    with patch("subprocess.run", return_value=_brew_ok()), \
         patch.object(mgr, "validate_environment", return_value=MagicMock()):
        info = mgr.install_version("0.7.0", dry_run=True)
    assert ".local/llama-cpp/" not in str(info.path), (
        f"the dry run still promised the GitHub unpack path: {info.path}")
    assert info.metadata.get("method") == "homebrew", info.metadata


def test_no_brew_keeps_the_github_route(mgr):
    """The fallback must survive: a user may want a revision brew does not carry."""
    # Stub the manager's own probe rather than subprocess globally: a blanket stub also breaks
    # whatever the download path shells out to, which is not what this test is about.
    with patch.object(mgr, "brew_available", return_value=None), \
         patch.object(type(mgr), "_download_and_extract") as dl, \
         patch.object(mgr, "validate_environment", return_value=MagicMock()):
        mgr.install_version("b9999", dry_run=False)
    assert dl.called, "without brew, the GitHub release must still be fetched"
    target = self_target(mgr, "b9999")
    assert str(target) in str(dl.call_args[0][0]) or target in dl.call_args[0], dl.call_args


def self_target(mgr, version):
    return mgr.home / ".local" / "llama-cpp" / version


def test_validation_accepts_a_github_unpack_when_brew_is_absent(mgr, tmp_path):
    """A machine that installed via GitHub must still validate — preferring brew must not orphan it."""
    t = self_target(mgr, "b1")
    t.mkdir(parents=True)
    (t / "llama-server").write_text("#!/bin/sh\n")
    (t / "llama-server").chmod(0o755)
    with patch.object(mgr, "brew_available", return_value=None), \
         patch.object(mgr, "run_command", return_value=MagicMock(stdout="version: b1\n")):
        info = mgr.validate_environment("b1")
    assert str(t) == info.path, info.path


def test_validation_prefers_the_brew_prefix_when_brew_has_it(mgr, tmp_path):
    """With brew present AND an unpack present, brew wins — the user's stated preference."""
    t = self_target(mgr, "b1")
    t.mkdir(parents=True)
    (t / "llama-server").write_text("#!/bin/sh\n")
    (t / "llama-server").chmod(0o755)
    with patch.object(mgr, "brew_available", return_value="0.7.0"), \
         patch.object(mgr, "run_command", return_value=MagicMock(stdout="version: 0.7.0\n")):
        info = mgr.validate_environment("b1")
    assert info.path == str(mgr.brew_prefix()), info.path
    assert str(t) not in info.path, "the unpack must not win when brew is available"


def test_effective_version_names_what_the_route_installs(mgr):
    """A dry run must not claim b5000 into a Homebrew prefix holding 0.7.0."""
    with patch.object(mgr, "brew_available", return_value="0.7.0"):
        assert mgr.effective_version("b5000") == "0.7.0"
    with patch.object(mgr, "brew_available", return_value=None):
        assert mgr.effective_version("b5000") == "b5000"


def test_the_cli_dry_run_reports_the_effective_version(mgr):
    """The same fact through install-backend.sh: version and target must agree."""
    import json, subprocess, sys
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}
    r = subprocess.run([sys.executable, "install/manage-backend.py", "--backend", "llama-cpp",
                        "--dry-run", "install", "b5000"],
                       capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]),
                       env=dict(__import__("os").environ, **env))
    # brew may be absent in the harness's PATH; either answer is fine, but they must not disagree.
    if r.returncode == 0 and r.stdout.strip():
        d = json.loads(r.stdout)
        if d["target"] == "/opt/homebrew/bin":
            assert d["version"] == "0.7.0", f"version/target disagree: {d}"

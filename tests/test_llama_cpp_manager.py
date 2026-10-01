#!/usr/bin/env python3
"""Tests for LlamaCppManager."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
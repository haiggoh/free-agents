#!/usr/bin/env python3
"""Tests for OMLXManager."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add install directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

import pytest

from managers import get_manager, get_manager_instance, list_managers
from managers.omlx import OMLXManager


def test_omlx_manager_registration():
    """Test that OMLXManager is registered."""
    assert "omlx" in list_managers()
    mgr_class = get_manager("omlx")
    assert mgr_class is OMLXManager
    mgr = get_manager_instance("omlx")
    assert isinstance(mgr, OMLXManager)
    assert mgr.BACKEND_NAME == "omlx"
    assert mgr.VENV_PREFIX == "omlx"


def test_omlx_get_installed_versions():
    """Test get_installed_versions returns a list."""
    mgr = OMLXManager()
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="omlx 0.7.0\n")
        versions = mgr.get_installed_versions()
        assert isinstance(versions, list)


def test_omlx_get_available_versions():
    """Test get_available_versions with mocked GitHub response."""
    mgr = OMLXManager()

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.read.return_value = b'[{"tag_name": "v0.7.0"}, {"tag_name": "v0.6.4"}]'
        mock_urlopen.return_value = mock_response

        import json
        with patch("json.load", return_value=[
            {"tag_name": "v0.7.0", "prerelease": False},
            {"tag_name": "v0.6.4", "prerelease": False},
        ]):
            versions = mgr.get_available_versions()
            assert isinstance(versions, list)
            assert "0.7.0" in versions
            assert "0.6.4" in versions


def test_omlx_get_packages_to_check():
    """Test get_packages_to_check returns expected packages."""
    mgr = OMLXManager()
    packages = mgr.get_packages_to_check()
    assert packages == ["omlx"]


def test_omlx_package_source():
    """Test package source is homebrew."""
    mgr = OMLXManager()
    assert mgr.get_package_source("omlx") == "homebrew"
    assert mgr.get_version_source() == "github"


def test_omlx_version_key():
    """Test version_key correctly parses and sorts versions."""
    mgr = OMLXManager()
    versions = ["0.7.0", "0.6.4", "0.6.3", "0.5.0"]
    sorted_versions = sorted(versions, key=mgr.version_key, reverse=True)
    assert sorted_versions == ["0.7.0", "0.6.4", "0.6.3", "0.5.0"]


def test_omlx_class_attributes():
    """Test that class attributes are correctly set."""
    mgr = OMLXManager()
    assert mgr.BACKEND_NAME == "omlx"
    assert mgr.VENV_PREFIX == "omlx"
    assert mgr.DEFAULT_VERSION == "0.7.0"
    assert mgr.PACKAGE_NAME == "omlx"
    assert mgr.PYPI_URL == ""


def test_omlx_version_key_invalid():
    """Test version_key raises ValueError for invalid versions."""
    mgr = OMLXManager()
    with pytest.raises(ValueError):
        mgr.version_key("invalid")


def test_omlx_require_version():
    """Test require_version validates and returns version."""
    mgr = OMLXManager()
    assert mgr.require_version("0.7.0") == "0.7.0"
    assert mgr.require_version("0.6.4") == "0.6.4"

    with pytest.raises(Exception):  # ManagerError
        mgr.require_version("invalid")


def test_omlx_valid_versions():
    """Test valid_versions filters correctly."""
    mgr = OMLXManager()
    # Create mock releases with proper structure
    releases = {}
    for ver in ["0.7.0", "0.6.4", "0.6.3", "0.5.0"]:
        releases[ver] = [{"yanked": False}]
    releases["0.4.0"] = [{"yanked": True}]

    versions = mgr.valid_versions(releases, include_prereleases=False)
    assert "0.7.0" in versions
    assert "0.6.4" in versions
    assert "0.4.0" not in versions  # yanked


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
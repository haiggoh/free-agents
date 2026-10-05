#!/usr/bin/env python3
"""Tests for RapidMLXManager class."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add install directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

import pytest

from managers import get_manager, get_manager_instance, list_managers
from managers._common import ManagerError
from managers.rapid_mlx import RapidMLXManager


def test_rapid_mlx_manager_registration():
    """Test that RapidMLXManager is registered."""
    assert "rapid-mlx" in list_managers()
    mgr_class = get_manager("rapid-mlx")
    assert mgr_class is RapidMLXManager
    mgr = get_manager_instance("rapid-mlx")
    assert isinstance(mgr, RapidMLXManager)
    assert mgr.BACKEND_NAME == "rapid-mlx"
    assert mgr.VENV_PREFIX == "rapid-mlx"


def test_rapid_mlx_get_installed_versions():
    """Test get_installed_versions returns a list."""
    mgr = RapidMLXManager()
    versions = mgr.get_installed_versions()
    assert isinstance(versions, list)


def test_rapid_mlx_get_available_versions():
    """Test get_available_versions with mocked PyPI response."""
    mgr = RapidMLXManager()

    # Mock the PyPI response
    mock_releases = {
        "0.15.3": [{"yanked": False}],
        "0.15.2": [{"yanked": False}],
        "0.15.1": [{"yanked": True}],
        "0.14.0": [{"yanked": False}],
        "0.13.1": [{"yanked": False}],
    }
    mock_payload = {"releases": mock_releases}

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.read.return_value = b'{"releases": {"0.15.3": [{"yanked": false}], "0.15.2": [{"yanked": false}], "0.14.0": [{"yanked": false}]}}'
        mock_urlopen.return_value = mock_response

        with patch("json.load", return_value=mock_payload):
            versions = mgr.get_available_versions()
            assert isinstance(versions, list)
            assert "0.15.3" in versions
            assert "0.15.2" in versions
            assert "0.14.0" in versions


def test_rapid_mlx_get_packages_to_check():
    """Test get_packages_to_check returns expected packages."""
    mgr = RapidMLXManager()
    packages = mgr.get_packages_to_check()
    assert "rapid-mlx" in packages
    assert "mlx" in packages
    assert "mlx-lm" in packages


def test_rapid_mlx_version_key():
    """Test version_key correctly parses and sorts versions."""
    mgr = RapidMLXManager()
    versions = ["0.15.3", "0.15.2", "0.14.0", "0.13.1"]
    sorted_versions = sorted(versions, key=mgr.version_key, reverse=True)
    assert sorted_versions == ["0.15.3", "0.15.2", "0.14.0", "0.13.1"]

    # Test with prerelease
    versions_with_pre = ["0.15.3", "0.15.3a1", "0.15.2"]
    sorted_with_pre = sorted(versions_with_pre, key=mgr.version_key, reverse=True)
    assert sorted_with_pre == ["0.15.3", "0.15.3a1", "0.15.2"]


def test_rapid_mlx_class_attributes():
    """Test that class attributes are correctly set."""
    mgr = RapidMLXManager()
    assert mgr.BACKEND_NAME == "rapid-mlx"
    assert mgr.VENV_PREFIX == "rapid-mlx"
    assert mgr.DEFAULT_VERSION == "0.15.3"
    assert mgr.PACKAGE_NAME == "rapid-mlx"
    assert mgr.PYPI_URL == "https://pypi.org/pypi/rapid-mlx/json"


def test_rapid_mlx_version_key_invalid():
    """Test version_key raises ValueError for invalid versions."""
    mgr = RapidMLXManager()
    with pytest.raises(ValueError):
        mgr.version_key("invalid")
    with pytest.raises(ValueError):
        mgr.version_key("1.2")


def test_rapid_mlx_require_version():
    """Test require_version validates and returns version."""
    mgr = RapidMLXManager()
    assert mgr.require_version("0.15.3") == "0.15.3"

    with pytest.raises(ManagerError):
        mgr.require_version("invalid")


def test_rapid_mlx_valid_versions():
    """Test valid_versions filters correctly."""
    mgr = RapidMLXManager()
    releases = {
        "0.15.3": [{"yanked": False}],
        "0.15.2": [{"yanked": False}],
        "0.15.3a1": [{"yanked": False}],
        "0.14.0": [{"yanked": True}],
        "invalid": [{"yanked": False}],
    }

    # Without prereleases
    versions = mgr.valid_versions(releases, include_prereleases=False)
    assert "0.15.3" in versions
    assert "0.15.2" in versions
    assert "0.15.3a1" not in versions
    assert "0.14.0" not in versions  # yanked
    assert "invalid" not in versions

    # With prereleases
    versions_pre = mgr.valid_versions(releases, include_prereleases=True)
    assert "0.15.3a1" in versions_pre


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
#!/usr/bin/env python3
"""Tests for LitellmManager."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add install directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

import pytest

from managers import get_manager, get_manager_instance, list_managers
from managers.litellm import LitellmManager


def test_litellm_manager_registration():
    """Test that LitellmManager is registered."""
    assert "litellm" in list_managers()
    mgr_class = get_manager("litellm")
    assert mgr_class is LitellmManager
    mgr = get_manager_instance("litellm")
    assert isinstance(mgr, LitellmManager)
    assert mgr.BACKEND_NAME == "litellm"
    assert mgr.VENV_PREFIX == "litellm"


def test_litellm_get_installed_versions():
    """Test get_installed_versions returns a list."""
    mgr = LitellmManager()
    versions = mgr.get_installed_versions()
    assert isinstance(versions, list)


def test_litellm_get_available_versions():
    """Test get_available_versions with mocked PyPI response."""
    mgr = LitellmManager()

    mock_releases = {
        "1.60.0": [{"yanked": False}],
        "1.59.0": [{"yanked": False}],
        "1.58.0": [{"yanked": True}],
    }
    mock_payload = {"releases": mock_releases}

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.read.return_value = b'{"releases": {"1.60.0": [{"yanked": false}], "1.59.0": [{"yanked": false}]}}'
        mock_urlopen.return_value = mock_response

        import json
        with patch("json.load", return_value=mock_payload):
            versions = mgr.get_available_versions()
            assert isinstance(versions, list)
            assert "1.60.0" in versions
            assert "1.59.0" in versions


def test_litellm_get_packages_to_check():
    """Test get_packages_to_check returns expected packages."""
    mgr = LitellmManager()
    packages = mgr.get_packages_to_check()
    assert "litellm" in packages
    assert "litellm-proxy" in packages


def test_litellm_is_proxy():
    """Test is_proxy returns True."""
    mgr = LitellmManager()
    assert mgr.is_proxy() is True


def test_litellm_version_key():
    """Test version_key correctly parses and sorts versions."""
    mgr = LitellmManager()
    versions = ["1.60.0", "1.59.0", "1.58.0"]
    sorted_versions = sorted(versions, key=mgr.version_key, reverse=True)
    assert sorted_versions == ["1.60.0", "1.59.0", "1.58.0"]


def test_litellm_class_attributes():
    """Test that class attributes are correctly set."""
    mgr = LitellmManager()
    assert mgr.BACKEND_NAME == "litellm"
    assert mgr.VENV_PREFIX == "litellm"
    assert mgr.DEFAULT_VERSION == "1.60.0"
    assert mgr.PACKAGE_NAME == "litellm"
    assert mgr.PYPI_URL == "https://pypi.org/pypi/litellm/json"


def test_litellm_version_key_invalid():
    """Test version_key raises ValueError for invalid versions."""
    mgr = LitellmManager()
    with pytest.raises(ValueError):
        mgr.version_key("invalid")
    with pytest.raises(ValueError):
        mgr.version_key("1.2")


def test_litellm_require_version():
    """Test require_version validates and returns version."""
    mgr = LitellmManager()
    assert mgr.require_version("1.60.0") == "1.60.0"

    with pytest.raises(Exception):  # ManagerError
        mgr.require_version("invalid")


def test_litellm_valid_versions():
    """Test valid_versions filters correctly."""
    mgr = LitellmManager()
    releases = {
        "1.60.0": [{"yanked": False}],
        "1.59.0": [{"yanked": False}],
        "1.58.0": [{"yanked": True}],
        "1.57.0a1": [{"yanked": False}],
    }

    versions = mgr.valid_versions(releases, include_prereleases=False)
    assert "1.60.0" in versions
    assert "1.59.0" in versions
    assert "1.58.0" not in versions  # yanked
    assert "1.57.0a1" not in versions  # prerelease

    versions_pre = mgr.valid_versions(releases, include_prereleases=True)
    assert "1.57.0a1" in versions_pre


def test_litellm_is_proxy_method():
    """Test is_proxy method."""
    mgr = LitellmManager()
    assert mgr.is_proxy() is True


def test_litellm_get_config_path():
    """Test get_config_path returns Path or None."""
    mgr = LitellmManager()
    path = mgr.get_config_path()
    assert path is None or isinstance(path, Path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
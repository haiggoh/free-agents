#!/usr/bin/env python3
"""Tests for VLLMMLXManager."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add install directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

import pytest

from managers import get_manager, get_manager_instance, list_managers
from managers.vllm_mlx import VLLMMLXManager


def test_vllm_mlx_manager_registration():
    """Test that VLLMMLXManager is registered."""
    assert "vllm-mlx" in list_managers()
    mgr_class = get_manager("vllm-mlx")
    assert mgr_class is VLLMMLXManager
    mgr = get_manager_instance("vllm-mlx")
    assert isinstance(mgr, VLLMMLXManager)
    assert mgr.BACKEND_NAME == "vllm-mlx"
    assert mgr.VENV_PREFIX == "vllm-mlx"


def test_vllm_mlx_get_installed_versions():
    """Test get_installed_versions returns a list."""
    mgr = VLLMMLXManager()
    versions = mgr.get_installed_versions()
    assert isinstance(versions, list)


def test_vllm_mlx_get_available_versions():
    """Test get_available_versions with mocked PyPI response."""
    mgr = VLLMMLXManager()

    # Mock the PyPI response
    mock_releases = {
        "0.5.0": [{"yanked": False}],
        "0.4.1": [{"yanked": False}],
        "0.4.0": [{"yanked": True}],
        "0.3.0": [{"yanked": False}],
    }
    mock_payload = {"releases": mock_releases}

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.read.return_value = b'{"releases": {"0.5.0": [{"yanked": false}], "0.4.1": [{"yanked": false}], "0.3.0": [{"yanked": false}]}}'
        mock_urlopen.return_value = mock_response

        import json
        with patch("json.load", return_value=mock_payload):
            versions = mgr.get_available_versions()
            assert isinstance(versions, list)
            assert "0.5.0" in versions
            assert "0.4.1" in versions
            assert "0.3.0" in versions


def test_vllm_mlx_get_packages_to_check():
    """Test get_packages_to_check returns expected packages."""
    mgr = VLLMMLXManager()
    packages = mgr.get_packages_to_check()
    assert "vllm-mlx" in packages
    assert "mlx" in packages
    assert "mlx-lm" in packages
    assert "mlx-vlm" in packages


def test_vllm_mlx_needs_fork_patches():
    """Test needs_fork_patches correctly identifies versions needing patches."""
    mgr = VLLMMLXManager()
    assert mgr.needs_fork_patches("0.4.1") is True
    assert mgr.needs_fork_patches("0.4.0") is True
    assert mgr.needs_fork_patches("0.5.0") is False
    assert mgr.needs_fork_patches("0.5.1") is False


def test_vllm_mlx_version_key():
    """Test version_key correctly parses and sorts versions."""
    mgr = VLLMMLXManager()
    versions = ["0.5.0", "0.4.1", "0.3.0", "0.2.0"]
    sorted_versions = sorted(versions, key=mgr.version_key, reverse=True)
    assert sorted_versions == ["0.5.0", "0.4.1", "0.3.0", "0.2.0"]


def test_vllm_mlx_class_attributes():
    """Test that class attributes are correctly set."""
    mgr = VLLMMLXManager()
    assert mgr.BACKEND_NAME == "vllm-mlx"
    assert mgr.VENV_PREFIX == "vllm-mlx"
    assert mgr.DEFAULT_VERSION == "0.5.0"
    assert mgr.PACKAGE_NAME == "vllm-mlx"
    assert mgr.PYPI_URL == "https://pypi.org/pypi/vllm-mlx/json"


def test_vllm_mlx_version_key_invalid():
    """Test version_key raises ValueError for invalid versions."""
    mgr = VLLMMLXManager()
    with pytest.raises(ValueError):
        mgr.version_key("invalid")
    with pytest.raises(ValueError):
        mgr.version_key("1.2")


def test_vllm_mlx_require_version():
    """Test require_version validates and returns version."""
    mgr = VLLMMLXManager()
    assert mgr.require_version("0.5.0") == "0.5.0"
    assert mgr.require_version("0.4.1") == "0.4.1"

    with pytest.raises(Exception):  # ManagerError
        mgr.require_version("invalid")


def test_vllm_mlx_valid_versions():
    """Test valid_versions filters correctly."""
    mgr = VLLMMLXManager()
    releases = {
        "0.5.0": [{"yanked": False}],
        "0.4.1": [{"yanked": False}],
        "0.4.0": [{"yanked": True}],
        "0.3.0a1": [{"yanked": False}],
        "invalid": [{"yanked": False}],
    }

    # Without prereleases
    versions = mgr.valid_versions(releases, include_prereleases=False)
    assert "0.5.0" in versions
    assert "0.4.1" in versions
    assert "0.4.0" not in versions  # yanked
    assert "0.3.0a1" not in versions  # prerelease
    assert "invalid" not in versions

    # With prereleases
    versions_pre = mgr.valid_versions(releases, include_prereleases=True)
    assert "0.3.0a1" in versions_pre


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
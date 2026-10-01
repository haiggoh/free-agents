#!/usr/bin/env python3
"""Tests for the unified backend management abstraction."""

from __future__ import annotations

import sys
from pathlib import Path

# Add install directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

from managers.base import (
    BackendInfo,
    BackendManager,
    ManagerError,
    VersionInfo,
)
from managers import (
    get_manager,
    get_manager_instance,
    list_managers,
    register_manager,
)


class ConcreteManager(BackendManager):
    """Concrete implementation for testing."""
    BACKEND_NAME = "test"
    VENV_PREFIX = "test"
    DEFAULT_VERSION = "1.0.0"
    PACKAGE_NAME = "test-package"
    PYPI_URL = "https://pypi.org/pypi/test-package/json"

    def get_installed_versions(self) -> list[str]:
        return ["1.0.0"]

    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        return ["1.0.0", "0.9.0"]

    def get_latest_version(self, include_prereleases: bool = False) -> str:
        return "1.0.0"

    def install_version(
        self,
        version: str,
        python: str | None = None,
        refresh_deps: bool = False,
        dry_run: bool = False,
    ) -> BackendInfo:
        return BackendInfo(
            name=self.BACKEND_NAME,
            version=version,
            path=f"/tmp/test-{version}",
            status="installed",
            metadata={},
        )

    def validate_environment(self, version: str) -> BackendInfo:
        return BackendInfo(
            name=self.BACKEND_NAME,
            version=version,
            path=f"/tmp/test-{version}",
            status="validated",
            metadata={"validated": True},
        )

    def get_packages_to_check(self) -> list[str]:
        return ["test-package"]

    def check_updates(self) -> list[tuple[str, str, str]]:
        return []

    def _get_latest_package_version(self, package: str) -> str:
        return "1.0.0"


def test_backend_manager_base_class():
    """Test that BackendManager base class works correctly."""
    mgr = ConcreteManager()

    # Test class attributes
    assert mgr.BACKEND_NAME == "test"
    assert mgr.VENV_PREFIX == "test"
    assert mgr.DEFAULT_VERSION == "1.0.0"
    assert mgr.PACKAGE_NAME == "test-package"

    # Test that required methods exist
    assert hasattr(mgr, 'install_version')
    assert hasattr(mgr, 'validate_environment')
    assert hasattr(mgr, 'get_installed_versions')
    assert hasattr(mgr, 'get_available_versions')
    assert hasattr(mgr, 'get_latest_version')
    assert hasattr(mgr, 'get_packages_to_check')
    assert hasattr(mgr, 'check_updates')
    assert hasattr(mgr, '_get_latest_package_version')
    assert hasattr(mgr, '_version_newer')
    assert hasattr(mgr, 'run_command')

    # Test version_key
    key = mgr.version_key("1.2.3")
    assert key == (1, 2, 3, 1, "", 0)

    # Test require_version
    assert mgr.require_version("1.0.0") == "1.0.0"

    # Test valid_versions
    releases = {
        "1.0.0": [{}],
        "0.9.0": [{}],
        "1.0.0a1": [{}],
    }
    versions = mgr.valid_versions(releases, include_prereleases=False)
    assert versions == ["1.0.0", "0.9.0"]

    versions_pre = mgr.valid_versions(releases, include_prereleases=True)
    assert versions_pre == ["1.0.0", "1.0.0a1", "0.9.0"]

    # Test _version_newer
    assert mgr._version_newer("1.0.1", "1.0.0") is True
    assert mgr._version_newer("1.0.0", "1.0.1") is False

    print("test_backend_manager_base_class PASSED")


def test_backend_info_dataclass():
    """Test BackendInfo dataclass."""
    info = BackendInfo(
        name="test",
        version="1.0.0",
        path="/tmp/test",
        status="installed",
        metadata={"key": "value"}
    )
    assert info.name == "test"
    assert info.version == "1.0.0"
    assert info.path == "/tmp/test"
    assert info.status == "installed"
    assert info.metadata == {"key": "value"}

    # Test with default metadata
    info2 = BackendInfo(name="test", version="1.0.0", path="/tmp/test", status="installed")
    assert info2.metadata == {}

    print("test_backend_info_dataclass PASSED")


def test_version_info_dataclass():
    """Test VersionInfo dataclass."""
    vinfo = VersionInfo(
        version="1.0.0",
        source="pypi",
        release_date="2026-01-01",
        url="https://pypi.org/project/test/1.0.0",
        changelog="https://example.com/changelog"
    )
    assert vinfo.version == "1.0.0"
    assert vinfo.source == "pypi"
    assert vinfo.release_date == "2026-01-01"
    assert vinfo.url == "https://pypi.org/project/test/1.0.0"
    assert vinfo.changelog == "https://example.com/changelog"

    # Test with default changelog
    vinfo2 = VersionInfo(version="1.0.0", source="pypi", release_date="2026-01-01", url="https://example.com")
    assert vinfo2.changelog == ""

    print("test_version_info_dataclass PASSED")


def test_registry():
    """Test manager registry functions."""
    # Register our test manager
    register_manager(ConcreteManager)

    # Test list_managers
    managers = list_managers()
    assert "test" in managers

    # Test get_manager
    mgr_class = get_manager("test")
    assert mgr_class is ConcreteManager

    # Test get_manager_instance
    mgr_instance = get_manager_instance("test")
    assert isinstance(mgr_instance, ConcreteManager)
    assert mgr_instance.BACKEND_NAME == "test"

    # Test get_manager for unknown
    assert get_manager("unknown") is None
    assert get_manager_instance("unknown") is None

    # Test iter_managers
    from managers import iter_managers
    registered = iter_managers()
    assert ("test", ConcreteManager) in registered

    print("test_registry PASSED")


def test_manager_error():
    """Test ManagerError exception."""
    try:
        raise ManagerError("test error")
    except ManagerError as e:
        assert str(e) == "test error"

    print("test_manager_error PASSED")


def test_concrete_methods():
    """Test concrete methods on the manager."""
    mgr = ConcreteManager()

    # Test installed_versions
    entries = mgr.installed_versions()
    # Should return empty list since no actual environments exist
    assert isinstance(entries, list)

    # Test get_installed_version_infos
    infos = mgr.get_installed_version_infos()
    assert isinstance(infos, list)

    # Test choose_python - should work with current interpreter
    python_path = mgr.choose_python(None)
    assert python_path.exists()
    assert python_path.is_file()

    print("test_concrete_methods PASSED")


def test_cli_help():
    """Test that CLI supports --help."""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "managers.base", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent / "install"
    )
    # Base module doesn't have a main, so this will fail - that's expected
    # The test is really for manage-backend.py

    result = subprocess.run(
        [sys.executable, "install/manage-backend.py", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower() or "unified backend manager" in result.stdout.lower()

    print("test_cli_help PASSED")


if __name__ == "__main__":
    test_backend_manager_base_class()
    test_backend_info_dataclass()
    test_version_info_dataclass()
    test_registry()
    test_manager_error()
    test_concrete_methods()
    test_cli_help()
    print("\nALL TESTS PASSED")
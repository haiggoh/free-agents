#!/usr/bin/env python3
"""Tests for unsupported backend operations.

Tests that backends lacking promote/remove/smoke print "not supported for <backend>" and exit 2.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import manage-backend.py
MODULE_PATH = Path(__file__).resolve().parent.parent / "install" / "manage-backend.py"
spec = importlib.util.spec_from_file_location("manage_backend", MODULE_PATH)
assert spec and spec.loader
manage_backend = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = manage_backend
spec.loader.exec_module(manage_backend)

import pytest
import managers as _managers_pkg
from managers import BackendManager, ManagerError, register_manager


class MinimalBackend(BackendManager):
    """A minimal backend that doesn't support promote/remove/smoke."""
    BACKEND_NAME = "minimal-test"
    VENV_PREFIX = "minimal-test"
    DEFAULT_VERSION = "1.0.0"
    PACKAGE_NAME = "minimal-test"
    PYPI_URL = "https://pypi.org/pypi/minimal-test/json"

    def get_installed_versions(self) -> list[str]:
        return []

    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        return ["1.0.0"]

    def get_latest_version(self, include_prereleases: bool = False) -> str:
        return "1.0.0"

    def install_version(self, version: str, python: str | None = None, refresh_deps: bool = False, dry_run: bool = False):
        from managers.base import BackendInfo
        return BackendInfo(name=self.BACKEND_NAME, version=version, path="/tmp/test", status="dry_run", metadata={})

    def validate_environment(self, version: str):
        from managers.base import BackendInfo
        return BackendInfo(name=self.BACKEND_NAME, version=version, path="/tmp/test", status="validated", metadata={})

    def get_packages_to_check(self) -> list[str]:
        return []

    def _get_latest_package_version(self, package: str) -> str:
        return "1.0.0"


@pytest.fixture(autouse=True)
def _registered_minimal_backend():
    """Register the fake backend ONLY for this module's tests. Registering at import time leaked
    `minimal-test` into the global manager registry and broke test_manage_backend's list_managers
    assertion whenever the full suite collected this file first."""
    register_manager(MinimalBackend)
    yield
    _managers_pkg._managers.pop(MinimalBackend.BACKEND_NAME, None)


def test_promote_not_supported():
    """Test that promote prints error and exits 2 for unsupported backend."""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        mgr = MinimalBackend(home=home)

        # Create a mock args object
        class Args:
            backend = "minimal-test"
            version = "1.0.0"
            repo = None
            dry_run = False
            home = None

        args = Args()

        # Capture stderr
        import io
        import sys
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()

        try:
            result = manage_backend.cmd_promote(args)
        finally:
            output = sys.stderr.getvalue()
            sys.stderr = old_stderr

        assert result == 2, f"Expected exit code 2, got {result}"
        assert "not supported for minimal-test" in output, f"Expected error message, got: {output}"
        print("PASS: promote not supported prints correct error and exits 2")
        return True


def test_remove_not_supported():
    """Test that remove prints error and exits 2 for unsupported backend."""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        mgr = MinimalBackend(home=home)

        class Args:
            backend = "minimal-test"
            version = "1.0.0"
            yes = False
            repo = None
            dry_run = False
            home = None

        args = Args()

        import io
        import sys
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()

        try:
            result = manage_backend.cmd_remove(args)
        finally:
            output = sys.stderr.getvalue()
            sys.stderr = old_stderr

        assert result == 2, f"Expected exit code 2, got {result}"
        assert "not supported for minimal-test" in output, f"Expected error message, got: {output}"
        print("PASS: remove not supported prints correct error and exits 2")
        return True


def test_smoke_not_supported():
    """Test that smoke prints error and exits 2 for unsupported backend."""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        mgr = MinimalBackend(home=home)

        class Args:
            backend = "minimal-test"
            version = "1.0.0"
            dry_run = False
            home = None

        args = Args()

        import io
        import sys
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()

        try:
            result = manage_backend.cmd_smoke(args)
        finally:
            output = sys.stderr.getvalue()
            sys.stderr = old_stderr

        assert result == 2, f"Expected exit code 2, got {result}"
        assert "not supported for minimal-test" in output, f"Expected error message, got: {output}"
        print("PASS: smoke not supported prints correct error and exits 2")
        return True


def test_snapshot_not_supported():
    """Test that snapshot prints error and exits 2 for unsupported backend."""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        mgr = MinimalBackend(home=home)

        class Args:
            backend = "minimal-test"
            version = "1.0.0"
            output = None
            dry_run = False
            home = None

        args = Args()

        import io
        import sys
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()

        try:
            result = manage_backend.cmd_snapshot(args)
        finally:
            output = sys.stderr.getvalue()
            sys.stderr = old_stderr

        assert result == 2, f"Expected exit code 2, got {result}"
        assert "not supported for minimal-test" in output, f"Expected error message, got: {output}"
        print("PASS: snapshot not supported prints correct error and exits 2")
        return True


def test_inspect_not_supported():
    """Test that inspect prints error and exits 2 for unsupported backend."""
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        mgr = MinimalBackend(home=home)

        class Args:
            backend = "minimal-test"
            version = "1.0.0"
            output = None
            dry_run = False
            home = None

        args = Args()

        import io
        import sys
        old_stderr = sys.stderr
        sys.stderr = io.StringIO()

        try:
            result = manage_backend.cmd_inspect(args)
        finally:
            output = sys.stderr.getvalue()
            sys.stderr = old_stderr

        assert result == 2, f"Expected exit code 2, got {result}"
        assert "not supported for minimal-test" in output, f"Expected error message, got: {output}"
        print("PASS: inspect not supported prints correct error and exits 2")
        return True


if __name__ == "__main__":
    passed = 0
    failed = 0

    for test_func in [test_promote_not_supported, test_remove_not_supported, test_smoke_not_supported,
                      test_snapshot_not_supported, test_inspect_not_supported]:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"FAIL: {test_func.__name__} raised {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
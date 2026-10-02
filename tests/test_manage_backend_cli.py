#!/usr/bin/env python3
"""Tests for manage-backend.py CLI."""

import sys
import subprocess
from pathlib import Path

# Add install directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "install"))


def run_cli(*args):
    """Run manage-backend.py CLI and return (exit_code, stdout, stderr)."""
    cmd = [sys.executable, str(Path(__file__).parent.parent / "install" / "manage-backend.py")] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def test_cli_help():
    """Test --help output."""
    exit_code, stdout, stderr = run_cli("--help")
    assert exit_code == 0
    # Help output includes the description in stdout
    assert "rapid-mlx" in stdout
    assert "vllm-mlx" in stdout
    assert "omlx" in stdout
    assert "llama-cpp" in stdout
    assert "litellm" in stdout


def test_cli_list_backends():
    """Test list command."""
    exit_code, stdout, stderr = run_cli("--backend", "rapid-mlx", "list")
    assert exit_code == 0


def test_cli_releases():
    """Test releases command."""
    exit_code, stdout, stderr = run_cli("--backend", "rapid-mlx", "releases", "--limit", "5")
    assert exit_code == 0


def test_cli_check_updates():
    """Test check-updates command."""
    exit_code, stdout, stderr = run_cli("--backend", "rapid-mlx", "check-updates")
    # The command requires network, so it may fail with ManagerError but should not crash
    assert exit_code in (0, 2)


def test_cli_info():
    """Test info command requires version."""
    exit_code, stdout, stderr = run_cli("--backend", "rapid-mlx", "info")
    # info command requires a version argument
    assert exit_code == 2

    # Test with a version that doesn't exist (should fail gracefully)
    exit_code, stdout, stderr = run_cli("--backend", "rapid-mlx", "info", "0.15.3")
    # Should either succeed (if installed) or fail with ManagerError
    assert exit_code in (0, 2)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
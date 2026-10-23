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


import os as _os
import pytest as _pytest

# These two call PyPI for real (measured 60 s and 180 s on a slow network). The suite is otherwise
# offline; set FA_NETWORK_TESTS=1 to run them.
_network = _pytest.mark.skipif(_os.environ.get("FA_NETWORK_TESTS") != "1",
                               reason="network test; set FA_NETWORK_TESTS=1")


@_network
def test_cli_releases():
    """Test releases command."""
    exit_code, stdout, stderr = run_cli("--backend", "rapid-mlx", "releases", "--limit", "5")
    assert exit_code == 0


@_network
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


def test_cli_promote_defaults_repo_to_script_repository():
    """0.41.0 bug (user report: Rapid-MLX would not update in the backend manager): promote
    without --repo died with "Repository required for pin promotion" although --help promises
    "default: script repository" — nothing wired the default, get_manager_instance passed no
    repo and the install subparser had no --repo at all. The picker's install+promote (the
    only update route in the backend manager UI) therefore left pin_update "pending" forever
    and the launcher kept running the pinned old version. Dry-run reads the real pin surfaces
    of this repo and writes nothing."""
    exit_code, stdout, stderr = run_cli("--dry-run", "--backend", "rapid-mlx", "promote", "0.16.1")
    combined = stdout + stderr
    assert "Repository required for pin promotion" not in combined
    assert exit_code == 0, f"promote --dry-run failed: {stderr or stdout}"


def test_tracked_repo_clean_refuses_non_git_dir_with_remedy(tmp_path):
    """Promoting pins into a directory that is not a git checkout must say so and name the
    remedy — an installed plugin copy resolves "script repository" to its own derived cache,
    where the old code's generic "requires a clean tracked worktree" (git diff failing inside
    a non-repo) told the user nothing actionable. Tested on the method directly: a full promote
    would first validate an installed venv, which would make this test depend on machine
    state; the promote path reaches this check by composition."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "install"))
    from managers.rapid_mlx import RapidMLXManager

    mgr = RapidMLXManager()
    missing = tmp_path / "not-a-checkout"
    missing.mkdir()                       # exists, but is not a git repo
    with _pytest.raises(Exception) as excinfo:
        mgr.tracked_repo_clean(missing)
    message = str(excinfo.value)
    assert "not a git checkout" in message
    assert "--repo" in message           # the remedy names the flag
    with _pytest.raises(Exception) as excinfo:
        mgr.tracked_repo_clean(tmp_path / "does-not-exist")   # typo'd --repo: same remedy
    assert "not a git checkout" in str(excinfo.value)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
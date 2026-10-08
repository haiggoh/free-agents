#!/usr/bin/env python3
"""Tests for check-backend-updates.py."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

import pytest

# Import the functions directly from the module
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import check_backend_updates

from check_backend_updates import check_all_backends, format_update_report


def test_format_update_report():
    """Test format_update_report with various inputs."""
    # All up to date
    results = {
        "rapid-mlx": [],
        "vllm-mlx": [],
    }
    report = format_update_report(results)
    assert "All backends up to date." in report

    # Some outdated
    results = {
        "rapid-mlx": [("rapid-mlx", "0.15.2", "0.15.3")],
        "vllm-mlx": [("vllm-mlx", "0.4.1", "0.5.0")],
        "omlx": [],
    }
    report = format_update_report(results)
    assert "0.15.2 -> 0.15.3" in report
    assert "0.4.1 -> 0.5.0" in report
    assert "omlx" in report
    assert "up to date" in report


def test_check_all_backends(tmp_path):
    """Test check_all_backends with mocked managers."""
    # The outdated branch logs and fires an osascript notification. Both must stay inside the
    # test: unpatched, every suite run sent the user a REAL "0.15.2 -> 0.15.3" desktop
    # notification and appended the fake OUTDATED line to ~/.claude/logs.
    with patch('check_backend_updates.get_manager') as mock_get_mgr, \
         patch.object(check_backend_updates, 'LOG_DIR', tmp_path), \
         patch.object(check_backend_updates, 'LOG_FILE', tmp_path / "backend-update-check.log"), \
         patch('subprocess.run') as mock_run:
        # Mock each backend manager
        mock_mgr = MagicMock()
        mock_mgr.check_updates.return_value = [("rapid-mlx", "0.15.2", "0.15.3")]
        mock_mgr.get_packages_to_check.return_value = ["rapid-mlx", "mlx", "mlx-lm"]
        mock_get_mgr.return_value = mock_mgr

        results = check_all_backends(["rapid-mlx"])
        assert "rapid-mlx" in results
        assert len(results["rapid-mlx"]) == 1
        # The notification is still attempted (behaviour kept), just intercepted.
        assert mock_run.call_args[0][0][0] == "osascript"
        assert "0.15.2->0.15.3" in (tmp_path / "backend-update-check.log").read_text()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
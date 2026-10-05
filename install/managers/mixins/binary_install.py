"""BinaryInstallMixin - Binary package installation utilities.

This mixin provides common binary package installation utilities shared across
backend managers that install binary packages (e.g., from GitHub releases).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Protocol


class HasBinaryInstallConfig(Protocol):
    """Protocol for classes that can use BinaryInstallMixin."""
    GITHUB_REPO: str
    GITHUB_API_URL: str
    PLATFORM_ASSET_PATTERN: str
    VERSION_RE: re.Pattern

    def version_key(self, value: str) -> tuple[int, int, int, int, str, int]: ...


class BinaryInstallMixin:
    """Mixin providing binary package installation utilities.

    This mixin encapsulates common binary package installation operations
    shared across backend managers that install binary packages from
    GitHub releases or other binary sources.

    Requires the class to have:
    - GITHUB_REPO: str - GitHub repository (e.g., "owner/repo")
    - GITHUB_API_URL: str - GitHub API URL for releases
    - PLATFORM_ASSET_PATTERN: str - Pattern to match the asset name
    - VERSION_RE: re.Pattern - Regex for version matching
    - version_key: method to parse version strings
    """

    GITHUB_REPO: str = ""
    GITHUB_API_URL: str = ""
    PLATFORM_ASSET_PATTERN: str = ""
    VERSION_RE: re.Pattern

    def _download_and_extract(self, version: str, target: Path, asset_pattern: str = "", extract_dir: str = "") -> None:
        """Download and extract a binary package from GitHub releases.

        Args:
            version: Version to download
            target: Target directory for extraction
            asset_pattern: Pattern to match the asset name
            extract_dir: Subdirectory within archive to extract (empty for root)
        """
        # Get release info
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.GITHUB_REPO}/releases/tags/{version}",
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=30.0) as response:
            release = json.load(response)

        # Find matching asset
        asset_url = None
        pattern = asset_pattern or self.PLATFORM_ASSET_PATTERN
        for asset in release.get("assets", []):
            if pattern and pattern in asset.get("name", ""):
                asset_url = asset.get("browser_download_url")
                break

        if not asset_url:
            raise RuntimeError(f"No matching asset found for {self.GITHUB_REPO} version {version}")

        # Download and extract
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            urllib.request.urlretrieve(asset_url, tmp.name)
            shutil.unpack_archive(tmp.name, target)
            os.unlink(tmp.name)

        # Ensure any binaries are executable
        for bin_path in target.rglob("*"):
            if bin_path.is_file() and os.access(bin_path, os.X_OK):
                bin_path.chmod(0o755)

    def _ensure_executable(self, path: Path) -> None:
        """Ensure a file is executable."""
        if path.exists():
            path.chmod(0o755)
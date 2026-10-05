"""GitHubSourceMixin - GitHub package source utilities.

This mixin provides common GitHub package source operations shared across
backend managers that install packages from GitHub releases.
"""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from typing import Any, Protocol


class HasGitHubConfig(Protocol):
    """Protocol for classes that can use GitHubSourceMixin."""
    GITHUB_API_URL: str
    GITHUB_REPO: str
    VERSION_RE: re.Pattern
    BACKEND_NAME: str

    def version_key(self, value: str) -> tuple[int, int, int, int, str, int]: ...


class GitHubSourceMixin:
    """Mixin providing GitHub package source operations.

    This mixin encapsulates common GitHub operations shared across
    backend managers that install packages from GitHub releases.

    Requires the class to have:
    - GITHUB_API_URL: str - GitHub API URL for releases
    - GITHUB_REPO: str - GitHub repository (e.g., "owner/repo")
    - VERSION_RE: re.Pattern - Regex for version matching
    - BACKEND_NAME: str - Backend identifier
    - version_key: method to parse version strings
    """

    GITHUB_API_URL: str = ""
    GITHUB_REPO: str = ""
    VERSION_RE: re.Pattern

    def _fetch_github_releases(self) -> list[dict[str, Any]]:
        """Fetch releases from GitHub API.

        Returns:
            List of release objects from GitHub API

        Raises:
            RuntimeError: If fetch fails
        """
        if not self.GITHUB_API_URL:
            raise RuntimeError("GITHUB_API_URL not set")
        request = urllib.request.Request(
            self.GITHUB_API_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "local-agents-backend-manager/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15.0) as response:
                return json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"could not retrieve GitHub releases: {exc}") from exc

    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        """Get list of available versions from GitHub releases.

        Args:
            include_prereleases: Whether to include pre-release versions

        Returns:
            List of version strings, sorted newest first
        """
        releases = self._fetch_github_releases()
        result: list[str] = []
        for rel in releases:
            tag = rel.get("tag_name", "").lstrip('v')
            if self.VERSION_RE.fullmatch(tag):
                if not include_prereleases and rel.get("prerelease", False):
                    continue
                result.append(tag)
        return sorted(result, key=self.version_key, reverse=True)

    def get_latest_version(self, include_prereleases: bool = False) -> str:
        """Get the latest available version from GitHub.

        Args:
            include_prereleases: Whether to consider pre-release versions

        Returns:
            Latest version string
        """
        versions = self.get_available_versions(include_prereleases)
        if not versions:
            raise RuntimeError(f"No available versions for {self.BACKEND_NAME}")
        return versions[0]
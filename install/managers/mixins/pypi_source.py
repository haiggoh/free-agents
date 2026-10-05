"""PyPISourceMixin - PyPI package source utilities.

This mixin provides common PyPI package source operations shared across
backend managers that install packages from PyPI.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any, Protocol


class HasPyPIConfig(Protocol):
    """Protocol for classes that can use PyPISourceMixin."""
    PYPI_URL: str
    PACKAGE_NAME: str
    BACKEND_NAME: str

    def valid_versions(self, releases: dict[str, Any], include_prereleases: bool = False) -> list[str]: ...


class PyPISourceMixin:
    """Mixin providing PyPI package source operations.

    This mixin encapsulates common PyPI operations shared across
    backend managers that install packages from PyPI.

    Requires the class to have:
    - PYPI_URL: str - PyPI API URL
    - PACKAGE_NAME: str - Package name for PyPI
    - valid_versions: method to filter and sort versions
    - BACKEND_NAME: str - Backend identifier
    """

    PYPI_URL: str = ""
    PACKAGE_NAME: str = ""

    def _fetch_pypi_releases(self) -> dict[str, Any]:
        """Fetch releases from PyPI.

        Returns:
            Releases dictionary from PyPI API

        Raises:
            RuntimeError: If fetch fails
        """
        if not self.PYPI_URL:
            raise RuntimeError("PYPI_URL not set")
        request = urllib.request.Request(
            self.PYPI_URL,
            headers={"Accept": "application/json", "User-Agent": "local-agents-backend-manager/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15.0) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"could not retrieve releases: {exc}") from exc
        releases = payload.get("releases") if isinstance(payload, dict) else None
        if not isinstance(releases, dict):
            raise RuntimeError("PyPI response has no releases object")
        return releases

    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        """Get list of available versions from PyPI.

        Args:
            include_prereleases: Whether to include pre-release versions

        Returns:
            List of version strings, sorted newest first
        """
        releases = self._fetch_pypi_releases()
        return self.valid_versions(releases, include_prereleases)

    def get_latest_version(self, include_prereleases: bool = False) -> str:
        """Get the latest available version.

        Args:
            include_prereleases: Whether to consider pre-release versions

        Returns:
            Latest version string
        """
        versions = self.get_available_versions(include_prereleases)
        if not versions:
            raise RuntimeError(f"No available versions for {self.BACKEND_NAME}")
        return versions[0]

    def _get_latest_package_version(self, package: str) -> str:
        """Get the latest version of a specific package from PyPI.

        Args:
            package: Package name

        Returns:
            Latest version string
        """
        url = f"https://pypi.org/pypi/{package}/json"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15.0) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"could not retrieve {package} releases: {exc}") from exc
        return payload.get("info", {}).get("version", "")
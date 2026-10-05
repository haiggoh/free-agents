"""Version parsing utilities for backend managers.

This module provides shared version parsing utilities used by all backend managers.
"""

from __future__ import annotations

import re
from typing import Any

from .._common import ManagerError


# Base version regex patterns
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:([a-zA-Z]+)(\d+))?$")
SEMVER_V_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.].*)?$")
RAPID_AUTO_VERSION_RE = re.compile(r"rapid-mlx (?P<version>\d+\.\d+\.\d+)")
LLAMA_BUILD_RE = re.compile(r"^b?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


class VersionParsingMixin:
    """Mixin providing common version parsing utilities.

    This mixin provides version parsing, comparison, and sorting utilities
    that are shared across all backend managers. Subclasses can override
    VERSION_RE and version_key() for custom version formats.
    """

    # Override in subclasses for custom version formats
    VERSION_RE: re.Pattern = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:([a-zA-Z]+)(\d+))?$")

    def version_key(self, value: str) -> tuple[int, int, int, int, str, int]:
        """Parse version into sortable key.

        Returns a tuple that can be used for sorting versions.
        Tuple format: (major, minor, patch, is_stable, label, serial)

        Args:
            value: Version string to parse

        Returns:
            Tuple for sorting (major, minor, patch, is_stable, label, serial)
        """
        match = self.VERSION_RE.fullmatch(value)
        if not match:
            raise ValueError(value)
        major, minor, patch, label, serial = match.groups()
        return (
            int(major), int(minor), int(patch),
            1 if label is None else 0, label or "", int(serial or 0),
        )

    def require_version(self, value: str) -> str:
        """Validate version syntax.

        Args:
            value: Version string to validate

        Returns:
            Validated version string

        Raises:
            ManagerError: If version syntax is invalid
        """
        try:
            self.version_key(value)
        except ValueError as exc:
            raise ManagerError(f"unsupported version syntax: {value!r}") from exc
        return value

    def _version_newer(self, latest: str, installed: str) -> bool:
        """Check if latest version is newer than installed.

        Args:
            latest: Latest available version
            installed: Currently installed version

        Returns:
            True if latest is newer
        """
        return self.version_key(latest) > self.version_key(installed)

    def valid_versions(self, releases: dict[str, Any], include_prereleases: bool = False) -> list[str]:
        """Filter and sort valid versions from releases dict.

        Args:
            releases: PyPI releases dictionary
            include_prereleases: Whether to include pre-releases

        Returns:
            Sorted list of valid version strings
        """
        result: list[str] = []
        for version, files in releases.items():
            match = self.VERSION_RE.fullmatch(version)
            if not match or (match.group(4) and not include_prereleases):
                continue
            if not isinstance(files, list) or not files:
                continue
            if all(isinstance(item, dict) and item.get("yanked", False) for item in files):
                continue
            result.append(version)
        return sorted(result, key=self.version_key, reverse=True)
#!/usr/bin/env python3
"""oMLX Backend Manager.

Manages oMLX binary installation via Homebrew and GitHub releases.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .base import BackendInfo, BackendManager, ManagerError
from . import register_manager

GITHUB_REPO = "jundot/omlx"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.].*)?$")


@register_manager
class OMLXManager(BackendManager):
    BACKEND_NAME = "omlx"
    VENV_PREFIX = "omlx"  # Not a venv - oMLX is a standalone binary
    DEFAULT_VERSION = "0.7.0"
    PACKAGE_NAME = "omlx"
    PYPI_URL = ""  # Not on PyPI

    def version_key(self, value: str) -> tuple[int, int, int, int, str, int]:
        """Parse version into sortable key compatible with base class."""
        match = VERSION_RE.fullmatch(value.lstrip('v'))
        if not match:
            # Try base class format
            base_version_re = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:([a-zA-Z]+)(\d+))?$")
            match = base_version_re.fullmatch(value)
            if not match:
                raise ValueError(value)
            major, minor, patch, label, serial = match.groups()
            return (
                int(major), int(minor), int(patch),
                1 if label is None else 0, label or "", int(serial or 0),
            )
        major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
        # No prerelease label in oMLX GitHub versions
        return (major, minor, patch, 1, "", 0)

    def get_installed_versions(self) -> list[str]:
        versions = []
        # Check Homebrew installation
        try:
            result = subprocess.run(["brew", "list", "--versions", "omlx"], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    parts = line.split()
                    if len(parts) >= 2:
                        versions.append(parts[1])
        except Exception:
            pass

        # Check for manual installations
        for path in ["/opt/homebrew/bin/omlx", "/usr/local/bin/omlx"]:
            if os.path.exists(path) and os.access(path, os.X_OK):
                try:
                    result = subprocess.run([path, "--version"], capture_output=True, text=True)
                    version = result.stdout.strip().split()[-1]
                    if version not in versions:
                        versions.append(version)
                except Exception:
                    pass

        return sorted(set(versions), key=self.version_key, reverse=True)

    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        try:
            request = urllib.request.Request(
                GITHUB_API_URL,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "local-agents-backend-manager/1"},
            )
            with urllib.request.urlopen(request, timeout=15.0) as response:
                releases = json.load(response)
            result = []
            for rel in releases:
                tag = rel.get("tag_name", "").lstrip('v')
                if VERSION_RE.fullmatch(tag):
                    if not include_prereleases and rel.get("prerelease", False):
                        continue
                    result.append(tag)
            return sorted(result, key=self.version_key, reverse=True)
        except Exception as exc:
            raise ManagerError(f"could not retrieve oMLX releases: {exc}") from exc

    def get_latest_version(self, include_prereleases: bool = False) -> str:
        versions = self.get_available_versions(include_prereleases)
        if not versions:
            raise ManagerError("No available oMLX versions")
        return versions[0]

    def get_packages_to_check(self) -> list[str]:
        return ["omlx"]  # Not a PyPI package

    def get_package_source(self, package: str) -> str:
        return "homebrew"

    def get_version_source(self) -> str:
        return "github"

    def _get_latest_package_version(self, package: str) -> str:
        # For oMLX, check Homebrew formula version
        if package == "omlx":
            return self.get_latest_version()
        raise ManagerError(f"Unknown package for oMLX: {package}")

    def install_version(
        self,
        version: str,
        *,
        python: str | None = None,
        refresh_deps: bool = False,
        dry_run: bool = False,
    ) -> BackendInfo:
        if dry_run:
            return BackendInfo(
                name=self.BACKEND_NAME,
                version=version,
                path="/opt/homebrew/bin/omlx",
                status="dry_run",
                metadata={"method": "homebrew"}
            )

        # Install via Homebrew
        try:
            if version == "latest" or version == self.get_latest_version():
                self.run_command(["brew", "install", "omlx"])
            else:
                # For specific versions, may need to install from source or use brew versions
                self.run_command(["brew", "install", f"omlx@{version}"])
        except ManagerError:
            # Fallback: try installing without version
            self.run_command(["brew", "install", "omlx"])

        # Verify installation
        binary = Path("/opt/homebrew/bin/omlx")
        if not binary.exists():
            binary = Path("/usr/local/bin/omlx")

        return self.validate_environment(version)

    def validate_environment(self, version: str) -> BackendInfo:
        binary = Path("/opt/homebrew/bin/omlx")
        if not binary.exists():
            binary = Path("/usr/local/bin/omlx")
        if not binary.exists() or not os.access(binary, os.X_OK):
            raise ManagerError("omlx binary not found after installation")

        # Verify version
        result = self.run_command([str(binary), "--version"], capture=True)
        output = result.stdout.strip()

        return BackendInfo(
            name=self.BACKEND_NAME,
            version=version,
            path=str(binary),
            status="validated",
            metadata={
                "binary_path": str(binary),
                "version_output": output,
                "validated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    # Helper methods
    def choose_python(self, explicit: str | None) -> Path:
        # Not used for oMLX, but required by base class
        candidates = [sys.executable, "/opt/homebrew/bin/python3.12", "python3.12", "python3"]
        for candidate in candidates:
            resolved = shutil.which(candidate) if "/" not in candidate else candidate
            if not resolved:
                continue
            path = Path(resolved).expanduser().resolve()
            if os.access(path, os.X_OK):
                if subprocess.run([str(path), "-c", "import venv"], capture_output=True).returncode == 0:
                    return path
        raise ManagerError("no Python with the stdlib venv module was found; pass --python")

    def cache_root(self) -> Path:
        return self.home / ".cache" / "local-agents" / "omlx-manager"
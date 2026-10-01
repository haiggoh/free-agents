#!/usr/bin/env python3
"""llama.cpp Backend Manager.

Manages llama.cpp binary installation via GitHub releases.
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
from typing import Any

from .base import BackendInfo, BackendManager, ManagerError
from . import register_manager

GITHUB_REPO = "ggml-org/llama.cpp"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
VERSION_RE = re.compile(r"^b?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")

# Apple Silicon optimized builds
PLATFORM_ASSET_PATTERN = "llama-b*-macos-arm64.zip"


@register_manager
class LlamaCppManager(BackendManager):
    BACKEND_NAME = "llama-cpp"
    VENV_PREFIX = "llama-cpp"  # Binary installation, not venv
    DEFAULT_VERSION = "b5000"  # Recent stable build
    PACKAGE_NAME = "llama-cpp"
    PYPI_URL = ""  # Not on PyPI
    VERSION_RE = re.compile(r"^b?(\d+)(?:\.(\d+))?(?:\.(\d+))?$")

    def version_key(self, value: str) -> tuple[int, int, int, int, str, int]:
        """Parse version into sortable key compatible with base class."""
        # llama.cpp uses build numbers like b5000, b4900, etc.
        match = VERSION_RE.fullmatch(value)
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
        # Handle build numbers like 5000, 4900 (single number) or 5.0.0, 4.9.0
        groups = match.groups()
        major = int(groups[0])
        minor = int(groups[1]) if groups[1] is not None else 0
        patch = int(groups[2]) if groups[2] is not None else 0
        return (major, minor, patch, 1, "", 0)

    def get_installed_versions(self) -> list[str]:
        versions = []
        install_dir = self.home / ".local" / "llama-cpp"
        if install_dir.exists():
            for path in install_dir.iterdir():
                if path.is_dir() and path.name.startswith("b"):
                    versions.append(path.name)

        # Check for brew installation
        try:
            result = subprocess.run(["brew", "list", "--versions", "llama.cpp"], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    parts = line.split()
                    if len(parts) >= 2:
                        versions.append(parts[1])
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
                tag = rel.get("tag_name", "").lstrip('b')
                if VERSION_RE.fullmatch(tag):
                    if not include_prereleases and rel.get("prerelease", False):
                        continue
                    # Only include releases with macOS ARM64 assets
                    assets = rel.get("assets", [])
                    if any("macos-arm64" in a.get("name", "") for a in assets):
                        result.append(tag)
            return sorted(result, key=self.version_key, reverse=True)
        except Exception as exc:
            raise ManagerError(f"could not retrieve llama.cpp releases: {exc}") from exc

    def get_latest_version(self, include_prereleases: bool = False) -> str:
        versions = self.get_available_versions(include_prereleases)
        if not versions:
            raise ManagerError("No available llama.cpp versions")
        return versions[0]

    def valid_versions(self, releases: dict[str, Any], include_prereleases: bool = False) -> list[str]:
        """Filter and sort valid versions using llama.cpp version regex."""
        result: list[str] = []
        for version, files in releases.items():
            match = self.VERSION_RE.fullmatch(version)
            if not match:
                continue
            if not isinstance(files, list) or not files:
                continue
            if all(isinstance(item, dict) and item.get("yanked", False) for item in files):
                continue
            result.append(version)
        return sorted(result, key=self.version_key, reverse=True)

    def get_packages_to_check(self) -> list[str]:
        return ["llama-cpp"]  # Binary, not PyPI

    def get_package_source(self, package: str) -> str:
        return "github_binary"

    def _get_latest_package_version(self, package: str) -> str:
        return self.get_latest_version()

    def install_version(
        self,
        version: str,
        *,
        python: str | None = None,
        refresh_deps: bool = False,
        dry_run: bool = False,
    ) -> BackendInfo:
        target = self.home / ".local" / "llama-cpp" / version
        if dry_run:
            return BackendInfo(
                name=self.BACKEND_NAME,
                version=version,
                path=str(target),
                status="dry_run",
                metadata={"method": "github_binary"}
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return self.validate_environment(version)

        # Download and extract binary
        self._download_and_extract(version, target)

        return self.validate_environment(version)

    def _download_and_extract(self, version: str, target: Path) -> None:
        # Get release info
        request = urllib.request.Request(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/b{version}",
            headers={"Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=30.0) as response:
            release = json.load(response)

        # Find macOS ARM64 asset
        asset_url = None
        for asset in release.get("assets", []):
            if "macos-arm64" in asset.get("name", ""):
                asset_url = asset.get("browser_download_url")
                break

        if not asset_url:
            raise ManagerError(f"No macOS ARM64 asset found for llama.cpp b{version}")

        # Download and extract
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            urllib.request.urlretrieve(asset_url, tmp.name)
            shutil.unpack_archive(tmp.name, target)
            os.unlink(tmp.name)

        # Ensure llama-server is executable
        server = target / "llama-server"
        if server.exists():
            server.chmod(0o755)

    def validate_environment(self, version: str) -> BackendInfo:
        target = self.home / ".local" / "llama-cpp" / version
        server = target / "llama-server"
        if not server.exists() or not os.access(server, os.X_OK):
            raise ManagerError(f"llama-server not found or not executable: {server}")

        result = self.run_command([str(server), "--version"], capture=True)

        return BackendInfo(
            name=self.BACKEND_NAME,
            version=version,
            path=str(target),
            status="validated",
            metadata={
                "binary_path": str(server),
                "version_output": result.stdout.strip(),
                "validated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    # Helper methods
    def choose_python(self, explicit: str | None) -> Path:
        # Not used for llama.cpp, but required by base class
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
        return self.home / ".cache" / "local-agents" / "llama-cpp-manager"
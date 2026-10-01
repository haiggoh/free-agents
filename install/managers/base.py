#!/usr/bin/env python3
"""Backend Manager Abstraction - Base classes for unified backend management.

This module provides the abstract base class and dataclasses that all backend
managers (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm) inherit from.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BackendInfo:
    """Information about a backend installation.

    Attributes:
        name: Backend identifier (e.g., "rapid-mlx", "vllm-mlx")
        version: Installed version string
        path: Path to the virtual environment
        status: Installation status ("installed", "incomplete", "validated", "error")
        metadata: Additional backend-specific information
    """
    name: str
    version: str
    path: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VersionInfo:
    """Version metadata from a package registry.

    Attributes:
        version: Version string
        source: Registry source (e.g., "pypi", "github")
        release_date: Release date in ISO format
        url: Download/registry URL
        changelog: Optional changelog URL or text
    """
    version: str
    source: str
    release_date: str
    url: str
    changelog: str = ""


class ManagerError(RuntimeError):
    """A fail-closed manager error suitable for a concise CLI message."""
    pass


class BackendManager(ABC):
    """Abstract base class for backend managers.

    All backend-specific managers must inherit from this class and implement
    the abstract methods. The base class provides common infrastructure for
    version management, virtual environment handling, and CLI integration.

    Class Attributes (must be defined by subclasses):
        BACKEND_NAME: Unique identifier for the backend (e.g., "rapid-mlx")
        VENV_PREFIX: Prefix for virtual environment directories (e.g., "rapid-mlx")
        DEFAULT_VERSION: Default version to install if none specified
        PACKAGE_NAME: PyPI/package name for installation
        PYPI_URL: URL for fetching release information
    """

    # Must be overridden by subclasses
    BACKEND_NAME: str = ""
    VENV_PREFIX: str = ""
    DEFAULT_VERSION: str = ""
    PACKAGE_NAME: str = ""
    PYPI_URL: str = ""

    # Version regex - override if needed
    VERSION_RE: re.Pattern = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:([a-zA-Z]+)(\d+))?$")

    # Schema version for receipts
    SCHEMA: int = 1

    def __init__(self, home: Path | None = None) -> None:
        """Initialize the backend manager.

        Args:
            home: Override home directory (useful for testing)
        """
        self._home = home or Path.home()

    @property
    def home(self) -> Path:
        """Home directory for this manager."""
        return self._home

    @property
    def venv_root(self) -> Path:
        """Root directory for versioned virtual environments."""
        return self.home / ".venvs"

    def target_for(self, version: str) -> Path:
        """Get the target path for a specific version.

        Args:
            version: Version string

        Returns:
            Path to the virtual environment directory
        """
        self.require_version(version)
        return self.venv_root / f"{self.VENV_PREFIX}-{version}"

    def cache_root(self) -> Path:
        """Cache directory for this backend."""
        return self.home / ".cache" / "local-agents" / f"{self.BACKEND_NAME}-runtime-manager"

    def receipt_path(self, version: str) -> Path:
        """Get the receipt path for a specific version."""
        return self.cache_root() / "receipts" / f"{self.VENV_PREFIX}-{version}.json"

    # ---- Abstract Methods ----

    @abstractmethod
    def get_installed_versions(self) -> list[str]:
        """Get list of installed versions.

        Returns:
            List of version strings, sorted newest first
        """
        ...

    @abstractmethod
    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        """Get list of available versions from the registry.

        Args:
            include_prereleases: Whether to include pre-release versions

        Returns:
            List of version strings, sorted newest first
        """
        ...

    @abstractmethod
    def get_latest_version(self, include_prereleases: bool = False) -> str:
        """Get the latest available version.

        Args:
            include_prereleases: Whether to consider pre-release versions

        Returns:
            Latest version string
        """
        ...

    @abstractmethod
    def install_version(
        self,
        version: str,
        python: str | None = None,
        refresh_deps: bool = False,
        dry_run: bool = False,
    ) -> BackendInfo:
        """Install a specific version.

        Args:
            version: Version to install
            python: Base Python executable to use
            refresh_deps: Ignore existing lock and re-resolve dependencies
            dry_run: Print plan without executing

        Returns:
            BackendInfo describing the installation
        """
        ...

    @abstractmethod
    def validate_environment(self, version: str) -> BackendInfo:
        """Validate an installed environment.

        Args:
            version: Version to validate

        Returns:
            BackendInfo with validation results

        Raises:
            ManagerError: If validation fails
        """
        ...

    @abstractmethod
    def get_packages_to_check(self) -> list[str]:
        """Get list of package names to check for updates.

        Returns:
            List of package names
        """
        ...

    @abstractmethod
    def check_updates(self) -> list[tuple[str, str, str]]:
        """Check for available updates.

        Returns:
            List of (package, current_version, latest_version) tuples
        """
        ...

    @abstractmethod
    def _get_latest_package_version(self, package: str) -> str:
        """Get the latest version of a specific package.

        Args:
            package: Package name

        Returns:
            Latest version string
        """
        ...

    # ---- Concrete Methods ----

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

    def version_key(self, value: str) -> tuple[int, int, int, int, str, int]:
        """Parse version into sortable key.

        Args:
            value: Version string

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

    def _version_newer(self, latest: str, installed: str) -> bool:
        """Check if latest version is newer than installed.

        Args:
            latest: Latest available version
            installed: Currently installed version

        Returns:
            True if latest is newer
        """
        return self.version_key(latest) > self.version_key(installed)

    def run_command(
        self,
        command: list[str],
        *,
        capture: bool = False,
        log: Path | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command with optional logging.

        Args:
            command: Command and arguments
            capture: Capture output instead of streaming
            log: Optional log file path
            cwd: Working directory

        Returns:
            CompletedProcess result

        Raises:
            ManagerError: If command fails
        """
        if log is None:
            return subprocess.run(command, check=True, text=True, capture_output=capture, cwd=cwd)

        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write("$ " + shlex.join(command) + "\n")
            handle.flush()
            process = subprocess.run(
                command, text=True, stdout=handle, stderr=subprocess.STDOUT, cwd=cwd,
            )
        if process.returncode:
            raise ManagerError(f"command failed with exit {process.returncode}; see {log}")
        return process

    def choose_python(self, explicit: str | None) -> Path:
        """Choose a Python interpreter with venv support.

        Args:
            explicit: Explicitly requested Python path

        Returns:
            Path to Python executable

        Raises:
            ManagerError: If no suitable Python is found
        """
        candidates: list[str] = []
        if explicit:
            candidates.append(explicit)
        if os.environ.get("LA_BASE_PYTHON"):
            candidates.append(os.environ["LA_BASE_PYTHON"])
        candidates.extend([sys.executable, "/opt/homebrew/bin/python3.14", "python3.14", "python3"])

        seen: set[str] = set()
        for candidate in candidates:
            resolved = candidate
            if "/" not in candidate:
                which_result = shutil.which(candidate)
                if which_result:
                    resolved = which_result
                else:
                    continue
            path = Path(resolved).expanduser().resolve()
            if str(path) in seen or not os.access(path, os.X_OK):
                continue
            seen.add(str(path))
            if subprocess.run([str(path), "-c", "import venv"], capture_output=True).returncode == 0:
                return path
        raise ManagerError("no Python with the stdlib venv module was found; pass --python")

    def sha256_file(self, path: Path) -> str:
        """Compute SHA256 hash of a file."""
        import hashlib
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def atomic_json(self, path: Path, payload: dict[str, Any]) -> None:
        """Write JSON atomically."""
        import tempfile
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def locked_requirements(self, version: str) -> list[str] | None:
        """Get locked requirements from receipt if available."""
        path = self.receipt_path(version)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        lines = payload.get("pip_freeze") if isinstance(payload, dict) else None
        if payload.get("version") != version or not isinstance(lines, list):
            return None
        clean = [line for line in lines if isinstance(line, str) and line.strip()]
        normalized = {line.casefold().replace("_", "-") for line in clean}
        pkg_normalized = self.PACKAGE_NAME.replace("_", "-")
        return clean if f"{pkg_normalized}=={version}" in normalized else None

    def installed_versions(self) -> list[tuple[str, Path, str]]:
        """List installed versioned environments.

        Returns:
            List of (version, path, status) tuples
        """
        root = self.venv_root
        entries: list[tuple[str, Path, str]] = []
        if not root.is_dir():
            return entries
        for path in root.glob(f"{self.VENV_PREFIX}-*"):
            if path.is_symlink() or not path.is_dir():
                continue
            version = path.name.removeprefix(f"{self.VENV_PREFIX}-")
            if not self.VERSION_RE.fullmatch(version):
                continue
            state = "installed" if self._is_complete(path) else "incomplete"
            entries.append((version, path, state))
        return sorted(entries, key=lambda item: self.version_key(item[0]), reverse=True)

    def _is_complete(self, path: Path) -> bool:
        """Check if environment is complete (override for backend-specific checks)."""
        binary = path / "bin" / self.BACKEND_NAME
        return binary.is_file() and os.access(binary, os.X_OK)

    def get_installed_version_infos(self) -> list[BackendInfo]:
        """Get BackendInfo for all installed versions."""
        infos = []
        for version, path, status in self.installed_versions():
            metadata = {}
            if status == "installed":
                try:
                    validated = self.validate_environment(version)
                    status = "validated"
                    metadata = validated.metadata
                except ManagerError:
                    status = "error"
            infos.append(BackendInfo(
                name=self.BACKEND_NAME,
                version=version,
                path=str(path),
                status=status,
                metadata=metadata,
            ))
        return infos



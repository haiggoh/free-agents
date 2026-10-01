#!/usr/bin/env python3
"""vllm-mlx Backend Manager.

Manages versioned vllm-mlx virtual environments with PyPI/GitHub sources and patch handling.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .base import BackendInfo, BackendManager, ManagerError
from . import register_manager

PYPI_URL = "https://pypi.org/pypi/vllm-mlx/json"
GITHUB_REPO = "waybarrios/vllm-mlx"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.].*)?$")

# Patch sets per version range
PATCH_SETS = {
    "0.4": "vllm-mlx-local-fork-patches.patch",  # Applied to v0.4.x
    # 0.5.0+ has patches integrated upstream
}


@register_manager
class VLLMMLXManager(BackendManager):
    BACKEND_NAME = "vllm-mlx"
    VENV_PREFIX = "vllm-mlx"
    DEFAULT_VERSION = "0.5.0"
    PACKAGE_NAME = "vllm-mlx"
    PYPI_URL = PYPI_URL

    def version_key(self, value: str) -> tuple[int, int, int, int, str, int]:
        """Parse version into sortable key compatible with base class."""
        # vllm-mlx uses simpler versioning (no prerelease labels in PyPI)
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
        # No prerelease label in vllm-mlx PyPI versions
        return (major, minor, patch, 1, "", 0)

    def get_installed_versions(self) -> list[str]:
        root = self.venv_root
        entries: list[tuple[str, Path, str]] = []
        if not root.is_dir():
            return []
        for path in root.glob(f"{self.VENV_PREFIX}-*"):
            if path.is_symlink() or not path.is_dir():
                continue
            version = path.name.removeprefix(f"{self.VENV_PREFIX}-")
            if not VERSION_RE.fullmatch(version.lstrip('v')):
                continue
            binary = path / "bin" / "vllm-mlx"
            state = "complete" if binary.is_file() and os.access(binary, os.X_OK) else "incomplete"
            entries.append((version, path, state))
        return [v for v, _, _ in sorted(entries, key=lambda item: self.version_key(item[0]), reverse=True)]

    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        # Check PyPI first
        try:
            request = urllib.request.Request(
                PYPI_URL,
                headers={"Accept": "application/json", "User-Agent": "local-agents-backend-manager/1"},
            )
            with urllib.request.urlopen(request, timeout=15.0) as response:
                payload = json.load(response)
            releases = payload.get("releases") if isinstance(payload, dict) else None
            if isinstance(releases, dict):
                result: list[str] = []
                for version, files in releases.items():
                    if not VERSION_RE.fullmatch(version):
                        continue
                    if not isinstance(files, list) or not files:
                        continue
                    if all(isinstance(item, dict) and item.get("yanked", False) for item in files):
                        continue
                    if not include_prereleases and any(c in version for c in ['a', 'b', 'rc', 'dev']):
                        continue
                    result.append(version)
                if result:
                    return sorted(result, key=self.version_key, reverse=True)
        except Exception:
            pass  # Fall back to GitHub

        # Fallback to GitHub releases
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
            raise ManagerError(f"could not retrieve vllm-mlx releases: {exc}") from exc

    def get_latest_version(self, include_prereleases: bool = False) -> str:
        versions = self.get_available_versions(include_prereleases)
        if not versions:
            raise ManagerError("No available vllm-mlx versions")
        return versions[0]

    def get_packages_to_check(self) -> list[str]:
        return ["vllm-mlx", "mlx", "mlx-lm", "mlx-vlm"]

    def _get_latest_package_version(self, package: str) -> str:
        url = f"https://pypi.org/pypi/{package}/json"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15.0) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ManagerError(f"could not retrieve {package} releases: {exc}") from exc
        return payload.get("info", {}).get("version", "")

    def needs_fork_patches(self, version: str) -> bool:
        """Check if a version needs the local fork patches applied."""
        # v0.4.x needs patches, 0.5.0+ has them integrated
        major_minor = ".".join(version.split(".")[:2])
        return major_minor in PATCH_SETS

    def install_version(
        self,
        version: str,
        *,
        python: str | None = None,
        refresh_deps: bool = False,
        dry_run: bool = False,
        **kwargs,
    ) -> BackendInfo:
        source = kwargs.get("source", "pypi")  # "pypi" or "github"
        target = self.target_for(version)
        if dry_run:
            return BackendInfo(
                name=self.BACKEND_NAME,
                version=version,
                path=str(target),
                status="dry_run",
                metadata={"source": source, "needs_patches": self.needs_fork_patches(version)}
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            receipt = self.validate_environment(version)
            receipt.metadata["result"] = "already_complete"
            return receipt

        base_python = self.choose_python(python)
        log = self.cache_root() / "logs" / f"install-{version}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.log"

        try:
            self.run_command([str(base_python), "-m", "venv", str(target)], log=log)
            child = target / "bin" / "python"

            if source == "github" or self.needs_fork_patches(version):
                # Install from GitHub with patches
                self._install_from_github(version, child, log)
            else:
                # Install from PyPI
                self.run_command([str(child), "-m", "pip", "install", "--disable-pip-version-check", f"vllm-mlx=={version}"], log=log)

            receipt = self.validate_environment(version)
            receipt.metadata.update({
                "result": "installed",
                "source": source,
                "base_python": str(base_python),
                "install_log": str(log),
            })
            return receipt
        except Exception:
            if target.exists() and not target.is_symlink():
                shutil.rmtree(target)
            raise

    def _install_from_github(self, version: str, python: Path, log: Path) -> None:
        """Install vllm-mlx from GitHub at specific tag, applying patches if needed."""
        src_dir = self.home / "vllm-mlx"
        if not src_dir.exists():
            self.run_command(["git", "clone", f"https://github.com/{GITHUB_REPO}", str(src_dir)], log=log)

        self.run_command(["git", "-C", str(src_dir), "fetch", "--tags", "--quiet"], log=log)
        self.run_command(["git", "-C", str(src_dir), "checkout", "--quiet", f"v{version}"], log=log)

        # Apply patches if needed
        if self.needs_fork_patches(version):
            patch_file = Path(__file__).parent.parent / "vllm-mlx-local-fork-patches.patch"
            if patch_file.exists():
                self.run_command(["git", "-C", str(src_dir), "apply", str(patch_file)], log=log)

        self.run_command([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-e", str(src_dir)], log=log)
        self.run_command([str(python), "-m", "pip", "install", "--disable-pip-version-check", "mlx-lm"], log=log)

    def validate_environment(self, version: str) -> BackendInfo:
        target = self.target_for(version)
        if target.is_symlink() or not target.is_dir():
            raise ManagerError(f"environment is missing or unsafe: {target}")
        binary = target / "bin" / "vllm-mlx"
        python = target / "bin" / "python"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ManagerError(f"vllm-mlx executable is missing: {binary}")

        # Verify vllm-mlx CLI works
        self.run_command([str(binary), "--help"], capture=True)
        self.run_command([str(python), "-m", "pip", "check"], capture=True)

        return BackendInfo(
            name=self.BACKEND_NAME,
            version=version,
            path=str(target),
            status="validated",
            metadata={
                "python": str(python),
                "python_version": self.run_command([str(python), "--version"], capture=True).stdout.strip(),
                "packages": self._package_inventory(python),
                "pip_freeze": self._pip_freeze(python),
                "validated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    # Helper methods
    def choose_python(self, explicit: str | None) -> Path:
        candidates: list[str] = []
        if explicit:
            candidates.append(explicit)
        candidates.extend([sys.executable, "/opt/homebrew/bin/python3.12", "python3.12", "python3"])
        for candidate in candidates:
            resolved = shutil.which(candidate) if "/" not in candidate else candidate
            if not resolved:
                continue
            path = Path(resolved).expanduser().resolve()
            if not os.access(path, os.X_OK):
                continue
            if subprocess.run([str(path), "-c", "import venv"], capture_output=True).returncode == 0:
                return path
        raise ManagerError("no Python with the stdlib venv module was found; pass --python")

    def _package_inventory(self, python: Path) -> dict[str, str]:
        code = """
import importlib.metadata, json
names=['vllm-mlx','mlx','mlx-metal','mlx-lm','mlx-vlm','transformers','tokenizers','huggingface-hub']
out={}
for name in names:
    try: out[name]=importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: out[name]='ABSENT'
print(json.dumps(out, sort_keys=True))
"""
        return json.loads(self.run_command([str(python), "-c", code], capture=True).stdout)

    def _pip_freeze(self, python: Path) -> list[str]:
        return [
            line for line in self.run_command([str(python), "-m", "pip", "freeze"], capture=True).stdout.splitlines()
            if line.strip()
        ]

    def cache_root(self) -> Path:
        return self.home / ".cache" / "local-agents" / "vllm-mlx-manager"
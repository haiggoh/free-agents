#!/usr/bin/env python3
"""litellm Backend Manager.

Manages litellm proxy installation via PyPI.
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

PYPI_URL = "https://pypi.org/pypi/litellm/json"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-.].*)?$")


@register_manager
class LitellmManager(BackendManager):
    BACKEND_NAME = "litellm"
    VENV_PREFIX = "litellm"
    DEFAULT_VERSION = "1.60.0"
    PACKAGE_NAME = "litellm"
    PYPI_URL = PYPI_URL
    VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:([a-zA-Z]+)(\d+))?$")

    def is_proxy(self) -> bool:
        return True

    def get_config_path(self) -> Path | None:
        # Check standard litellm config locations
        for path in [
            self.home / ".litellm" / "config.yaml",
            Path("/etc/litellm/config.yaml"),
            Path(os.environ.get("LITELLM_CONFIG_PATH", "")),
        ]:
            if path and path.exists():
                return path
        return None

    def version_key(self, value: str) -> tuple[int, int, int, int, str, int]:
        match = self.VERSION_RE.fullmatch(value)
        if not match:
            raise ValueError(value)
        major = int(match.group(1))
        minor = int(match.group(2))
        patch = int(match.group(3))
        label = match.group(4)
        serial = match.group(5)
        if label is not None:
            # Prerelease version
            return (major, minor, patch, 0, label or "", int(serial or 0))
        else:
            # Stable version
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
            if not VERSION_RE.fullmatch(version):
                continue
            binary = path / "bin" / "litellm"
            state = "complete" if binary.is_file() and os.access(binary, os.X_OK) else "incomplete"
            entries.append((version, path, state))
        return [v for v, _, _ in sorted(entries, key=lambda item: self.version_key(item[0]), reverse=True)]

    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        try:
            request = urllib.request.Request(
                PYPI_URL,
                headers={"Accept": "application/json", "User-Agent": "local-agents-backend-manager/1"},
            )
            with urllib.request.urlopen(request, timeout=15.0) as response:
                payload = json.load(response)
            releases = payload.get("releases") if isinstance(payload, dict) else None
            if not isinstance(releases, dict):
                raise ManagerError("PyPI response has no releases object")

            result: list[str] = []
            for version, files in releases.items():
                if not self.VERSION_RE.fullmatch(version):
                    continue
                if not isinstance(files, list) or not files:
                    continue
                if all(isinstance(item, dict) and item.get("yanked", False) for item in files):
                    continue
                if not include_prereleases and any(c in version for c in ['a', 'b', 'rc', 'dev']):
                    continue
                result.append(version)
            return sorted(result, key=self.version_key, reverse=True)
        except Exception as exc:
            raise ManagerError(f"could not retrieve litellm releases: {exc}") from exc

    def get_latest_version(self, include_prereleases: bool = False) -> str:
        versions = self.get_available_versions(include_prereleases)
        if not versions:
            raise ManagerError("No available litellm versions")
        return versions[0]

    def get_packages_to_check(self) -> list[str]:
        return ["litellm", "litellm-proxy"]

    def _get_latest_package_version(self, package: str) -> str:
        url = f"https://pypi.org/pypi/{package}/json"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=15.0) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ManagerError(f"could not retrieve {package} releases: {exc}") from exc
        return payload.get("info", {}).get("version", "")

    def install_version(
        self,
        version: str,
        *,
        python: str | None = None,
        refresh_deps: bool = False,
        dry_run: bool = False,
    ) -> BackendInfo:
        target = self.target_for(version)
        if dry_run:
            return BackendInfo(
                name=self.BACKEND_NAME,
                version=version,
                path=str(target),
                status="dry_run",
                metadata={"type": "proxy"}
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
            self.run_command([str(child), "-m", "pip", "install", "--disable-pip-version-check", f"litellm=={version}"], log=log)

            receipt = self.validate_environment(version)
            receipt.metadata.update({
                "result": "installed",
                "base_python": str(base_python),
                "install_log": str(log),
            })
            return receipt
        except Exception:
            if target.exists() and not target.is_symlink():
                shutil.rmtree(target)
            raise

    def validate_environment(self, version: str) -> BackendInfo:
        target = self.target_for(version)
        if target.is_symlink() or not target.is_dir():
            raise ManagerError(f"environment is missing or unsafe: {target}")
        binary = target / "bin" / "litellm"
        python = target / "bin" / "python"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ManagerError(f"litellm executable is missing: {binary}")

        self.run_command([str(binary), "--version"], capture=True)
        self.run_command([str(python), "-m", "pip", "check"], capture=True)

        # Check config
        config_path = self.get_config_path()

        return BackendInfo(
            name=self.BACKEND_NAME,
            version=version,
            path=str(target),
            status="validated",
            metadata={
                "type": "proxy",
                "python": str(python),
                "python_version": self.run_command([str(python), "--version"], capture=True).stdout.strip(),
                "config_path": str(config_path) if config_path else None,
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
names=['litellm','litellm-proxy','openai','anthropic','google-generativeai','groq']
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
        return self.home / ".cache" / "local-agents" / "litellm-manager"
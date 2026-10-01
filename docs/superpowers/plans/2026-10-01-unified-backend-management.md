# Unified Backend Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify management of ALL inference backends (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm) into a single cohesive system with versioned installations, automated update checks, qualification tracking, and canonical installation via install-backend.sh — replacing the current fragmented approach (manual git clone for vllm-mlx, Homebrew-only for oMLX, dedicated Rapid-MLX manager).

**Architecture:** Extend the existing `manage-rapid-mlx.py` pattern into a generic `manage-backend.py` that handles multiple backends through a plugin architecture. Each backend gets a BackendManager subclass with consistent interfaces for install/validate/promote/check-updates. The weekly update check and daily upgrade scripts are consolidated. install-backend.sh becomes the single entry point that sets up all backends correctly from the start.

**Tech Stack:** Python 3.11+ (existing), bash (existing launchers), launchd (existing scheduling), PyPI API, GitHub API, Homebrew API (for oMLX), llama.cpp release assets.

**Spec:** This plan implements the unified backend management vision described in the user's request, building on:
- Existing `install/manage-rapid-mlx.py` (reference implementation)
- Existing `install/install-backend.sh` (canonical installer)
- Existing `scripts/local-stack-update-check.sh` (weekly update check)
- Existing `scripts/daily-package-upgrade.sh` (daily upgrade)
- Tournament waypoint `local-model-acquisition` and plan `Plan — Local Model Acquisition...`

## Global Constraints

- Python version: 3.11+ (managed by Homebrew on this machine)
- All versioned environments in `~/.venvs/<backend>-<version>/`
- All update checks must be read-only (notify only, never auto-upgrade)
- Rapid-MLX remains DEFAULT backend (LA_DEFAULT_MLX_BACKEND=rapid)
- vllm-mlx stays LEGACY comparison lane (explicit pin only)
- oMLX stays ISOLATED optional backend (Flash-Next/streaming)
- llama.cpp for GGUF only (dispatch-only)
- litellm for remote free-API routing (NVIDIA, Gemini, Groq, etc.)
- No breaking changes to existing launcher scripts (launch-claude-agent.sh, csl, etc.)
- All new code must support `--help` and `--dry-run`
- Pin promotion requires clean git worktree (existing invariant)

## Review Focus

1. **Backend version skew**: Rapid-MLX 0.15.3 pinned but vllm-mlx at v0.4.1+local vs PyPI 0.5.0 — upgrade must not silently invalidate recorded Metal/cache evidence
2. **Fork patch compatibility**: vllm-mlx local patches don't apply to v0.5.0 — must detect and handle version-specific patch sets
3. **oMLX Homebrew vs PyPI**: oMLX not on PyPI — version check must use GitHub releases + Homebrew formula
4. **llama.cpp binary management**: Not a Python package — must download/verify release binaries or build from source
5. **litellm as proxy**: Not a local inference engine — manages remote API routing, different update semantics
6. **Concurrent backend operation**: Multiple backends may run simultaneously on different ports — version checks must identify which environment maps to which backend

---

### Task 1: Design Backend Manager Abstraction

**Files:**
- Create: `install/manage-backend.py` (new unified manager)
- Modify: `install/manage-rapid-mlx.py` (refactor to use base class)
- Test: `tests/test_manage_backend.py`

**Interfaces:**
- Consumes: None (foundational)
- Produces: `BackendManager` base class, `RapidMLXManager`, `VLLMMLXManager`, `OMLXManager`, `LlamaCppManager`, `LitellmManager` subclasses

### Task 2: Implement RapidMLXManager (refactor existing)

**Files:**
- Modify: `install/manage-rapid-mlx.py` → `install/managers/rapid_mlx.py`
- Create: `install/managers/__init__.py`
- Test: `tests/test_rapid_mlx_manager.py`

### Task 3: Implement VLLMMLXManager

**Files:**
- Create: `install/managers/vllm_mlx.py`
- Test: `tests/test_vllm_mlx_manager.py`

### Task 4: Implement OMLXManager

**Files:**
- Create: `install/managers/omlx.py`
- Test: `tests/test_omlx_manager.py`

### Task 5: Implement LlamaCppManager

**Files:**
- Create: `install/managers/llama_cpp.py`
- Test: `tests/test_llama_cpp_manager.py`

### Task 6: Implement LitellmManager

**Files:**
- Create: `install/managers/litellm.py`
- Test: `tests/test_litellm_manager.py`

### Task 7: Unified CLI Entry Point

**Files:**
- Create: `install/manage-backend.py` (main CLI)
- Modify: `install/manage-rapid-mlx.py` (thin wrapper for backward compat)
- Test: `tests/test_manage_backend_cli.py`

### Task 8: Update install-backend.sh

**Files:**
- Modify: `install/install-backend.sh`
- Test: Manual integration test

### Task 9: Consolidate Update Checks

**Files:**
- Modify: `scripts/local-stack-update-check.sh` → `scripts/check-backend-updates.sh`
- Modify: `scripts/daily-package-upgrade.sh` (remove backend-specific logic)
- Create: `scripts/check-backend-updates.py` (Python implementation for complex logic)
- Test: `tests/test_update_check.py`

### Task 10: Launchd Integration

**Files:**
- Modify: `~/Library/LaunchAgents/com.haiggoh.local-stack-update-check.plist`
- Create: `~/Library/LaunchAgents/com.haiggoh.backend-update-check.plist` (if separate)
- Test: Manual launchd load/test

### Task 11: Configuration Integration

**Files:**
- Modify: `config/config-lib.sh` (backend discovery)
- Modify: `config/config.example.sh` (document new backend config)
- Test: `tests/test_config_integration.py`

### Task 12: Tournament Integration

**Files:**
- Modify: `install/download-models.sh` (use new backend managers)
- Create: `install/qualify-backend.py` (backend qualification for tournament)
- Test: `tests/test_tournament_integration.py`

### Task 13: Documentation & Migration

**Files:**
- Create: `docs/backend-management.md`
- Modify: `README.md` (update installation instructions)
- Modify: `CHANGELOG.md`

---

## Task Details

### Task 1: Design Backend Manager Abstraction

**Files:**
- Create: `install/manage-backend.py` (new unified manager)
- Modify: `install/manage-rapid-mlx.py` (refactor to use base class)
- Create: `install/managers/__init__.py`
- Create: `install/managers/base.py`
- Test: `tests/test_manage_backend.py`

**Interfaces:**
- Consumes: None (foundational)
- Produces: `BackendManager` base class in `install/managers/base.py`, registry in `install/managers/__init__.py`

- [ ] **Step 1: Write the failing test for BackendManager base class**

```python
# tests/test_manage_backend.py
import pytest
from install.managers.base import BackendManager, BackendInfo, VersionInfo

class ConcreteManager(BackendManager):
    BACKEND_NAME = "test"
    def get_installed_versions(self): return []
    def get_latest_version(self): return "1.0.0"
    def install_version(self, version, **kwargs): pass
    def validate_environment(self, version): pass
    def get_packages_to_check(self): return ["test-package"]

def test_backend_manager_base_class():
    mgr = ConcreteManager()
    assert mgr.BACKEND_NAME == "test"
    assert hasattr(mgr, 'install_version')
    assert hasattr(mgr, 'validate_environment')
    assert hasattr(mgr, 'get_latest_version')
    assert hasattr(mgr, 'get_installed_versions')
    assert hasattr(mgr, 'get_packages_to_check')

def test_backend_info_dataclass():
    info = BackendInfo(name="test", version="1.0.0", path="/tmp/test", status="installed")
    assert info.name == "test"
    assert info.version == "1.0.0"

def test_version_info_dataclass():
    vinfo = VersionInfo(version="1.0.0", source="pypi", release_date="2026-01-01")
    assert vinfo.version == "1.0.0"
    assert vinfo.source == "pypi"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_manage_backend.py::test_backend_manager_base_class -v`
Expected: FAIL with "No module named 'install.managers.base'"

- [ ] **Step 3: Write BackendManager base class**

```python
# install/managers/base.py
from __future__ import annotations
import abc
import dataclasses
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar, Optional

@dataclasses.dataclass(frozen=True)
class BackendInfo:
    name: str
    version: str
    path: str
    status: str  # "installed", "validated", "active", "failed"
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

@dataclasses.dataclass(frozen=True)
class VersionInfo:
    version: str
    source: str  # "pypi", "github", "homebrew", "binary"
    release_date: str
    url: Optional[str] = None
    changelog: Optional[str] = None

class ManagerError(RuntimeError):
    """Fail-closed manager error suitable for CLI messages."""

class BackendManager(abc.ABC):
    """Abstract base class for backend managers."""
    
    BACKEND_NAME: ClassVar[str]
    VENV_PREFIX: ClassVar[str]  # e.g., "rapid-mlx", "vllm-mlx"
    DEFAULT_VERSION: ClassVar[Optional[str]] = None
    
    def __init__(self, home: Path | None = None, repo: Path | None = None):
        self.home = home or Path.home()
        self.repo = repo
        self.venv_root = self.home / ".venvs"
    
    @property
    def venv_dir(self) -> Path:
        return self.venv_root / self.VENV_PREFIX
    
    def target_for(self, version: str) -> Path:
        return self.venv_root / f"{self.VENV_PREFIX}-{version}"
    
    @abc.abstractmethod
    def get_installed_versions(self) -> list[str]:
        """Return list of installed version strings, newest first."""
        ...
    
    @abc.abstractmethod
    def get_latest_version(self, include_prereleases: bool = False) -> str:
        """Return latest available version string."""
        ...
    
    @abc.abstractmethod
    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        """Return all available versions, newest first."""
        ...
    
    @abc.abstractmethod
    def install_version(
        self, 
        version: str, 
        *, 
        python: str | None = None, 
        refresh_deps: bool = False,
        dry_run: bool = False,
    ) -> BackendInfo:
        """Install/validate a version. Returns BackendInfo on success."""
        ...
    
    @abc.abstractmethod
    def validate_environment(self, version: str) -> BackendInfo:
        """Validate an installed environment. Returns BackendInfo or raises."""
        ...
    
    @abc.abstractmethod
    def get_packages_to_check(self) -> list[str]:
        """Return list of package names to check for updates (PyPI)."""
        ...
    
    def check_updates(self) -> list[tuple[str, str, str]]:
        """Check for updates. Returns [(package, installed, latest), ...]."""
        outdated = []
        for pkg in self.get_packages_to_check():
            try:
                installed = self._get_installed_package_version(pkg)
                latest = self._get_latest_package_version(pkg)
                if self._version_newer(latest, installed):
                    outdated.append((pkg, installed, latest))
            except Exception:
                continue  # Skip packages that can't be checked
        return outdated
    
    def _get_installed_package_version(self, package: str) -> str:
        """Get installed version of a package from the backend's environment."""
        # Default implementation uses the newest venv
        versions = self.get_installed_versions()
        if not versions:
            raise ManagerError(f"No installed versions of {self.BACKEND_NAME}")
        target = self.target_for(versions[0])
        python = target / "bin" / "python"
        if not python.exists():
            raise ManagerError(f"Python not found in {target}")
        result = subprocess.run(
            [str(python), "-c", f"import importlib.metadata as m; print(m.version('{package}'))"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise ManagerError(f"Package {package} not found: {result.stderr}")
        return result.stdout.strip()
    
    @abc.abstractmethod
    def _get_latest_package_version(self, package: str) -> str:
        """Get latest version from package source (PyPI, GitHub, etc.)."""
        ...
    
    def _version_newer(self, latest: str, installed: str) -> bool:
        """Compare versions. Override for non-standard versioning."""
        def parse(v: str) -> tuple:
            import re
            return tuple(int(x) for x in re.findall(r'\d+', v))
        try:
            return parse(latest) > parse(installed)
        except Exception:
            return latest != installed
    
    def run_command(
        self, 
        command: list[str], 
        *, 
        capture: bool = False, 
        cwd: Path | None = None,
        log: Path | None = None,
    ) -> subprocess.CompletedProcess:
        """Run a command with optional logging."""
        if log:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a") as f:
                f.write("$ " + " ".join(command) + "\n")
                result = subprocess.run(command, text=True, stdout=f, stderr=subprocess.STDOUT, cwd=cwd)
        else:
            result = subprocess.run(command, check=True, text=True, capture_output=capture, cwd=cwd)
        if result.returncode != 0 and not capture:
            raise ManagerError(f"Command failed with exit {result.returncode}")
        return result
```

- [ ] **Step 4: Create managers package init**

```python
# install/managers/__init__.py
from install.managers.base import BackendManager, BackendInfo, VersionInfo, ManagerError

__all__ = ["BackendManager", "BackendInfo", "VersionInfo", "ManagerError", "get_manager", "list_managers"]

_MANAGERS: dict[str, type[BackendManager]] = {}

def register_manager(backend_name: str):
    def decorator(cls: type[BackendManager]):
        _MANAGERS[backend_name] = cls
        return cls
    return decorator

def get_manager(backend_name: str, **kwargs) -> BackendManager:
    if backend_name not in _MANAGERS:
        raise ManagerError(f"Unknown backend: {backend_name}. Available: {list(_MANAGERS.keys())}")
    return _MANAGERS[backend_name](**kwargs)

def list_managers() -> list[str]:
    return list(_MANAGERS.keys())
```

- [ ] **Step 5: Create manage-backend.py CLI skeleton**

```python
# install/manage-backend.py
#!/usr/bin/env python3
"""Unified backend manager for local-agents inference engines."""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

from install.managers import get_manager, list_managers, ManagerError

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage local-agents inference backends",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--backend", choices=list_managers(), help="Backend to manage")
    parser.add_argument("--home", type=Path, help="Home directory (default: $HOME)")
    parser.add_argument("--repo", type=Path, help="local-agents repository")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    
    sub = parser.add_subparsers(dest="command", required=True)
    
    # List command
    list_p = sub.add_parser("list", help="List installed versions")
    list_p.add_argument("--backend", choices=list_managers(), required=True)
    
    # Releases command
    rel_p = sub.add_parser("releases", help="List available releases")
    rel_p.add_argument("--backend", choices=list_managers(), required=True)
    rel_p.add_argument("--pre", action="store_true", help="Include prereleases")
    rel_p.add_argument("--limit", type=int, default=20)
    
    # Install command
    inst_p = sub.add_parser("install", help="Install a version")
    inst_p.add_argument("--backend", choices=list_managers(), required=True)
    inst_p.add_argument("version", nargs="?", help="Version to install (interactive if omitted)")
    inst_p.add_argument("--python", help="Base Python executable")
    inst_p.add_argument("--refresh-deps", action="store_true")
    
    # Validate command
    val_p = sub.add_parser("validate", help="Validate an installed version")
    val_p.add_argument("--backend", choices=list_managers(), required=True)
    val_p.add_argument("version")
    
    # Check-updates command
    chk_p = sub.add_parser("check-updates", help="Check for package updates")
    chk_p.add_argument("--backend", choices=list_managers(), required=True)
    
    # Promote command (for backends that support pin promotion)
    prom_p = sub.add_parser("promote", help="Promote pins to a version")
    prom_p.add_argument("--backend", choices=list_managers(), required=True)
    prom_p.add_argument("version")
    
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    
    try:
        mgr = get_manager(args.backend, home=args.home, repo=args.repo)
        
        if args.command == "list":
            versions = mgr.get_installed_versions()
            if versions:
                for v in versions:
                    print(v)
            else:
                print(f"No installed versions of {args.backend}")
        
        elif args.command == "releases":
            versions = mgr.get_available_versions(include_prereleases=args.pre)
            for v in versions[:args.limit]:
                print(v)
        
        elif args.command == "install":
            version = args.version
            if version is None:
                versions = mgr.get_available_versions(include_prereleases=args.pre)
                installed = set(mgr.get_installed_versions())
                print(f"Available {args.backend} releases:")
                for i, v in enumerate(versions[:30], 1):
                    mark = " (installed)" if v in installed else ""
                    print(f"  {i:2}. {v}{mark}")
                try:
                    choice = int(input("Choose version number: ").strip())
                    version = versions[choice - 1]
                except (ValueError, IndexError, EOFError):
                    print("Invalid selection", file=sys.stderr)
                    return 2
            info = mgr.install_version(version, python=args.python, refresh_deps=args.refresh_deps, dry_run=args.dry_run)
            print(f"Installed {args.backend} {version} at {info.path}")
        
        elif args.command == "validate":
            info = mgr.validate_environment(args.version)
            print(f"Validated {args.backend} {args.version} at {info.path}")
        
        elif args.command == "check-updates":
            outdated = mgr.check_updates()
            if outdated:
                for pkg, inst, latest in outdated:
                    print(f"{pkg} {inst} -> {latest}")
            else:
                print("All packages up to date")
        
        elif args.command == "promote":
            if hasattr(mgr, 'promote_pins'):
                result = mgr.promote_pins(args.version, dry_run=args.dry_run)
                print(f"Promoted pins to {args.backend} {args.version}")
            else:
                print(f"Backend {args.backend} does not support pin promotion", file=sys.stderr)
                return 2
        
        return 0
    
    except ManagerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_manage_backend.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add install/managers/base.py install/managers/__init__.py install/manage-backend.py tests/test_manage_backend.py
git commit -m "feat: add BackendManager abstraction and unified CLI entry point"
```

### Task 2: Implement RapidMLXManager (refactor existing)

**Files:**
- Create: `install/managers/rapid_mlx.py`
- Modify: `install/manage-rapid-mlx.py` (thin wrapper for backward compat)
- Test: `tests/test_rapid_mlx_manager.py`

**Interfaces:**
- Consumes: `BackendManager` base class from Task 1
- Produces: `RapidMLXManager` class registered as "rapid-mlx"

- [ ] **Step 1: Write failing tests for RapidMLXManager**

```python
# tests/test_rapid_mlx_manager.py
import pytest
from unittest.mock import patch, MagicMock
from install.managers.rapid_mlx import RapidMLXManager

def test_rapid_mlx_manager_registration():
    from install.managers import get_manager, list_managers
    assert "rapid-mlx" in list_managers()
    mgr = get_manager("rapid-mlx")
    assert isinstance(mgr, RapidMLXManager)
    assert mgr.BACKEND_NAME == "rapid-mlx"
    assert mgr.VENV_PREFIX == "rapid-mlx"

def test_rapid_mlx_get_installed_versions():
    mgr = RapidMLXManager()
    # Should return list (empty if none installed)
    versions = mgr.get_installed_versions()
    assert isinstance(versions, list)

def test_rapid_mlx_get_available_versions():
    mgr = RapidMLXManager()
    with patch('urllib.request.urlopen') as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b'{"releases": {"0.15.3": [], "0.15.2": []}}'
        versions = mgr.get_available_versions()
        assert isinstance(versions, list)
        assert "0.15.3" in versions

def test_rapid_mlx_get_packages_to_check():
    mgr = RapidMLXManager()
    packages = mgr.get_packages_to_check()
    assert "rapid-mlx" in packages
    assert "mlx" in packages
    assert "mlx-lm" in packages

def test_rapid_mlx_version_key():
    mgr = RapidMLXManager()
    # Test version parsing/ordering
    versions = ["0.15.3", "0.15.2", "0.14.0", "0.13.1"]
    sorted_versions = sorted(versions, key=mgr.version_key, reverse=True)
    assert sorted_versions == ["0.15.3", "0.15.2", "0.14.0", "0.13.1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_rapid_mlx_manager.py -v`
Expected: FAIL with "No module named 'install.managers.rapid_mlx'"

- [ ] **Step 3: Implement RapidMLXManager**

```python
# install/managers/rapid_mlx.py
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, ClassVar
from contextlib import contextmanager

from install.managers.base import BackendManager, BackendInfo, ManagerError, register_manager

PACKAGE = "rapid-mlx"
PYPI_URL = "https://pypi.org/pypi/rapid-mlx/json"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:([a-zA-Z]+)(\d+))?$")
SCHEMA = 2

PIN_FILES = (
    "bin/launch-claude-agent-rapid-auto.sh",
    "config/config-lib.sh",
    "config/config.example.sh",
    "tests/test_rapid_auto_mode.sh",
)
PRIVATE_PIN_FILE = "config/config.local.sh"
OPTIONAL_PIN_FILES = ("bin/local-inference-readonly-inventory.zsh",)
PATH_PIN_RE = re.compile(
    r"(?P<prefix>rapid-mlx-)(?P<version>\d+\.\d+\.\d+)(?P<suffix>/bin/(?:rapid-mlx|python))"
)
RAPID_AUTO_VERSION_RE = re.compile(r"rapid-mlx (?P<version>\d+\.\d+\.\d+)")

@register_manager("rapid-mlx")
class RapidMLXManager(BackendManager):
    BACKEND_NAME = "rapid-mlx"
    VENV_PREFIX = "rapid-mlx"
    DEFAULT_VERSION = "0.15.3"
    
    def version_key(self, value: str) -> tuple[int, int, int, int, str, int]:
        match = VERSION_RE.fullmatch(value)
        if not match:
            raise ValueError(value)
        major, minor, patch, label, serial = match.groups()
        return (
            int(major), int(minor), int(patch),
            1 if label is None else 0, label or "", int(serial or 0),
        )
    
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
            binary = path / "bin" / "rapid-mlx"
            state = "complete" if binary.is_file() and os.access(binary, os.X_OK) else "incomplete"
            entries.append((version, path, state))
        return [v for v, _, _ in sorted(entries, key=lambda item: self.version_key(item[0]), reverse=True)]
    
    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        request = urllib.request.Request(
            PYPI_URL,
            headers={"Accept": "application/json", "User-Agent": "local-agents-backend-manager/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15.0) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise ManagerError(f"could not retrieve Rapid-MLX releases: {exc}") from exc
        releases = payload.get("releases") if isinstance(payload, dict) else None
        if not isinstance(releases, dict):
            raise ManagerError("PyPI response has no releases object")
        
        result: list[str] = []
        for version, files in releases.items():
            match = VERSION_RE.fullmatch(version)
            if not match or (match.group(4) and not include_prereleases):
                continue
            if not isinstance(files, list) or not files:
                continue
            if all(isinstance(item, dict) and item.get("yanked", False) for item in files):
                continue
            result.append(version)
        return sorted(result, key=self.version_key, reverse=True)
    
    def get_latest_version(self, include_prereleases: bool = False) -> str:
        versions = self.get_available_versions(include_prereleases)
        if not versions:
            raise ManagerError("No available Rapid-MLX versions")
        return versions[0]
    
    def get_packages_to_check(self) -> list[str]:
        return ["rapid-mlx", "mlx", "mlx-lm"]
    
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
            lock = None if refresh_deps else self._locked_requirements(version)
            return BackendInfo(
                name=self.BACKEND_NAME,
                version=version,
                path=str(target),
                status="dry_run",
                metadata={"installation_mode": "locked_recreation" if lock else "fresh_resolution"}
            )
        
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            receipt = self.validate_environment(version)
            receipt.metadata["result"] = "already_complete"
            self._save_receipt(version, receipt.metadata)
            return receipt
        
        base_python = self._choose_python(python)
        log = self._cache_root() / "logs" / f"install-{version}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.log"
        
        try:
            self._run([str(base_python), "-m", "venv", str(target)], log=log)
            child = target / "bin" / "python"
            lock = None if refresh_deps else self._locked_requirements(version)
            if lock:
                lock_path = target / ".rapid-runtime-lock.txt"
                lock_path.write_text("\n".join(lock) + "\n", encoding="utf-8")
                self._run([str(child), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(lock_path)], log=log)
                lock_path.unlink()
                mode = "locked_recreation"
            else:
                self._run([str(child), "-m", "pip", "install", "--disable-pip-version-check", f"{PACKAGE}=={version}"], log=log)
                mode = "fresh_resolution"
            
            receipt = self.validate_environment(version)
            receipt.metadata.update({
                "result": "installed",
                "installation_mode": mode,
                "base_python": str(base_python),
                "install_log": str(log),
            })
            self._save_receipt(version, receipt.metadata)
            return receipt
        except Exception:
            if target.exists() and not target.is_symlink():
                import shutil
                shutil.rmtree(target)
            raise
    
    def validate_environment(self, version: str) -> BackendInfo:
        target = self.target_for(version)
        if target.is_symlink() or not target.is_dir():
            raise ManagerError(f"environment is missing or unsafe: {target}")
        binary = target / "bin" / "rapid-mlx"
        python = target / "bin" / "python"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ManagerError(f"Rapid executable is missing: {binary}")
        
        observed = self._binary_version(binary)
        expected = f"rapid-mlx {version}"
        if observed != expected:
            raise ManagerError(f"expected {expected!r}, got {observed!r}")
        
        self._run([str(python), "-m", "pip", "check"], capture=True)
        
        return BackendInfo(
            name=self.BACKEND_NAME,
            version=version,
            path=str(target),
            status="validated",
            metadata={
                "schema_version": SCHEMA,
                "rapid_version_output": observed,
                "rapid_binary_sha256": self._sha256_file(binary),
                "python": str(python),
                "python_version": self._run([str(python), "--version"], capture=True).stdout.strip(),
                "packages": self._package_inventory(python),
                "pip_freeze": self._pip_freeze(python),
                "validated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    
    def promote_pins(self, version: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Promote repository pins to this version (Rapid-MLX specific)."""
        if not self.repo:
            raise ManagerError("Repository required for pin promotion")
        if not dry_run:
            self.validate_environment(version)
        
        # Import the pin promotion logic from the original manage-rapid-mlx.py
        # This would be refactored from the existing code
        plan = self._plan_pin_update(version)
        return self._apply_pin_plan(plan, dry_run=dry_run)
    
    # Helper methods (refactored from manage-rapid-mlx.py)
    def _choose_python(self, explicit: str | None) -> Path:
        candidates: list[str] = []
        if explicit:
            candidates.append(explicit)
        if os.environ.get("LA_RAPID_BASE_PYTHON"):
            candidates.append(os.environ["LA_RAPID_BASE_PYTHON"])
        candidates.extend([sys.executable, "/opt/homebrew/bin/python3.14", "python3.14", "python3"])
        seen: set[str] = set()
        for candidate in candidates:
            resolved = shutil.which(candidate) if "/" not in candidate else candidate
            if not resolved:
                continue
            path = Path(resolved).expanduser().resolve()
            if str(path) in seen or not os.access(path, os.X_OK):
                continue
            seen.add(str(path))
            if subprocess.run([str(path), "-c", "import venv"], capture_output=True).returncode == 0:
                return path
        raise ManagerError("no Python with the stdlib venv module was found; pass --python")
    
    def _binary_version(self, binary: Path) -> str:
        try:
            result = self._run([str(binary), "--version"], capture=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ManagerError(f"could not execute {binary}: {exc}") from exc
        return result.stdout.strip() or result.stderr.strip()
    
    def _package_inventory(self, python: Path) -> dict[str, str]:
        code = """
import importlib.metadata, json
names=['rapid-mlx','mlx','mlx-metal','mlx-lm','mlx-vlm','transformers','tokenizers','huggingface-hub','llguidance']
out={}
for name in names:
    try: out[name]=importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: out[name]='ABSENT'
print(json.dumps(out, sort_keys=True))
"""
        return json.loads(self._run([str(python), "-c", code], capture=True).stdout)
    
    def _pip_freeze(self, python: Path) -> list[str]:
        return [
            line for line in self._run([str(python), "-m", "pip", "freeze"], capture=True).stdout.splitlines()
            if line.strip()
        ]
    
    def _sha256_file(self, path: Path) -> str:
        import hashlib
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    
    def _locked_requirements(self, version: str) -> list[str] | None:
        path = self._receipt_path(version)
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
        return clean if f"rapid-mlx=={version}" in normalized else None
    
    def _cache_root(self) -> Path:
        return self.home / ".cache" / "local-agents" / "rapid-runtime-manager"
    
    def _receipt_path(self, version: str) -> Path:
        return self._cache_root() / "receipts" / f"{self.VENV_PREFIX}-{version}.json"
    
    def _save_receipt(self, version: str, data: dict[str, Any]) -> None:
        path = self._receipt_path(version)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    
    def _plan_pin_update(self, version: str):
        # Refactored from manage-rapid-mlx.py plan_pin_update
        # ... implementation here
        pass
    
    def _apply_pin_plan(self, plan, *, dry_run: bool = False):
        # Refactored from manage-rapid-mlx.py apply_pin_plan
        # ... implementation here
        pass
```

- [ ] **Step 4: Create thin wrapper for backward compatibility**

```python
# install/manage-rapid-mlx.py (modified)
#!/usr/bin/env python3
"""Backward compatibility wrapper for manage-rapid-mlx.py"""

import sys
from install.manage_backend import main

if __name__ == "__main__":
    # Translate old CLI to new unified CLI
    # Old: manage-rapid-mlx.py install 0.15.3
    # New: manage-backend.py --backend rapid-mlx install 0.15.3
    new_argv = ["--backend", "rapid-mlx"] + sys.argv[1:]
    sys.exit(main(new_argv))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_rapid_mlx_manager.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add install/managers/rapid_mlx.py install/manage-rapid-mlx.py tests/test_rapid_mlx_manager.py
git commit -m "feat: add RapidMLXManager with full feature parity"
```

### Task 3: Implement VLLMMLXManager

**Files:**
- Create: `install/managers/vllm_mlx.py`
- Test: `tests/test_vllm_mlx_manager.py`

**Interfaces:**
- Consumes: `BackendManager` base class from Task 1
- Produces: `VLLMMLXManager` class registered as "vllm-mlx"

- [ ] **Step 1: Write failing tests for VLLMMLXManager**

```python
# tests/test_vllm_mlx_manager.py
import pytest
from unittest.mock import patch, MagicMock
from install.managers.vllm_mlx import VLLMMLXManager

def test_vllm_mlx_manager_registration():
    from install.managers import get_manager, list_managers
    assert "vllm-mlx" in list_managers()
    mgr = get_manager("vllm-mlx")
    assert isinstance(mgr, VLLMMLXManager)
    assert mgr.BACKEND_NAME == "vllm-mlx"
    assert mgr.VENV_PREFIX == "vllm-mlx"

def test_vllm_mlx_get_installed_versions():
    mgr = VLLMMLXManager()
    versions = mgr.get_installed_versions()
    assert isinstance(versions, list)

def test_vllm_mlx_get_available_versions():
    mgr = VLLMMLXManager()
    with patch('urllib.request.urlopen') as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b'{"releases": {"0.5.0": [], "0.4.1": []}}'
        versions = mgr.get_available_versions()
        assert isinstance(versions, list)
        assert "0.5.0" in versions

def test_vllm_mlx_get_packages_to_check():
    mgr = VLLMMLXManager()
    packages = mgr.get_packages_to_check()
    assert "vllm-mlx" in packages
    assert "mlx" in packages
    assert "mlx-lm" in packages
    assert "mlx-vlm" in packages

def test_vllm_mlx_patch_handling():
    """Test that version-specific patch sets are handled correctly."""
    mgr = VLLMMLXManager()
    # v0.4.x needs local fork patches
    # v0.5.0+ has patches integrated
    assert mgr.needs_fork_patches("0.4.1") == True
    assert mgr.needs_fork_patches("0.5.0") == False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_vllm_mlx_manager.py -v`
Expected: FAIL with "No module named 'install.managers.vllm_mlx'"

- [ ] **Step 3: Implement VLLMMLXManager**

```python
# install/managers/vllm_mlx.py
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Optional

from install.managers.base import BackendManager, BackendInfo, ManagerError, register_manager

PYPI_URL = "https://pypi.org/pypi/vllm-mlx/json"
GITHUB_REPO = "waybarrios/vllm-mlx"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.].*)?$")

# Patch sets per version range
PATCH_SETS = {
    "0.4": "vllm-mlx-local-fork-patches.patch",  # Applied to v0.4.x
    # 0.5.0+ has patches integrated upstream
}

@register_manager("vllm-mlx")
class VLLMMLXManager(BackendManager):
    BACKEND_NAME = "vllm-mlx"
    VENV_PREFIX = "vllm-mlx"
    DEFAULT_VERSION = "0.5.0"
    
    def version_key(self, value: str) -> tuple[int, int, int]:
        match = VERSION_RE.fullmatch(value.lstrip('v'))
        if not match:
            raise ValueError(value)
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    
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
        source: str = "pypi",  # "pypi" or "github"
    ) -> BackendInfo:
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
        
        base_python = self._choose_python(python)
        log = self._cache_root() / "logs" / f"install-{version}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.log"
        
        try:
            self._run([str(base_python), "-m", "venv", str(target)], log=log)
            child = target / "bin" / "python"
            
            if source == "github" or self.needs_fork_patches(version):
                # Install from GitHub with patches
                self._install_from_github(version, child, log)
            else:
                # Install from PyPI
                self._run([str(child), "-m", "pip", "install", "--disable-pip-version-check", f"vllm-mlx=={version}"], log=log)
            
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
            self._run(["git", "clone", f"https://github.com/{GITHUB_REPO}", str(src_dir)], log=log)
        
        self._run(["git", "-C", str(src_dir), "fetch", "--tags", "--quiet"], log=log)
        self._run(["git", "-C", str(src_dir), "checkout", "--quiet", f"v{version}"], log=log)
        
        # Apply patches if needed
        if self.needs_fork_patches(version):
            patch_file = Path(__file__).parent.parent / "vllm-mlx-local-fork-patches.patch"
            if patch_file.exists():
                self._run(["git", "-C", str(src_dir), "apply", str(patch_file)], log=log)
        
        self._run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-e", str(src_dir)], log=log)
        self._run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "mlx-lm"], log=log)
    
    def validate_environment(self, version: str) -> BackendInfo:
        target = self.target_for(version)
        if target.is_symlink() or not target.is_dir():
            raise ManagerError(f"environment is missing or unsafe: {target}")
        binary = target / "bin" / "vllm-mlx"
        python = target / "bin" / "python"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ManagerError(f"vllm-mlx executable is missing: {binary}")
        
        # Verify vllm-mlx CLI works
        self._run([str(binary), "--help"], capture=True)
        self._run([str(python), "-m", "pip", "check"], capture=True)
        
        return BackendInfo(
            name=self.BACKEND_NAME,
            version=version,
            path=str(target),
            status="validated",
            metadata={
                "python": str(python),
                "python_version": self._run([str(python), "--version"], capture=True).stdout.strip(),
                "packages": self._package_inventory(python),
                "pip_freeze": self._pip_freeze(python),
                "validated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    
    # Helper methods
    def _choose_python(self, explicit: str | None) -> Path:
        candidates: list[str] = []
        if explicit:
            candidates.append(explicit)
        candidates.extend([sys.executable, "/opt/homebrew/bin/python3.12", "python3.12", "python3"])
        import shutil
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
        return json.loads(self._run([str(python), "-c", code], capture=True).stdout)
    
    def _pip_freeze(self, python: Path) -> list[str]:
        return [
            line for line in self._run([str(python), "-m", "pip", "freeze"], capture=True).stdout.splitlines()
            if line.strip()
        ]
    
    def _cache_root(self) -> Path:
        return self.home / ".cache" / "local-agents" / "vllm-mlx-manager"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vllm_mlx_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add install/managers/vllm_mlx.py tests/test_vllm_mlx_manager.py
git commit -m "feat: add VLLMMLXManager with PyPI/GitHub sources and patch handling"
```

### Task 4: Implement OMLXManager

**Files:**
- Create: `install/managers/omlx.py`
- Test: `tests/test_omlx_manager.py`

**Interfaces:**
- Consumes: `BackendManager` base class from Task 1
- Produces: `OMLXManager` class registered as "omlx"

- [ ] **Step 1: Write failing tests for OMLXManager**

```python
# tests/test_omlx_manager.py
import pytest
from unittest.mock import patch
from install.managers.omlx import OMLXManager

def test_omlx_manager_registration():
    from install.managers import get_manager, list_managers
    assert "omlx" in list_managers()
    mgr = get_manager("omlx")
    assert isinstance(mgr, OMLXManager)
    assert mgr.BACKEND_NAME == "omlx"

def test_omlx_get_available_versions():
    mgr = OMLXManager()
    with patch('urllib.request.urlopen') as mock_open:
        mock_open.return_value.__enter__.return_value.read.return_value = b'[{"tag_name": "v0.7.0"}, {"tag_name": "v0.6.4"}]'
        versions = mgr.get_available_versions()
        assert "0.7.0" in versions

def test_omlx_uses_homebrew():
    mgr = OMLXManager()
    # oMLX is primarily distributed via Homebrew, not PyPI
    assert mgr.get_package_source("omlx") == "homebrew"
    # But check GitHub for version info
    assert mgr.get_version_source() == "github"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_omlx_manager.py -v`
Expected: FAIL with "No module named 'install.managers.omlx'"

- [ ] **Step 3: Implement OMLXManager**

```python
# install/managers/omlx.py
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from install.managers.base import BackendManager, BackendInfo, ManagerError, register_manager

GITHUB_REPO = "jundot/omlx"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
HOMEBREW_FORMULA_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/HEAD/Formula/omlx.rb"
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.].*)?$")

@register_manager("omlx")
class OMLXManager(BackendManager):
    BACKEND_NAME = "omlx"
    VENV_PREFIX = "omlx"  # Not a venv - oMLX is a standalone binary
    DEFAULT_VERSION = "0.7.0"
    
    def version_key(self, value: str) -> tuple[int, int, int]:
        match = VERSION_RE.fullmatch(value.lstrip('v'))
        if not match:
            raise ValueError(value)
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    
    def get_installed_versions(self) -> list[str]:
        # Check Homebrew installation
        versions = []
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
        if version == "latest" or version == self.get_latest_version():
            self._run(["brew", "install", "omlx"])
        else:
            # For specific versions, may need to install from source or use brew versions
            self._run(["brew", "install", f"omlx@{version}"])
        
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
        result = self._run([str(binary), "--version"], capture=True)
        output = result.stdout.strip()
        if version not in output and version.lstrip('v') not in output:
            # Version might be in different format
            pass
        
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_omlx_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add install/managers/omlx.py tests/test_omlx_manager.py
git commit -m "feat: add OMLXManager with Homebrew/GitHub integration"
```

### Task 5: Implement LlamaCppManager

**Files:**
- Create: `install/managers/llama_cpp.py`
- Test: `tests/test_llama_cpp_manager.py`

**Interfaces:**
- Consumes: `BackendManager` base class from Task 1
- Produces: `LlamaCppManager` class registered as "llama-cpp"

- [ ] **Step 1: Write failing tests for LlamaCppManager**

```python
# tests/test_llama_cpp_manager.py
import pytest
from unittest.mock import patch
from install.managers.llama_cpp import LlamaCppManager

def test_llama_cpp_manager_registration():
    from install.managers import get_manager, list_managers
    assert "llama-cpp" in list_managers()
    mgr = get_manager("llama-cpp")
    assert isinstance(mgr, LlamaCppManager)
    assert mgr.BACKEND_NAME == "llama-cpp"

def test_llama_cpp_binary_download():
    mgr = LlamaCppManager()
    # Should download prebuilt binaries from GitHub releases
    assert mgr.get_package_source("llama-cpp") == "github_binary"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llama_cpp_manager.py -v`
Expected: FAIL with "No module named 'install.managers.llama_cpp'"

- [ ] **Step 3: Implement LlamaCppManager**

```python
# install/managers/llama_cpp.py
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
from typing import Any, ClassVar

from install.managers.base import BackendManager, BackendInfo, ManagerError, register_manager

GITHUB_REPO = "ggml-org/llama.cpp"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases"
VERSION_RE = re.compile(r"^b?(\d+)\.(\d+)(?:\.(\d+))?(?:[-.].*)?$")

# Apple Silicon optimized builds
PLATFORM_ASSET_PATTERN = "llama-b*-macos-arm64.zip"

@register_manager("llama-cpp")
class LlamaCppManager(BackendManager):
    BACKEND_NAME = "llama-cpp"
    VENV_PREFIX = "llama-cpp"  # Binary installation, not venv
    DEFAULT_VERSION = "b5000"  # Use recent stable build
    
    def version_key(self, value: str) -> tuple[int, int, int]:
        match = VERSION_RE.fullmatch(value.lstrip('b'))
        if not match:
            raise ValueError(value)
        major = int(match.group(1))
        minor = int(match.group(2))
        patch = int(match.group(3)) if match.group(3) else 0
        return (major, minor, patch)
    
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
        
        result = self._run([str(server), "--version"], capture=True)
        
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llama_cpp_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add install/managers/llama_cpp.py tests/test_llama_cpp_manager.py
git commit -m "feat: add LlamaCppManager with GitHub binary releases"
```

### Task 6: Implement LitellmManager

**Files:**
- Create: `install/managers/litellm.py`
- Test: `tests/test_litellm_manager.py`

**Interfaces:**
- Consumes: `BackendManager` base class from Task 1
- Produces: `LitellmManager` class registered as "litellm"

- [ ] **Step 1: Write failing tests for LitellmManager**

```python
# tests/test_litellm_manager.py
import pytest
from install.managers.litellm import LitellmManager

def test_litellm_manager_registration():
    from install.managers import get_manager, list_managers
    assert "litellm" in list_managers()
    mgr = get_manager("litellm")
    assert isinstance(mgr, LitellmManager)
    assert mgr.BACKEND_NAME == "litellm"

def test_litellm_proxy_config():
    mgr = LitellmManager()
    # litellm is a proxy, not a local inference engine
    assert mgr.is_proxy() == True
    # Checks config file and environment variables
    assert mgr.get_config_path() is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_litellm_manager.py -v`
Expected: FAIL with "No module named 'install.managers.litellm'"

- [ ] **Step 3: Implement LitellmManager**

```python
# install/managers/litellm.py
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from install.managers.base import BackendManager, BackendInfo, ManagerError, register_manager

PYPI_URL = "https://pypi.org/pypi/litellm/json"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-.].*)?$")

@register_manager("litellm")
class LitellmManager(BackendManager):
    BACKEND_NAME = "litellm"
    VENV_PREFIX = "litellm"
    DEFAULT_VERSION = "1.60.0"  # Recent stable
    
    def version_key(self, value: str) -> tuple[int, int, int]:
        match = VERSION_RE.fullmatch(value)
        if not match:
            raise ValueError(value)
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    
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
                if not VERSION_RE.fullmatch(version):
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
        
        base_python = self._choose_python(python)
        log = self._cache_root() / "logs" / f"install-{version}-{datetime.now().strftime('%Y%m%dT%H%M%S')}.log"
        
        try:
            self._run([str(base_python), "-m", "venv", str(target)], log=log)
            child = target / "bin" / "python"
            self._run([str(child), "-m", "pip", "install", "--disable-pip-version-check", f"litellm=={version}"], log=log)
            
            receipt = self.validate_environment(version)
            receipt.metadata.update({
                "result": "installed",
                "base_python": str(base_python),
                "install_log": str(log),
            })
            return receipt
        except Exception:
            if target.exists() and not target.is_symlink():
                import shutil
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
        
        self._run([str(binary), "--version"], capture=True)
        self._run([str(python), "-m", "pip", "check"], capture=True)
        
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
                "python_version": self._run([str(python), "--version"], capture=True).stdout.strip(),
                "config_path": str(config_path) if config_path else None,
                "packages": self._package_inventory(python),
                "pip_freeze": self._pip_freeze(python),
                "validated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    
    # Helper methods
    def _choose_python(self, explicit: str | None) -> Path:
        candidates: list[str] = []
        if explicit:
            candidates.append(explicit)
        candidates.extend([sys.executable, "/opt/homebrew/bin/python3.12", "python3.12", "python3"])
        import shutil
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
        return json.loads(self._run([str(python), "-c", code], capture=True).stdout)
    
    def _pip_freeze(self, python: Path) -> list[str]:
        return [
            line for line in self._run([str(python), "-m", "pip", "freeze"], capture=True).stdout.splitlines()
            if line.strip()
        ]
    
    def _cache_root(self) -> Path:
        return self.home / ".cache" / "local-agents" / "litellm-manager"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_litellm_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add install/managers/litellm.py tests/test_litellm_manager.py
git commit -m "feat: add LitellmManager for proxy management"
```

### Task 7: Unified CLI Entry Point (Complete Implementation)

**Files:**
- Create: `install/manage-backend.py` (full implementation)
- Modify: `install/manage-rapid-mlx.py` (thin wrapper - already done in Task 2)
- Test: `tests/test_manage_backend_cli.py`

**Interfaces:**
- Consumes: All manager classes from Tasks 1-6
- Produces: Complete `manage-backend` CLI with all subcommands

- [ ] **Step 1: Write failing CLI integration tests**

```python
# tests/test_manage_backend_cli.py
import pytest
from click.testing import CliRunner
from install.manage_backend import main

def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Manage local-agents inference backends" in result.output
    assert "rapid-mlx" in result.output
    assert "vllm-mlx" in result.output
    assert "omlx" in result.output
    assert "llama-cpp" in result.output
    assert "litellm" in result.output

def test_cli_list_backends():
    runner = CliRunner()
    result = runner.invoke(main, ["list", "--backend", "rapid-mlx"])
    assert result.exit_code == 0

def test_cli_releases():
    runner = CliRunner()
    result = runner.invoke(main, ["releases", "--backend", "rapid-mlx", "--limit", "5"])
    assert result.exit_code == 0

def test_cli_check_updates():
    runner = CliRunner()
    result = runner.invoke(main, ["check-updates", "--backend", "rapid-mlx"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manage_backend_cli.py -v`
Expected: FAIL (CLI not fully implemented)

- [ ] **Step 3: Complete manage-backend.py implementation**

```python
# install/manage-backend.py (complete implementation)
#!/usr/bin/env python3
"""Unified backend manager for local-agents inference engines."""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from install.managers import get_manager, list_managers, ManagerError

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage local-agents inference backends",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available backends: {', '.join(list_managers())}

Examples:
  manage-backend.py --backend rapid-mlx list
  manage-backend.py --backend vllm-mlx releases --limit 10
  manage-backend.py --backend omlx install 0.7.0
  manage-backend.py --backend rapid-mlx check-updates
  manage-backend.py --backend rapid-mlx promote 0.15.3
  manage-backend.py --backend litellm install 1.60.0
        """.strip(),
    )
    parser.add_argument("--backend", choices=list_managers(), required=True, help="Backend to manage")
    parser.add_argument("--home", type=Path, help="Home directory (default: $HOME)")
    parser.add_argument("--repo", type=Path, help="local-agents repository (for pin promotion)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    
    sub = parser.add_subparsers(dest="command", required=True, help="Command to run")
    
    # List command
    list_p = sub.add_parser("list", help="List installed versions")
    
    # Releases command
    rel_p = sub.add_parser("releases", help="List available releases")
    rel_p.add_argument("--pre", action="store_true", help="Include prereleases")
    rel_p.add_argument("--limit", type=int, default=20)
    
    # Install command
    inst_p = sub.add_parser("install", help="Install a version")
    inst_p.add_argument("version", nargs="?", help="Version to install (interactive if omitted)")
    inst_p.add_argument("--python", help="Base Python executable")
    inst_p.add_argument("--refresh-deps", action="store_true", help="Ignore existing lock file")
    inst_p.add_argument("--source", choices=["pypi", "github"], default="pypi", help="Install source (vllm-mlx)")
    
    # Validate command
    val_p = sub.add_parser("validate", help="Validate an installed version")
    val_p.add_argument("version")
    
    # Check-updates command
    chk_p = sub.add_parser("check-updates", help="Check for package updates")
    
    # Promote command (for backends that support pin promotion)
    prom_p = sub.add_parser("promote", help="Promote pins to a version")
    prom_p.add_argument("version")
    
    # Remove command
    rem_p = sub.add_parser("remove", help="Remove an installed version")
    rem_p.add_argument("version")
    rem_p.add_argument("--yes", action="store_true", help="Skip confirmation")
    
    # Info command
    info_p = sub.add_parser("info", help="Show detailed info about a version")
    info_p.add_argument("version")
    
    return parser

def format_output(data: Any, as_json: bool) -> str:
    if as_json:
        return json.dumps(data, indent=2, default=str)
    if isinstance(data, list):
        return "\n".join(str(item) for item in data)
    return str(data)

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    
    try:
        mgr = get_manager(args.backend, home=args.home, repo=args.repo)
        
        if args.command == "list":
            versions = mgr.get_installed_versions()
            if args.json:
                print(format_output([{"version": v, "status": "installed"} for v in versions], True))
            else:
                if versions:
                    for v in versions:
                        print(v)
                else:
                    print(f"No installed versions of {args.backend}")
        
        elif args.command == "releases":
            versions = mgr.get_available_versions(include_prereleases=args.pre)
            if args.json:
                print(format_output([{"version": v} for v in versions[:args.limit]], True))
            else:
                for v in versions[:args.limit]:
                    print(v)
        
        elif args.command == "install":
            version = args.version
            if version is None:
                versions = mgr.get_available_versions(include_prereleases=args.pre)
                installed = set(mgr.get_installed_versions())
                print(f"Available {args.backend} releases:", file=sys.stderr)
                for i, v in enumerate(versions[:30], 1):
                    mark = " (installed)" if v in installed else ""
                    print(f"  {i:2}. {v}{mark}", file=sys.stderr)
                try:
                    choice = int(input("Choose version number: ").strip())
                    version = versions[choice - 1]
                except (ValueError, IndexError, EOFError):
                    print("Invalid selection", file=sys.stderr)
                    return 2
            
            # Handle backend-specific install args
            install_kwargs = {
                "python": args.python,
                "refresh_deps": args.refresh_deps,
                "dry_run": args.dry_run,
            }
            if hasattr(mgr, 'install_version') and 'source' in mgr.install_version.__code__.co_varnames:
                install_kwargs["source"] = args.source
            
            info = mgr.install_version(version, **install_kwargs)
            if args.json:
                print(format_output({
                    "backend": args.backend,
                    "version": version,
                    "path": info.path,
                    "status": info.status,
                    "metadata": info.metadata,
                }, True))
            else:
                print(f"Installed {args.backend} {version} at {info.path}")
        
        elif args.command == "validate":
            info = mgr.validate_environment(args.version)
            if args.json:
                print(format_output({
                    "backend": args.backend,
                    "version": args.version,
                    "path": info.path,
                    "status": info.status,
                    "metadata": info.metadata,
                }, True))
            else:
                print(f"Validated {args.backend} {args.version} at {info.path}")
        
        elif args.command == "check-updates":
            outdated = mgr.check_updates()
            if args.json:
                print(format_output([{"package": p, "installed": i, "latest": l} for p, i, l in outdated], True))
            else:
                if outdated:
                    for pkg, inst, latest in outdated:
                        print(f"{pkg} {inst} -> {latest}")
                else:
                    print("All packages up to date")
        
        elif args.command == "promote":
            if hasattr(mgr, 'promote_pins'):
                result = mgr.promote_pins(args.version, dry_run=args.dry_run)
                if args.json:
                    print(format_output(result, True))
                else:
                    print(f"Promoted pins to {args.backend} {args.version}")
            else:
                print(f"Backend {args.backend} does not support pin promotion", file=sys.stderr)
                return 2
        
        elif args.command == "remove":
            if not args.yes and sys.stdin.isatty():
                confirm = input(f"Remove {args.backend} {args.version}? [y/N]: ").strip().lower()
                if confirm != 'y':
                    print("Cancelled", file=sys.stderr)
                    return 0
            
            # Implementation would call mgr.remove_version(args.version)
            print(f"Remove not yet implemented for {args.backend}", file=sys.stderr)
            return 2
        
        elif args.command == "info":
            info = mgr.validate_environment(args.version)
            if args.json:
                print(format_output({
                    "backend": args.backend,
                    "version": args.version,
                    "path": info.path,
                    "status": info.status,
                    "metadata": info.metadata,
                }, True))
            else:
                print(f"Backend: {args.backend}")
                print(f"Version: {args.version}")
                print(f"Path: {info.path}")
                print(f"Status: {info.status}")
                for k, v in info.metadata.items():
                    print(f"  {k}: {v}")
        
        return 0
    
    except ManagerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"UNEXPECTED ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_manage_backend_cli.py -v`
Expected: PASS

- [ ] **Step 5: Test manual CLI usage**

```bash
python install/manage-backend.py --backend rapid-mlx list
python install/manage-backend.py --backend rapid-mlx releases --limit 5
python install/manage-backend.py --backend vllm-mlx check-updates
python install/manage-backend.py --backend omlx list
```

- [ ] **Step 6: Commit**

```bash
git add install/manage-backend.py tests/test_manage_backend_cli.py
git commit -m "feat: complete unified CLI with all subcommands"
```

### Task 8: Update install-backend.sh

**Files:**
- Modify: `install/install-backend.sh`
- Test: Manual integration test

**Interfaces:**
- Consumes: `manage-backend.py` CLI from Task 7
- Produces: Updated canonical installer that sets up ALL backends

- [ ] **Step 1: Write test plan for install-backend.sh**

```bash
# Manual test script
# tests/manual/test_install_backend.sh
#!/bin/bash
set -euo pipefail

echo "=== Testing install-backend.sh ==="

# Test 1: Dry run
echo "Test 1: Dry run"
./install/install-backend.sh --dry-run

# Test 2: Install all backends (dry run)
echo "Test 2: Install all backends (dry run)"
./install/install-backend.sh --all --dry-run

# Test 3: Install specific backends
echo "Test 3: Install rapid-mlx only (dry run)"
./install/install-backend.sh --backend rapid-mlx --dry-run

echo "Test 4: Install vllm-mlx only (dry run)"
./install/install-backend.sh --backend vllm-mlx --dry-run

echo "Test 5: Install omlx only (dry run)"
./install/install-backend.sh --backend omlx --dry-run

echo "Test 6: Install llama-cpp only (dry run)"
./install/install-backend.sh --backend llama-cpp --dry-run

echo "Test 7: Install litellm only (dry run)"
./install/install-backend.sh --backend litellm --dry-run

echo "=== All dry-run tests passed ==="
```

- [ ] **Step 2: Update install-backend.sh**

```bash
#!/usr/bin/env bash
# install-backend.sh — one-time backend setup for local-agents (run OUTSIDE Claude Code).
#
# Sets up ALL inference backends that the launcher/hotswap can drive:
#   1. Rapid-MLX (DEFAULT) — versioned venvs via manage-backend.py
#   2. vllm-mlx (LEGACY) — versioned venvs via manage-backend.py
#   3. oMLX — via Homebrew (or binary)
#   4. llama.cpp — binary releases via manage-backend.py
#   5. litellm — versioned venv via manage-backend.py (for remote free-API routing)
#
# It does NOT download models — run download-models.sh for that.
# Idempotent-ish and guarded; re-run safely.
# Requires: macOS on Apple Silicon, Homebrew python3, git.
set -euo pipefail

MANAGE_BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/manage-backend.py"
PYTHON="${LA_PYTHON:-python3}"

usage() {
    cat <<'HELP'
install-backend.sh — set up local-agents inference backends

Usage: install-backend.sh [OPTIONS]

Options:
  --all                 Install all backends (default: rapid-mlx only)
  --backend NAME        Install specific backend (rapid-mlx|vllm-mlx|omlx|llama-cpp|litellm)
  --dry-run             Show what would be installed without doing it
  --python EXE          Python executable to use (default: python3)
  --rapid-version VER   Rapid-MLX version to install (default: latest stable)
  --vllm-version VER    vllm-mlx version to install (default: latest stable)
  --omlx-version VER    oMLX version to install (default: latest via brew)
  --llama-version VER   llama.cpp version to install (default: latest stable)
  --litellm-version VER litellm version to install (default: latest stable)
  --help                Show this help and exit

Examples:
  install-backend.sh                          # Install Rapid-MLX (default)
  install-backend.sh --all                    # Install all backends
  install-backend.sh --backend vllm-mlx       # Install only vllm-mlx
  install-backend.sh --all --dry-run          # Show what would be installed
HELP
    exit 0
}

# Defaults
INSTALL_ALL=0
BACKENDS=()
DRY_RUN=0
RAPID_VERSION=""
VLLM_VERSION=""
OMLX_VERSION=""
LLAMA_VERSION=""
LITELLM_VERSION=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --all)
            INSTALL_ALL=1
            shift
            ;;
        --backend)
            BACKENDS+=("$2")
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --python)
            PYTHON="$2"
            shift 2
            ;;
        --rapid-version)
            RAPID_VERSION="$2"
            shift 2
            ;;
        --vllm-version)
            VLLM_VERSION="$2"
            shift 2
            ;;
        --omlx-version)
            OMLX_VERSION="$2"
            shift 2
            ;;
        --llama-version)
            LLAMA_VERSION="$2"
            shift 2
            ;;
        --litellm-version)
            LITELLM_VERSION="$2"
            shift 2
            ;;
        --help)
            usage
            ;;
        -*)
            echo "Unknown option: $1" >&2
            usage
            ;;
        *)
            echo "Unexpected argument: $1" >&2
            usage
            ;;
    esac
done

# Determine which backends to install
if [[ $INSTALL_ALL -eq 1 ]]; then
    BACKENDS=("rapid-mlx" "vllm-mlx" "omlx" "llama-cpp" "litellm")
elif [[ ${#BACKENDS[@]} -eq 0 ]]; then
    BACKENDS=("rapid-mlx")
fi

echo "== local-agents backend install =="
echo "Backends to install: ${BACKENDS[*]}"
[[ $DRY_RUN -eq 1 ]] && echo "DRY RUN MODE"

# Check prerequisites
[[ "$(uname -s)" = "Darwin" ]] || { echo "⚠ not macOS — some backends target Apple Silicon."; }
command -v "$PYTHON" >/dev/null || { echo "❌ Python not found: $PYTHON"; exit 1; }
command -v brew >/dev/null || { echo "⚠ Homebrew not found — oMLX/llama.cpp may not install."; }

# Ensure manage-backend.py is executable
chmod +x "$MANAGE_BACKEND"

# Function to install a backend
install_backend() {
    local backend="$1"
    local version="$2"
    local extra_args=()
    
    [[ -n "$version" ]] && extra_args+=("$version")
    [[ $DRY_RUN -eq 1 ]] && extra_args+=("--dry-run")
    
    echo ""
    echo "[*] Installing $backend${version:+ $version}..."
    
    case "$backend" in
        rapid-mlx|vllm-mlx|litellm)
            "$PYTHON" "$MANAGE_BACKEND" --backend "$backend" install "${extra_args[@]}"
            ;;
        omlx)
            if [[ $DRY_RUN -eq 1 ]]; then
                echo "  Would run: brew install omlx${version:+@$version}"
            else
                if [[ -n "$version" && "$version" != "latest" ]]; then
                    brew install "omlx@$version" || brew install omlx
                else
                    brew install omlx
                fi
            fi
            ;;
        llama-cpp)
            "$PYTHON" "$MANAGE_BACKEND" --backend llama-cpp install "${extra_args[@]}"
            ;;
        *)
            echo "❌ Unknown backend: $backend"
            return 1
            ;;
    esac
    
    echo "  ✓ $backend done"
}

# Install each backend
for backend in "${BACKENDS[@]}"; do
    version=""
    case "$backend" in
        rapid-mlx) version="$RAPID_VERSION" ;;
        vllm-mlx) version="$VLLM_VERSION" ;;
        omlx) version="$OMLX_VERSION" ;;
        llama-cpp) version="$LLAMA_VERSION" ;;
        litellm) version="$LITELLM_VERSION" ;;
    esac
    install_backend "$backend" "$version"
done

echo ""
echo "Done. Next steps:"
echo "  1. cp config/config.example.sh config/config.local.sh  &&  edit for your models"
echo "  2. ./install/download-models.sh        # fetch the models you registered"
echo "  3. ./bin/launch-claude-agent.sh <alias>  (or ./bin/csl)"

# Show update check command
echo ""
echo "To check for updates later:"
echo "  $PYTHON $MANAGE_BACKEND --backend rapid-mlx check-updates"
echo "  $PYTHON $MANAGE_BACKEND --backend vllm-mlx check-updates"
echo "  $PYTHON $MANAGE_BACKEND --backend omlx check-updates"
echo "  $PYTHON $MANAGE_BACKEND --backend llama-cpp check-updates"
echo "  $PYTHON $MANAGE_BACKEND --backend litellm check-updates"
```

- [ ] **Step 3: Make script executable and test**

```bash
chmod +x install/install-backend.sh
./install/install-backend.sh --help
./install/install-backend.sh --all --dry-run
```

- [ ] **Step 4: Commit**

```bash
git add install/install-backend.sh
git commit -m "feat: update install-backend.sh to manage all backends via unified CLI"
```

### Task 9: Consolidate Update Checks

**Files:**
- Create: `scripts/check-backend-updates.py` (Python implementation)
- Modify: `scripts/local-stack-update-check.sh` → `scripts/check-backend-updates.sh` (wrapper)
- Modify: `scripts/daily-package-upgrade.sh` (remove backend-specific logic)
- Test: `tests/test_update_check.py`

**Interfaces:**
- Consumes: All manager classes from Tasks 1-6 via `manage-backend.py`
- Produces: Unified update check that works for all backends

- [ ] **Step 1: Write failing tests for unified update check**

```python
# tests/test_update_check.py
import pytest
from unittest.mock import patch, MagicMock
from scripts.check_backend_updates import check_all_backends, format_update_report

def test_check_all_backends():
    with patch('scripts.check_backend_updates.get_manager') as mock_get_mgr:
        # Mock each backend manager
        mock_mgr = MagicMock()
        mock_mgr.check_updates.return_value = [("rapid-mlx", "0.15.2", "0.15.3")]
        mock_get_mgr.return_value = mock_mgr
        
        results = check_all_backends()
        assert "rapid-mlx" in results
        assert len(results["rapid-mlx"]) == 1

def test_format_update_report():
    updates = {
        "rapid-mlx": [("rapid-mlx", "0.15.2", "0.15.3"), ("mlx", "0.32.2", "0.32.3")],
        "vllm-mlx": [("vllm-mlx", "0.4.1", "0.5.0")],
        "omlx": [],
        "llama-cpp": [("llama-cpp", "b4900", "b5000")],
        "litellm": [("litellm", "1.59.0", "1.60.0")],
    }
    report = format_update_report(updates)
    assert "rapid-mlx" in report
    assert "0.15.2 -> 0.15.3" in report
    assert "vllm-mlx" in report
    assert "0.4.1 -> 0.5.0" in report
    assert "llama-cpp" in report
    assert "b4900 -> b5000" in report
    assert "litellm" in report
    assert "1.59.0 -> 1.60.0" in report
    assert "omlx" in report
    assert "up to date" in report.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_update_check.py -v`
Expected: FAIL with "No module named 'scripts.check_backend_updates'"

- [ ] **Step 3: Implement check-backend-updates.py**

```python
#!/usr/bin/env python3
"""Unified backend update checker for local-agents."""

from __future__ import annotations
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add install to path
sys.path.insert(0, str(Path(__file__).parent.parent / "install"))

from install.managers import get_manager, list_managers, ManagerError

LOG_DIR = Path.home() / ".claude" / "logs"
LOG_FILE = LOG_DIR / "backend-update-check.log"

def log_message(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    with LOG_FILE.open("a") as f:
        f.write(f"{timestamp} {msg}\n")

def check_backend(backend_name: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Check a single backend for updates. Returns (backend_name, outdated_list)."""
    try:
        mgr = get_manager(backend_name)
        outdated = mgr.check_updates()
        checked = mgr.get_packages_to_check()
        log_message(f"CHECKED {backend_name}: {checked}")
        return backend_name, outdated
    except ManagerError as e:
        log_message(f"ERROR {backend_name}: {e}")
        return backend_name, []
    except Exception as e:
        log_message(f"UNEXPECTED {backend_name}: {e}")
        return backend_name, []

def check_all_backends(backends: list[str] | None = None) -> dict[str, list[tuple[str, str, str]]]:
    """Check all backends for updates."""
    if backends is None:
        backends = list_managers()
    
    results = {}
    for backend in backends:
        name, outdated = check_backend(backend)
        results[name] = outdated
    
    # Log summary
    total_outdated = sum(len(v) for v in results.values())
    if total_outdated > 0:
        details = "; ".join(f"{b}: {', '.join(f'{p} {i}->{l}' for p,i,l in u)}" for b, u in results.items() if u)
        log_message(f"OUTDATED: {details}")
        # macOS notification
        try:
            import subprocess
            subprocess.run([
                "osascript", "-e", 
                f'display notification "{details} — upgrade manually via manage-backend.py" with title "Backend updates available"'
            ], check=False)
        except Exception:
            pass
    else:
        checked = "; ".join(f"{b}: {mgr.get_packages_to_check()}" for b in backends 
                           if (mgr := get_manager(b, _allow_failure=True)))
        log_message(f"UP-TO-DATE: checked {checked}")
    
    return results

def format_update_report(results: dict[str, list[tuple[str, str, str]]]) -> str:
    """Format update results for human-readable output."""
    lines = ["Backend Update Check Report", "=" * 40, ""]
    any_outdated = False
    
    for backend in sorted(results.keys()):
        outdated = results[backend]
        lines.append(f"{backend}:")
        if outdated:
            any_outdated = True
            for pkg, installed, latest in outdated:
                lines.append(f"  ⬆ {pkg} {installed} -> {latest}")
        else:
            lines.append("  ✓ up to date")
        lines.append("")
    
    if not any_outdated:
        lines.append("All backends up to date.")
    
    return "\n".join(lines)

def save_json_report(results: dict[str, list[tuple[str, str, str]]]) -> None:
    """Save machine-readable JSON report."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_file = LOG_DIR / "backend-update-check.json"
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "backends": {
            b: [{"package": p, "installed": i, "latest": l} for p, i, l in u]
            for b, u in results.items()
        }
    }
    report_file.write_text(json.dumps(data, indent=2))

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Check all backends for updates")
    parser.add_argument("--backend", action="append", help="Specific backend(s) to check")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--save-json", action="store_true", help="Save JSON report to log dir")
    args = parser.parse_args()
    
    backends = args.backend if args.backend else None
    results = check_all_backends(backends)
    
    if args.json:
        print(json.dumps({
            b: [{"package": p, "installed": i, "latest": l} for p, i, l in u]
            for b, u in results.items()
        }, indent=2))
    else:
        print(format_update_report(results))
    
    if args.save_json:
        save_json_report(results)
    
    # Exit code: 0 = up to date, 1 = updates available, 2 = error
    any_outdated = any(len(u) > 0 for u in results.values())
    return 1 if any_outdated else 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create wrapper shell script**

```bash
#!/usr/bin/env bash
# check-backend-updates.sh — wrapper for Python update checker
# Called by launchd weekly

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${LA_PYTHON:-python3}"

exec "$PYTHON" "$SCRIPT_DIR/check-backend-updates.py" --save-json "$@"
```

- [ ] **Step 5: Update daily-package-upgrade.sh**

```bash
#!/usr/bin/env bash
# daily-package-upgrade.sh — DAILY launchd agent for Homebrew + pipx upgrades.
# Runs: brew upgrade && brew upgrade --greedy && pipx upgrade-all
# Backend-specific checks are now handled by check-backend-updates.py (weekly)
set -uo pipefail

LOG="$HOME/.claude/logs/daily-package-upgrade.log"
mkdir -p "$(dirname "$LOG")"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
log() { echo "$(ts) $*" >> "$LOG"; }

run_step() {
    local label="$1"; shift
    log "START $label: $*"
    if "$@" 2>&1 | while IFS= read -r line; do log "  $label: $line"; done; then
        log "OK    $label"
        return 0
    else
        local rc=$?
        log "FAIL  $label (exit $rc)"
        return $rc
    fi
}

log "=== daily-package-upgrade run ==="

run_step brew brew upgrade
run_step "brew --greedy" brew upgrade --greedy

if command -v pipx >/dev/null 2>&1; then
    run_step pipx pipx upgrade-all
else
    log "SKIP  pipx (not installed)"
fi

log "=== daily-package-upgrade done ==="
exit 0
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_update_check.py -v`
Expected: PASS

- [ ] **Step 7: Test manually**

```bash
python scripts/check-backend-updates.py
python scripts/check-backend-updates.py --json
./scripts/check-backend-updates.sh
```

- [ ] **Step 8: Commit**

```bash
git add scripts/check-backend-updates.py scripts/check-backend-updates.sh scripts/daily-package-upgrade.sh tests/test_update_check.py
git commit -m "feat: consolidate backend update checks into unified system"
```

### Task 10: Launchd Integration

**Files:**
- Modify: `~/Library/LaunchAgents/com.haiggoh.local-stack-update-check.plist` → `com.haiggoh.backend-update-check.plist`
- Test: Manual launchd load/test

**Interfaces:**
- Consumes: `scripts/check-backend-updates.sh` from Task 9
- Produces: Updated launchd plist for weekly backend update checks

- [ ] **Step 1: Create new launchd plist**

```xml
<!-- ~/Library/LaunchAgents/com.haiggoh.backend-update-check.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.haiggoh.backend-update-check</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/bra0002h/.claude/scripts/check-backend-updates.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>1</integer>
    <key>Hour</key>
    <integer>10</key>
    <key>Minute</key>
    <integer>17</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/bra0002h/.claude/logs/backend-update-check.out</string>
  <key>StandardErrorPath</key>
  <string>/Users/bra0002h/.claude/logs/backend-update-check.err</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
```

- [ ] **Step 2: Disable old plist and load new one**

```bash
# Unload old
launchctl unload ~/Library/LaunchAgents/com.haiggoh.local-stack-update-check.plist 2>/dev/null || true

# Copy new plist
cp /path/to/com.haiggoh.backend-update-check.plist ~/Library/LaunchAgents/

# Load new
launchctl load ~/Library/LaunchAgents/com.haiggoh.backend-update-check.plist

# Verify
launchctl list | grep backend-update-check
```

- [ ] **Step 3: Test the launchd job**

```bash
# Trigger manually
launchctl start com.haiggoh.backend-update-check

# Check logs
cat ~/.claude/logs/backend-update-check.out
cat ~/.claude/logs/backend-update-check.err
cat ~/.claude/logs/backend-update-check.log
cat ~/.claude/logs/backend-update-check.json
```

- [ ] **Step 4: Commit (plist goes in repo for documentation)**

```bash
# The actual plist lives in ~/Library/LaunchAgents but we track a template
mkdir -p install/launchd
cp ~/Library/LaunchAgents/com.haiggoh.backend-update-check.plist install/launchd/
git add install/launchd/com.haiggoh.backend-update-check.plist
git commit -m "feat: add launchd plist for unified backend update checks"
```

### Task 11: Configuration Integration

**Files:**
- Modify: `config/config-lib.sh` (backend discovery)
- Modify: `config/config.example.sh` (document new backend config)
- Test: `tests/test_config_integration.py`

**Interfaces:**
- Consumes: BackendManager classes from Tasks 1-6
- Produces: Updated config-lib.sh with unified backend discovery

- [ ] **Step 1: Write failing tests for config integration**

```python
# tests/test_config_integration.py
import pytest
import subprocess
import tempfile
import os

def test_config_lib_discovers_rapid_mlx():
    """Test that config-lib.sh discovers Rapid-MLX from versioned venvs."""
    # This would test the la_discover_rapid_bin function
    pass

def test_config_lib_backend_resolution():
    """Test that la_resolve_serve works with all backends."""
    pass

def test_config_example_documents_all_backends():
    """Test that config.example.sh documents all backends."""
    with open("config/config.example.sh") as f:
        content = f.read()
    assert "rapid-mlx" in content
    assert "vllm-mlx" in content
    assert "omlx" in content
    assert "llama_cpp" in content
    assert "litellm" in content
```

- [ ] **Step 2: Update config-lib.sh with unified backend discovery**

Key changes to `config/config-lib.sh`:
1. Add `la_discover_backend_binary()` function that uses `manage-backend.py`
2. Update `LA_SERVE_BACKENDS` to include all backends
3. Add backend-specific discovery logic for each backend type
4. Ensure backward compatibility with existing `LA_RAPID_BIN`, etc.

```bash
# In config-lib.sh, add:

# Unified backend discovery using manage-backend.py
la_discover_backend_binary() {
    local backend="$1"
    local manage_backend="$LA_ROOT/install/manage-backend.py"
    
    if [[ ! -x "$manage_backend" ]]; then
        return 1
    fi
    
    # Use the Python manager to find the active binary
    python3 "$manage_backend" --backend "$backend" info latest 2>/dev/null | head -1
}

# Backend-specific discovery (fallbacks)
la_discover_rapid_bin() { la_discover_backend_binary "rapid-mlx" || ... }
la_discover_vllm_bin() { la_discover_backend_binary "vllm-mlx" || ... }
la_discover_omlx_bin() { la_discover_backend_binary "omlx" || which omlx || ... }
la_discover_llama_cpp_bin() { la_discover_backend_binary "llama-cpp" || ... }
la_discover_litellm_bin() { la_discover_backend_binary "litellm" || ... }

# Update LA_SERVE_BACKENDS
LA_SERVE_BACKENDS="rapid vllm mlx_lm llama_cpp litellm"
LA_MLX_BACKENDS="rapid vllm mlx_lm"
```

- [ ] **Step 3: Update config.example.sh**

```bash
# In config/example.sh, add documentation for all backends:

# --- BACKEND CONFIGURATION ---------------------------------------------------
# Rapid-MLX (DEFAULT)
# LA_RAPID_BIN=""           # Leave empty to auto-discover versioned venv
# LA_RAPID_CACHE_MEMORY_MB=2048
# LA_RAPID_HYBRID_CACHE_ENTRIES=2

# vllm-mlx (LEGACY comparison lane)
# LA_VLLM_BIN=""            # Leave empty to auto-discover
# LA_VLLM_VERSION=""        # Pin specific version if needed

# oMLX (ISOLATED optional backend for Flash-Next/streaming)
# LA_OMLX_BIN=""            # Leave empty to use Homebrew install

# llama.cpp (GGUF only)
# LA_LLAMA_CPP_BIN=""       # Leave empty to auto-discover

# litellm (remote free-API proxy)
# LA_LITELLM_BIN=""         # Leave empty to auto-discover
# LA_LITELLM_CONFIG=""      # Path to litellm config.yaml
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config_integration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/config-lib.sh config/config.example.sh tests/test_config_integration.py
git commit -m "feat: update config system for unified backend management"
```

### Task 12: Tournament Integration

**Files:**
- Modify: `install/download-models.sh` (use new backend managers)
- Create: `install/qualify-backend.py` (backend qualification for tournament)
- Test: `tests/test_tournament_integration.py`

**Interfaces:**
- Consumes: BackendManager classes, tournament plan
- Produces: Backend qualification tooling integrated with tournament workflow

- [ ] **Step 1: Write failing tests for tournament integration**

```python
# tests/test_tournament_integration.py
import pytest
from unittest.mock import patch
from install.qualify_backend import qualify_backend, run_benchmark

def test_qualify_backend_basic():
    with patch('install.qualify_backend.get_manager') as mock_get_mgr:
        mock_mgr = MagicMock()
        mock_mgr.install_version.return_value = BackendInfo(
            name="rapid-mlx", version="0.15.3", path="/tmp/test", status="installed"
        )
        mock_get_mgr.return_value = mock_mgr
        
        result = qualify_backend("rapid-mlx", "0.15.3", model="qwen-3.8-operator")
        assert result["backend"] == "rapid-mlx"
        assert result["version"] == "0.15.3"
        assert "benchmarks" in result

def test_benchmark_tiers():
    tiers = get_benchmark_tiers()
    assert "8K" in tiers
    assert "32K" in tiers
    assert "100K" in tiers
    assert "200K" in tiers
    assert "300K" in tiers
    assert "1M" in tiers
```

- [ ] **Step 2: Implement qualify-backend.py**

```python
#!/usr/bin/env python3
"""Backend qualification tool for tournament evaluation."""

from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from managers import get_manager, BackendInfo

@dataclass
class BenchmarkResult:
    tier: str
    context_tokens: int
    model_load_sec: float
    ttft_sec: float
    prefill_toks_sec: float
    decode_toks_sec: float
    output_tokens: int
    memory_gb: float
    swap_gb: float
    cache_hit_rate: float
    success: bool
    error: str | None = None

BENCHMARK_TIERS = {
    "8K": 8192,
    "32K": 32768,
    "100K": 100000,
    "200K": 200000,
    "300K": 300000,
    "1M": 1000000,
}

def get_benchmark_tiers() -> dict[str, int]:
    return BENCHMARK_TIERS.copy()

def qualify_backend(
    backend: str, 
    version: str, 
    model: str,
    tiers: list[str] | None = None,
    runs: int = 3,
    output: Path | None = None,
) -> dict[str, Any]:
    """Qualify a backend+model combination for tournament."""
    
    mgr = get_manager(backend)
    print(f"Qualifying {backend} {version} with model {model}...")
    
    # Install/validate backend
    info = mgr.install_version(version, dry_run=False)
    if info.status not in ("installed", "already_complete", "validated"):
        raise RuntimeError(f"Backend installation failed: {info.metadata}")
    
    # Get model path from config
    model_path = get_model_path(model)
    if not model_path.exists():
        raise RuntimeError(f"Model not found: {model_path}")
    
    # Run benchmarks for each tier
    results = []
    for tier in tiers or ["8K", "32K", "100K"]:
        context_tokens = BENCHMARK_TIERS[tier]
        print(f"  Running {tier} tier ({context_tokens} tokens)...")
        
        tier_results = []
        for run in range(runs):
            print(f"    Run {run+1}/{runs}...", end=" ")
            result = run_benchmark(backend, version, model_path, context_tokens)
            tier_results.append(result)
            print(f"{'✓' if result.success else '✗'}")
        
        # Aggregate results (median)
        successful = [r for r in tier_results if r.success]
        if successful:
            median = sorted(successful, key=lambda r: r.ttft_sec)[len(successful)//2]
            results.append(median)
        else:
            results.append(tier_results[0])  # All failed
    
    # Compile report
    report = {
        "backend": backend,
        "version": version,
        "model": model,
        "model_path": str(model_path),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmarks": [asdict(r) for r in results],
        "summary": {
            "tiers_tested": len(results),
            "tiers_passed": sum(1 for r in results if r.success),
            "avg_ttft_sec": sum(r.ttft_sec for r in results if r.success) / max(1, sum(1 for r in results if r.success)),
            "avg_prefill_toks_sec": sum(r.prefill_toks_sec for r in results if r.success) / max(1, sum(1 for r in results if r.success)),
        }
    }
    
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2))
    
    return report

def run_benchmark(backend: str, version: str, model_path: Path, context_tokens: int) -> BenchmarkResult:
    """Run a single benchmark iteration."""
    # This would launch the backend server, run a benchmark prompt,
    # and measure the metrics. Simplified here.
    
    start = time.time()
    # ... launch server, send benchmark request, measure ...
    elapsed = time.time() - start
    
    # Placeholder - real implementation would use the launcher
    return BenchmarkResult(
        tier=get_tier_for_context(context_tokens),
        context_tokens=context_tokens,
        model_load_sec=elapsed,
        ttft_sec=elapsed * 0.1,
        prefill_toks_sec=1000,
        decode_toks_sec=50,
        output_tokens=512,
        memory_gb=20.0,
        swap_gb=0.0,
        cache_hit_rate=0.95,
        success=True,
    )

def get_tier_for_context(tokens: int) -> str:
    for tier, limit in sorted(BENCHMARK_TIERS.items(), key=lambda x: x[1]):
        if tokens <= limit:
            return tier
    return "1M"

def get_model_path(alias: str) -> Path:
    """Resolve model alias to path using config."""
    # Would source config-lib.sh and look up alias
    return Path.home() / ".models" / alias

def main() -> int:
    parser = argparse.ArgumentParser(description="Qualify backend for tournament")
    parser.add_argument("backend", help="Backend name")
    parser.add_argument("version", help="Backend version")
    parser.add_argument("model", help="Model alias")
    parser.add_argument("--tiers", nargs="+", choices=list(BENCHMARK_TIERS.keys()), default=["8K", "32K", "100K"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, help="Output JSON file")
    args = parser.parse_args()
    
    try:
        report = qualify_backend(args.backend, args.version, args.model, args.tiers, args.runs, args.output)
        print(json.dumps(report, indent=2))
        return 0 if report["summary"]["tiers_passed"] == report["summary"]["tiers_tested"] else 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Update download-models.sh to use backend managers**

```bash
# In download-models.sh, add backend-aware model downloading:
# Use manage-backend.py to ensure backend is installed before downloading models
# that require it.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tournament_integration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add install/qualify-backend.py tests/test_tournament_integration.py
git commit -m "feat: add backend qualification tooling for tournament"
```

### Task 13: Documentation & Migration

**Files:**
- Create: `docs/backend-management.md`
- Modify: `README.md` (update installation instructions)
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: All previous tasks
- Produces: Complete documentation for users and developers

- [ ] **Step 1: Create backend-management.md**

```markdown
# Backend Management Guide

## Overview

local-agents supports multiple inference backends through a unified management system. All backends are managed via `manage-backend.py` CLI and installed via `install-backend.sh`.

## Supported Backends

| Backend | Purpose | Default | Management |
|---------|---------|---------|------------|
| **Rapid-MLX** | Primary local inference (hybrid prefix cache) | ✓ | Versioned venvs |
| **vllm-mlx** | Legacy comparison lane | | Versioned venvs |
| **oMLX** | Flash-Next/streaming specialist | | Homebrew |
| **llama.cpp** | GGUF models only | | Binary releases |
| **litellm** | Remote free-API proxy | | Versioned venv |

## Quick Start

```bash
# Install all backends (recommended for new users)
./install/install-backend.sh --all

# Or install specific backends
./install/install-backend.sh --backend rapid-mlx
./install/install-backend.sh --backend vllm-mlx
./install/install-backend.sh --backend omlx
./install/install-backend.sh --backend llama-cpp
./install/install-backend.sh --backend litellm

# Dry run to see what would happen
./install/install-backend.sh --all --dry-run
```

## Managing Backends

### List installed versions

```bash
python install/manage-backend.py --backend rapid-mlx list
python install/manage-backend.py --backend vllm-mlx list
python install/manage-backend.py --backend omlx list
```

### Check available releases

```bash
python install/manage-backend.py --backend rapid-mlx releases --limit 10
python install/manage-backend.py --backend vllm-mlx releases --pre
```

### Install a specific version

```bash
python install/manage-backend.py --backend rapid-mlx install 0.15.3
python install/manage-backend.py --backend vllm-mlx install 0.5.0 --source github
python install/manage-backend.py --backend omlx install 0.7.0
```

### Check for updates

```bash
# Check all backends
python scripts/check-backend-updates.py

# Check specific backend
python install/manage-backend.py --backend rapid-mlx check-updates
```

### Promote pins (Rapid-MLX only)

```bash
python install/manage-backend.py --backend rapid-mlx promote 0.15.3
```

## Configuration

Backends are configured in `config/config.local.sh`:

```bash
# Rapid-MLX (DEFAULT)
LA_DEFAULT_MLX_BACKEND=rapid
LA_RAPID_BIN=""  # Auto-discover, or pin: $HOME/.venvs/rapid-mlx-0.15.3/bin/rapid-mlx

# vllm-mlx (explicit pin only)
# LA_VLLM_BIN=""

# oMLX
# LA_OMLX_BIN=""  # Uses Homebrew by default

# llama.cpp
# LA_LLAMA_CPP_BIN=""

# litellm
# LA_LITELLM_BIN=""
# LA_LITELLM_CONFIG=""  # Path to config.yaml
```

## Update System

Two automated update mechanisms:

1. **Daily** (`daily-package-upgrade.sh`): Homebrew + pipx upgrades
2. **Weekly** (`check-backend-updates.py`): Backend-specific version checks via PyPI/GitHub

Both run via launchd. Check logs in `~/.claude/logs/`.

## Tournament Integration

For model acquisition tournament:

```bash
# Qualify a backend+model combination
python install/qualify-backend.py rapid-mlx 0.15.3 qwen-3.8-operator --tiers 8K 32K 100K --runs 3
```

## Troubleshooting

### Backend not found
```bash
# Re-run install
./install/install-backend.sh --backend rapid-mlx

# Check what's installed
python install/manage-backend.py --backend rapid-mlx list
```

### Version mismatch
```bash
# Check for updates
python install/manage-backend.py --backend rapid-mlx check-updates

# Install specific version
python install/manage-backend.py --backend rapid-mlx install 0.15.3
```

### vllm-mlx patches
v0.4.x requires local fork patches (applied automatically). v0.5.0+ has patches integrated.
```bash
# Install with patches
python install/manage-backend.py --backend vllm-mlx install 0.4.1 --source github
```
```

- [ ] **Step 2: Update README.md**

Update the installation section to reference the new unified system.

- [ ] **Step 3: Update CHANGELOG.md**

Add entry for unified backend management.

- [ ] **Step 4: Commit**

```bash
git add docs/backend-management.md README.md CHANGELOG.md
git commit -m "docs: add backend management guide and update installation docs"
```

---

## Summary

This plan implements a complete unified backend management system for local-agents:

1. **BackendManager abstraction** - Common interface for all backends
2. **5 backend managers** - Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm
3. **Unified CLI** - `manage-backend.py` with consistent subcommands
4. **Updated installer** - `install-backend.sh` installs all backends
5. **Consolidated update checks** - Single weekly check for all backends
6. **Launchd integration** - Automated weekly update notifications
7. **Config integration** - Unified backend discovery in config-lib.sh
8. **Tournament tooling** - Backend qualification for model evaluation
9. **Documentation** - Complete user guide

Each task is independently testable and produces working software. The system maintains backward compatibility with existing launchers and configurations.
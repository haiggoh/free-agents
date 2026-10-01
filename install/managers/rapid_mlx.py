#!/usr/bin/env python3
"""Rapid-MLX Backend Manager.

Manages versioned Rapid-MLX virtual environments with pin promotion.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import register_manager
from .base import BackendInfo, BackendManager, ManagerError

PACKAGE = "rapid-mlx"
PYPI_URL = "https://pypi.org/pypi/rapid-mlx/json"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:([a-zA-Z]+)(\d+))?$")
SCHEMA = 2

# Files that get pinned during promotion
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


@register_manager
class RapidMLXManager(BackendManager):
    BACKEND_NAME = "rapid-mlx"
    VENV_PREFIX = "rapid-mlx"
    DEFAULT_VERSION = "0.15.3"
    PACKAGE_NAME = "rapid-mlx"
    PYPI_URL = PYPI_URL

    def __init__(self, home: Path | None = None, repo: Path | None = None) -> None:
        """Initialize the Rapid-MLX manager.

        Args:
            home: Override home directory (useful for testing)
            repo: Override local-agents repository path (for pin promotion)
        """
        super().__init__(home)
        self._repo = repo

    def get_installed_versions(self) -> list[str]:
        root = self.venv_root
        entries: list[tuple[str, Path, str]] = []
        if not root.is_dir():
            return entries
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
            headers={"Accept": "application/json", "User-Agent": "local-agents-rapid-manager/2"},
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
                metadata={"installation_mode": "locked_recreation" if lock else "fresh_resolution"},
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            receipt = self.validate_environment(version)
            receipt.metadata["result"] = "already_complete"
            self._save_receipt(version, receipt.metadata)
            return receipt

        base_python = self.choose_python(python)
        log = self.cache_root() / "logs" / f"install-{version}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.log"

        try:
            self.run_command([str(base_python), "-m", "venv", str(target)], log=log)
            child = target / "bin" / "python"
            lock = None if refresh_deps else self._locked_requirements(version)
            if lock:
                lock_path = target / ".rapid-runtime-lock.txt"
                lock_path.write_text("\n".join(lock) + "\n", encoding="utf-8")
                self.run_command([str(child), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(lock_path)], log=log)
                lock_path.unlink()
                mode = "locked_recreation"
            else:
                self.run_command([str(child), "-m", "pip", "install", "--disable-pip-version-check", f"{PACKAGE}=={version}"], log=log)
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

        self.run_command([str(python), "-m", "pip", "check"], capture=True)

        return BackendInfo(
            name=self.BACKEND_NAME,
            version=version,
            path=str(target),
            status="validated",
            metadata={
                "schema_version": SCHEMA,
                "rapid_version_output": observed,
                "rapid_binary_sha256": self.sha256_file(binary),
                "python": str(python),
                "python_version": self.run_command([str(python), "--version"], capture=True).stdout.strip(),
                "packages": self._package_inventory(python),
                "pip_freeze": self._pip_freeze(python),
                "validated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def promote_pins(self, version: str, *, dry_run: bool = False) -> dict[str, Any]:
        if not self._repo:
            raise ManagerError("Repository required for pin promotion")
        if not dry_run:
            self.validate_environment(version)

        plan = self._plan_pin_update(version)
        return self._apply_pin_plan(plan, dry_run=dry_run)

    def plan_pin_update(self, repo: Path, version: str):
        """Public wrapper for _plan_pin_update."""
        # Temporarily set _repo for the internal method
        old_repo = self._repo
        self._repo = repo
        try:
            return self._plan_pin_update(version)
        finally:
            self._repo = old_repo

    def apply_pin_plan(self, plan, *, dry_run: bool = False):
        """Public wrapper for _apply_pin_plan."""
        return self._apply_pin_plan(plan, dry_run=dry_run)

    # Helper methods (refactored from manage-rapid-mlx.py)
    def _binary_version(self, binary: Path) -> str:
        try:
            result = self.run_command([str(binary), "--version"], capture=True)
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
        return json.loads(self.run_command([str(python), "-c", code], capture=True).stdout)

    def _pip_freeze(self, python: Path) -> list[str]:
        return [
            line for line in self.run_command([str(python), "-m", "pip", "freeze"], capture=True).stdout.splitlines()
            if line.strip()
        ]

    def _locked_requirements(self, version: str) -> list[str] | None:
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
        return clean if f"rapid-mlx=={version}" in normalized else None

    def _cache_root(self) -> Path:
        return self.home / ".cache" / "local-agents" / "rapid-runtime-manager"

    def _save_receipt(self, version: str, data: dict[str, Any]) -> None:
        path = self.receipt_path(version)
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
        self.require_version(version)
        if not self._repo:
            raise ManagerError("Repository required for pin promotion")
        repo = self._repo.resolve()
        changes = []
        scanned = []
        all_files = PIN_FILES + OPTIONAL_PIN_FILES + (PRIVATE_PIN_FILE,)

        for relative in all_files:
            path = repo / relative
            private = relative == PRIVATE_PIN_FILE
            if not path.exists():
                if private or relative in OPTIONAL_PIN_FILES:
                    continue
                raise ManagerError(f"required pin surface is absent: {path}")
            if path.is_symlink() or not path.is_file():
                raise ManagerError(f"unsafe pin surface: {path}")
            before = path.read_bytes()
            try:
                text = before.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ManagerError(f"pin surface is not UTF-8: {path}") from exc
            output = PATH_PIN_RE.sub(lambda m: m.group("prefix") + version + m.group("suffix"), text)
            if relative in ("bin/launch-claude-agent-rapid-auto.sh", "tests/test_rapid_auto_mode.sh"):
                output = RAPID_AUTO_VERSION_RE.sub(f"rapid-mlx {version}", output)
            after = output.encode("utf-8")
            scanned.append(path)
            if after != before:
                changes.append((path, before, after, private, path.stat().st_mode & 0o777))

        required_invariants = {
            "bin/launch-claude-agent-rapid-auto.sh": (f"rapid-mlx-{version}/bin/rapid-mlx", f"rapid-mlx {version}"),
            "config/config-lib.sh": (f"rapid-mlx-{version}/bin/rapid-mlx",),
            "config/config.example.sh": (f"rapid-mlx-{version}/bin/rapid-mlx",),
            "tests/test_rapid_auto_mode.sh": (f"rapid-mlx {version}",),
        }
        final_by_path = {str(p.relative_to(repo)): p.read_text(encoding="utf-8") for p in scanned}
        for path, _, after, _, _ in changes:
            final_by_path[str(path.relative_to(repo))] = after.decode("utf-8")
        missing = []
        for relative, markers in required_invariants.items():
            final = final_by_path.get(relative, "")
            for marker in markers:
                if marker not in final:
                    missing.append(f"{relative}: {marker}")
        if missing:
            raise ManagerError("pin surfaces are partially migrated: " + "; ".join(missing))
        return (version, tuple(changes), tuple(scanned))

    def _apply_pin_plan(self, plan, *, dry_run: bool = False):
        version, changes, _scanned = plan
        repo = self._repo.resolve() if self._repo else None
        if dry_run or not changes:
            return {"result": "dry_run" if dry_run else "already_promoted", "changed": []}

        if repo:
            # Check clean worktree
            unstaged = subprocess.run(["git", "diff", "--quiet"], cwd=repo, check=False).returncode
            staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo, check=False).returncode
            if unstaged or staged:
                raise ManagerError("pin promotion requires a clean tracked worktree and index")

        transaction = self.cache_root() / "pin-transactions" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}")
        transaction.mkdir(parents=True, exist_ok=False)
        os.chmod(transaction, 0o700)
        manifest = []
        written = []
        try:
            for path, before, after, private, _mode in changes:
                relative = path.relative_to(repo)
                backup = transaction / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(before)
                os.chmod(backup, 0o600)
                if backup.read_bytes() != before:
                    raise ManagerError(f"backup verification failed: {backup}")
                manifest.append({
                    "path": str(relative),
                    "before_sha256": hashlib.sha256(before).hexdigest(),
                    "after_sha256": hashlib.sha256(after).hexdigest(),
                    "private": private,
                })
            self.atomic_json(transaction / "manifest.json", {"version": version, "files": manifest})
            for path, _before, after, private, mode in changes:
                fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
                temporary = Path(name)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(after)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(temporary, 0o600 if private else mode)
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                written.append((path, before, mode))

            # Validate
            if repo:
                launcher = repo / "bin" / "launch-claude-agent-rapid-auto.sh"
                config = repo / "config" / "config-lib.sh"
                tests = repo / "tests" / "test_rapid_auto_mode.sh"
                for p in (launcher, config, tests):
                    text = p.read_text(encoding="utf-8")
                    if p.name != "test_rapid_auto_mode.sh" and f"rapid-mlx-{version}/bin/rapid-mlx" not in text:
                        raise ManagerError(f"post-promotion versioned path missing from {p}")
                    if p in (launcher, tests) and f"rapid-mlx {version}" not in text:
                        raise ManagerError(f"post-promotion exact version assertion missing from {p}")
                # Compile check
                compile_code = (
                    "from pathlib import Path; "
                    "[compile(Path(p).read_text(encoding='utf-8'), p, 'exec') "
                    "for p in ('install/manage-rapid-mlx.py', 'tests/test_manage_rapid_mlx.py')]"
                )
                self.run_command([sys.executable, "-B", "-c", compile_code], cwd=repo)
                self.run_command(["bash", "tests/test_rapid_auto_mode.sh"], cwd=repo)
                self.run_command(["git", "diff", "--check"], cwd=repo)

            for path, _before, _mode in written:
                if path.read_bytes() != after:
                    raise ManagerError(f"post-write verification failed: {path}")

            return {
                "result": "promoted",
                "changed": [str(c[0].relative_to(repo)) for c in changes],
                "transaction": str(transaction),
            }
        except OSError as exc:
            rollback_errors = []
            for path, before, mode in reversed(written):
                try:
                    fd, name = tempfile.mkstemp(prefix=path.name + ".rollback.", dir=path.parent)
                    temporary = Path(name)
                    try:
                        with os.fdopen(fd, "wb") as handle:
                            handle.write(before)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.chmod(temporary, mode)
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{path}: {rollback_exc}")
            if rollback_errors:
                raise ManagerError(f"pin promotion failed ({exc}); rollback also failed:\n" + "\n".join(rollback_errors)) from exc
            raise ManagerError(f"pin promotion failed and was rolled back: {exc}") from exc
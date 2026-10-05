"""VenvMixin - Virtual environment management utilities.

This mixin provides common virtual environment operations shared across
backend managers that use Python virtual environments.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


class VenvMixin:
    """Mixin providing virtual environment management utilities.

    This mixin encapsulates common virtual environment operations
    shared across backend managers that use Python virtual environments.
    """

    VENV_PREFIX: str = ""
    BACKEND_NAME: str = ""
    _home: Path

    @property
    def home(self) -> Path:
        """Home directory for this manager."""
        return self._home

    @property
    def venv_root(self) -> Path:
        """Root directory for versioned virtual environments."""
        return self.home / ".venvs"

    def target_for(self, version: str) -> Path:
        """Get the target path for a specific version."""
        self.require_version(version)
        return self.venv_root / f"{self.VENV_PREFIX}-{version}"

    def cache_root(self) -> Path:
        """Cache directory for this backend."""
        return self.home / ".cache" / "local-agents" / f"{self.BACKEND_NAME}-runtime-manager"

    def receipt_path(self, version: str) -> Path:
        """Get the receipt path for a specific version."""
        return self.cache_root() / "receipts" / f"{self.VENV_PREFIX}-{version}.json"

    def choose_python(self, explicit: Optional[str] = None) -> Path:
        """Choose a Python interpreter with venv support.

        Args:
            explicit: Explicitly requested Python path

        Returns:
            Path to Python executable

        Raises:
            RuntimeError: If no suitable Python is found
        """
        candidates: list[str] = []
        if explicit:
            candidates.append(explicit)
        if os.environ.get("LA_BASE_PYTHON"):
            candidates.append(os.environ["LA_BASE_PYTHON"])
        candidates.extend([sys.executable, "/opt/homebrew/bin/python3.12", "python3.12", "python3"])

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
        raise RuntimeError("no Python with the stdlib venv module was found; pass --python")

    def run_command(
        self,
        command: list[str],
        *,
        capture: bool = False,
        log: Optional[Path] = None,
        cwd: Optional[Path] = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command with optional logging.

        Args:
            command: Command and arguments
            capture: Capture output instead of streaming
            log: Optional log file path
            cwd: Working directory

        Returns:
            CompletedProcess result
        """
        import shlex
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
            raise RuntimeError(f"command failed with exit {process.returncode}; see {log}")
        return process

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
                import json
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def locked_requirements(self, version: str) -> Optional[list[str]]:
        """Get locked requirements from receipt if available."""
        import json
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
            state = "complete" if self._is_complete(path) else "incomplete"
            entries.append((version, path, state))
        return sorted(entries, key=lambda item: self.version_key(item[0]), reverse=True)

    def _is_complete(self, path: Path) -> bool:
        """Check if environment is complete (override for backend-specific checks)."""
        binary = path / "bin" / self.BACKEND_NAME
        return binary.is_file() and os.access(binary, os.X_OK)

    def get_installed_version_infos(self) -> list:
        """Get BackendInfo for all installed versions."""
        from ..base import BackendInfo
        infos = []
        for version, path, status in self.installed_versions():
            metadata = {}
            if status == "complete":
                try:
                    validated = self.validate_environment(version)
                    status = "validated"
                    metadata = validated.metadata
                except Exception:
                    status = "error"
            infos.append(BackendInfo(
                name=self.BACKEND_NAME,
                version=version,
                path=str(path),
                status=status,
                metadata=metadata,
            ))
        return infos
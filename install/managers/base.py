#!/usr/bin/env python3
"""Backend Manager Abstraction - Base classes for unified backend management.

This module provides the abstract base class and dataclasses that all backend
managers (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm) inherit from.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

def _minor(text: str) -> tuple[int, int]:
    """'3.15\\n' -> (3, 15); anything unparseable sorts as newest, so a ceiling refuses it."""
    try:
        major, minor = text.strip().split(".")[:2]
        return int(major), int(minor)
    except ValueError:
        return (99, 99)


from .durability import durable_python, homebrew_prefixes, normalize_venv_base, venv_durability
from ._common import (
    BackendInfo,
    VersionInfo,
    ManagerError,
    receipt_freeze,
    venv_health,
)
from .mixins import (
    VersionParsingMixin,
    VenvMixin,
    PyPISourceMixin,
    GitHubSourceMixin,
    BinaryInstallMixin,
)


class BackendManager(
    VersionParsingMixin,
    VenvMixin,
    PyPISourceMixin,
    GitHubSourceMixin,
    BinaryInstallMixin,
    ABC,
):
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

    # Newest Python a venv for this backend may be built on, or None for no ceiling. Backends that
    # pull torch need it: torch lags each CPython release, and Homebrew moving `python3` to a new
    # minor (3.15, measured 2026-10-23) made every implicit choice a venv pip could not fill.
    PYTHON_MAX: tuple[int, int] | None = None

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
    def _get_latest_package_version(self, package: str) -> str:
        """Get the latest version of a specific package.

        Args:
            package: Package name

        Returns:
            Latest version string
        """
        ...

    # ---- Optional Methods (override in subclasses that support them) ----

    def promote_pins(self, version: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Promote pins to a version.

        Args:
            version: Version to promote
            dry_run: Print plan without executing

        Returns:
            Dict with promotion results

        Raises:
            ManagerError: If promotion is not supported or fails
        """
        raise ManagerError(f"promote not supported for {self.BACKEND_NAME}")

    # Venv backends (one ~/.venvs/<prefix>-<version> per version) opt in to the shared remove /
    # snapshot / repair machinery; Homebrew- and binary-managed backends keep "not supported".
    SUPPORTS_VENV_REMOVE: bool = False
    # Per-version operations beyond install/validate/info that this backend really supports. The
    # picker's BACKEND_VERSION_OPS must equal this (tests/test_session_picker_model.py), so the menu
    # never offers a row that can only fail and never hides one that works.
    VERSION_OPS: tuple[str, ...] = ()
    # Keep at least one COMPLETE version (a core runtime). LiteLLM turns this off: remote sessions
    # run the pipx install, so the manager's venvs are leftovers (user 2026-10-30).
    REMOVE_KEEPS_ONE: bool = True

    def remove_version(
        self,
        version: str,
        *,
        yes: bool = False,
        repo: Path | None = None,
        home: Path | None = None,
        dry_run: bool = False,
    ) -> None:
        """Remove one installed venv version — the one implementation for every venv backend.

        Refuses only for a stated reason: the last complete version (REMOVE_KEEPS_ONE), a healthy
        version with no recreation receipt, a version a running process uses, or the version the
        LIVE config pins. A BROKEN venv (its base Python is gone) is removable without validation
        or a receipt — it is exactly what needs removing, and it cannot be recreated from itself.
        """
        if not self.SUPPORTS_VENV_REMOVE:
            raise ManagerError(f"remove not supported for {self.BACKEND_NAME}")
        self.require_version(version)
        target = (home or self.home) / ".venvs" / f"{self.VENV_PREFIX}-{version}"
        if target.is_symlink() or not target.is_dir():
            raise ManagerError(f"refusing removal: not a real managed directory: {target}")
        health = venv_health(target, self.BACKEND_NAME)
        if self.REMOVE_KEEPS_ONE:
            others = [v for v, _, st in self.installed_versions() if v != version and st == "complete"]
            if not others:
                raise ManagerError(
                    f"refusing removal: {version} is the only installed {self.BACKEND_NAME} version. "
                    f"{self.BACKEND_NAME} is a core dependency of free-agents; install another version first.")
        if health.state == "complete":
            self.validate_environment(version)
            if not self.locked_requirements(version):
                raise ManagerError(f"refusing removal without a valid recreation receipt; run snapshot {version}")
        else:
            print(f"{self.BACKEND_NAME} {version} is {health.state} ({health.reason}); "
                  "removing without a receipt — it is not recreatable from itself")
        try:
            Path(sys.executable).resolve().relative_to(target.resolve())
        except ValueError:
            pass
        else:
            raise ManagerError("refusing to remove the environment running this manager")
        running = self._running_references(target)
        if running:
            raise ManagerError("environment is referenced by running process(es):\n" + "\n".join(running))
        references = self._referenced_paths(repo, target)
        if references:
            raise ManagerError("environment is still pinned:\n" + "\n".join(references))
        if dry_run:
            return  # the CLI prints the outcome line (manage-backend.py cmd_remove)
        if not yes:
            if not sys.stdin.isatty():
                raise ManagerError("removal requires a TTY or --yes")
            if input(f"Permanently remove {target}? Type 'delete {version}': ") != f"delete {version}":
                raise ManagerError("removal cancelled")
        shutil.rmtree(target)
        if target.exists() or target.is_symlink():
            raise ManagerError(f"removal verification failed: {target}")

    def set_live_config_var(self, name: str, value: str, *, purpose: str, validate=None,
                            dry_run: bool = False) -> dict[str, Any]:
        """Set ONE `NAME="value"` line in the machine's live config.local.sh — the only file a
        backend update may write (never the repo). Replaces the first non-comment NAME= line or
        appends one; keeps the file's mode; backs it up into <live>/backups/ first; then runs
        validate() (the launcher-visible outcome) and restores the file byte-for-byte on failure."""
        live = live_config_file(Path(getattr(self, "_repo", None) or Path(__file__).resolve().parents[2]))
        if live.is_symlink() or (live.exists() and not live.is_file()):
            raise ManagerError(f"unsafe live config (symlink or not a file): {live}")
        before = live.read_bytes() if live.exists() else b""
        text = before.decode("utf-8")
        new_line = f'{name}="{value}"   # set by manage-backend.py ({purpose})'
        lines, replaced = text.splitlines(), False
        for i, line in enumerate(lines):
            if not replaced and line.lstrip().startswith(f"{name}=") and not line.lstrip().startswith("#"):
                lines[i], replaced = new_line, True
        if not replaced:
            lines.append(new_line)
        after = ("\n".join(lines) + "\n").encode("utf-8")
        if after == before:
            return {"result": "already_promoted", "changed": []}
        if dry_run:
            return {"result": "dry_run", "changed": [str(live)]}
        mode = live.stat().st_mode & 0o777 if live.exists() else 0o600
        backups = live.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = backups / f"config.local.sh.pre-{purpose.replace(' ', '-')}-{stamp}"
        backup.write_bytes(before)
        os.chmod(backup, 0o600)

        def write(data: bytes) -> None:
            fd, name_ = tempfile.mkstemp(prefix=live.name + ".", dir=live.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(name_, mode)
                os.replace(name_, live)
            finally:
                Path(name_).unlink(missing_ok=True)

        write(after)
        try:
            if validate:
                validate()
        except Exception as exc:
            write(before)
            raise ManagerError(f"promotion failed and was rolled back: {exc}") from exc
        return {"result": "promoted", "changed": [str(live)], "backup": str(backup)}

    def health_report(self) -> list[dict[str, Any]]:
        """One row per installed version: state (complete|incomplete|broken), reason, base
        durability (durable|pinned-keg), and whether the LIVE config pins it."""
        rows: list[dict[str, Any]] = []
        for version, path, state in self.installed_versions():
            health = venv_health(path, self.BACKEND_NAME)
            try:
                pinned = bool(self._referenced_paths(None, path))
            except ManagerError:
                pinned = None
            rows.append({
                "backend": self.BACKEND_NAME, "version": version, "path": str(path),
                "state": health.state if state != "incomplete" else "incomplete",
                "reason": health.reason,
                "durability": venv_durability(path) if (path / "pyvenv.cfg").is_file() else "n/a",
                "pinned": pinned,
            })
        return rows

    def repair_version(self, version: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Rebuild `version` in place — for a BROKEN venv, or a healthy one still built on a
        Homebrew patch keg — and never leave the user with less than they had.

        Order (make-before-break, adapted: pip writes ABSOLUTE shebangs, so a venv built elsewhere
        and renamed would point at the wrong path): move the old venv aside, rebuild at the real
        path through install_version (an exact recreation when a receipt exists), RUN the result,
        then delete the set-aside original. Any failure deletes the half-built one and puts the
        original back."""
        if not self.SUPPORTS_VENV_REMOVE:
            raise ManagerError(f"repair not supported for {self.BACKEND_NAME}")
        self.require_version(version)
        target = self.home / ".venvs" / f"{self.VENV_PREFIX}-{version}"
        if target.is_symlink() or not target.is_dir():
            raise ManagerError(f"nothing to repair: {target} is not an installed version")
        running = self._running_references(target)
        if running:
            raise ManagerError("environment is referenced by running process(es):\n" + "\n".join(running))
        mode = "locked_recreation" if self.locked_requirements(version) else "fresh_resolution"
        if dry_run:
            return {"result": "dry_run", "target": str(target), "mode": mode}
        aside = target.with_name(f"{target.name}.repair-{os.getpid()}-{int(time.time())}")
        os.rename(target, aside)
        try:
            kwargs: dict[str, Any] = {"python": None, "refresh_deps": False, "dry_run": False}
            try:
                # The CLASS method is the contract (an instance attribute may be a wrapper/stub).
                if "skip_pin_update" in inspect.signature(type(self).install_version).parameters:
                    kwargs["skip_pin_update"] = True        # a repair never promotes
            except (TypeError, ValueError):
                pass
            self.install_version(version, **kwargs)
            health = venv_health(target, self.BACKEND_NAME)
            if health.state != "complete":
                raise ManagerError(f"the rebuilt venv is {health.state}: {health.reason}")
            self.validate_environment(version)
        except Exception as exc:
            if target.exists() or target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            os.rename(aside, target)
            raise ManagerError(f"repair of {self.BACKEND_NAME} {version} failed and the original was restored: {exc}") from exc
        shutil.rmtree(aside, ignore_errors=True)
        return {"result": "repaired", "target": str(target), "mode": mode,
                "durability": venv_durability(target)}

    def _referenced_paths(self, repo: Path | None, target: Path) -> list[str]:
        """Lines of the LIVE config that pin the target (the file the launchers read).

        Fails CLOSED: a live config that cannot be resolved raises, so `remove` refuses rather
        than deleting a version the machine may be running on. Until 0.49.0 this scanned
        <repo>/config/config.local.sh, which no real checkout has — so it would have allowed
        removing the live-pinned version. Comment lines are not pins."""
        live = live_config_file((repo or Path(__file__).resolve().parents[2]).resolve())
        if not live.is_file() or live.is_symlink():
            return []
        matches: list[str] = []
        for number, line in enumerate(live.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r"(?<![\w.-])" + re.escape(target.name) + r"(?![\w.-])", line) or str(target) in line:
                matches.append(f"{live}:{number}:{line.strip()}")
        return matches

    def _running_references(self, target: Path) -> list[str]:
        """Running processes referencing the target, via `ps` (no psutil dependency: the manager
        runs under whatever python3 is on PATH). A guard that cannot run must refuse, never wave
        the removal through, so a failed `ps` is an error, not "no matches"."""
        try:
            out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True,
                                 text=True, timeout=10, check=True).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise ManagerError(f"cannot check running processes (ps failed: {exc}); refusing removal") from exc
        own_pid = os.getpid()
        matches: list[str] = []
        for line in out.splitlines():
            pid, _, command = line.strip().partition(" ")
            if pid.isdigit() and int(pid) != own_pid and str(target) in command:
                matches.append(f"PID {pid}: {command.strip()}")
        return matches

    def smoke_environment(self, version: str, home: Path | None = None) -> dict[str, Any]:
        """Smoke test an environment (package import + CLI --help, no server).

        Args:
            version: Version to test
            home: Home directory override

        Returns:
            Dict with smoke test results

        Raises:
            ManagerError: If smoke test is not supported or fails
        """
        raise ManagerError(f"smoke not supported for {self.BACKEND_NAME}")

    def snapshot_environment(self, version: str, home: Path | None = None, *, dry_run: bool = False) -> Path:
        """Save an exact recreation receipt.

        Args:
            version: Version to snapshot
            home: Home directory override
            dry_run: Print plan without executing

        Returns:
            Path to the receipt file

        Raises:
            ManagerError: If snapshot is not supported or fails
        """
        if not self.SUPPORTS_VENV_REMOVE:
            raise ManagerError(f"snapshot not supported for {self.BACKEND_NAME}")
        path = self.receipt_path(version)
        if dry_run:
            print(f"would write recreation receipt: {path}")
            return path
        receipt = self.validate_environment(version)
        metadata = dict(getattr(receipt, "metadata", None) or {})
        if not metadata.get("pip_freeze"):
            raise ManagerError(f"{self.BACKEND_NAME} {version}: validation recorded no pip freeze — nothing to snapshot")
        metadata["result"] = "snapshot"
        return self.save_receipt(version, metadata)

    def inspect_environment(
        self,
        version: str,
        output: Path | None = None,
        home: Path | None = None,
        *,
        dry_run: bool = False,
    ) -> Path:
        """Capture CLI/package evidence.

        Args:
            version: Version to inspect
            output: Output file path
            home: Home directory override
            dry_run: Print plan without executing

        Returns:
            Path to the inspection file

        Raises:
            ManagerError: If inspect is not supported or fails
        """
        raise ManagerError(f"inspect not supported for {self.BACKEND_NAME}")

    # ---- Utility Methods ----

    def check_updates(self) -> list[tuple[str, str, str]]:
        """Check for available updates.

        Returns:
            List of (package, current_version, latest_version) tuples
        """
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
        """Get installed version of a package from the backend's environment.

        Args:
            package: Package name

        Returns:
            Installed version string

        Raises:
            ManagerError: If no versions installed or package not found
        """
        versions = self.get_installed_versions()
        if not versions:
            raise ManagerError(f"No installed versions of {self.BACKEND_NAME}")
        target = self.target_for(versions[0])
        python = target / "bin" / "python"
        if not python.exists():
            raise ManagerError(f"Python not found in {target}")
        result = self.run_command(
            [str(python), "-c", f"import importlib.metadata as m; print(m.version('{package}'))"],
            capture=True
        )
        if result.returncode != 0:
            raise ManagerError(f"Package {package} not found: {result.stderr}")
        return result.stdout.strip()

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
        # An explicit choice (--python, LA_BASE_PYTHON) is the operator's decision and skips the
        # ceiling; only the implicit default list is filtered by PYTHON_MAX.
        chosen = [c for c in (explicit, os.environ.get("LA_BASE_PYTHON")) if c]
        candidates = [(c, False) for c in chosen] + [(c, True) for c in self.python_candidates()]

        seen: set[str] = set()
        too_new: list[str] = []
        for candidate, capped in candidates:
            resolved = candidate
            if "/" not in candidate:
                which_result = shutil.which(candidate)
                if which_result:
                    resolved = which_result
                else:
                    continue
            # Dedupe on the real file, but RETURN the path as found: a venv records its base
            # interpreter, and the resolved target of /opt/homebrew/bin/python3.14 is one patch
            # keg (Cellar/.../3.14.8_2) that `brew cleanup` deletes after the next patch upgrade.
            path = Path(resolved).expanduser().absolute()
            real = path.resolve()
            if str(real) in seen or not os.access(real, os.X_OK):
                continue
            seen.add(str(real))
            probe = subprocess.run(
                [str(path), "-c", "import venv, sys; print('%d.%d' % sys.version_info[:2])"],
                capture_output=True, text=True,
            )
            if probe.returncode != 0:
                continue
            if capped and self.PYTHON_MAX and _minor(probe.stdout) > self.PYTHON_MAX:
                too_new.append(f"{path} ({probe.stdout.strip()})")
                continue
            return durable_python(path)
        if too_new:
            ceiling = "%d.%d" % self.PYTHON_MAX
            raise ManagerError(
                f"{self.BACKEND_NAME} needs Python <= {ceiling} (torch has no wheels past it); found "
                f"only {', '.join(too_new)}. Install python@{ceiling} or pass --python / LA_BASE_PYTHON"
            )
        raise ManagerError("no Python with the stdlib venv module was found; pass --python")

    def python_candidates(self) -> list[str]:
        """The implicit interpreter list, in preference order. The versioned names come after
        sys.executable so a ceiling has something older to fall back to."""
        out = [sys.executable]
        for minor in ("3.14", "3.13", "3.12"):
            out += [str(prefix / "opt" / f"python@{minor}" / "bin" / f"python{minor}")
                    for prefix in homebrew_prefixes()]
            out.append(f"python{minor}")
        return out + ["python3"]

    def after_venv_created(self, target: Path) -> str:
        """Run right after `python -m venv`: move a keg-based base onto Homebrew's patch-stable
        opt/ path and RUN it (durability.normalize_venv_base). A venv that cannot be moved is
        kept but flagged — it will break at the next Python patch upgrade, and `health` shows it."""
        result = normalize_venv_base(target)
        if result == "pinned-keg":
            print(f"⚠️  {target} is built on a Homebrew patch keg and will break at the next Python patch "
                  "upgrade; `manage-backend.py … repair` can rebuild it later.", file=sys.stderr)
        return result

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

    def save_receipt(self, version: str, data: dict[str, Any]) -> Path:
        """Write the recreation receipt for `version` (mode 600) — THE writer every backend uses.

        Always records `version` and `backend`, the keys locked_requirements matches on; until
        0.49.0 the writer omitted `version` while every reader required it."""
        payload = dict(data)
        payload["version"] = version
        payload["backend"] = self.BACKEND_NAME
        path = self.receipt_path(version)
        self.atomic_json(path, payload)
        return path

    def locked_requirements(self, version: str) -> list[str] | None:
        """The pip freeze the receipt can rebuild `version` from, or None (see receipt_freeze)."""
        path = self.receipt_path(version)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return receipt_freeze(payload, version, self.PACKAGE_NAME)

    def _is_complete(self, path: Path) -> bool:
        """Check if environment is complete (override for backend-specific checks)."""
        binary = path / "bin" / self.BACKEND_NAME
        return binary.is_file() and os.access(binary, os.X_OK)

    def get_installed_version_infos(self) -> list[BackendInfo]:
        """Get BackendInfo for all installed versions."""
        infos = []
        for version, path, status in self.installed_versions():
            metadata = {}
            if status == "complete":
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


def live_config_file(repo: Path) -> Path:
    """The machine's LIVE config.local.sh — the one file a backend update may write.

    Resolved by config/config-lib.sh itself (one implementation of the precedence: operator
    override, the tree's own file, the machine state dir), so the manager can never again write
    a file the launcher does not read (until 2026-10-30 it wrote <repo>/config/config.local.sh,
    which no real checkout has had since v0.41.1). An explicit LA_LIVE_CONFIG_DIR that is set
    but not a directory aborts naming it — never a silent fallthrough.
    """
    override = os.environ.get("LA_LIVE_CONFIG_DIR", "")
    if override and not os.environ.get("_LA_LIVE_FOR"):
        if not Path(override).is_dir():
            raise ManagerError(f"LA_LIVE_CONFIG_DIR is set but is not a directory: {override}")
        return Path(override) / "config.local.sh"
    lib = repo / "config" / "config-lib.sh"
    if not lib.is_file():
        raise ManagerError(f"cannot resolve the live config: {lib} is missing")
    env = {k: v for k, v in os.environ.items() if k not in ("LA_LIVE_CONFIG_DIR", "_LA_LIVE_FOR")}
    probe = subprocess.run(
        ["bash", "-c", 'source "$1" >/dev/null 2>&1; printf %s "$LA_LIVE_CONFIG_DIR"', "_", str(lib)],
        capture_output=True, text=True, cwd=repo, env=env, timeout=30)
    resolved = probe.stdout.strip()
    if probe.returncode != 0 or not resolved:
        raise ManagerError(f"config-lib.sh did not report a live config dir (exit {probe.returncode})")
    return Path(resolved) / "config.local.sh"


def repository_root(explicit: Path | None = None) -> Path:
    """The free-agents checkout this manager runs from (two levels above install/managers/).

    A worktree has a `.git` FILE, not a directory, so `exists()` (not `is_dir()`) is the test.
    """
    candidate = (explicit or Path(__file__).resolve().parent.parent.parent).expanduser().resolve()
    if not (candidate / ".git").exists():
        raise ManagerError(f"not a free-agents Git repository: {candidate}")
    return candidate

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
from ._common import braille_spinner, venv_health
from .base import live_config_file, repository_root

PACKAGE = "rapid-mlx"
PYPI_URL = "https://pypi.org/pypi/rapid-mlx/json"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:([a-zA-Z]+)(\d+))?$")
SCHEMA = 2

# Promotion writes ONE file: the machine's live config.local.sh (base.live_config_file). Until
# 0.49.0 it also stamped four tracked repo files, so every backend update dirtied the checkout
# and shipped one machine's version choice in the release (user 2026-10-30: an update is a user
# action, not release work). tests/test_live_config_promotion.py guards that no shipped file
# carries a backend venv path again.
PATH_PIN_RE = re.compile(
    r"(?P<prefix>rapid-mlx-)(?P<version>\d+\.\d+\.\d+)(?P<suffix>/bin/(?:rapid-mlx|python))"
)
RAPID_AUTO_VERSION_RE = re.compile(r"rapid-mlx (?P<version>\d+\.\d+\.\d+)")


# Sentinel default: "use the manager's own default_pin_validator" (None means skip it).
_DEFAULT_VALIDATOR = object()


@register_manager
class RapidMLXManager(BackendManager):
    BACKEND_NAME = "rapid-mlx"
    VENV_PREFIX = "rapid-mlx"
    DEFAULT_VERSION = "1.0.0"
    PACKAGE_NAME = "rapid-mlx"
    PYPI_URL = PYPI_URL
    PYTHON_MAX = (3, 14)  # torch/torchvision wheels stop at cp314 (base.PYTHON_MAX)
    SUPPORTS_VENV_REMOVE = True

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

    def apply_pin_plan(self, plan, *, dry_run: bool = False, validator=_DEFAULT_VALIDATOR,
                       backup_root: Path | None = None):
        """Public wrapper for _apply_pin_plan (validator=None skips the repo's own test run)."""
        return self._apply_pin_plan(plan, dry_run=dry_run, validator=validator, backup_root=backup_root)

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
        """Kept for callers of the old name; the one rule lives in base.locked_requirements."""
        return self.locked_requirements(version)

    def _save_receipt(self, version: str, data: dict[str, Any]) -> None:
        """Kept for callers of the old name; base.save_receipt is THE writer (adds version/backend)."""
        self.save_receipt(version, data)

    def _plan_pin_update(self, version: str):
        """Plan the ONE write a promotion makes: the live config's LA_RAPID_BIN line.

        Returns (version, changes, scanned, note). note = "no_live_config" (nothing to write:
        the machine runs on auto-discovery), "no_explicit_pin" (the live file has no Rapid pin
        line — also auto-discovery; the file is left byte-identical, never gains a line), or ""."""
        self.require_version(version)
        if not self._repo:
            raise ManagerError("Repository required for pin promotion")
        live = live_config_file(self._repo.resolve())
        if not os.path.lexists(live):
            return (version, (), (), "no_live_config")
        if live.is_symlink() or not live.is_file():
            raise ManagerError(f"unsafe live config (symlink or not a file): {live}")
        before = live.read_bytes()
        try:
            text = before.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ManagerError(f"live config is not UTF-8: {live}") from exc
        if not any(PATH_PIN_RE.search(line) for line in text.splitlines()
                   if line.lstrip().startswith("LA_RAPID_BIN=")):
            return (version, (), (live,), "no_explicit_pin")
        after = PATH_PIN_RE.sub(lambda m: m.group("prefix") + version + m.group("suffix"), text).encode("utf-8")
        changes = ((live, before, after, True, live.stat().st_mode & 0o777),) if after != before else ()
        return (version, changes, (live,), "")

    def default_pin_validator(self, repo: Path, version: str) -> None:
        """Validate the OUTCOME the launcher will see: source config-lib like a launcher does and
        require LA_RAPID_BIN to resolve to the promoted version AND pass the health check."""
        expected = f"rapid-mlx-{version}/bin/rapid-mlx"
        probe = subprocess.run(
            ["bash", "-c", 'source "$1" >/dev/null 2>&1; la_load_config >/dev/null 2>&1; '
             'printf "%s\\n" "$LA_RAPID_BIN"; la_backend_bin_health "$LA_RAPID_BIN"',
             "_", str(repo / "config" / "config-lib.sh")],
            capture_output=True, text=True, cwd=repo, timeout=60)
        resolved = probe.stdout.strip().splitlines()[:1]
        if not resolved or not resolved[0].endswith(expected):
            raise ManagerError(f"after promotion the launcher resolves LA_RAPID_BIN={resolved[0] if resolved else '<empty>'}, "
                               f"not …/{expected}")
        if probe.returncode != 0:
            raise ManagerError(f"promoted binary cannot run: {probe.stderr.strip() or 'health check failed'}")

    def _apply_pin_plan(self, plan, *, dry_run: bool = False, validator=_DEFAULT_VALIDATOR,
                        backup_root: Path | None = None):
        """Back up, write, validate, verify — and roll EVERY written file back on ANY failure.

        Restores two guarantees the 0.21.0 class port lost: a validation failure (ManagerError,
        or CalledProcessError from the repo's own tests) now rolls back instead of leaving the
        repo half-promoted, and post-write verification compares each file with ITS OWN planned
        bytes (it compared every file with the last file's content, so any multi-file promotion
        failed verification)."""
        version, changes, _scanned, note = plan if len(plan) == 4 else (*plan, "")
        repo = self._repo.resolve() if self._repo else None
        if dry_run or not changes:
            return {"result": "dry_run" if dry_run else (note or "already_promoted"), "changed": []}
        if repo is None:
            raise ManagerError("Repository required for pin promotion")
        if validator is _DEFAULT_VALIDATOR:
            validator = self.default_pin_validator

        transaction = backup_root or (
            self._cache_root() / "pin-transactions" /
            (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"))
        transaction.mkdir(parents=True, exist_ok=False)
        os.chmod(transaction, 0o700)
        manifest = []
        written = []
        try:
            for path, before, after, private, _mode in changes:
                relative = Path(path.name)
                backup = transaction / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(before)
                os.chmod(backup, 0o600)
                if backup.read_bytes() != before:
                    raise ManagerError(f"backup verification failed: {backup}")
                manifest.append({
                    "path": str(path),
                    "before_sha256": hashlib.sha256(before).hexdigest(),
                    "after_sha256": hashlib.sha256(after).hexdigest(),
                    "private": private,
                })
            self.atomic_json(transaction / "manifest.json", {"version": version, "files": manifest})
            for path, before, after, private, mode in changes:
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
            if validator:
                validator(repo, version)
            for path, _before, after, _private, _mode in changes:
                if path.read_bytes() != after:
                    raise ManagerError(f"post-write verification failed: {path}")
            return {
                "result": "promoted",
                "changed": [str(c[0]) for c in changes],
                "transaction": str(transaction),
            }
        except Exception as exc:
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
                except Exception as rollback_exc:  # catastrophic filesystem case
                    rollback_errors.append(f"{path}: {rollback_exc}")
            if rollback_errors:
                raise ManagerError(f"pin promotion failed ({exc}); rollback also failed:\n"
                                   + "\n".join(rollback_errors)) from exc
            raise ManagerError(f"pin promotion failed and was rolled back: {exc}") from exc

    def smoke_environment(self, version: str, home: Path | None = None) -> dict[str, Any]:
        """Validate package and CLI surfaces without starting a server."""
        target = self.target_for(version, home or self.home)
        receipt = self.validate_environment(version)
        binary = target / "bin" / "rapid-mlx"
        top_help = self.run_command([str(binary), "--help"], capture=True).stdout
        serve_help = self.run_command([str(binary), "serve", "--help"], capture=True).stdout
        if "serve" not in top_help or "--host" not in serve_help:
            raise ManagerError("Rapid CLI smoke test did not find expected serve/help surfaces")
        return {"result": "smoke_pass", "version": version, "binary_sha256": receipt.metadata["rapid_binary_sha256"], "model_server_started": False}

    def inspect_environment(
        self,
        version: str,
        output: Path | None = None,
        home: Path | None = None,
        *,
        dry_run: bool = False,
    ) -> Path:
        """Capture CLI/package evidence."""
        target = self.target_for(version, home or self.home)
        destination = (output or self._cache_root() / "inspections" / f"rapid-mlx-{version}.txt").expanduser()
        if dry_run:
            print(f"would inspect {target} and write {destination}")
            return destination
        receipt = self.validate_environment(version)
        binary = target / "bin" / "rapid-mlx"
        sections = [
            "=== RECEIPT ===\n" + json.dumps(receipt.metadata, indent=2, sort_keys=True),
            "=== TOP-LEVEL HELP ===\n" + self.run_command([str(binary), "--help"], capture=True).stdout,
            "=== SERVE HELP ===\n" + self.run_command([str(binary), "serve", "--help"], capture=True).stdout,
        ]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n\n".join(sections).rstrip() + "\n", encoding="utf-8")
        os.chmod(destination, 0o600)
        return destination

    def install_version(
        self,
        version: str,
        *,
        python: str | None = None,
        refresh_deps: bool = False,
        dry_run: bool = False,
        skip_pin_update: bool = False,
        repo: Path | None = None,
        home: Path | None = None,
    ) -> BackendInfo:
        """Install a specific version with optional pin promotion."""
        # Check pin promotion restriction
        if not skip_pin_update and home is not None and home.resolve() != Path.home().resolve():
            raise ManagerError(
                "pin promotion is disabled with the test-only --home override; "
                "pass --skip-pin-update"
            )

        target = self.target_for(version, home)
        if dry_run:
            lock = None if refresh_deps else self._locked_requirements(version)
            receipt = BackendInfo(
                name=self.BACKEND_NAME,
                version=version,
                path=str(target),
                status="dry_run",
                metadata={"installation_mode": "locked_recreation" if lock else "fresh_resolution"},
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                receipt = self.validate_environment(version)
                receipt.metadata["result"] = "already_complete"
                self._save_receipt(version, receipt.metadata)
            else:
                base_python = self.choose_python(python)
                log = self._cache_root(home) / "logs" / f"install-{version}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.log"

                try:
                    with braille_spinner("Creating virtual environment..."):
                        self.run_command([str(base_python), "-m", "venv", str(target)], log=log)
                    child = target / "bin" / "python"
                    lock = None if refresh_deps else self._locked_requirements(version)
                    if lock:
                        lock_path = target / ".rapid-runtime-lock.txt"
                        lock_path.write_text("\n".join(lock) + "\n", encoding="utf-8")
                        with braille_spinner("Installing locked dependencies..."):
                            self.run_command([str(child), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(lock_path)], log=log)
                        lock_path.unlink()
                        mode = "locked_recreation"
                    else:
                        with braille_spinner(f"Installing {PACKAGE}=={version}..."):
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
                except Exception:
                    if target.exists() and not target.is_symlink():
                        shutil.rmtree(target)
                    raise

        receipt.metadata["pin_update"] = "skipped" if skip_pin_update else "pending"
        if skip_pin_update:
            return receipt
        try:
            if hasattr(self, 'promote_pins') and repo is not None:
                receipt.metadata["pin_promotion"] = self.promote_pins(version, dry_run=dry_run)
                receipt.metadata["pin_update"] = "dry_run" if dry_run else "promoted"
        except Exception:
            receipt.metadata["pin_update"] = "failed_rolled_back"
            if not dry_run:
                self._save_receipt(version, receipt.metadata)
            raise
        if not dry_run:
            self._save_receipt(version, receipt.metadata)
        return receipt

    def target_for(self, version: str, home: Path | None = None) -> Path:
        """Get target path for a version."""
        self.require_version(version)
        return (home or self.home) / ".venvs" / f"{self.VENV_PREFIX}-{version}"

    @property
    def venv_root(self) -> Path:
        """Get venv root directory (property for backward compatibility)."""
        return self.home / ".venvs"

    def _venv_root(self, home: Path | None = None) -> Path:
        """Get venv root directory with optional home override."""
        return (home or self.home) / ".venvs"

    @property
    def cache_root(self) -> Path:
        """Get cache root directory (property for backward compatibility)."""
        return self.home / ".cache" / "local-agents" / "rapid-runtime-manager"

    def _cache_root(self, home: Path | None = None) -> Path:
        """Get cache root directory with optional home override."""
        return (home or self.home) / ".cache" / "local-agents" / "rapid-runtime-manager"

    def receipt_path(self, version: str, home: Path | None = None) -> Path:
        """Get receipt path for a version."""
        return self._cache_root(home) / "receipts" / f"{self.VENV_PREFIX}-{version}.json"

    def installed_versions(self, home: Path | None = None) -> list[tuple[str, Path, str]]:
        """List installed versions."""
        root = self._venv_root(home)
        entries: list[tuple[str, Path, str]] = []
        if not root.is_dir():
            return entries
        for path in root.glob(f"{self.VENV_PREFIX}-*"):
            if path.is_symlink() or not path.is_dir():
                continue
            version = path.name.removeprefix(f"{self.VENV_PREFIX}-")
            if not VERSION_RE.fullmatch(version):
                continue
            entries.append((version, path, venv_health(path, "rapid-mlx").state))
        return sorted(entries, key=lambda item: self.version_key(item[0]), reverse=True)

    @staticmethod
    def _menu_emoji() -> dict[str, str]:
        """The picker's emoji for the same rows, from config/emoji.sh located by THIS file's
        path, never by environment: a standalone run (`!python install/manage-rapid-mlx.py`)
        has no launcher env, and the menu used to print bare labels there. Empty on failure."""
        import importlib.util
        path = Path(__file__).resolve().parents[2] / "bin" / "emoji_constants.py"
        try:
            spec = importlib.util.spec_from_file_location("_fa_emoji_constants", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except (OSError, ImportError, AttributeError, SyntaxError, TypeError):
            return {}
        names = {"releases": "EMOJI_RELEASES_STR", "installed": "EMOJI_VALIDATE_STR",
                 "install": "EMOJI_INSTALL_STR", "promote": "EMOJI_PROMOTE_STR",
                 "snapshot": "EMOJI_SNAPSHOT_STR", "remove": "EMOJI_REMOVE_STR",
                 "quit": "EMOJI_BACK_STR", "title": "EMOJI_TOOLS_STR"}
        return {k: getattr(mod, v, "") for k, v in names.items()}

    def run_interactive_menu(self, repo: Path | None = None) -> int:
        """Run the interactive menu (requires TTY)."""
        if not sys.stdin.isatty():
            print("Interactive menu requires a TTY", file=sys.stderr)
            return 2

        # If repo not set, try to find it
        if repo is None:
            try:
                repo = repository_root()
            except ManagerError:
                repo = None

        e = self._menu_emoji()
        mark = lambda k: f"{e[k]} " if e.get(k) else ""   # noqa: E731
        while True:
            print(f"\n=== {mark('title')}Rapid-MLX Runtime Manager ===")
            print(f"1. {mark('releases')}List releases (PyPI)")
            print(f"2. {mark('installed')}List installed versions")
            print(f"3. {mark('install')}Install a version")
            print(f"4. {mark('promote')}Promote pins to a version")
            print(f"5. {mark('snapshot')}Snapshot a version")
            print(f"6. {mark('remove')}Remove a version")
            print(f"7. {mark('quit')}Quit")

            try:
                choice = input("\nChoose an option (1-7): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                return 0

            if choice == "1":
                self._menu_list_releases()
            elif choice == "2":
                self._menu_list_installed()
            elif choice == "3":
                self._menu_install()
            elif choice == "4":
                self._menu_promote(repo)
            elif choice == "5":
                self._menu_snapshot()
            elif choice == "6":
                self._menu_remove(repo)
            elif choice == "7":
                print("Goodbye!")
                return 0
            else:
                print("Invalid choice. Please enter 1-7.")

    def _menu_list_releases(self) -> None:
        """List available releases."""
        try:
            versions = self.get_available_versions()
            print("\nAvailable Rapid-MLX releases:")
            for i, version in enumerate(versions[:20], 1):
                print(f"  {i:2}. {version}")
        except ManagerError as exc:
            print(f"ERROR: {exc}")

    def _menu_list_installed(self) -> None:
        """List installed versions."""
        entries = self.installed_versions()
        if entries:
            print("\nInstalled Rapid-MLX environments:")
            for version, path, status in entries:
                print(f"  {version:14} {status:10} {path}")
        else:
            print("\nNo versioned Rapid-MLX environments found.")

    def _menu_install(self) -> None:
        """Install a version."""
        try:
            versions = self.get_available_versions()
            print("\nAvailable Rapid-MLX releases:")
            for i, version in enumerate(versions[:30], 1):
                print(f"  {i:2}. {version}")
            raw = input("\nChoose version number (or enter version): ").strip()
            if not raw:
                return
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(versions):
                    version = versions[idx]
                else:
                    print("Invalid selection")
                    return
            else:
                self.require_version(raw)
                version = raw

            # Ask about pin promotion
            skip_pin = input("Skip pin promotion? (y/N): ").strip().lower() == 'y'
            receipt = self.install_version(version, skip_pin_update=skip_pin, repo=self._repo)
            print(json.dumps(receipt.metadata, indent=2, sort_keys=True))
        except ManagerError as exc:
            print(f"ERROR: {exc}")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled")

    def _menu_promote(self, repo: Path | None) -> None:
        """Promote pins to a version."""
        if repo is None:
            print("Repository required for pin promotion (not found)")
            return
        try:
            entries = self.installed_versions()
            if not entries:
                print("No installed versions to promote")
                return
            print("\nInstalled versions:")
            for i, (version, _, status) in enumerate(entries, 1):
                print(f"  {i:2}. {version} ({status})")
            raw = input("\nChoose version number to promote: ").strip()
            if not raw.isdigit():
                print("Invalid selection")
                return
            idx = int(raw) - 1
            if not 0 <= idx < len(entries):
                print("Invalid selection")
                return
            version = entries[idx][0]

            result = self.promote_pins(version, dry_run=False)
            print(json.dumps(result, indent=2, sort_keys=True))
        except ManagerError as exc:
            print(f"ERROR: {exc}")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled")

    def _menu_snapshot(self) -> None:
        """Snapshot a version."""
        try:
            entries = self.installed_versions()
            if not entries:
                print("No installed versions to snapshot")
                return
            print("\nInstalled versions:")
            for i, (version, _, status) in enumerate(entries, 1):
                print(f"  {i:2}. {version} ({status})")
            raw = input("\nChoose version number to snapshot: ").strip()
            if not raw.isdigit():
                print("Invalid selection")
                return
            idx = int(raw) - 1
            if not 0 <= idx < len(entries):
                print("Invalid selection")
                return
            version = entries[idx][0]

            path = self.snapshot_environment(version)
            print(f"Snapshot saved to: {path}")
        except ManagerError as exc:
            print(f"ERROR: {exc}")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled")

    def _menu_remove(self, repo: Path | None) -> None:
        """Remove a version."""
        try:
            entries = self.installed_versions()
            if not entries:
                print("No installed versions to remove")
                return
            print("\nInstalled versions:")
            for i, (version, _, status) in enumerate(entries, 1):
                print(f"  {i:2}. {version} ({status})")
            raw = input("\nChoose version number to remove: ").strip()
            if not raw.isdigit():
                print("Invalid selection")
                return
            idx = int(raw) - 1
            if not 0 <= idx < len(entries):
                print("Invalid selection")
                return
            version = entries[idx][0]

            confirm = input(f"Type 'delete {version}' to confirm: ").strip()
            if confirm != f"delete {version}":
                print("Removal cancelled")
                return

            self.remove_version(version, yes=False, repo=repo)
            print(f"Removed {self.target_for(version)}")
        except ManagerError as exc:
            print(f"ERROR: {exc}")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled")

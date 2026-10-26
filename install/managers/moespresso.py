"""MoEspresso Backend Manager.

MoEspresso serves MoEspresso-packaged models — DeepSeek V4 IQ1 packages whose weights are a
custom serialization (`weight_format: "mxtq"`, `*.tq_bundle` keys) that no MLX engine can read.
Measured 2026-10-25 against ~/.models/deepseek-v4-flash-0731-coder:

- 4.0.0 (the tap's linked `moespresso` formula) REFUSES the package:
  `Error: unsupported required package feature` — the manifest requires
  `calibration` + `deepseek_v4_per_layer_experts`, and 4.0.0 is a Qwen engine.
- 3.0.0 (the `moespresso@3` formula) loads it: 1328 tensors, `tool_calls=on dialect=dsml`.

So this manager installs and reports the **3.x line only**. `brew install moespresso@3` succeeds
while `/opt/homebrew/bin/moespresso` still points at 4.0.0 (Homebrew: "the following
moespresso@3 executables are shadowed by linked moespresso commands"), so resolution is by KEG
PATH, never by the PATH name.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .base import BackendInfo, BackendManager, ManagerError
from . import register_manager

TAP = "steadfastgaze/tap"
# The versioned formula. Installing the unversioned `moespresso` gets 4.x, which cannot load a
# DeepSeek V4 package — the whole reason this manager exists.
FORMULA = "steadfastgaze/tap/moespresso@3"
# Which major line this backend needs, and the HARD CEILING it may never be upgraded past. 4.x
# dropped the DeepSeek V4 features; a weekly upgrade check that proposed it would break the one
# artifact this backend exists to serve, and do so while every command succeeded (user 2026-10-26).
REQUIRED_MAJOR = 3
MAX_MAJOR = REQUIRED_MAJOR


def _major(version: str) -> int | None:
    head = version.lstrip("vV").split(".", 1)[0]
    return int(head) if head.isdigit() else None
HOMEBREW_BIN_CANDIDATES = ("/opt/homebrew/bin/moespresso@3", "/usr/local/bin/moespresso@3")


@register_manager
class MoespressoManager(BackendManager):
    BACKEND_NAME = "moespresso"
    VENV_PREFIX = "moespresso"  # not a venv — a Homebrew bottle
    DEFAULT_VERSION = "3.0.0"
    PACKAGE_NAME = "moespresso"
    PYPI_URL = ""  # not on PyPI

    # --- binary resolution (the load-bearing part) ----------------------------------------
    def cellar_roots(self) -> list[Path]:
        """Keg roots to search. A method (not a constant) so a test can redirect it."""
        return [Path("/opt/homebrew/Cellar/moespresso@3"),
                Path("/usr/local/Cellar/moespresso@3")]

    def candidate_kegs(self) -> list[Path]:
        """Versioned kegs, NEWEST first. Versionless numeric sort — no pinned version string."""
        out: list[Path] = []
        for root in self.cellar_roots():
            if not root.is_dir():
                continue
            for ver_dir in sorted(root.iterdir(), reverse=True):
                binpath = ver_dir / "bin" / "moespresso"
                if binpath.is_file():
                    out.append(binpath)
        return out

    def resolve_binary(self) -> Path | None:
        """A MoEspresso binary that can actually load a DeepSeek V4 package, or None.

        Order is load-bearing: an explicit LA_MOESPRESSO_BIN first (operator pin), then the
        @3 kegs, then Homebrew's `moespresso@3` symlink. The UNVERSIONED
        `/opt/homebrew/bin/moespresso` is deliberately LAST and only accepted when it reports a
        3.x version — it is 4.x on any machine that installed the default formula, and 4.x cannot
        load the packages this backend exists for.
        """
        pinned = self._pinned_bin()
        if pinned:
            return pinned
        for binpath in self.candidate_kegs():
            return binpath
        for cand in HOMEBREW_BIN_CANDIDATES:
            path = Path(cand)
            if path.is_file() and self._reports_required_major(path):
                return path
        return None

    def _pinned_bin(self) -> Path | None:
        import os
        pinned = os.environ.get("LA_MOESPRESSO_BIN", "").strip()
        if not pinned:
            return None
        path = Path(pinned).expanduser()
        return path if path.is_file() else None

    def _reports_required_major(self, path: Path) -> bool:
        """Ask the binary its version; a 4.x binary must not be mistaken for a capable one."""
        try:
            result = subprocess.run([str(path), "--version"], capture_output=True,
                                    text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return False
        text = (result.stdout or "") + (result.stderr or "")
        for token in text.replace(",", " ").split():
            head = token.lstrip("vV")
            if head[:1].isdigit() and "." in head:
                try:
                    return int(head.split(".", 1)[0]) == REQUIRED_MAJOR
                except ValueError:
                    continue
        return False

    def require_binary(self) -> Path:
        binpath = self.resolve_binary()
        if binpath is None:
            raise ManagerError(
                "no usable MoEspresso found. MoEspresso-packaged artifacts need the 3.x line: "
                f"brew install {FORMULA}  (4.x cannot load them — it refuses a DeepSeek V4 "
                "package with 'unsupported required package feature')")
        return binpath

    # --- BackendManager interface ----------------------------------------------------------
    def get_installed_versions(self) -> list[str]:
        """Only the @3 keg counts. `brew list --versions moespresso` reporting 4.0.0 does NOT."""
        versions: list[str] = []
        for binpath in self.candidate_kegs():
            # The keg's directory name is the version Homebrew installed.
            versions.append(binpath.parent.parent.name)
        for binpath in self.candidate_kegs():
            text = self._version_output(binpath)
            if text:
                versions.append(text)
        return sorted(set(versions), reverse=True)

    def _version_output(self, path: Path) -> str | None:
        try:
            result = subprocess.run([str(path), "--version"], capture_output=True,
                                    text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        for token in ((result.stdout or "") + (result.stderr or "")).split():
            head = token.lstrip("vV")
            if head[:1].isdigit() and "." in head:
                return head
        return None

    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        """Versions Homebrew can install, from the tap's own releases.

        The project repository publishes NO GitHub releases (verified 2026-10-25), so GitHub is
        the wrong source; the tap's releases are authoritative. Bounded by the 3.x ceiling: a
        version above it is not "available" for this backend, because it cannot load the artifact
        the backend exists for.
        """
        try:
            result = subprocess.run(
                ["brew", "info", "--json=v2", FORMULA], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagerError(f"could not read {FORMULA}: {exc}") from exc
        if result.returncode != 0:
            raise ManagerError(f"brew info {FORMULA} failed: {result.stderr.strip()}")
        import json
        try:
            stable = json.loads(result.stdout)["formulae"][0]["versions"]["stable"]
        except (ValueError, KeyError, IndexError) as exc:
            raise ManagerError(f"unexpected brew info output: {exc}") from exc
        if (_major(stable) or 0) > MAX_MAJOR:
            raise ManagerError(
                f"{FORMULA} now ships {stable}, which is past this backend's {MAX_MAJOR}.x ceiling "
                f"and cannot load a MoEspresso package. Pin an older tap revision, or keep serving "
                f"the artifacts a {MAX_MAJOR}.x runtime can read.")
        return [stable]

    def get_latest_version(self, include_prereleases: bool = False) -> str:
        versions = self.get_available_versions(include_prereleases)
        if not versions:
            raise ManagerError(f"no available MoEspresso versions in {FORMULA}")
        return versions[0]

    def check_updates(self) -> list[tuple[str, str, str]]:
        installed = self.get_installed_versions()
        if not installed:
            raise ManagerError("MoEspresso 3.x is not installed")
        current = installed[0]
        stable = self.get_latest_version()
        if (_major(stable) or 0) > MAX_MAJOR:
            # Never propose an upgrade the model cannot survive. An empty list would read as
            # "up to date", which is exactly wrong — so the reason is carried for the caller.
            return []
        if self._version_newer(stable, current):
            return [("moespresso", current, stable)]
        return []

    def get_packages_to_check(self) -> list[str]:
        return ["moespresso"]

    def get_package_source(self, package: str) -> str:
        return "homebrew"

    def get_version_source(self) -> str:
        return "homebrew"

    def _get_latest_package_version(self, package: str) -> str:
        if package == "moespresso":
            return self.get_latest_version()
        raise ManagerError(f"Unknown package for MoEspresso: {package}")

    def install_version(self, version: str, python: str | None = None,
                        refresh_deps: bool = False, dry_run: bool = False) -> BackendInfo:
        # The ceiling, enforced at the only method that can install: an explicit request for a
        # version past it is refused, never quietly clamped to 3.x — a silent clamp would report
        # success for something the caller did not ask for.
        if version not in (None, "") and (_major(str(version)) or 0) > MAX_MAJOR:
            raise ManagerError(
                f"MoEspresso {version} is past this backend's {MAX_MAJOR}.x ceiling: it cannot load "
                f"a MoEspresso package (4.x refuses one with 'unsupported required package "
                f"feature'). Install a {MAX_MAJOR}.x version, e.g. {self.DEFAULT_VERSION}.")
        binpath = self.resolve_binary()
        if dry_run:
            return BackendInfo(name=self.BACKEND_NAME, version=str(version),
                               path=str(binpath or f"<{FORMULA}>"), status="dry_run",
                               metadata={"method": "homebrew", "formula": FORMULA})

        # Always the @3 formula, whatever version string was asked: 4.x is a different engine.
        self.run_command(["brew", "install", FORMULA], capture=False)
        return self.validate_environment(str(version))

    def validate_environment(self, version: str) -> BackendInfo:
        binpath = self.require_binary()
        result = self.run_command([str(binpath), "--version"], capture=True)
        return BackendInfo(
            name=self.BACKEND_NAME,
            version=version,
            path=str(binpath),
            status="validated",
            metadata={
                "binary_path": str(binpath),
                "version_output": (result.stdout or "").strip(),
                "formula": FORMULA,
                "validated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    # Required by the base class; MoEspresso is a bottle, not a venv.
    def choose_python(self, explicit: str | None) -> Path:
        return Path("/usr/bin/env")

    def target_for(self, version: str, home: Path | None = None) -> Path:
        """MoEspresso is a Homebrew BOTTLE, so the dry run must not promise a venv path.

        Without this, `install-backend.sh --backend moespresso --dry-run` reports
        `~/.venvs/moespresso-3.0.0` — a path that will never be created, because the real install
        runs `brew install steadfastgaze/tap/moespresso@3`. Report the binary that will exist.
        (oMLX and llama.cpp are bottles too and do not override this; that gap is theirs to fix.)
        """
        return Path(self.resolve_binary() or f"<brew formula {FORMULA}>")

    def cache_root(self) -> Path:
        return self.home / ".cache" / "local-agents" / "moespresso-manager"

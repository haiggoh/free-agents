"""_cuda.py — shared base for the registered CUDA backend managers (0.49.0 Task 14).

CUDA users choose among FOUR backends like Mac users do: cuda-vllm, cuda-llama-cpp (a real
CUDA llama-server build), cuda-exllamav2 (TabbyAPI, git repo theroyallab/tabbyAPI — verified via
GitHub REST, it is NOT a PyPI package), cuda-sglang (PyPI sglang + pre-built flashinfer-python
wheel; never source-build flashinfer — pip runs with --only-binary so a missing wheel FAILS
instead of compiling for 20 minutes).

Layout: VERSIONED side-by-side dirs at <LA_USER_STATE_DIR>/cuda-backends/<name>-<version>
(the legacy unversioned <name> dir is listed as "legacy", removable, never auto-deleted).
The live pin (LA_CUDA_BIN / LA_LLAMA_SERVER / LA_EXL2_BIN / LA_SGLANG_BIN) is written to the
LIVE config only. promote is GATED by a GPU smoke (torch.cuda.is_available() + a .cuda()
tensor) — an import-only check passes on a driver mismatch. CUDA version: nvcc first, else the
nvidia-smi driver table; --cuda-version overrides and must look like 12.8 or it aborts naming
it. No CUDA hardware here: every test stubs the runners.
"""

from __future__ import annotations

import os
import re
import subprocess
import venv
from pathlib import Path
from typing import Any, Optional

from . import register_manager
from .base import BackendManager, ManagerError
from ._common import venv_health

# nvidia-smi driver major -> max supported CUDA (kept as a table; the chosen value is printed
# and overridable with --cuda-version, which fails loudly when malformed).
DRIVER_TO_CUDA: list[tuple[int, str]] = [
    (580, "13.0"), (570, "12.8"), (550, "12.4"), (535, "12.2"), (525, "12.0"),
    (515, "11.8"), (510, "11.7"), (470, "11.4"),
]
# CUDA x.y -> PyTorch wheel index tag (download.pytorch.org/whl/cuXXX). VERIFIED against the
# live index 2026-10-30: cu120/cu122/cu114 return 403 — those tags DO NOT EXIST, so they are
# not in the table and unknown versions fall back to the nearest lower EXISTING tag (cu121).
CUDA_TO_INDEX = {
    "13.0": "cu130", "12.8": "cu128", "12.6": "cu126", "12.4": "cu124", "12.1": "cu121",
    "11.8": "cu118", "11.7": "cu117", "11.6": "cu116",
}

GPU_SMOKE = ("import torch; assert torch.cuda.is_available(), 'no CUDA device visible'; "
             "torch.zeros(1).cuda()")


def _fetch_json(url: str) -> Any:
    """Module-level so tests can patch it (release discovery never runs in tests)."""
    import json
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "free-agents-cuda-manager/1"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


class CudaBackendManager(BackendManager):
    """Base for the four CUDA backends. Subclasses set the class attrs below."""

    VENV_DIR = "cuda-backends"
    PIN_VAR = ""        # live-config variable this backend promotes into
    SERVE = ""          # serve= vocabulary value ("" = launcher-resolved, no model routing)
    PACKAGE = ""        # pip package to install
    BINARY = ""         # bin/<BINARY> the venv must provide (venv_health probe)
    GIT_REPO = ""       # set for git-clone installs (TabbyAPI)
    ONLY_BINARY = ()    # packages pip must never source-build
    SUPPORTS_VENV_REMOVE = True
    VERSION_OPS = ("snapshot", "promote", "remove", "repair")
    REMOVE_KEEPS_ONE = True

    # ---- layout -------------------------------------------------------------

    @property
    def venv_root(self) -> Path:
        state = os.environ.get("LA_USER_STATE_DIR") or str(
            self.home / ".local" / "share" / "free-agents")
        return Path(state) / self.VENV_DIR

    def legacy_path(self) -> Optional[Path]:
        p = self.venv_root / self.VENV_PREFIX
        return p if p.is_dir() and not p.is_symlink() else None

    def installed_versions(self, home: Path | None = None) -> list[tuple[str, Path, str]]:
        root = self.venv_root
        entries: list[tuple[str, Path, str]] = []
        if root.is_dir():
            for path in root.glob(f"{self.VENV_PREFIX}-*"):
                if path.is_symlink() or not path.is_dir():
                    continue
                version = path.name.removeprefix(f"{self.VENV_PREFIX}-")
                if not self.VERSION_RE.fullmatch(version):
                    continue
                entries.append((version, path, venv_health(path, self.BINARY).state))
        legacy = self.legacy_path()
        if legacy is not None:
            state = venv_health(legacy, self.BINARY).state if (legacy / "bin").is_dir() else "incomplete"
            entries.append(("legacy", legacy, state))
        return sorted(entries, key=lambda e: e[0], reverse=True)

    def target_for(self, version: str) -> Path:
        if version == "legacy":
            legacy = self.legacy_path()
            if legacy is None:
                raise ManagerError(f"no legacy {self.VENV_PREFIX} dir")
            return legacy
        self.require_version(version)
        return self.venv_root / f"{self.VENV_PREFIX}-{version}"

    # ---- CUDA version + wheel index ------------------------------------------

    def detect_cuda(self, override: Optional[str] = None, runner=subprocess.run) -> str:
        if override is not None:
            if not re.fullmatch(r"\d+\.\d+", override):
                raise ManagerError(f"--cuda-version must look like 12.8, got: {override!r}")
            return override
        try:
            out = runner(["nvcc", "--version"], capture_output=True, text=True, timeout=10).stdout
            m = re.search(r"release (\d+\.\d+)", out)
            if m:
                return m.group(1)
        except Exception:
            pass
        try:
            out = runner(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                         capture_output=True, text=True, timeout=10).stdout.strip()
            m = re.search(r"(\d+)\.", out)
            if m:
                major = int(m.group(1))
                for floor, cuda in DRIVER_TO_CUDA:
                    if major >= floor:
                        return cuda
        except Exception:
            pass
        raise ManagerError("could not detect the CUDA version (no nvcc, no nvidia-smi) — "
                           "pass --cuda-version explicitly")

    def pytorch_index(self, cuda: str) -> str:
        tag = CUDA_TO_INDEX.get(cuda)
        if tag is None:
            major, minor = (int(x) for x in cuda.split(".")[:2])
            best = None
            for v, t in sorted(CUDA_TO_INDEX.items(), key=lambda kv: [int(x) for x in kv[0].split(".")]):
                vmaj, vmin = int(v.split(".")[0]), int(v.split(".")[1])
                if vmaj == major and vmin <= minor:
                    best = t
            tag = best or "cu121"
        url = f"https://download.pytorch.org/whl/{tag}"
        print(f"CUDA {cuda} -> PyTorch wheel index {url}")
        return url

    # ---- build + smoke --------------------------------------------------------

    def _build(self, target: Path, version: str, index_url: str, runner) -> None:
        """Default build: a venv + pip install. Subclasses override (cmake, git clone)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True).create(target)
        cmd = [str(target / "bin" / "pip"), "install", "--upgrade", "pip", "wheel", "setuptools"]
        if index_url:
            cmd += ["--extra-index-url", index_url]
        for pkg in self.ONLY_BINARY:
            cmd += ["--only-binary", pkg]
        cmd.append(self.PACKAGE)
        r = runner(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise ManagerError(f"pip install {self.PACKAGE} failed: "
                               f"{(r.stderr or r.stdout).strip().splitlines()[-1:]}")

    def gpu_smoke(self, target: Path, runner=subprocess.run) -> None:
        r = runner([str(target / "bin" / "python"), "-c", GPU_SMOKE],
                   capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:]
            raise ManagerError(f"GPU smoke failed in {target}: {tail[0] if tail else 'no output'}")

    def install_version(self, version: str, *, python: str | None = None,
                        refresh_deps: bool = False, dry_run: bool = False,
                        cuda_version: Optional[str] = None, runner=subprocess.run) -> Any:
        from .base import BackendInfo
        target = self.venv_root / f"{self.VENV_PREFIX}-{version}"
        if target.exists() or target.is_symlink():
            raise ManagerError(f"{target} already exists")
        if dry_run:
            return BackendInfo(name=self.BACKEND_NAME, version=version, path=str(target),
                               status="dry_run", metadata={})
        cuda = self.detect_cuda(cuda_version, runner)
        index_url = self.pytorch_index(cuda)
        try:
            self._build(target, version, index_url, runner)
        except Exception:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
            raise
        self.gpu_smoke(target, runner)
        return BackendInfo(name=self.BACKEND_NAME, version=version, path=str(target),
                           status="complete", metadata={"cuda": cuda, "index": index_url})

    def validate_environment(self, version: str) -> Any:
        target = self.target_for(version)
        r = subprocess.run([str(target / "bin" / "python"), "-c", f"import {self.PACKAGE}"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise ManagerError(f"{self.PACKAGE} does not import in {target}")
        return {"version": version, "path": str(target)}

    def promote_pins(self, version: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Point the live config at this version — ONLY after the GPU smoke passes."""
        target = self.target_for(version)
        self.gpu_smoke(target)
        binary = target / "bin" / self.BINARY
        if not dry_run:
            self.validate_environment(version)
        return self.set_live_config_var(self.PIN_VAR, str(binary),
                                        purpose=f"promote-{self.BACKEND_NAME}-{version}",
                                        dry_run=dry_run)

    # ---- release discovery (PyPI JSON / GitHub REST — never from memory) ----------

    def get_available_versions(self, include_prereleases: bool = False) -> list[str]:
        if self.GIT_REPO:
            owner_repo = self.GIT_REPO.removeprefix("https://github.com/").removesuffix(".git")
            url = f"https://api.github.com/repos/{owner_repo}/tags?per_page=100"
            tags = [t["name"] for t in _fetch_json(url)]
            if self.BACKEND_NAME == "cuda-llama-cpp":
                tags = [t.removeprefix("b") for t in tags if re.fullmatch(r"b\d+", t)]
            return sorted(tags, key=self.version_key, reverse=True)
        payload = _fetch_json(f"https://pypi.org/pypi/{self.PACKAGE}/json")
        releases = payload.get("releases") or {}
        out = []
        for version, files in releases.items():
            if not self.VERSION_RE.fullmatch(version):
                continue
            if not include_prereleases and re.search(r"[a-zA-Z]", version):
                continue
            if files and all(isinstance(f, dict) and f.get("yanked") for f in files):
                continue
            out.append(version)
        return sorted(out, key=self.version_key, reverse=True)

    def get_latest_version(self, include_prereleases: bool = False) -> str:
        versions = self.get_available_versions(include_prereleases)
        if not versions:
            raise ManagerError(f"no releases found for {self.PACKAGE or self.GIT_REPO}")
        return versions[0]

    def get_installed_versions(self) -> list[str]:
        return [v for v, _p, _s in self.installed_versions()]

    def get_packages_to_check(self) -> list[str]:
        return [self.PACKAGE] if self.PACKAGE else []

    def _get_latest_package_version(self, package: str) -> str:
        return self.get_latest_version()

    def version_key(self, value: str) -> tuple:
        if value == "legacy":
            return (0, 0, 0, 0, "", 0)
        m = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
        if not m:
            raise ValueError(value)
        return (*[int(x) for x in m.groups()], 1, "", 0)


@register_manager
class CudaVLLMManager(CudaBackendManager):
    BACKEND_NAME = "cuda-vllm"
    VENV_PREFIX = "cuda-vllm"
    PIN_VAR = "LA_CUDA_BIN"
    PACKAGE = "vllm"
    BINARY = "vllm"


@register_manager
class CudaLlamaCppManager(CudaBackendManager):
    """A REAL CUDA llama-server: cmake -DGGML_CUDA=ON from a release tag, built into the
    versioned dir (bin/llama-server). The old manager pip-installed llama-cpp-python, which
    ships no llama-server — hotswap's own hint could never fix its error."""

    BACKEND_NAME = "cuda-llama-cpp"
    VENV_PREFIX = "cuda-llama-cpp"
    PIN_VAR = "LA_LLAMA_SERVER"
    PACKAGE = "llama-cpp-python"   # import probe only; the server comes from the cmake build
    BINARY = "llama-server"
    LLAMA_CPP_REPO = "https://github.com/ggml-org/llama.cpp.git"

    def _build(self, target: Path, version: str, index_url: str, runner) -> None:
        import shutil
        target.parent.mkdir(parents=True, exist_ok=True)
        src = target / "src"
        r = runner(["git", "clone", "--depth", "1", "--branch", f"b{version.lstrip('v')}",
                    self.LLAMA_CPP_REPO, str(src)], capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            shutil.rmtree(target, ignore_errors=True)
            raise ManagerError(f"clone of llama.cpp b{version} failed: {(r.stderr or '').strip()[-200:]}")
        build = target / "build"
        r = runner(["cmake", "-S", str(src), "-B", str(build), "-DGGML_CUDA=ON",
                    "-DLLAMA_BUILD_SERVER=ON", "-DLLAMA_BUILD_EXAMPLES=OFF"],
                   capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            shutil.rmtree(target, ignore_errors=True)
            raise ManagerError("cmake configure failed (no CUDA toolkit?)")
        r = runner(["cmake", "--build", str(build), "--target", "llama-server", "-j"],
                   capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            shutil.rmtree(target, ignore_errors=True)
            raise ManagerError("llama-server build failed")
        (target / "bin").mkdir(exist_ok=True)
        shutil.copy2(build / "bin" / "llama-server", target / "bin" / "llama-server")

    def gpu_smoke(self, target: Path, runner=subprocess.run) -> None:
        r = runner([str(target / "bin" / "llama-server"), "--version"],
                   capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise ManagerError(f"llama-server --version failed in {target}")
        out = (r.stdout or "") + (r.stderr or "")
        if "CUDA" not in out and "cuda" not in out:
            raise ManagerError("built llama-server reports no CUDA — refusing to promote a CPU build")


@register_manager
class CudaExLlamaV2Manager(CudaBackendManager):
    """TabbyAPI (ExLlamaV2 server). NOT on PyPI — git clone of theroyalallab/tabbyAPI
    (verified via GitHub REST 2026-10-30); pip installs its requirements into the venv.
    Serves via `<venv>/bin/python -m tabbyapi`; models route here with serve=exl2."""

    BACKEND_NAME = "cuda-exllamav2"
    VENV_PREFIX = "cuda-exllamav2"
    PIN_VAR = "LA_EXL2_BIN"
    SERVE = "exl2"
    PACKAGE = "tabbyapi"          # import probe
    BINARY = "python"             # pinned var points at the venv python; branch runs -m tabbyapi
    GIT_REPO = "https://github.com/theroyallab/tabbyAPI.git"

    def _build(self, target: Path, version: str, index_url: str, runner) -> None:
        import shutil
        target.parent.mkdir(parents=True, exist_ok=True)
        r = runner(["git", "clone", "--depth", "1", self.GIT_REPO, str(target)],
                   capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            shutil.rmtree(target, ignore_errors=True)
            raise ManagerError(f"clone of tabbyAPI failed: {(r.stderr or '').strip()[-200:]}")
        venv.EnvBuilder(with_pip=True).create(target / "venv")
        pip = target / "venv" / "bin" / "pip"
        r = runner([str(pip), "install", "--upgrade", "pip", "wheel", "setuptools"],
                   capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise ManagerError("pip bootstrap failed")
        r = runner([str(pip), "install", "-r", str(target / "requirements.txt")],
                   capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            shutil.rmtree(target, ignore_errors=True)
            raise ManagerError(f"tabbyAPI requirements failed: {(r.stderr or '').strip()[-200:]}")
        # the pinned binary is the venv python; link it at bin/python for uniform health checks
        bin_dir = target / "bin"
        bin_dir.mkdir(exist_ok=True)
        py = target / "venv" / "bin" / "python"
        link = bin_dir / "python"
        if not link.exists():
            link.symlink_to(py)

    def gpu_smoke(self, target: Path, runner=subprocess.run) -> None:
        CudaBackendManager.gpu_smoke(self, target, runner)


@register_manager
class CudaSGLangManager(CudaBackendManager):
    """SGLang (PyPI). Depends on flashinfer — pip runs --only-binary flashinfer-python so a
    missing pre-built wheel for the torch/CUDA pair FAILS instead of source-building for
    20+ minutes. Serves via `<venv>/bin/python -m sglang.launch_server`; serve=sglang."""

    BACKEND_NAME = "cuda-sglang"
    VENV_PREFIX = "cuda-sglang"
    PIN_VAR = "LA_SGLANG_BIN"
    SERVE = "sglang"
    PACKAGE = "sglang"
    BINARY = "python"
    ONLY_BINARY = ("flashinfer-python",)

    def _build(self, target: Path, version: str, index_url: str, runner) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True).create(target)
        pip = target / "bin" / "pip"
        cmd = [str(pip), "install", "--upgrade", "pip", "wheel", "setuptools",
               "--extra-index-url", index_url, "--only-binary", "flashinfer-python",
               "sglang", "flashinfer-python"]
        r = runner(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:]
            raise ManagerError("sglang/flashinfer install failed (no pre-built flashinfer wheel "
                               f"for this torch/CUDA pair?): {tail[0] if tail else 'no output'}")

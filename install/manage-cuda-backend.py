#!/usr/bin/env python3
"""
manage-cuda-backend.py — CUDA backend lifecycle manager for free-agents.

Forks the architecture of manage-rapid-mlx.py for NVIDIA CUDA environments:
- Side-by-side venvs with rollback capability
- LA_CUDA_BIN pointer for active backend
- Dynamic PyTorch wheel selection via --extra-index-url based on host CUDA version
- Strict smoke test: torch.cuda.is_available() + tensor.cuda()
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Optional


# --- Constants -----------------------------------------------------------------

# Base directory for CUDA backends (side-by-side venvs)
CUDA_BACKENDS_DIR = Path.home() / ".local" / "share" / "free-agents" / "cuda-backends"
# Pointer to active backend
LA_CUDA_BIN_FILE = Path.home() / ".local" / "state" / "free-agents" / "cuda-active-bin"
# Metadata for rollback
BACKEND_META_FILE = CUDA_BACKENDS_DIR / "metadata.json"

# Supported backends
BACKENDS = {
    "vllm": {
        "package": "vllm",
        "description": "vLLM for CUDA (AWQ/EXL2 models with UVM offload)",
    },
    "llama-cpp": {
        "package": "llama-cpp-python",
        "description": "llama.cpp Python bindings (GGUF models with -ngl offload)",
    },
}

# --- Helpers -------------------------------------------------------------------


def get_cuda_version() -> Optional[str]:
    """Extract CUDA version from nvidia-smi or nvcc."""
    # Try nvidia-smi first (driver version, which maps to max supported CUDA)
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        # Driver version format: "550.90.07" -> we need to map to CUDA version
        # For simplicity, use a heuristic: driver 550 -> CUDA 12.4, 535 -> 12.2, etc.
        # In practice, we'll use nvcc if available for exact CUDA toolkit version
        match = re.search(r"(\d+)\.(\d+)", out)
        if match:
            major = int(match.group(1))
            # Heuristic mapping (approximate)
            if major >= 550:
                return "12.4"
            elif major >= 535:
                return "12.2"
            elif major >= 525:
                return "12.0"
            elif major >= 515:
                return "11.8"
            elif major >= 510:
                return "11.7"
            elif major >= 470:
                return "11.4"
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass

    # Fallback: nvcc --version
    try:
        out = subprocess.run(
            ["nvcc", "--version"], capture_output=True, text=True, timeout=5, check=True
        ).stdout
        match = re.search(r"release (\d+)\.(\d+)", out)
        if match:
            return f"{match.group(1)}.{match.group(2)}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return None


def get_pytorch_index_url(cuda_version: Optional[str]) -> str:
    """Return the appropriate PyTorch --extra-index-url for the CUDA version."""
    if not cuda_version:
        return "https://download.pytorch.org/whl/cpu"

    # Parse major.minor
    match = re.match(r"(\d+)\.(\d+)", cuda_version)
    if not match:
        return "https://download.pytorch.org/whl/cu121"

    major, minor = int(match.group(1)), int(match.group(2))

    # PyTorch wheel index URLs
    if major == 12:
        if minor >= 4:
            return "https://download.pytorch.org/whl/cu124"
        elif minor >= 1:
            return "https://download.pytorch.org/whl/cu121"
    elif major == 11:
        if minor >= 8:
            return "https://download.pytorch.org/whl/cu118"
        elif minor >= 7:
            return "https://download.pytorch.org/whl/cu117"
        elif minor >= 6:
            return "https://download.pytorch.org/whl/cu116"

    # Default fallback
    return "https://download.pytorch.org/whl/cu121"


def create_venv(venv_path: Path) -> bool:
    """Create a virtual environment at the given path."""
    try:
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        builder = venv.EnvBuilder(with_pip=True, upgrade_deps=True)
        builder.create(venv_path)
        return True
    except Exception as e:
        print(f"Failed to create venv at {venv_path}: {e}")
        return False


def pip_install(venv_path: Path, packages: list, extra_index_url: Optional[str] = None) -> bool:
    """Install packages into the venv using its pip."""
    pip = venv_path / "bin" / "pip"
    if not pip.exists():
        # Windows fallback
        pip = venv_path / "Scripts" / "pip.exe"

    cmd = [str(pip), "install", "--upgrade", "pip", "wheel", "setuptools"]
    if extra_index_url:
        cmd.extend(["--extra-index-url", extra_index_url])
    cmd.extend(packages)

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        return True
    except subprocess.CalledProcessError as e:
        print(f"pip install failed: {e.stderr}")
        return False


def run_smoke_test(venv_path: Path, backend: str) -> tuple[bool, str]:
    """Run the strict CUDA smoke test for the given backend."""
    python = venv_path / "bin" / "python"
    if not python.exists():
        python = venv_path / "Scripts" / "python.exe"

    if backend == "vllm":
        test_code = """
import torch
print(f"PyTorch: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"CUDA version: {torch.version.cuda}")
    print(f"Device: {torch.cuda.get_device_name(0)}")
    # Allocate a tensor on GPU
    x = torch.zeros(1).cuda()
    print(f"GPU tensor allocated: {x.device}")
    del x
    torch.cuda.empty_cache()
print("vLLM smoke test PASSED")
"""
    elif backend == "llama-cpp":
        test_code = """
import llama_cpp
print("llama_cpp imported successfully")
# Check for CUDA support
try:
    from llama_cpp import llama_supports_gpu_offload
    print(f"GPU offload supported: {llama_supports_gpu_offload()}")
except:
    pass
print("llama.cpp smoke test PASSED")
"""
    else:
        return False, f"Unknown backend: {backend}"

    try:
        result = subprocess.run(
            [str(python), "-c", test_code],
            capture_output=True,
            text=True,
            timeout=60,
        )
        success = result.returncode == 0
        return success, result.stdout if success else result.stderr
    except subprocess.TimeoutExpired:
        return False, "Smoke test timed out"
    except Exception as e:
        return False, str(e)


def load_metadata() -> dict:
    """Load backend metadata from disk."""
    if BACKEND_META_FILE.exists():
        try:
            return json.loads(BACKEND_META_FILE.read_text())
        except Exception:
            pass
    return {}


def save_metadata(meta: dict) -> None:
    """Save backend metadata to disk."""
    BACKEND_META_FILE.parent.mkdir(parents=True, exist_ok=True)
    BACKEND_META_FILE.write_text(json.dumps(meta, indent=2))


def get_active_backend() -> Optional[dict]:
    """Get the currently active backend from metadata."""
    meta = load_metadata()
    active = meta.get("active")
    if active and active in BACKENDS:
        return active
    return None


def set_active_backend(backend: str) -> None:
    """Set the active backend in metadata and update LA_CUDA_BIN_FILE."""
    meta = load_metadata()
    meta["active"] = backend
    save_metadata(meta)

    # Update LA_CUDA_BIN_FILE pointer
    venv_path = CUDA_BACKENDS_DIR / backend
    bin_path = venv_path / "bin"
    if not bin_path.exists():
        bin_path = venv_path / "Scripts"
    LA_CUDA_BIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    LA_CUDA_BIN_FILE.write_text(str(bin_path))


def install_backend(backend: str, force: bool = False) -> bool:
    """Install a CUDA backend (vllm or llama-cpp)."""
    if backend not in BACKENDS:
        print(f"Unknown backend: {backend}")
        return False

    venv_path = CUDA_BACKENDS_DIR / backend

    # Check if already installed
    meta = load_metadata()
    if not force and backend in meta.get("installed", {}):
        print(f"{backend} already installed at {venv_path}")
        return True

    # Get CUDA version and PyTorch index URL
    cuda_version = get_cuda_version()
    if not cuda_version:
        print("WARNING: Could not detect CUDA version; using CPU-only PyTorch")
        index_url = None
    else:
        index_url = get_pytorch_index_url(cuda_version)
        print(f"Detected CUDA {cuda_version}; using index: {index_url}")

    # Create venv
    print(f"Creating venv for {backend} at {venv_path}...")
    if venv_path.exists():
        shutil.rmtree(venv_path)
    if not create_venv(venv_path):
        return False

    # Install packages
    if backend == "vllm":
        packages = ["vllm"]
    elif backend == "llama-cpp":
        packages = ["llama-cpp-python"]
    else:
        return False

    print(f"Installing {backend} packages...")
    if not pip_install(venv_path, packages, index_url):
        return False

    # Run smoke test
    print(f"Running smoke test for {backend}...")
    success, output = run_smoke_test(Path(CUDA_BACKENDS_DIR / backend), backend)
    if not success:
        print(f"Smoke test FAILED for {backend}:")
        print(output)
        # Rollback
        shutil.rmtree(venv_path, ignore_errors=True)
        return False

    print("Smoke test PASSED:")
    print(output)

    # Record installation
    meta = load_metadata()
    if "installed" not in meta:
        meta["installed"] = {}
    meta["installed"][backend] = {
        "version": "latest",
        "cuda_version": cuda_version,
        "index_url": index_url,
    }
    save_metadata(meta)

    print(f"{backend} installed successfully at {venv_path}")
    return True


def uninstall_backend(backend: str) -> bool:
    """Uninstall a CUDA backend."""
    if backend not in BACKENDS:
        print(f"Unknown backend: {backend}")
        return False

    venv_path = CUDA_BACKENDS_DIR / backend
    if not venv_path.exists():
        print(f"{backend} not installed at {venv_path}")
        return False

    # Check if active
    active = get_active_backend()
    if active == backend:
        print(f"Cannot uninstall active backend {backend}. Use 'switch' first.")
        return False

    shutil.rmtree(venv_path, ignore_errors=True)
    meta = load_metadata()
    if "installed" in meta and backend in meta["installed"]:
        del meta["installed"][backend]
    save_metadata(meta)
    print(f"{backend} uninstalled")
    return True


def switch_backend(backend: str) -> bool:
    """Switch the active backend."""
    if backend not in BACKENDS:
        print(f"Unknown backend: {backend}")
        return False

    venv_path = CUDA_BACKENDS_DIR / backend
    if not venv_path.exists():
        print(f"{backend} not installed. Install it first.")
        return False

    set_active_backend(backend)
    print(f"Active backend switched to {backend} ({venv_path})")
    return True


def info() -> dict:
    """Get info about all backends."""
    meta = load_metadata()
    result = {"active": get_active_backend(), "backends": {}}
    for name in BACKENDS:
        venv_path = CUDA_BACKENDS_DIR / name
        result["backends"][name] = {
            "installed": name in meta.get("installed", {}),
            "path": str(venv_path) if venv_path.exists() else None,
            "details": meta.get("installed", {}).get(name, {}),
        }
    return result


# --- CLI -------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="CUDA backend lifecycle manager for free-agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # install
    install_p = sub.add_parser("install", help="Install a CUDA backend")
    install_p.add_argument(
        "backend", choices=list(BACKENDS.keys()), help="Backend to install"
    )
    install_p.add_argument("--force", action="store_true", help="Reinstall if already present")

    # uninstall
    uninstall_p = sub.add_parser("uninstall", help="Uninstall a CUDA backend")
    uninstall_p.add_argument(
        "backend", choices=list(BACKENDS.keys()), help="Backend to uninstall"
    )

    # switch
    switch_p = sub.add_parser("switch", help="Set the active backend")
    switch_p.add_argument(
        "backend", choices=list(BACKENDS.keys()), help="Backend to activate"
    )

    # info
    sub.add_parser("info", help="Show backend status")

    # smoke test
    smoke_p = sub.add_parser("smoke", help="Run smoke test for a backend")
    smoke_p.add_argument(
        "backend", choices=list(BACKENDS.keys()), help="Backend to test"
    )

    # detect CUDA version
    sub.add_parser("detect-cuda", help="Detect host CUDA version")

    args = parser.parse_args()

    if args.cmd == "install":
        success = install_backend(args.backend, force=args.force)
        sys.exit(0 if success else 1)
    elif args.cmd == "uninstall":
        success = uninstall_backend(args.backend)
        sys.exit(0 if success else 1)
    elif args.cmd == "switch":
        success = switch_backend(args.backend)
        sys.exit(0 if success else 1)
    elif args.cmd == "info":
        print(json.dumps(info(), indent=2))
    elif args.cmd == "smoke":
        success, output = run_smoke_test(CUDA_BACKENDS_DIR / args.backend, args.backend)
        print(output)
        sys.exit(0 if success else 1)
    elif args.cmd == "detect-cuda":
        cuda_version = get_cuda_version()
        if cuda_version:
            print(f"CUDA version: {cuda_version}")
            print(f"PyTorch index URL: {get_pytorch_index_url(cuda_version)}")
        else:
            print("CUDA not detected")
            sys.exit(1)


if __name__ == "__main__":
    main()
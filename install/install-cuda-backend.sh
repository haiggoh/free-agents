#!/usr/bin/env bash
# install-cuda-backend.sh — CUDA backend setup for local-agents on Linux/WSL.
#
# This is a thin wrapper around manage-cuda-backend.py for the install-backend.sh
# script to route CUDA backend installation through the same interface.
#
# Usage: install-cuda-backend.sh [--backend vllm-cuda|llama-cpp-cuda] [--version VER] [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANAGE_CUDA_BACKEND="${SCRIPT_DIR}/manage-cuda-backend.py"
PYTHON="${LA_PYTHON:-python3}"

usage() {
    cat <<'HELP'
install-cuda-backend.sh — install CUDA inference backends

Usage: install-cuda-backend.sh [OPTIONS]

Options:
  --backend NAME        Install specific backend (vllm-cuda|llama-cpp-cuda)
  --version VER         Version to install (default: latest stable)
  --dry-run             Show what would be installed without doing it
  --python EXE          Python executable to use (default: python3)
  --help                Show this help and exit

Examples:
  install-cuda-backend.sh --backend vllm-cuda
  install-cuda-backend.sh --backend llama-cpp-cuda --dry-run
HELP
    exit 0
}

BACKEND=""
VERSION=""
DRY_RUN=0
PYTHON="${LA_PYTHON:-python3}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --backend)
            shift
            BACKEND="${1:-}"
            shift
            ;;
        --version)
            shift
            VERSION="${1:-}"
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --python)
            PYTHON="${2:-python3}"
            shift 2
            ;;
        -h|--help)
            sed -n '2,/^set -euo pipefail/{ /^set -euo pipefail/d; p; }' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "usage: $(basename "$0") [--backend vllm-cuda|llama-cpp-cuda] [--version VER] [--dry-run] [--python EXE]"
            exit 2
            ;;
    esac
done

[[ -n "$BACKEND" ]] || { echo "❌ --backend is required (vllm-cuda|llama-cpp-cuda)" >&2; exit 2; }
[[ "$BACKEND" =~ ^(vllm-cuda|llama-cpp-cuda)$ ]] || { echo "❌ Invalid backend: $BACKEND (must be vllm-cuda or llama-cpp-cuda)" >&2; exit 2; }

# Check prerequisites
command -v nvidia-smi >/dev/null 2>&1 || { echo "❌ nvidia-smi not found — CUDA not available" >&2; exit 1; }
command -v "$PYTHON" >/dev/null || { echo "❌ Python not found: $PYTHON"; exit 1; }

# Ensure manage-cuda-backend.py is executable
chmod +x "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/manage-cuda-backend.py"

extra_args=()
if [[ -n "$VERSION" ]]; then
    extra_args+=("$VERSION")
fi

if [[ $DRY_RUN -eq 1 ]]; then
    echo "[*] DRY RUN: would install $BACKEND${VERSION:+ $VERSION} via manage-cuda-backend.py"
    "$PYTHON" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/manage-cuda-backend.py" --dry-run --backend "$BACKEND" install "${extra_args[@]}"
else
    python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/manage-cuda-backend.py" --backend "$BACKEND" install "${extra_args[@]}"
fi

echo "✓ $BACKEND done"
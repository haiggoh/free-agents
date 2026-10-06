#!/usr/bin/env bash
# install-backend.sh — one-time backend setup for local-agents (run OUTSIDE Claude Code).
#
# Sets up ALL inference backends that the launcher/hotswap can drive:
#   1. Rapid-MLX (DEFAULT) — versioned venvs via manage-backend.py
#   2. vllm-mlx (LEGACY) — versioned venvs via manage-backend.py
#   3. oMLX — via Homebrew (or binary)
#   4. llama.cpp — binary releases via manage-backend.py
#   5. litellm — versioned venv via manage-backend.py (for remote free-API routing)
#   6. CUDA backends (vllm-cuda, llama-cpp-cuda) — versioned venvs via manage-cuda-backend.py
#
# It does NOT download models — run download-models.sh for that.
# Idempotent-ish and guarded; re-run safely.
# Requires: macOS on Apple Silicon (for MLX), Linux/WSL with CUDA (for CUDA backends), Homebrew python3, git.
set -euo pipefail

# Source hardware detection first
HW_DETECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../bin" && pwd)/la-hw-detect.sh"
# shellcheck source=/dev/null
. "$HW_DETECT"

MANAGE_BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/manage-backend.py"
MANAGE_CUDA_BACKEND="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/manage-cuda-backend.py"
PYTHON="${LA_PYTHON:-python3}"

usage() {
    cat <<'HELP'
install-backend.sh — set up local-agents inference backends

Usage: install-backend.sh [OPTIONS]

Options:
  --all                 Install all backends (default: rapid-mlx only)
  --backend NAME        Install specific backend (rapid-mlx|vllm-mlx|omlx|llama-cpp|litellm|vllm-cuda|llama-cpp-cuda)
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
  install-backend.sh --all                    # Install all backends (MLX + CUDA if available)
  install-backend.sh --backend vllm-cuda      # Install CUDA vLLM
  install-backend.sh --backend llama-cpp-cuda # Install CUDA llama.cpp
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
    if [[ "$LA_HARDWARE" = "cuda" ]]; then
        BACKENDS=("rapid-mlx" "vllm-mlx" "omlx" "llama-cpp" "litellm" "vllm-cuda" "llama-cpp-cuda")
    else
        BACKENDS=("rapid-mlx" "vllm-mlx" "omlx" "llama-cpp" "litellm")
    fi
elif [[ ${#BACKENDS[@]} -eq 0 ]]; then
    BACKENDS=("rapid-mlx")
fi

echo "== local-agents backend install =="
echo "Backends to install: ${BACKENDS[*]}"
echo "Hardware: $LA_HARDWARE${LA_VRAM_GB:+ (VRAM: ${LA_VRAM_GB}GB)}"
[[ $DRY_RUN -eq 1 ]] && echo "DRY RUN MODE"

# Check prerequisites
case "$LA_HARDWARE" in
    mlx) [[ "$(uname -s)" = "Darwin" ]] || { echo "❌ MLX backends require macOS"; exit 1; } ;;
    cuda) command -v nvidia-smi >/dev/null 2>&1 || { echo "❌ CUDA backends require nvidia-smi"; exit 1; } ;;
    cpu-only) echo "⚠ cpu-only mode: no GPU detected; MLX and CUDA backends will not run here (not recommended)"; ;;
    rocm|opencl) echo "⚠ $LA_HARDWARE GPU detected: no backend in this installer targets it; only CPU-capable ones will work"; ;;
esac
command -v "$PYTHON" >/dev/null || { echo "❌ Python not found: $PYTHON"; exit 1; }
command -v brew >/dev/null || { echo "⚠ Homebrew not found — oMLX/llama.cpp may not install."; }

# Ensure manage scripts are executable
chmod +x "$MANAGE_BACKEND"
[[ -f "$MANAGE_CUDA_BACKEND" ]] && chmod +x "$MANAGE_CUDA_BACKEND"

# Function to install a backend
install_backend() {
    local backend="$1"
    local version="$2"
    local extra_args=()

    # For dry-run, use default version if none specified
    if [[ -n "$version" ]]; then
        extra_args+=("$version")
    elif [[ $DRY_RUN -eq 1 ]]; then
        # Use a default version for dry-run to avoid interactive prompt
        case "$backend" in
            rapid-mlx) extra_args+=("0.15.3") ;;
            vllm-mlx) extra_args+=("0.5.0") ;;
            litellm) extra_args+=("1.60.0") ;;
            llama-cpp) extra_args+=("b5000") ;;
            vllm-cuda) extra_args+=("0.7.0") ;;
            llama-cpp-cuda) extra_args+=("b5000") ;;
        esac
    fi

    echo ""
    echo "[*] Installing $backend${version:+ $version}..."

    # Route CUDA backends to manage-cuda-backend.py
    case "$backend" in
        rapid-mlx|vllm-mlx|litellm|omlx|llama-cpp)
            if [[ $DRY_RUN -eq 1 ]]; then
                "$PYTHON" "$MANAGE_BACKEND" --dry-run --backend "$backend" install "${extra_args[@]}"
            else
                "$PYTHON" "$MANAGE_BACKEND" --backend "$backend" install "${extra_args[@]}"
            fi
            ;;
        vllm-cuda|llama-cpp-cuda)
            if [[ $DRY_RUN -eq 1 ]]; then
                "$PYTHON" "$MANAGE_CUDA_BACKEND" --dry-run --backend "$backend" install "${extra_args[@]}"
            else
                "$PYTHON" "$MANAGE_CUDA_BACKEND" --backend "$backend" install "${extra_args[@]}"
            fi
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
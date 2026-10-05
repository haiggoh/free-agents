#!/usr/bin/env bash
# la-hw-detect.sh — hardware & OS detection for free-agents.
# Exports: LA_HARDWARE (mlx|cuda|cpu-only), LA_OS (mac|linux|windows-wsl), LA_VRAM_GB.
# Safe to source multiple times; idempotent.
set -uo pipefail

# Resolve this script's directory (portable even when invoked via symlink).
# Handle both direct execution and sourcing from bash -c 'source ...'
if [[ -n "${BASH_SOURCE[0]:-}" && "${BASH_SOURCE[0]}" != "${0}" ]]; then
    # Being sourced
    _s="${BASH_SOURCE[0]}"
elif [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    # Direct execution
    _s="${BASH_SOURCE[0]}"
else
    # bash -c 'source ...' fallback
    _s="${0}"
fi
while [ -h "$_s" ]; do _d="$(cd -P "$(dirname "$_s")" && pwd)"; _s="$(readlink "$_s")"; case "$_s" in /*) ;; *) _s="$_d/$_s";; esac; done
LA_HW_DETECT_DIR="$(cd -P "$(dirname "$_s")" && pwd)"
# shellcheck source=/dev/null
. "$LA_HW_DETECT_DIR/../config/config-lib.sh"

# --- OS detection -----------------------------------------------------------
# We export LA_OS as one of: mac | linux | windows-wsl
case "$(uname -s)" in
    Darwin)  LA_OS="mac" ;;
    Linux)
        # Check for WSL2
        if grep -qi microsoft /proc/version 2>/dev/null || [ -n "${WSL_DISTRO_NAME:-}" ]; then
            LA_OS="windows-wsl"
        else
            LA_OS="linux"
        fi
        ;;
    *)       LA_OS="unknown" ;;
esac
export LA_OS

# --- Hardware detection -----------------------------------------------------
# We export LA_HARDWARE as one of: mlx | cuda | cpu-only
# And for CUDA: LA_VRAM_GB (integer), LA_CUDA_WARNING (1 if nvidia-smi failed on Linux/WSL)
# mlx is only on macOS with Apple Silicon.

# Default to cpu-only (safest fallback)
LA_HARDWARE="cpu-only"
LA_VRAM_GB=""
LA_CUDA_WARNING=0

case "$LA_OS" in
    mac)
        # Apple Silicon check
        if [[ "$(uname -m)" == "arm64" ]]; then
            LA_HARDWARE="mlx"
        fi
        ;;
    linux|windows-wsl)
        # Try to query NVIDIA GPU
        if command -v nvidia-smi >/dev/null 2>&1; then
            # Get VRAM in GB (integer, rounded down)
            vram_mb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | awk '{print int($1/1024)}')
            if [[ -n "$vram_mb" && "$vram_mb" =~ ^[0-9]+$ && "$vram_mb" -gt 0 ]]; then
                LA_VRAM_GB="$vram_mb"
                LA_HARDWARE="cuda"
            else
                # nvidia-smi ran but couldn't parse VRAM
                LA_CUDA_WARNING=1
            fi
        else
            # nvidia-smi not found on Linux/WSL → false negative guard
            LA_CUDA_WARNING=1
        fi
        ;;
esac

export LA_HARDWARE LA_VRAM_GB LA_CUDA_WARNING

# --- Debug / dry-run support ------------------------------------------------
# If sourced with LA_HW_DETECT_DEBUG=1, print the resolved vars.
if [[ "${LA_HW_DETECT_DEBUG:-0}" -eq 1 ]]; then
    cat <<EOF
LA_OS=$LA_OS
LA_HARDWARE=$LA_HARDWARE
LA_VRAM_GB=${LA_VRAM_GB:-}
LA_CUDA_WARNING=$LA_CUDA_WARNING
EOF
fi

# Exit success when run directly; return when sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    exit 0
else
    return 0
fi
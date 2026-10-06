#!/usr/bin/env bash
# la-hw-detect.sh — hardware & OS detection for free-agents.
# Exports comprehensive hardware/OS information for cross-platform compatibility.
# Exports: LA_OS, LA_OS_FAMILY, LA_DISTRO, LA_DISTRO_VERSION, LA_DISTRO_CODENAME,
#          LA_ARCH, LA_HARDWARE (mlx|cuda|cpu-only|rocm|opencl), LA_VRAM_GB,
#          LA_CPU_ARCH, LA_CPU_MODEL, LA_CPU_CORES, LA_CPU_THREADS,
#          LA_CPU_FLAGS, LA_SYSTEM_RAM_GB, LA_SWAP_GB,
#          LA_GPU_VENDOR, LA_GPU_MODEL, LA_GPU_DRIVER_VERSION,
#          LA_CONTAINER, LA_VIRTUALIZATION, LA_KERNEL_VERSION,
#          LA_CUDA_VERSION, LA_ROCM_VERSION, LA_CUDA_WARNING.
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

# ============================================================================
# OS DETECTION
# ============================================================================
# We export LA_OS as one of: mac | linux | windows-wsl | windows-cygwin | windows-msys | windows | unknown
# We export LA_OS_FAMILY as one of: unix | windows | unknown
# We export LA_DISTRO, LA_DISTRO_VERSION, LA_DISTRO_CODENAME for Linux

# OS kernel detection
KERNEL_NAME="$(uname -s 2>/dev/null || echo "unknown")"
KERNEL_RELEASE="$(uname -r 2>/dev/null || echo "unknown")"
KERNEL_VERSION="$(uname -v 2>/dev/null || echo "unknown")"
KERNEL_ARCH="$(uname -m 2>/dev/null || echo "unknown")"

case "$KERNEL_NAME" in
    Darwin)
        LA_OS="mac"
        LA_OS_FAMILY="unix"
        LA_DISTRO="macos"
        LA_DISTRO_VERSION="$KERNEL_RELEASE"
        LA_DISTRO_CODENAME=""
        LA_KERNEL_VERSION="$KERNEL_RELEASE"
        ;;
    Linux)
        LA_OS_FAMILY="unix"
        LA_KERNEL_VERSION="$KERNEL_RELEASE"
        # Check for WSL2
        if grep -qi microsoft /proc/version 2>/dev/null || [ -n "${WSL_DISTRO_NAME:-}" ]; then
            LA_OS="windows-wsl"
            LA_DISTRO="wsl"
            LA_DISTRO_VERSION="${WSL_DISTRO_VERSION:-unknown}"
            LA_DISTRO_CODENAME="${WSL_DISTRO_CODENAME:-}"
        else
            LA_OS="linux"
            # Detect Linux distribution
            if [[ -r /etc/os-release ]]; then
                # shellcheck source=/dev/null
                . /etc/os-release
                LA_DISTRO="${ID:-unknown}"
                LA_DISTRO_VERSION="${VERSION_ID:-unknown}"
                LA_DISTRO_CODENAME="${VERSION_CODENAME:-}"
            elif [[ -r /etc/lsb-release ]]; then
                # shellcheck source=/dev/null
                . /etc/lsb-release
                LA_DISTRO="${DISTRIB_ID:-unknown}"
                LA_DISTRO_VERSION="${DISTRIB_RELEASE:-unknown}"
                LA_DISTRO_CODENAME="${DISTRIB_CODENAME:-}"
            elif [[ -r /etc/debian_version ]]; then
                LA_DISTRO="debian"
                LA_DISTRO_VERSION="$(cat /etc/debian_version 2>/dev/null || echo "unknown")"
            elif [[ -r /etc/redhat-release ]]; then
                LA_DISTRO="rhel"
                LA_DISTRO_VERSION="$(sed -n 's/.*release \([0-9.]*\).*/\1/p' /etc/redhat-release 2>/dev/null || echo "unknown")"
            elif [[ -r /etc/arch-release ]]; then
                LA_DISTRO="arch"
                LA_DISTRO_VERSION="rolling"
            elif [[ -r /etc/alpine-release ]]; then
                LA_DISTRO="alpine"
                LA_DISTRO_VERSION="$(cat /etc/alpine-release 2>/dev/null || echo "unknown")"
            else
                LA_DISTRO="linux"
                LA_DISTRO_VERSION="unknown"
            fi
        fi
        ;;
    CYGWIN*|MINGW*|MSYS*)
        LA_OS="windows"
        LA_OS_FAMILY="windows"
        if [[ "$KERNEL_NAME" == "CYGWIN"* ]]; then
            LA_OS="windows-cygwin"
        elif [[ "$KERNEL_NAME" == "MINGW"* ]]; then
            LA_OS="windows-mingw"
        elif [[ "$KERNEL_NAME" == "MSYS"* ]]; then
            LA_OS="windows-msys"
        fi
        # Try to get Windows version
        if command -v wmic >/dev/null 2>&1; then
            LA_DISTRO_VERSION="$(wmic os get Version /value 2>/dev/null | sed -n 's/Version=//p' || echo "unknown")"
        elif command -v powershell >/dev/null 2>&1; then
            LA_DISTRO_VERSION="$(powershell -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).Version" 2>/dev/null || echo "unknown")"
        fi
        LA_DISTRO="windows"
        LA_DISTRO_CODENAME=""
        ;;
    *)
        LA_OS="unknown"
        LA_OS_FAMILY="unknown"
        LA_DISTRO="unknown"
        LA_DISTRO_VERSION="unknown"
        LA_DISTRO_CODENAME="unknown"
        ;;
esac

# Set OS family if not already set
: "${LA_OS_FAMILY:=${LA_OS%%-*}}"
case "$LA_OS" in
    mac|linux|windows-wsl) LA_OS_FAMILY="unix" ;;
    windows|windows-cygwin|windows-mingw|windows-msys) LA_OS_FAMILY="windows" ;;
    *) LA_OS_FAMILY="unknown" ;;
esac

export LA_OS LA_OS_FAMILY LA_DISTRO LA_DISTRO_VERSION LA_DISTRO_CODENAME LA_KERNEL_VERSION

# ============================================================================
# ARCHITECTURE DETECTION
# ============================================================================
# We export LA_ARCH (uname -m normalized), LA_CPU_ARCH (normalized family)

# Normalize architecture
case "$KERNEL_ARCH" in
    x86_64|amd64) LA_ARCH="x86_64"; LA_CPU_ARCH="x86_64" ;;
    aarch64|arm64) LA_ARCH="aarch64"; LA_CPU_ARCH="aarch64" ;;
    armv7l|armv7) LA_ARCH="armv7l"; LA_CPU_ARCH="arm" ;;
    armv6l|armv6) LA_ARCH="armv6l"; LA_CPU_ARCH="arm" ;;
    armv5*) LA_ARCH="armv5"; LA_CPU_ARCH="arm" ;;
    i386|i486|i586|i686) LA_ARCH="i386"; LA_CPU_ARCH="x86" ;;
    ppc64le) LA_ARCH="ppc64le"; LA_CPU_ARCH="ppc64le" ;;
    ppc64) LA_ARCH="ppc64"; LA_CPU_ARCH="ppc64" ;;
    s390x) LA_ARCH="s390x"; LA_CPU_ARCH="s390x" ;;
    riscv64) LA_ARCH="riscv64"; LA_CPU_ARCH="riscv64" ;;
    *) LA_ARCH="$KERNEL_ARCH"; LA_CPU_ARCH="unknown" ;;
esac

# Detailed CPU information
LA_CPU_MODEL=""
LA_CPU_CORES=""
LA_CPU_THREADS=""
LA_CPU_FLAGS=""

if [[ "$LA_OS" == "mac" ]]; then
    # macOS: use sysctl
    LA_CPU_MODEL="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "unknown")"
    LA_CPU_CORES="$(sysctl -n hw.physicalcpu 2>/dev/null || echo "0")"
    LA_CPU_THREADS="$(sysctl -n hw.logicalcpu 2>/dev/null || echo "0")"
    LA_CPU_FLAGS="$(sysctl -n machdep.cpu.features 2>/dev/null | tr ' ' ',' || echo "")"
elif [[ "$LA_OS_FAMILY" == "unix" ]]; then
    # Linux/WSL: use /proc/cpuinfo and lscpu
    if [[ -r /proc/cpuinfo ]]; then
        LA_CPU_MODEL="$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ *//' || echo "unknown")"
        LA_CPU_CORES="$(grep -c '^processor' /proc/cpuinfo 2>/dev/null || echo "0")"
        LA_CPU_THREADS="$(grep -c '^processor' /proc/cpuinfo 2>/dev/null || echo "0")"
        LA_CPU_FLAGS="$(grep -m1 'flags' /proc/cpuinfo | cut -d: -f2 | sed 's/^ *//' | tr ' ' ',' || echo "")"
    elif command -v lscpu >/dev/null 2>&1; then
        LA_CPU_MODEL="$(lscpu 2>/dev/null | grep 'Model name' | cut -d: -f2 | sed 's/^ *//' || echo "unknown")"
        LA_CPU_CORES="$(lscpu 2>/dev/null | grep '^CPU(s):' | awk '{print $2}' || echo "0")"
        LA_CPU_THREADS="$(lscpu 2>/dev/null | grep '^Thread(s) per core:' | awk '{print $4}' | awk '{print $1 * $2}' 2>/dev/null || echo "0")"
    fi
elif [[ "$LA_OS_FAMILY" == "windows" ]]; then
    # Windows: use wmic or powershell
    if command -v powershell >/dev/null 2>&1; then
        LA_CPU_MODEL="$(powershell -NoProfile -Command "(Get-CimInstance Win32_Processor).Name" 2>/dev/null | head -1 | sed 's/^ *//' || echo "unknown")"
        LA_CPU_CORES="$(powershell -NoProfile -Command "(Get-CimInstance Win32_Processor).NumberOfCores" 2>/dev/null | head -1 || echo "0")"
        LA_CPU_THREADS="$(powershell -NoProfile -Command "(Get-CimInstance Win32_Processor).NumberOfLogicalProcessors" 2>/dev/null | head -1 || echo "0")"
    elif command -v wmic >/dev/null 2>&1; then
        LA_CPU_MODEL="$(wmic cpu get Name /value 2>/dev/null | sed -n 's/Name=//p' | head -1 || echo "unknown")"
        LA_CPU_CORES="$(wmic cpu get NumberOfCores /value 2>/dev/null | sed -n 's/NumberOfCores=//p' | head -1 || echo "0")"
        LA_CPU_THREADS="$(wmic cpu get NumberOfLogicalProcessors /value 2>/dev/null | sed -n 's/NumberOfLogicalProcessors=//p' | head -1 || echo "0")"
    fi
fi

export LA_ARCH LA_CPU_ARCH LA_CPU_MODEL LA_CPU_CORES LA_CPU_THREADS LA_CPU_FLAGS

# ============================================================================
# MEMORY DETECTION
# ============================================================================
LA_SYSTEM_RAM_GB=""
LA_SWAP_GB=""

if [[ "$LA_OS" == "mac" ]]; then
    # macOS: use sysctl
    MEMSIZE_BYTES="$(sysctl -n hw.memsize 2>/dev/null || echo "0")"
    if [[ "$MEMSIZE_BYTES" =~ ^[0-9]+$ ]]; then
        LA_SYSTEM_RAM_GB=$(( MEMSIZE_BYTES / 1024 / 1024 / 1024 ))
    fi
    SWAPSIZE_BYTES="$(sysctl -n vm.swapusage 2>/dev/null | awk '{print $3}' | sed 's/[^0-9.]//g' || echo "0")"
    if [[ "$SWAPSIZE_BYTES" =~ ^[0-9]+$ ]]; then
        LA_SWAP_GB=$(( SWAPSIZE_BYTES / 1024 / 1024 / 1024 ))
    fi
elif [[ "$LA_OS_FAMILY" == "unix" ]]; then
    # Linux/WSL: use free and /proc/meminfo
    if [[ -r /proc/meminfo ]]; then
        MEM_TOTAL_KB="$(grep '^MemTotal:' /proc/meminfo | awk '{print $2}')"
        SWAP_TOTAL_KB="$(grep '^SwapTotal:' /proc/meminfo | awk '{print $2}')"
        if [[ "$MEM_TOTAL_KB" =~ ^[0-9]+$ ]]; then
            LA_SYSTEM_RAM_GB=$(( MEM_TOTAL_KB / 1024 / 1024 ))
        fi
        if [[ "$SWAP_TOTAL_KB" =~ ^[0-9]+$ ]]; then
            LA_SWAP_GB=$(( SWAP_TOTAL_KB / 1024 / 1024 ))
        fi
    elif command -v free >/dev/null 2>&1; then
        LA_SYSTEM_RAM_GB="$(free -g 2>/dev/null | awk '/^Mem:/ {print $2}' || echo "0")"
        LA_SWAP_GB="$(free -g 2>/dev/null | awk '/^Swap:/ {print $2}' || echo "0")"
    fi
elif [[ "$LA_OS_FAMILY" == "windows" ]]; then
    # Windows: use wmic or powershell
    if command -v powershell >/dev/null 2>&1; then
        LA_SYSTEM_RAM_GB="$(powershell -NoProfile -Command "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)" 2>/dev/null || echo "0")"
        LA_SWAP_GB="$(powershell -NoProfile -Command "[math]::Round((Get-CimInstance Win32_PageFileUsage | Measure-Object -Property CurrentUsage -Sum).Sum/1GB)" 2>/dev/null || echo "0")"
    elif command -v wmic >/dev/null 2>&1; then
        LA_SYSTEM_RAM_GB="$(wmic ComputerSystem get TotalPhysicalMemory /value 2>/dev/null | sed -n 's/TotalPhysicalMemory=//p' | awk '{print int($1/1024/1024/1024)}' || echo "0")"
    fi
fi

export LA_SYSTEM_RAM_GB LA_SWAP_GB

# ============================================================================
# GPU DETECTION
# ============================================================================
# We export LA_GPU_VENDOR, LA_GPU_MODEL, LA_GPU_DRIVER_VERSION, LA_VRAM_GB
# And LA_HARDWARE (mlx|cuda|rocm|opencl|cpu-only)

# Default values
LA_HARDWARE="cpu-only"
LA_VRAM_GB=""
LA_GPU_VENDOR=""
LA_GPU_MODEL=""
LA_GPU_DRIVER_VERSION=""
LA_CUDA_VERSION=""
LA_ROCM_VERSION=""
LA_CUDA_WARNING=0

# NVIDIA GPU detection
if command -v nvidia-smi >/dev/null 2>&1; then
    LA_GPU_VENDOR="nvidia"
    LA_GPU_MODEL="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | sed 's/^ *//' || echo "unknown")"
    LA_GPU_DRIVER_VERSION="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | sed 's/^ *//' || echo "unknown")"

    # Get VRAM in GB
    VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | awk '{print int($1/1024)}' 2>/dev/null)"
    if [[ -n "$VRAM_MB" && "$VRAM_MB" =~ ^[0-9]+$ && "$VRAM_MB" -gt 0 ]]; then
        LA_VRAM_GB="$VRAM_MB"
        LA_HARDWARE="cuda"
    else
        LA_CUDA_WARNING=1
    fi

    # CUDA version
    if command -v nvcc >/dev/null 2>&1; then
        LA_CUDA_VERSION="$(nvcc --version 2>/dev/null | grep -oP 'release \K[0-9.]+' | head -1 || echo "unknown")"
    elif [[ -r /usr/local/cuda/version.txt ]]; then
        LA_CUDA_VERSION="$(cat /usr/local/cuda/version.txt 2>/dev/null | sed 's/.* //')"
    fi
fi

# AMD ROCm detection
if [[ "$LA_HARDWARE" == "cpu-only" ]] && command -v rocm-smi >/dev/null 2>&1; then
    LA_GPU_VENDOR="amd"
    LA_GPU_MODEL="$(rocm-smi --showproductname 2>/dev/null | grep -v '^==' | head -1 | sed 's/^ *//' || echo "unknown")"
    LA_ROCM_VERSION="$(rocm-smi --showversion 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "unknown")"
    # Try to get VRAM
    VRAM_MB="$(rocm-smi --showmeminfo vram 2>/dev/null | grep -oP '\d+' | head -1)"
    if [[ -n "$VRAM_MB" && "$VRAM_MB" =~ ^[0-9]+$ ]]; then
        LA_VRAM_GB=$(( VRAM_MB / 1024 ))
        LA_HARDWARE="rocm"
    fi
fi

# Intel GPU detection (integrated or discrete)
if [[ "$LA_HARDWARE" == "cpu-only" ]] && command -v clinfo >/dev/null 2>&1; then
    if clinfo 2>/dev/null | grep -qi intel; then
        LA_GPU_VENDOR="intel"
        LA_GPU_MODEL="$(clinfo 2>/dev/null | grep -m1 'Device Name' | cut -d: -f2 | sed 's/^ *//' || echo "unknown")"
        LA_HARDWARE="opencl"
    fi
fi

# Apple Silicon (Metal/MPS)
if [[ "$LA_OS" == "mac" && "$KERNEL_ARCH" == "arm64" ]]; then
    if [[ "$LA_HARDWARE" == "cpu-only" ]]; then
        LA_HARDWARE="mlx"
    fi
    # Get GPU info from system_profiler
    if command -v system_profiler >/dev/null 2>&1; then
        GPU_INFO="$(system_profiler SPDisplaysDataType 2>/dev/null | head -30)"
        if [[ -n "$GPU_INFO" ]]; then
            LA_GPU_VENDOR="apple"
            LA_GPU_MODEL="$(echo "$GPU_INFO" | grep -m1 'Chipset Model' | cut -d: -f2 | sed 's/^ *//' || echo "Apple Silicon")"
        fi
    fi
fi

# Determine final hardware type if not already set
case "$LA_HARDWARE" in
    mlx|cuda|rocm|opencl) ;;
    *) LA_HARDWARE="cpu-only" ;;
esac

export LA_HARDWARE LA_VRAM_GB LA_GPU_VENDOR LA_GPU_MODEL LA_GPU_DRIVER_VERSION LA_CUDA_VERSION LA_ROCM_VERSION LA_CUDA_WARNING

# ============================================================================
# CUDA VERSION DETECTION (improved)
# ============================================================================
if [[ -z "$LA_CUDA_VERSION" && "$LA_GPU_VENDOR" == "nvidia" ]]; then
    # Try multiple methods to get CUDA version
    if command -v nvcc >/dev/null 2>&1; then
        LA_CUDA_VERSION="$(nvcc --version 2>/dev/null | grep -oP 'release \K[0-9.]+' | head -1)"
    elif [[ -r /usr/local/cuda/version.txt ]]; then
        LA_CUDA_VERSION="$(cat /usr/local/cuda/version.txt 2>/dev/null | sed 's/.* //')"
    elif [[ -d /usr/local/cuda ]]; then
        # Try to infer from directory name
        LA_CUDA_VERSION="$(basename "$(readlink -f /usr/local/cuda 2>/dev/null || echo /usr/local/cuda)" | sed 's/cuda-//')"
    fi
    export LA_CUDA_VERSION
fi

# ============================================================================
# CONTAINER & VIRTUALIZATION DETECTION
# ============================================================================
LA_CONTAINER="none"
LA_VIRTUALIZATION="none"

# Docker detection
if [[ -f /.dockerenv ]]; then
    LA_CONTAINER="docker"
elif [[ -r /proc/1/cgroup ]]; then
    if grep -q docker /proc/1/cgroup 2>/dev/null; then
        LA_CONTAINER="docker"
    elif grep -q podman /proc/1/cgroup 2>/dev/null; then
        LA_CONTAINER="podman"
    elif grep -q containerd /proc/1/cgroup 2>/dev/null; then
        LA_CONTAINER="containerd"
    elif grep -q kubepods /proc/1/cgroup 2>/dev/null; then
        LA_CONTAINER="kubernetes"
    fi
fi

# Virtualization detection
if [[ -f /proc/cpuinfo ]]; then
    if grep -qi 'hypervisor' /proc/cpuinfo; then
        LA_VIRTUALIZATION="hypervisor"
    elif grep -q 'QEMU\|KVM\|VMware\|VirtualBox\|Xen\|Hyper-V' /proc/cpuinfo 2>/dev/null; then
        LA_VIRTUALIZATION="kvm"
    fi
elif [[ "$LA_OS_FAMILY" == "windows" ]]; then
    if command -v powershell >/dev/null 2>&1; then
        if powershell -NoProfile -Command "(Get-CimInstance Win32_ComputerSystem).Manufacturer" 2>/dev/null | grep -qi 'vmware\|virtualbox\|qemu\|xen\|microsoft corporation'; then
            LA_VIRTUALIZATION="hypervisor"
        fi
    fi
elif [[ "$LA_OS" == "mac" ]]; then
    if sysctl -n kern.hv_support 2>/dev/null | grep -q 1; then
        LA_VIRTUALIZATION="hypervisor"
    fi
fi

export LA_CONTAINER LA_VIRTUALIZATION

# ============================================================================
# DEBUG / DRY-RUN SUPPORT
# ============================================================================
if [[ "${LA_HW_DETECT_DEBUG:-0}" -eq 1 ]]; then
    cat <<EOF
LA_OS=$LA_OS
LA_OS_FAMILY=$LA_OS_FAMILY
LA_DISTRO=$LA_DISTRO
LA_DISTRO_VERSION=$LA_DISTRO_VERSION
LA_DISTRO_CODENAME=$LA_DISTRO_CODENAME
LA_KERNEL_VERSION=$LA_KERNEL_VERSION
LA_ARCH=$LA_ARCH
LA_CPU_ARCH=$LA_CPU_ARCH
LA_CPU_MODEL=$LA_CPU_MODEL
LA_CPU_CORES=$LA_CPU_CORES
LA_CPU_THREADS=$LA_CPU_THREADS
LA_CPU_FLAGS=$LA_CPU_FLAGS
LA_SYSTEM_RAM_GB=$LA_SYSTEM_RAM_GB
LA_SWAP_GB=$LA_SWAP_GB
LA_HARDWARE=$LA_HARDWARE
LA_VRAM_GB=$LA_VRAM_GB
LA_GPU_VENDOR=$LA_GPU_VENDOR
LA_GPU_MODEL=$LA_GPU_MODEL
LA_GPU_DRIVER_VERSION=$LA_GPU_DRIVER_VERSION
LA_CUDA_VERSION=$LA_CUDA_VERSION
LA_ROCM_VERSION=$LA_ROCM_VERSION
LA_CUDA_WARNING=$LA_CUDA_WARNING
LA_ROCM_VERSION=$LA_ROCM_VERSION
LA_CONTAINER=$LA_CONTAINER
LA_VIRTUALIZATION=$LA_VIRTUALIZATION
EOF
fi

# Exit success when run directly; return when sourced
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    exit 0
else
    return 0
fi
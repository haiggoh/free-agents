#!/usr/bin/env bash
# free-agents Master Installer — STANDALONE
# One-command seamless installation of free-agents plugin + local inference backend
# Cross-platform: macOS (Apple Silicon MLX) + Linux/WSL2 (NVIDIA CUDA)
# Downloads the plugin from GitHub release automatically — no prior download needed
# Can be run from terminal or double-clicked in Finder on macOS (.command extension)
#
# Usage:
#   curl -fsSL https://github.com/haiggoh/free-agents/releases/latest/download/install-free-agents.command | bash
#   # or download the .command file from the latest release and run it directly

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Configuration
REPO_OWNER="haiggoh"
REPO_NAME="free-agents"
PLUGIN_NAME="free-agents"
MARKETPLACE_NAME="haiggoh/get-haiggoh"
# Get latest release version from GitHub API
RELEASE_API="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/releases/latest"
RELEASE_VERSION=""  # Will be fetched

# Detect platform
UNAME_S="$(uname -s)"
UNAME_M="$(uname -m)"
IS_MAC=false
IS_LINUX=false
IS_WSL=false
IS_CUDA=false

case "$UNAME_S" in
    Darwin)
        IS_MAC=true
        ;;
    Linux)
        IS_LINUX=true
        if grep -qi microsoft /proc/version 2>/dev/null || [ -n "${WSL_DISTRO_NAME:-}" ]; then
            IS_WSL=true
        fi
        ;;
esac

# Check for CUDA on Linux
if [[ "$IS_LINUX" == "true" ]] && command -v nvidia-smi >/dev/null 2>&1; then
    IS_CUDA=true
fi

# Detect if running in a terminal
if [[ -t 0 ]]; then
    INTERACTIVE=true
else
    INTERACTIVE=false
fi

# Helper functions
print_header() {
    echo -e "${CYAN}${BOLD}================================${NC}"
    echo -e "${CYAN}${BOLD}  free-agents Master Installer  ${NC}"
    echo -e "${CYAN}${BOLD}================================${NC}"
    echo ""
}

print_step() {
    echo -e "${BLUE}${BOLD}▶ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_info() {
    echo -e "${CYAN}  $1${NC}"
}

confirm() {
    if [[ "$INTERACTIVE" == "true" ]]; then
        read -r -p "$(echo -e "${YELLOW}$1 [Y/n] ${NC}")" response
        case "$response" in
            [nN][oO]|[nN]) return 1 ;;
            *) return 0 ;;
        esac
    else
        return 0
    fi
}

confirm_skip() {
    if [[ "$INTERACTIVE" == "true" ]]; then
        read -r -p "$(echo -e "${YELLOW}$1 [y/N] ${NC}")" response
        case "$response" in
            [yY][eE][sS]|[yY]) return 0 ;;
            *) return 1 ;;
        esac
    else
        return 0
    fi
}

# Fetch latest release version
fetch_latest_version() {
    print_step "Fetching latest release version..."
    if command -v curl >/dev/null 2>&1; then
        RELEASE_VERSION=$(curl -fsSL "$RELEASE_API" 2>/dev/null | grep '"tag_name"' | head -1 | sed -E 's/.*"tag_name": *"v?([^"]+)".*/\1/')
    elif command -v wget >/dev/null 2>&1; then
        RELEASE_VERSION=$(wget -qO- "$RELEASE_API" 2>/dev/null | grep '"tag_name"' | head -1 | sed -E 's/.*"tag_name": *"v?([^"]+)".*/\1/')
    fi

    if [[ -z "$RELEASE_VERSION" || "$RELEASE_VERSION" == "null" ]]; then
        print_warning "Could not fetch latest version from GitHub API, using fallback"
        RELEASE_VERSION="0.25.7"
    fi
    print_success "Latest version: $RELEASE_VERSION"
}

# Download and extract plugin from GitHub release
download_plugin() {
    print_step "Downloading free-agents plugin (v$RELEASE_VERSION)..."

    local plugin_dir="$HOME/.claude/plugins/$PLUGIN_NAME"
    local tarball_url="https://github.com/${REPO_OWNER}/${REPO_NAME}/releases/download/v${RELEASE_VERSION}/free-agents-${RELEASE_VERSION}.tar.gz"
    local temp_dir=$(mktemp -d)
    local tarball="$temp_dir/free-agents-${RELEASE_VERSION}.tar.gz"

    # Clean up temp dir on exit
    trap 'rm -rf "$temp_dir"' EXIT

    # Download tarball
    if command -v curl >/dev/null 2>&1; then
        if ! curl -fsSL "$tarball_url" -o "$tarball"; then
            print_error "Failed to download release tarball from: $tarball_url"
            print_info "Trying fallback: clone from git..."
            download_plugin_via_git
            return $?
        fi
    elif command -v wget >/dev/null 2>&1; then
        if ! wget -q "$tarball_url" -O "$tarball"; then
            print_error "Failed to download release tarball from: $tarball_url"
            print_info "Trying fallback: clone from git..."
            download_plugin_via_git
            return $?
        fi
    else
        print_error "Neither curl nor wget available for download"
        download_plugin_via_git
        return $?
    fi

    # Verify tarball
    if [[ ! -f "$tarball" || ! -s "$tarball" ]]; then
        print_error "Downloaded tarball is empty or missing"
        download_plugin_via_git
        return $?
    fi

    # Extract
    print_info "Extracting plugin..."
    mkdir -p "$plugin_dir"
    if ! tar -xzf "$tarball" -C "$temp_dir"; then
        print_error "Failed to extract tarball"
        return 1
    fi

    # Move extracted contents to plugin dir
    local extracted_dir="$temp_dir/free-agents"
    if [[ ! -d "$extracted_dir" ]]; then
        print_error "Expected directory not found in tarball: free-agents/"
        return 1
    fi

    # Copy all files (preserving structure)
    cp -r "$extracted_dir/"* "$plugin_dir/"

    # Make scripts executable
    find "$plugin_dir/bin" -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
    find "$plugin_dir/bin" -name "*.py" -exec chmod +x {} \; 2>/dev/null || true
    find "$plugin_dir/install" -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
    find "$plugin_dir/install" -name "*.py" -exec chmod +x {} \; 2>/dev/null || true
    chmod +x "$plugin_dir/install-plugin.sh" 2>/dev/null || true
    chmod +x "$plugin_dir/master-install.command" 2>/dev/null || true

    print_success "Plugin installed to $plugin_dir"
}

# Fallback: clone from git
download_plugin_via_git() {
    print_step "Cloning free-agents from GitHub..."

    local plugin_dir="$HOME/.claude/plugins/$PLUGIN_NAME"
    local temp_dir=$(mktemp -d)

    trap 'rm -rf "$temp_dir"' EXIT

    if git clone --depth 1 --branch "main" "https://github.com/${REPO_OWNER}/${REPO_NAME}.git" "$temp_dir/repo" 2>/dev/null; then
        mkdir -p "$plugin_dir"
        cp -r "$temp_dir/repo/"* "$plugin_dir/"

        # Make scripts executable
        find "$plugin_dir/bin" -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
        find "$plugin_dir/bin" -name "*.py" -exec chmod +x {} \; 2>/dev/null || true
        find "$plugin_dir/install" -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
        find "$plugin_dir/install" -name "*.py" -exec chmod +x {} \; 2>/dev/null || true

        print_success "Plugin cloned and installed to $plugin_dir"
        return 0
    else
        print_error "Failed to clone repository"
        return 1
    fi
}

# Detect platform and show info
detect_platform() {
    print_step "Detecting platform..."

    if [[ "$IS_MAC" == "true" ]]; then
        local mac_model=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Apple Silicon")
        local ram_gb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 / 1024 / 1024 ))
        print_success "macOS detected: $mac_model, ${ram_gb}GB RAM"
        BACKEND_TYPE="MLX (Apple Silicon)"
        BACKEND_DETAILS="Rapid-MLX + vllm-mlx with fork patches"
    elif [[ "$IS_LINUX" == "true" ]]; then
        local cpu_model=$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2 | sed 's/^ *//' || echo "Linux CPU")
        local ram_gb=$(free -g 2>/dev/null | awk '/^Mem:/ {print $2}' || echo "?")
        if [[ "$IS_WSL" == "true" ]]; then
            print_success "Linux (WSL2) detected: $cpu_model, ${ram_gb}GB RAM"
        else
            print_success "Linux detected: $cpu_model, ${ram_gb}GB RAM"
        fi

        if [[ "$IS_CUDA" == "true" ]]; then
            local gpu_model=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | sed 's/^ *//')
            local vram_gb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | awk '{print int($1/1024)}')
            local cuda_version=$(nvcc --version 2>/dev/null | grep -oP 'release \K[0-9.]+' | head -1 || echo "unknown")
            print_success "NVIDIA GPU detected: $gpu_model (${vram_gb}GB VRAM, CUDA $cuda_version)"
            BACKEND_TYPE="CUDA (NVIDIA GPU)"
            BACKEND_DETAILS="vLLM CUDA + llama.cpp CUDA (Tier A/B/C auto-selection)"
        else
            print_warning "No NVIDIA GPU detected — CUDA backends unavailable"
            print_info "Only CPU/llama.cpp backend will work (slow for large models)"
            BACKEND_TYPE="CPU only"
            BACKEND_DETAILS="llama.cpp CPU mode (install with --backend llama-cpp)"
        fi
    else
        print_error "Unsupported platform: $UNAME_S"
        return 1
    fi
}

# Check if Claude Code is installed
check_claude_code() {
    print_step "Checking for Claude Code..."
    if command -v claude >/dev/null 2>&1; then
        local version=$(claude --version 2>/dev/null | head -1 || echo "unknown")
        print_success "Claude Code found: $version"
        return 0
    else
        print_error "Claude Code not found in PATH"
        print_info "Install from: https://claude.com/download"
        return 1
    fi
}

# Check if git is available
check_git() {
    print_step "Checking for git..."
    if command -v git >/dev/null 2>&1; then
        print_success "git found: $(git --version | cut -d' ' -f3)"
        return 0
    else
        print_error "git not found"
        if [[ "$IS_MAC" == "true" ]]; then
            print_info "Install with: brew install git"
        else
            print_info "Install with: sudo apt-get install git  (or your distro's package manager)"
        fi
        return 1
    fi
}

# Check Python
check_python() {
    print_step "Checking for Python 3..."
    if command -v python3 >/dev/null 2>&1; then
        local version=$(python3 --version 2>/dev/null | cut -d' ' -f2)
        print_success "Python 3 found: $version"
        return 0
    else
        print_error "Python 3 not found"
        if [[ "$IS_MAC" == "true" ]]; then
            print_info "Install with: brew install python"
        else
            print_info "Install with: sudo apt-get install python3 python3-venv  (or your distro's package manager)"
        fi
        return 1
    fi
}

# Add marketplace
add_marketplace() {
    print_step "Adding haiggoh marketplace..."
    if claude plugin marketplace list 2>/dev/null | grep -q "$MARKETPLACE_NAME"; then
        print_success "Marketplace already added"
        return 0
    fi

    if claude plugin marketplace add "$REPO_OWNER/$REPO_NAME" 2>&1; then
        print_success "Marketplace added successfully"
        return 0
    else
        print_warning "Could not add marketplace via CLI (may need interactive session)"
        print_info "You can add it manually in Claude Code with:"
        print_info "  /plugin marketplace add $REPO_OWNER/$REPO_NAME"
        return 1
    fi
}

# Install plugin
install_plugin() {
    print_step "Installing free-agents plugin..."

    # Check if already installed
    if claude plugin list 2>/dev/null | grep -q "$PLUGIN_NAME"; then
        print_success "Plugin already installed"
        return 0
    fi

    if claude plugin install "$PLUGIN_NAME@$MARKETPLACE_NAME" --scope user 2>&1; then
        print_success "Plugin installed successfully"
        return 0
    else
        print_warning "Could not install plugin via CLI (may need interactive session)"
        print_info "You can install it manually in Claude Code with:"
        print_info "  /plugin install $PLUGIN_NAME@$MARKETPLACE_NAME"
        return 1
    fi
}

# Enable plugin
enable_plugin() {
    print_step "Enabling free-agents plugin..."

    if claude plugin enable "$PLUGIN_NAME" 2>&1; then
        print_success "Plugin enabled"
        return 0
    else
        print_warning "Could not enable plugin via CLI"
        print_info "You can enable it manually in Claude Code with:"
        print_info "  /plugin enable $PLUGIN_NAME"
        return 1
    fi
}

# Reload plugins
reload_plugins() {
    print_step "Reloading plugins..."
    if command -v claude >/dev/null 2>&1; then
        print_info "Run '/reload-plugins' (or '/rl') in your Claude Code session to activate the plugin"
        print_info "Or restart Claude Code"
    fi
}

# Configure local settings
configure_local() {
    print_step "Configuring local settings..."

    local plugin_dir="$HOME/.claude/plugins/$PLUGIN_NAME"
    local config_example="$plugin_dir/config.example.sh"
    local config_local="$plugin_dir/config/config.local.sh"

    if [[ ! -f "$config_example" ]]; then
        print_warning "Plugin not found at expected location: $plugin_dir"
        print_info "The plugin may be installed at a different scope."
        print_info "Run 'claude plugin details $PLUGIN_NAME' to find the install location."
        return 1
    fi

    if [[ -f "$config_local" ]]; then
        print_success "Local config already exists: $config_local"
        if confirm_skip "Edit it now?"; then
            ${EDITOR:-vim} "$config_local"
        fi
        return 0
    fi

    cp "$config_example" "$config_local"
    print_success "Created local config: $config_local"

    if confirm "Open config.local.sh in your editor to set model directory and registry?"; then
        ${EDITOR:-vim} "$config_local"
    else
        print_warning "Remember to edit $config_local before proceeding!"
        print_info "At minimum, set LA_MODEL_DIR to where you want models stored."
    fi
}

# Install backend
install_backend() {
    print_step "Installing local inference backend..."

    local install_script="$HOME/.claude/plugins/$PLUGIN_NAME/install/install-backend.sh"

    if [[ ! -f "$install_script" ]]; then
        print_error "Install script not found: $install_script"
        return 1
    fi

    print_info "This will install: $BACKEND_DETAILS"
    print_info "This may take several minutes..."

    local backend_args=()
    if [[ "$IS_CUDA" == "true" ]]; then
        print_info "CUDA GPU detected — will install CUDA backends (vllm-cuda, llama-cpp-cuda) in addition to MLX"
        backend_args=(--all)
    fi

    if confirm "Proceed with backend installation?"; then
        if "$install_script" "${backend_args[@]}"; then
            print_success "Backend installed successfully"
            return 0
        else
            print_error "Backend installation failed"
            return 1
        fi
    else
        print_warning "Skipped backend installation"
        print_info "Run manually later: $install_script ${backend_args[*]}"
        return 1
    fi
}

# Download models
download_models() {
    print_step "Downloading models..."

    local download_script="$HOME/.claude/plugins/$PLUGIN_NAME/install/download-models.sh"

    if [[ ! -f "$download_script" ]]; then
        print_error "Download script not found: $download_script"
        return 1
    fi

    if confirm "Launch interactive model downloader?"; then
        "$download_script"
        print_success "Model download complete"
        return 0
    else
        print_warning "Skipped model download"
        print_info "Run manually later: $download_script"
        return 1
    fi
}

# Setup shortcuts
setup_shortcuts() {
    print_step "Setting up shell shortcuts..."

    local shortcut_script="$HOME/.claude/plugins/$PLUGIN_NAME/install/setup-shortcuts.sh"

    if [[ ! -f "$shortcut_script" ]]; then
        print_error "Shortcut script not found: $shortcut_script"
        return 1
    fi

    # Detect shell for user info
    local shell_name="${SHELL##*/}"
    local rc_file="$HOME/.zshrc"
    [[ "$shell_name" == "bash" ]] && rc_file="$HOME/.bashrc"

    if confirm "Add shell aliases (csl, local-operator, lowkey, etc.) to your $shell_name config ($rc_file)?"; then
        "$shortcut_script"
        print_success "Shortcuts installed"
        print_info "Restart your shell or run: source $rc_file"
        return 0
    else
        print_warning "Skipped shortcut setup"
        return 1
    fi
}

# Main installation flow
main() {
    clear
    print_header

    echo -e "This installer will set up ${BOLD}free-agents${NC} — free local & remote inference for Claude Code."
    echo -e "It downloads the plugin from GitHub release v${RELEASE_VERSION:-latest} automatically."
    echo ""
    echo -e "It will install:"
    echo -e "  1. ${CYAN}Plugin${NC} — skills, hooks, session picker, dispatch tools"
    echo -e "  2. ${CYAN}Local backend${NC} — (detected in Phase 1)"
    echo -e "  3. ${CYAN}Models${NC} — your choice of local models to download"
    echo -e "  4. ${CYAN}Shortcuts${NC} — shell aliases for quick access"
    echo ""

    if ! confirm "Continue with installation?"; then
        echo "Installation cancelled."
        exit 0
    fi

    echo ""

    # Phase 0: Fetch version
    print_header
    echo -e "${BOLD}Phase 0: Fetch Release Info${NC}"
    echo ""

    fetch_latest_version

    # Phase 1: Prerequisites
    echo ""
    print_header
    echo -e "${BOLD}Phase 1: Prerequisites${NC}"
    echo ""

    detect_platform || exit 1
    check_claude_code || exit 1
    check_git || exit 1
    check_python || exit 1

    # Phase 2: Download plugin
    echo ""
    print_header
    echo -e "${BOLD}Phase 2: Download Plugin${NC}"
    echo ""

    download_plugin || exit 1

    # Phase 3: Plugin installation
    echo ""
    print_header
    echo -e "${BOLD}Phase 3: Plugin Installation${NC}"
    echo ""

    add_marketplace
    install_plugin
    enable_plugin
    reload_plugins

    # Phase 4: Local configuration
    echo ""
    print_header
    echo -e "${BOLD}Phase 4: Local Configuration${NC}"
    echo ""

    configure_local

    # Phase 5: Backend installation
    echo ""
    print_header
    echo -e "${BOLD}Phase 5: Local Inference Backend${NC}"
    echo ""

    install_backend

    # Phase 6: Model download
    echo ""
    print_header
    echo -e "${BOLD}Phase 6: Model Download${NC}"
    echo ""

    download_models

    # Phase 7: Shortcuts
    echo ""
    print_header
    echo -e "${BOLD}Phase 7: Shell Shortcuts${NC}"
    echo ""

    setup_shortcuts

    # Summary
    echo ""
    print_header
    echo -e "${BOLD}Installation Complete!${NC}"
    echo ""
    echo -e "${GREEN}free-agents is now ready to use.${NC}"
    echo ""
    echo -e "Quick start:"
    echo -e "  ${CYAN}csl${NC}                    # Open session picker (local + remote)"
    echo -e "  ${CYAN}local-operator${NC}         # Quick local session with operator model"
    echo -e "  ${CYAN}lowkey${NC}                 # Universal local model dispatcher"
    echo -e "  ${CYAN}local-roles${NC}            # Show which models fill which roles"
    echo ""
    echo -e "Documentation:"
    echo -e "  ${CYAN}~/.claude/plugins/free-agents/README.md${NC}"
    echo -e "  ${CYAN}https://github.com/haiggoh/free-agents${NC}"
    echo ""

    if [[ "$INTERACTIVE" == "true" ]]; then
        read -r -p "Press Enter to exit..."
    fi
}

# Run main
main "$@"
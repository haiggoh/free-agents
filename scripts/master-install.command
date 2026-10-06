#!/usr/bin/env bash
# free-agents Master Installer
# One-command seamless installation of free-agents plugin + local inference backend
# Can be run from terminal or double-clicked in Finder on macOS

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
BRANCH="main"
MARKETPLACE_NAME="haiggoh/get-haiggoh"
PLUGIN_NAME="free-agents"

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
        # Try to reload via the running session or just inform user
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
    
    print_info "This will install:"
    print_info "  - Python venv with Rapid-MLX"
    print_info "  - vllm-mlx with fork patches"
    print_info "  - This may take several minutes..."
    
    if confirm "Proceed with backend installation?"; then
        if "$install_script"; then
            print_success "Backend installed successfully"
            return 0
        else
            print_error "Backend installation failed"
            return 1
        fi
    else
        print_warning "Skipped backend installation"
        print_info "Run manually later: $install_script"
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
    
    if confirm "Add shell aliases (csl, local-operator, lowkey, etc.) to your shell config?"; then
        "$shortcut_script"
        print_success "Shortcuts installed"
        print_info "Restart your shell or run: source ~/.zshrc"
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
    echo ""
    echo -e "It will install:"
    echo -e "  1. ${CYAN}Plugin${NC} — skills, hooks, session picker, dispatch tools"
    echo -e "  2. ${CYAN}Local backend${NC} — Rapid-MLX + vllm-mlx (Apple Silicon)"
    echo -e "  3. ${CYAN}Models${NC} — your choice of local models to download"
    echo -e "  4. ${CYAN}Shortcuts${NC} — shell aliases for quick access"
    echo ""
    
    if ! confirm "Continue with installation?"; then
        echo "Installation cancelled."
        exit 0
    fi
    
    echo ""
    
    # Phase 1: Prerequisites
    print_header
    echo -e "${BOLD}Phase 1: Prerequisites${NC}"
    echo ""
    
    check_claude_code || exit 1
    check_git || exit 1
    
    # Phase 2: Plugin installation
    echo ""
    print_header
    echo -e "${BOLD}Phase 2: Plugin Installation${NC}"
    echo ""
    
    add_marketplace
    install_plugin
    enable_plugin
    reload_plugins
    
    # Phase 3: Local configuration
    echo ""
    print_header
    echo -e "${BOLD}Phase 3: Local Configuration${NC}"
    echo ""
    
    configure_local
    
    # Phase 4: Backend installation
    echo ""
    print_header
    echo -e "${BOLD}Phase 4: Local Inference Backend${NC}"
    echo ""
    
    install_backend
    
    # Phase 5: Model download
    echo ""
    print_header
    echo -e "${BOLD}Phase 5: Model Download${NC}"
    echo ""
    
    download_models
    
    # Phase 6: Shortcuts
    echo ""
    print_header
    echo -e "${BOLD}Phase 6: Shell Shortcuts${NC}"
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

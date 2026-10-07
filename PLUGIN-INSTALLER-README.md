# free-agents Plugin — Manual Installation from GitHub Release

This archive contains the free-agents plugin packaged for manual installation from a GitHub release.

## Quick Install (Standalone Installer — Recommended)

**No download needed — just run this one command:**

```bash
# Runs entirely from GitHub, downloads everything automatically
curl -fsSL https://github.com/haiggoh/free-agents/releases/latest/download/install-free-agents.command | bash
```

Or download the **`install-free-agents.command`** file from the [latest release](https://github.com/haiggoh/free-agents/releases/latest) and:
- **macOS**: Double-click in Finder
- **Linux/WSL2**: `bash install-free-agents.command`

> ✅ **Version-agnostic**: The installer fetches the latest plugin version from GitHub at runtime. You never need to re-download the installer when free-agents updates.

The master installer:
- Detects your platform (macOS Apple Silicon / Linux / WSL2)
- Fetches the latest plugin release from GitHub
- Installs & enables the plugin
- Installs the correct local inference backend (MLX for Mac, CUDA for NVIDIA GPU)
- Guides you through model download and shortcut setup

---

## Manual Install from This Archive

If you already downloaded this tarball:

```bash
# 1. Extract the archive
tar -xzf free-agents-0.25.7.tar.gz
cd free-agents

# 2. Run the installer (copies plugin to ~/.claude/plugins/free-agents/)
./install-plugin.sh

# 3. Enable the plugin in your settings
claude plugin enable free-agents

# 4. Reload plugins
/rl
# or restart Claude Code
```

### What the Installer Does

The `install-plugin.sh` script:
- Copies the plugin to `~/.claude/plugins/free-agents/`
- Makes all scripts executable
- Shows you the next steps

---

## After Plugin Installation

Once the plugin is enabled and loaded, continue with the **local inference backend setup**:

```bash
# 1. Configure your local settings
cp ~/.claude/plugins/free-agents/config.example.sh ~/.claude/plugins/free-agents/config/config.local.sh
$EDITOR ~/.claude/plugins/free-agents/config/config.local.sh

# 2. Install the local inference backend
# macOS (Apple Silicon):
~/.claude/plugins/free-agents/install/install-backend.sh
# Linux with NVIDIA GPU (includes CUDA backends):
~/.claude/plugins/free-agents/install/install-backend.sh --all
# Linux CPU only:
~/.claude/plugins/free-agents/install/install-backend.sh --backend llama-cpp

# 3. Download models (interactive selection)
~/.claude/plugins/free-agents/install/download-models.sh

# 4. Optional: Add shell shortcuts
~/.claude/plugins/free-agents/install/setup-shortcuts.sh
```

---

## Alternative: Standard Marketplace Installation

The standard (and recommended) way to install free-agents:

```bash
# 1. Add the haiggoh marketplace
/plugin marketplace add haiggoh/get-haiggoh

# 2. Install free-agents from the marketplace
/plugin install free-agents@haiggoh

# 3. Then run the backend setup as above
```

---

## Platform Support

| Platform | Local Backend | Status |
|----------|---------------|--------|
| macOS (Apple Silicon) | Rapid-MLX + vllm-mlx | ✅ Fully supported |
| Linux (NVIDIA GPU) | vLLM CUDA + llama.cpp CUDA (3 tiers) | ✅ Fully supported |
| Linux (WSL2, NVIDIA GPU) | vLLM CUDA + llama.cpp CUDA | ✅ Fully supported |
| Linux (CPU only) | llama.cpp CPU mode | ⚠️ Slow for large models |

---

## Support

- Issues: https://github.com/haiggoh/free-agents/issues
- Documentation: https://github.com/haiggoh/free-agents/blob/main/README.md
- Master installer source: https://github.com/haiggoh/free-agents/blob/main/scripts/master-install.command
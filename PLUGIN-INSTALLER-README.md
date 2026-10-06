# free-agents Plugin — Manual Installation from GitHub Release

This archive contains the free-agents plugin packaged for manual installation from a GitHub release.

## Quick Install

```bash
# 1. Extract the archive
tar -xzf free-agents-0.25.7.tar.gz
cd free-agents

# 2. Run the installer (copies plugin to ~/.claude/plugins/free-agents/)
./install-plugin.sh

# 3. Enable the plugin in your settings
# Add "free-agents" to enabledPlugins in ~/.claude/settings.json
# (The installer will show you the exact command)
claude plugin enable free-agents

# 4. Reload plugins
/rl
# or restart Claude Code
```

## What the Installer Does

The `install-plugin.sh` script:
- Copies the plugin to `~/.claude/plugins/free-agents/`
- Makes all scripts executable
- Shows you the next steps

## After Plugin Installation

Once the plugin is enabled and loaded, continue with the **local inference backend setup**:

```bash
# 1. Configure your local settings
cp ~/.claude/plugins/free-agents/config.example.sh ~/.claude/plugins/free-agents/config/config.local.sh
$EDITOR ~/.claude/plugins/free-agents/config/config.local.sh

# 2. Install the local inference backend (venv + vllm-mlx + patches)
~/.claude/plugins/free-agents/install/install-backend.sh

# 3. Download models (interactive selection)
~/.claude/plugins/free-agents/install/download-models.sh

# 4. Optional: Add shell shortcuts
~/.claude/plugins/free-agents/install/setup-shortcuts.sh
```

## Recommended: Use the Master Installer

For a seamless one-command setup, use the master installer instead:

```bash
# Download and run the master installer
curl -fsSL https://raw.githubusercontent.com/haiggoh/free-agents/main/scripts/master-install.command | bash
```

The master installer handles everything: plugin installation, enabling, backend setup, model download, and shortcuts — all in one linear flow.

## Alternative: Standard Marketplace Installation

The standard (and recommended) way to install free-agents:

```bash
# 1. Add the haiggoh marketplace
/plugin marketplace add haiggoh/get-haiggoh

# 2. Install free-agents from the marketplace
/plugin install free-agents@haiggoh

# 3. Then run the backend setup as above
```

## Support

- Issues: https://github.com/haiggoh/free-agents/issues
- Documentation: https://github.com/haiggoh/free-agents/blob/main/README.md

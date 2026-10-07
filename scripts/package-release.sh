#!/usr/bin/env bash
# Package free-agents plugin for GitHub release distribution
# Creates a tarball that users can extract into ~/.claude/plugins/free-agents/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION_FILE="$REPO_ROOT/VERSION"

if [[ ! -f "$VERSION_FILE" ]]; then
    echo "ERROR: VERSION file not found at $VERSION_FILE"
    exit 1
fi

VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')
echo "Packaging free-agents v$VERSION for release..."

# Output directory
DIST_DIR="$REPO_ROOT/dist"
PLUGIN_DIR="$DIST_DIR/free-agents"
rm -rf "$DIST_DIR"
mkdir -p "$PLUGIN_DIR"

# Copy plugin manifest
mkdir -p "$PLUGIN_DIR/.claude-plugin"
cp "$REPO_ROOT/.claude-plugin/plugin.json" "$PLUGIN_DIR/.claude-plugin/"

# Copy core plugin directories
for dir in bin config install skills hooks; do
    if [[ -d "$REPO_ROOT/$dir" ]]; then
        echo "  Copying $dir/..."
        cp -r "$REPO_ROOT/$dir" "$PLUGIN_DIR/"
    fi
done

# Copy essential root files
for file in README.md CHANGELOG.md VERSION config.example.sh config.local.sh.example PLUGIN-INSTALLER-README.md RELEASE.md; do
    if [[ -f "$REPO_ROOT/$file" ]]; then
        cp "$REPO_ROOT/$file" "$PLUGIN_DIR/"
    fi
done

# Copy master installer (renamed to install-free-agents.command)
if [[ -f "$REPO_ROOT/scripts/install-free-agents.command" ]]; then
    cp "$REPO_ROOT/scripts/install-free-agents.command" "$PLUGIN_DIR/"
fi

# Create config.local.sh.example from config.example.sh if it doesn't exist
if [[ ! -f "$REPO_ROOT/config.local.sh.example" && -f "$REPO_ROOT/config.example.sh" ]]; then
    cp "$REPO_ROOT/config.example.sh" "$PLUGIN_DIR/config.local.sh.example"
fi

# Remove test files and development artifacts from the package
echo "  Cleaning up development artifacts..."
find "$PLUGIN_DIR" -name "*.pyc" -delete
find "$PLUGIN_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$PLUGIN_DIR" -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
find "$PLUGIN_DIR" -name "test_*.py" -delete 2>/dev/null || true
find "$PLUGIN_DIR" -name "test_*.sh" -delete 2>/dev/null || true
find "$PLUGIN_DIR" -path "*/tests/*" -prune -exec rm -rf {} + 2>/dev/null || true
find "$PLUGIN_DIR" -name "*.bak*" -delete 2>/dev/null || true
find "$PLUGIN_DIR" -name "*.orig" -delete 2>/dev/null || true
# Remove .DS_Store files (macOS filesystem artifacts) - do this after all copying is done
find "$PLUGIN_DIR" -name ".DS_Store" -delete 2>/dev/null || true

# Remove backup directories
rm -rf "$PLUGIN_DIR/backups" 2>/dev/null || true
rm -rf "$PLUGIN_DIR/.superpowers" 2>/dev/null || true
rm -rf "$PLUGIN_DIR/.remember" 2>/dev/null || true
rm -rf "$PLUGIN_DIR/.pytest_cache" 2>/dev/null || true
rm -rf "$PLUGIN_DIR/bin/.pytest_cache" 2>/dev/null || true

# Remove local-only config files
rm -f "$PLUGIN_DIR/config/config.local.sh" 2>/dev/null || true
rm -f "$PLUGIN_DIR/config/model-catalog.local.psv" 2>/dev/null || true
rm -f "$PLUGIN_DIR/config/session-menu.local.json" 2>/dev/null || true
rm -f "$PLUGIN_DIR/config/local-capable-remote-models.psv" 2>/dev/null || true
rm -f "$PLUGIN_DIR/config/model-catalog.acquisitions.*.psv" 2>/dev/null || true
rm -f "$PLUGIN_DIR/config/model-catalog.qwen38.psv" 2>/dev/null || true
rm -f "$PLUGIN_DIR/config/model-catalog.rapid-next.psv" 2>/dev/null || true
rm -f "$PLUGIN_DIR/config/nvidia-model-test-results.json" 2>/dev/null || true
rm -f "$PLUGIN_DIR/config/remote-providers.example.json" 2>/dev/null || true
rm -f "$PLUGIN_DIR/local-profile-notes.md" 2>/dev/null || true
rm -f "$PLUGIN_DIR/local-profile.json" 2>/dev/null || true
rm -f "$PLUGIN_DIR/queue_marker.py" 2>/dev/null || true  # duplicate in bin/
rm -f "$PLUGIN_DIR/.launchd-csl-salvage.patch" 2>/dev/null || true
rm -f "$PLUGIN_DIR/.claude/settings.local.json" 2>/dev/null || true

# Remove Copilot BYOK experimental files
rm -f "$PLUGIN_DIR/bin/copilot_local_proxy.py" 2>/dev/null || true
rm -f "$PLUGIN_DIR/bin/launch-copilot-agent.sh" 2>/dev/null || true
rm -f "$PLUGIN_DIR/bin/test-copilot-byok-integration.sh" 2>/dev/null || true
rm -rf "$PLUGIN_DIR/docs/local-copilot-byok" 2>/dev/null || true

# Remove docs that are not needed for runtime
rm -rf "$PLUGIN_DIR/docs/superpowers" 2>/dev/null || true
rm -rf "$PLUGIN_DIR/docs/planning" 2>/dev/null || true
rm -f "$PLUGIN_DIR/docs/releases/TAG-REPAIR-2026-09-16.md" 2>/dev/null || true

# Remove install scripts that are development-only
rm -f "$PLUGIN_DIR/install/local-stack-update-check.sh" 2>/dev/null || true
rm -f "$PLUGIN_DIR/install/session-picker-requirements.txt" 2>/dev/null || true

# Create a simple installer script for the release package
cat > "$PLUGIN_DIR/install-plugin.sh" << 'INSTALL_EOF'
#!/usr/bin/env bash
# free-agents plugin installer for GitHub release packages
# Usage: ./install-plugin.sh [--dry-run] [--target-dir DIR]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_NAME="free-agents"
DEFAULT_TARGET="$HOME/.claude/plugins/$PLUGIN_NAME"

DRY_RUN=false
TARGET_DIR="$DEFAULT_TARGET"

while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --target-dir)
            TARGET_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--dry-run] [--target-dir DIR]"
            echo "  --dry-run      Show what would be done without making changes"
            echo "  --target-dir   Target installation directory (default: ~/.claude/plugins/free-agents)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "Installing free-agents plugin to $TARGET_DIR"

if [[ "$DRY_RUN" == "true" ]]; then
    echo "DRY RUN - would copy:"
    echo "  $SCRIPT_DIR/.claude-plugin/ -> $TARGET_DIR/.claude-plugin/"
    echo "  $SCRIPT_DIR/bin/ -> $TARGET_DIR/bin/"
    echo "  $SCRIPT_DIR/config/ -> $TARGET_DIR/config/"
    echo "  $SCRIPT_DIR/install/ -> $TARGET_DIR/install/"
    echo "  $SCRIPT_DIR/skills/ -> $TARGET_DIR/skills/"
    echo "  $SCRIPT_DIR/hooks/ -> $TARGET_DIR/hooks/"
    echo "  $SCRIPT_DIR/README.md -> $TARGET_DIR/README.md"
    echo "  $SCRIPT_DIR/CHANGELOG.md -> $TARGET_DIR/CHANGELOG.md"
    echo "  $SCRIPT_DIR/VERSION -> $TARGET_DIR/VERSION"
    echo "  $SCRIPT_DIR/config.example.sh -> $TARGET_DIR/config.example.sh"
    echo "  $SCRIPT_DIR/config.local.sh.example -> $TARGET_DIR/config.local.sh.example"
    exit 0
fi

# Create target directory
mkdir -p "$TARGET_DIR"

# Copy plugin files
cp -r "$SCRIPT_DIR/.claude-plugin" "$TARGET_DIR/"
cp -r "$SCRIPT_DIR/bin" "$TARGET_DIR/"
cp -r "$SCRIPT_DIR/config" "$TARGET_DIR/"
cp -r "$SCRIPT_DIR/install" "$TARGET_DIR/"
cp -r "$SCRIPT_DIR/skills" "$TARGET_DIR/"
cp -r "$SCRIPT_DIR/hooks" "$TARGET_DIR/"
cp "$SCRIPT_DIR/README.md" "$TARGET_DIR/"
cp "$SCRIPT_DIR/CHANGELOG.md" "$TARGET_DIR/"
cp "$SCRIPT_DIR/VERSION" "$TARGET_DIR/"
cp "$SCRIPT_DIR/config.example.sh" "$TARGET_DIR/"
cp "$SCRIPT_DIR/config.local.sh.example" "$TARGET_DIR/"

# Make scripts executable
find "$TARGET_DIR/bin" -name "*.sh" -exec chmod +x {} \;
find "$TARGET_DIR/bin" -name "*.py" -exec chmod +x {} \;
find "$TARGET_DIR/install" -name "*.sh" -exec chmod +x {} \;
find "$TARGET_DIR/install" -name "*.py" -exec chmod +x {} \;
chmod +x "$TARGET_DIR/install-plugin.sh"

echo "Plugin installed to $TARGET_DIR"
echo ""
echo "Next steps:"
echo "  1. Configure your local settings:"
echo "     cp $TARGET_DIR/config.example.sh $TARGET_DIR/config/config.local.sh"
echo "     \$EDITOR $TARGET_DIR/config/config.local.sh"
echo "  2. Install the local inference backend:"
echo "     $TARGET_DIR/install/install-backend.sh"
echo "  3. Download models:"
echo "     $TARGET_DIR/install/download-models.sh"
echo "  4. Add shell shortcuts (optional):"
echo "     $TARGET_DIR/install/setup-shortcuts.sh"
echo ""
echo "Or use the session picker:"
echo "     $TARGET_DIR/bin/csl"
INSTALL_EOF

chmod +x "$PLUGIN_DIR/install-plugin.sh"

# Create tarball
cd "$DIST_DIR"
TARBALL="free-agents-${VERSION}.tar.gz"
tar -czf "$TARBALL" free-agents/
echo ""
echo "Created release package: $DIST_DIR/$TARBALL"
echo "Size: $(du -h "$TARBALL" | cut -f1)"
echo ""
echo "Contents:"
tar -tzf "$TARBALL" | head -40
echo "..."
echo "Total files: $(tar -tzf "$TARBALL" | wc -l)"
# Release Process for free-agents

This document captures the complete release process so future releases can be done consistently without manual re-derivation.

---

## Overview

Each release produces **two distribution assets** on GitHub:

| Asset | Source | Purpose |
|-------|--------|---------|
| `free-agents-X.Y.Z.tar.gz` | `scripts/package-release.sh` | Full plugin source + installers for manual installation |
| `install-free-agents.command` | `scripts/install-free-agents.command` | Standalone 19 KB installer (version-agnostic) |

---

## Prerequisites

- `gh` CLI authenticated (`gh auth login`)
- On `main` branch with clean working tree
- `VERSION` file updated to new version (e.g., `0.25.8`)
- `CHANGELOG.md` updated with release notes
- `.claude-plugin/plugin.json` version matches `VERSION`
- All tests pass: `~/.local/bin/pytest -q tests`

---

## Step-by-Step Release Process

### 1. Update Version Files

```bash
# Update VERSION file
echo "0.25.8" > VERSION

# Update plugin.json version (must match)
# Edit .claude-plugin/plugin.json "version": "0.25.8"

# Update CHANGELOG.md with new version section at top
# Follow existing format: ## [X.Y.Z] — YYYY-MM-DD
```

### 2. Commit Version Bump

```bash
git add VERSION .claude-plugin/plugin.json CHANGELOG.md
git commit -m "chore: release 0.25.8 — <one-line summary>"
```

### 3. Build Release Package

```bash
# This creates dist/free-agents-0.25.8.tar.gz with:
# - Full plugin source (bin, config, install, skills, hooks)
# - README.md, CHANGELOG.md, VERSION, PLUGIN-INSTALLER-README.md
# - install-free-agents.command (copied from scripts/)
# - install-plugin.sh (for manual tarball installation)
./scripts/package-release.sh
```

**What `package-release.sh` does:**
- Copies plugin directories (bin, config, install, skills, hooks)
- Copies root files (README, CHANGELOG, VERSION, config.example.sh, PLUGIN-INSTALLER-README.md)
- Copies `scripts/install-free-agents.command` → `install-free-agents.command` in tarball
- Strips test files, dev artifacts, local configs, .DS_Store
- Creates `install-plugin.sh` for manual installation

### 4. Create Git Tag (Annotated)

```bash
# Create annotated tag with release notes from CHANGELOG
git tag -a v0.25.8 -m "$(sed -n '/^## \[0.25.8\]/,/^## \[/p' CHANGELOG.md | head -n -1)"
# Or manually: git tag -a v0.25.8 -m "free-agents 0.25.8

# ...release notes from CHANGELOG..."
```

### 5. Push Commit + Tag

```bash
git push origin main
git push origin v0.25.8
```

### 6. Create GitHub Release + Upload Assets

```bash
# Create release (uses tag, generates release notes from commits)
gh release create v0.25.8 \
  --title "free-agents 0.25.8 — <one-line summary>" \
  --notes-file <(sed -n '/^## \[0.25.8\]/,/^## \[/p' CHANGELOG.md | head -n -1)

# Upload both assets
gh release upload v0.25.8 dist/free-agents-0.25.8.tar.gz --clobber
gh release upload v0.25.8 dist/install-free-agents.command --clobber
```

> **Note**: The `install-free-agents.command` in `dist/` is just a copy of `scripts/install-free-agents.command`. The script is version-agnostic (fetches latest release at runtime), so the same file works for all future releases.

### 7. Verify Release

```bash
# Check assets
gh release view v0.25.8 --json assets

# Test installer download
curl -fsSL https://github.com/haiggoh/free-agents/releases/latest/download/install-free-agents.command | head -5

# Test tarball download
curl -sL https://github.com/haiggoh/free-agents/releases/download/v0.25.8/free-agents-0.25.8.tar.gz | tar -tz | head -5
```

---

## Release Checklist

- [ ] `VERSION` file updated
- [ ] `.claude-plugin/plugin.json` version matches
- [ ] `CHANGELOG.md` has new version section at top
- [ ] All tests pass (`~/.local/bin/pytest -q tests`)
- [ ] Version bump committed
- [ ] Annotated tag created with release notes
- [ ] Commit + tag pushed to origin
- [ ] GitHub release created
- [ ] `free-agents-X.Y.Z.tar.gz` uploaded
- [ ] `install-free-agents.command` uploaded
- [ ] Release verified (assets present, downloads work)

---

## Files Involved

| File | Purpose | Updated Per Release? |
|------|---------|---------------------|
| `VERSION` | Version number | ✅ Yes |
| `.claude-plugin/plugin.json` | Plugin manifest version | ✅ Yes |
| `CHANGELOG.md` | Release notes | ✅ Yes |
| `scripts/package-release.sh` | Builds tarball | ❌ No (stable) |
| `scripts/install-free-agents.command` | Standalone installer | ❌ No (version-agnostic) |
| `PLUGIN-INSTALLER-README.md` | Manual install guide | ❌ Rarely |
| `README.md` | Installation docs | ❌ Rarely |

---

## Version-Agnostic Installer Design

The `install-free-agents.command` script is **intentionally version-agnostic**:

```bash
# It queries GitHub API at runtime:
RELEASE_API="https://api.github.com/repos/haiggoh/free-agents/releases/latest"
RELEASE_VERSION=$(curl -fsSL "$RELEASE_API" | jq -r '.tag_name' | sed 's/^v//')

# Then downloads:
https://github.com/haiggoh/free-agents/releases/download/v${RELEASE_VERSION}/free-agents-${RELEASE_VERSION}.tar.gz
```

**This means:**
- The same `install-free-agents.command` file works for ALL future releases
- Users never need to re-download the installer
- Just re-upload the same file to each new release (or don't — it's also available at `scripts/install-free-agents.command` in the repo)

---

## Automation Notes

### Current State: Manual but Documented
This process is currently manual but fully documented above. Future automation could include:
- A `scripts/release.sh` that orchestrates steps 1-6
- GitHub Actions workflow for automated releases on tag push
- Automatic CHANGELOG generation from conventional commits

### For Now
Follow the checklist above. The key insight is that **only 3 files change per release** (VERSION, plugin.json, CHANGELOG), and the packaging/upload steps are scripted.

---

## Troubleshooting

**"Package script fails"**
- Ensure you're in repo root: `cd /path/to/free-agents`
- Check `VERSION` file exists and has clean version string

**"Tag already exists"**
- Delete local: `git tag -d v0.25.8`
- Delete remote: `git push origin :refs/tags/v0.25.8`

**"Release asset upload fails"**
- Check `gh auth status`
- Ensure `dist/` files exist: `ls -la dist/`

**"Installer doesn't fetch latest"**
- Test API: `curl -fsSL https://api.github.com/repos/haiggoh/free-agents/releases/latest | jq .tag_name`
- The installer has git clone fallback if API fails

---

## Related Documentation

- `README.md` — User-facing installation instructions (3 options)
- `PLUGIN-INSTALLER-README.md` — Manual tarball installation guide
- `scripts/package-release.sh` — Packaging script
- `scripts/install-free-agents.command` — Standalone installer source
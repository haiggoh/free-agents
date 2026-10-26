#!/usr/bin/env bash
# .rtc/pre-package.sh — build free-agents' release assets.
#
# Extracted from the old ~/.claude/scripts/fa-ship.sh, which packaged the tarball and then copied
# the installer into dist/ under BOTH names. rtc-ship.sh calls this before it resolves the asset
# globs from rtc-ship.json, so everything a release needs must exist by the time this exits.
#
# ONE installer: install-free-agents.sh, which has superseded the .command name since 0.29.3. The
# dual upload was dropped on 2026-10-26 (user decision): the .command file was the same bytes under
# a legacy name, offered no benefit, and the deep links it existed for were judged not to be in use.
# A stale .../releases/latest/download/install-free-agents.command link will 404 from this release
# onward — that consequence was accepted deliberately, not overlooked.
#
# Usage: ./.rtc/pre-package.sh   (run by rtc-ship.sh from the repo root)
set -euo pipefail

./scripts/package-release.sh

[ -f scripts/install-free-agents.sh ] || {
  echo "✖ pre-package: no installer at scripts/install-free-agents.sh" >&2; exit 1; }
cp -p scripts/install-free-agents.sh dist/

# Remove a .command left in dist/ by an older package-release.sh: package-release.sh wipes dist/,
# but if it ever stops doing so, a stale legacy asset would be globbed and shipped again.
rm -f dist/install-free-agents.command

# Verify what rtc-ship.sh is about to glob. The tool now FAILS on a glob matching nothing (0.6.6),
# but a failure at glob time names a pattern; this one names the file that was never built, which is
# the actionable message. An EMPTY extraction is a failure, never a pass.
V=$(tr -d '[:space:]' < VERSION)
need=("dist/free-agents-$V.tar.gz" dist/install-free-agents.sh)
for f in "${need[@]}"; do
  [ -f "$f" ] || { echo "✖ pre-package: $f was not built (VERSION says $V)" >&2; exit 1; }
done
echo "packaged $V: ${#need[@]} assets"

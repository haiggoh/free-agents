#!/usr/bin/env bash
# .rtc/pre-package.sh — build free-agents' release assets.
#
# Extracted from the old ~/.claude/scripts/fa-ship.sh, which packaged the tarball and then copied
# the installer into dist/ under BOTH names. rtc-ship.sh calls this before it resolves the asset
# globs from rtc-ship.json, so everything a release needs must exist by the time this exits.
#
# The installer is install-free-agents.sh since 0.29.3. The same file is also uploaded under the
# old .command name, so .../releases/latest/download/install-free-agents.command links keep working
# — that is why one file ships under two names, and why dropping either breaks an existing install
# route rather than merely omitting a new one.
#
# Usage: ./.rtc/pre-package.sh   (run by rtc-ship.sh from the repo root, stdout discarded)
set -euo pipefail

./scripts/package-release.sh >/dev/null

if [ -f scripts/install-free-agents.sh ]; then
  cp -p scripts/install-free-agents.sh dist/
  cp -p scripts/install-free-agents.sh dist/install-free-agents.command
else
  # The .command name predates 0.29.3. If NEITHER exists, say so: dying inside `cp` reports a
  # missing file without saying it is the installer, which reads like a packaging bug.
  [ -f scripts/install-free-agents.command ] || {
    echo "✖ pre-package: no installer at scripts/install-free-agents.{sh,command}" >&2; exit 1; }
  cp scripts/install-free-agents.command dist/
fi

# Verify what rtc-ship.sh is about to glob. The tool now FAILS on a glob matching nothing (0.6.6),
# but a failure at glob time names a pattern; this one names the file that was never built, which is
# the actionable message. An EMPTY extraction is a failure, never a pass.
V=$(tr -d '[:space:]' < VERSION)
need=("dist/free-agents-$V.tar.gz" dist/install-free-agents.sh dist/install-free-agents.command)
for f in "${need[@]}"; do
  [ -f "$f" ] || { echo "✖ pre-package: $f was not built (VERSION says $V)" >&2; exit 1; }
done
echo "packaged $V: ${#need[@]} assets"

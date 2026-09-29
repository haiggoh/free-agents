#!/usr/bin/env bash
# backfill-model-manifests.sh — thin wrapper for portable manifest backfill
# Calls install/local-model-manifest.py with common options

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LA_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFEST_TOOL="$LA_ROOT/install/local-model-manifest.py"

usage() {
  cat <<EOF
Usage: backfill-model-manifests.sh [OPTIONS]

Thin wrapper for install/local-model-manifest.py backfill command.

Options:
  --select <folder>     Backfill a single model directory
  --all                 Backfill all installed models
  --force               Overwrite existing valid manifests
  --dry-run             Show what would be done without writing
  --help                Show this help and exit

Examples:
  backfill-model-manifests.sh --select Qwen3.8-27B-4bit
  backfill-model-manifests.sh --all --dry-run
  backfill-model-manifests.sh --all --force
EOF
}

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

# Pass through to the Python tool
exec python3 "$MANIFEST_TOOL" backfill "$@"
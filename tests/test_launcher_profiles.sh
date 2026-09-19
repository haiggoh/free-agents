#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LAUNCHER="$ROOT/bin/launch-claude-agent.sh"
CONFIG="$ROOT/config/config.example.sh"
PROMPT="$ROOT/config/local-agent-system-prompt.txt"

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

bash -n "$LAUNCHER"
bash -n "$CONFIG"

for expected in \
    LA_AGENT_PROMPT_FILE \
    LA_CLAUDE_SETTINGS \
    LA_CLAUDE_TOOLS \
    LA_AUTO_COMPACT_WINDOW
do
    grep -q "$expected" "$LAUNCHER" ||
        fail "$expected missing from launcher"
    grep -q "$expected" "$CONFIG" ||
        fail "$expected missing from config.example.sh"
done

grep -Fq '"${CLAUDE_EXTRA_ARGS[@]}"' "$LAUNCHER" ||
    fail "final invocation does not preserve argument boundaries"

grep -Fq -- '--settings "$LA_CLAUDE_SETTINGS"' "$LAUNCHER" ||
    fail "settings argument is not quoted"

grep -Fq -- '--tools "$LA_CLAUDE_TOOLS"' "$LAUNCHER" ||
    fail "tools argument is not quoted"

grep -Fq -- '--autocompact "$LA_AUTO_COMPACT_WINDOW"' "$LAUNCHER" ||
    fail "autocompact argument is not quoted"

grep -Fq 'LA_CLAUDE_TOOLS and LA_DENY_TOOLS' "$LAUNCHER" ||
    fail "allowed/denied tool conflict check missing"

python3 - "$PROMPT" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

expected = {
    "__LA_MODEL_ALIAS__",
    "__LA_MODEL_SPOOF__",
    "__LA_BACKEND__",
    "__LA_CURRENT_PORT__",
    "__LA_PORT_START__",
    "__LA_PORT_MAX__",
    "__LA_HOTSWAP_PATH__",
}

found = set(re.findall(r"__LA_[A-Z0-9_]+__", text))
if found != expected:
    raise SystemExit(
        f"prompt placeholders differ: expected={sorted(expected)} found={sorted(found)}"
    )

for placeholder in expected:
    if text.count(placeholder) != 1:
        raise SystemExit(
            f"placeholder must occur exactly once: {placeholder}"
        )

size = len(text.encode("utf-8"))
if size > 2048:
    raise SystemExit(f"prompt unexpectedly large: {size} bytes")

required_phrases = (
    "LOCAL CLAUDE CODE SESSION",
    "zero gateway cost",
    "Protect the runtime",
    "stop and ask",
)

for phrase in required_phrases:
    if phrase not in text:
        raise SystemExit(f"required prompt phrase missing: {phrase}")

print(f"prompt template: PASS ({size} bytes)")
PY

if grep -Fq 'You are an autonomous AI agent operating directly in a CLI' "$LAUNCHER"; then
    fail "old embedded prompt still present"
fi

printf 'launcher profile controls: PASS\n'

# --- live catalog scan ---------------------------------------------------------
# Verify that the model catalog (model-catalog.psv) can be loaded and parsed.
# This is a "live" scan because it reads the actual shipped catalog file.
CATALOG="$ROOT/config/model-catalog.psv"
if [ ! -r "$CATALOG" ]; then
    fail "model catalog not readable: $CATALOG"
fi

python3 - "$CATALOG" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()

entries = []
for i, line in enumerate(lines, 1):
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    fields = line.split('|')
    if len(fields) != 10:
        raise SystemExit(f"line {i}: expected 10 '|'-separated fields, got {len(fields)}: {line}")
    alias, label, repo, rev, subdir, size_gb, group, status, include, runtime = fields
    if not alias or not repo or not subdir:
        raise SystemExit(f"line {i}: alias, repo, and subdir are required: {line}")
    try:
        float(size_gb)
    except ValueError:
        raise SystemExit(f"line {i}: size_gb must be numeric: {size_gb}")
    entries.append(alias)

if not entries:
    raise SystemExit("catalog has no valid entries")

print(f"model catalog: PASS ({len(entries)} entries: {', '.join(entries)})")
PY

# Verify that every catalog entry has a corresponding HF repo that can be resolved
# (this is a lightweight check - it doesn't download, just validates the format).
# The registry in config.example.sh is separate; the catalog is the download list.
# We just ensure the catalog itself is well-formed.

# Optional: cross-check that catalog aliases don't conflict with registered aliases
# (they're in different namespaces but a collision would be confusing).
python3 - "$CATALOG" "$CONFIG" <<'PY'
import re
import sys
from pathlib import Path

cat_path = Path(sys.argv[1])
cfg_path = Path(sys.argv[2])

# Parse catalog aliases
cat_aliases = set()
for line in cat_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    fields = line.split('|')
    if len(fields) == 10:
        cat_aliases.add(fields[0])

# Parse registry aliases from config.example.sh
reg_aliases = set()
for line in cfg_path.read_text(encoding="utf-8").splitlines():
    m = re.match(r'la_register\s+(\S+)', line)
    if m:
        reg_aliases.add(m.group(1))

# Check for collisions
collision = cat_aliases & reg_aliases
if collision:
    # Not a hard failure - just warn. They're different namespaces (download vs launch).
    print(f"⚠️  catalog/registry alias collision (different namespaces): {sorted(collision)}", file=sys.stderr)

print("catalog/registry namespace check: PASS")
PY

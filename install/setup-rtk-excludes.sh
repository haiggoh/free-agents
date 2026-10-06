#!/usr/bin/env bash
# setup-rtk-excludes.sh — OPTIONAL, idempotent. Keep rtk from rewriting the commands whose
# condensed output dropped content (config/rtk/exclude-commands.txt), in EVERY session.
#
# rtk (rtk-ai.app) is a separate tool some users run as a Claude Code PreToolUse hook. This
# script only edits rtk's own config.toml, adding the listed patterns to [hooks]
# exclude_commands. It keeps every pattern already there, backs the file up first, and keeps
# its permissions. It does nothing (exit 0) when rtk is not installed.
#
# Usage: install/setup-rtk-excludes.sh [--dry-run | --check | --help]
#   --dry-run   print the resulting config.toml, write nothing
#   --check     exit 0 if every pattern is already excluded, 1 if any is missing
#
# Environment:
#   LA_RTK_CONFIG   rtk config.toml to edit (default: rtk's platform location)
#   LA_RTK_BIN      rtk executable to look for (default: rtk on PATH)
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIST="$HERE/../config/rtk/exclude-commands.txt"
MODE="write"
case "${1:-}" in
    "") ;;
    --dry-run) MODE=dry ;;
    --check) MODE=check ;;
    -h|--help) sed -n '2,/^set -uo pipefail/{ /^set -uo pipefail/d; s/^# \{0,1\}//; p; }' "$0"; exit 0 ;;
    *) echo "setup-rtk-excludes: unknown option '$1' (see --help)" >&2; exit 2 ;;
esac

RTK="${LA_RTK_BIN:-$(command -v rtk 2>/dev/null || true)}"
if [ -z "$RTK" ] || [ ! -x "$RTK" ]; then
    echo "rtk not installed — nothing to configure."
    exit 0
fi

if [ -n "${LA_RTK_CONFIG:-}" ]; then
    CONF="$LA_RTK_CONFIG"
elif [ "$(uname -s)" = Darwin ]; then
    CONF="$HOME/Library/Application Support/rtk/config.toml"
else
    CONF="${XDG_CONFIG_HOME:-$HOME/.config}/rtk/config.toml"
fi

exec python3 - "$CONF" "$LIST" "$MODE" <<'PY'
import os, re, shutil, sys, time

conf, listfile, mode = sys.argv[1:4]
want = [l.strip() for l in open(listfile) if l.strip() and not l.lstrip().startswith("#")]
text = open(conf).read() if os.path.exists(conf) else ""

# Locate the [hooks] table and an exclude_commands = [...] array inside it (may span lines).
hooks = re.search(r"(?m)^\[hooks\][ \t]*$", text)
end = len(text)
if hooks:
    nxt = re.search(r"(?m)^\[", text[hooks.end():])
    end = hooks.end() + nxt.start() if nxt else len(text)
body = text[hooks.end():end] if hooks else ""
arr = re.search(r"(?ms)^[ \t]*exclude_commands[ \t]*=[ \t]*\[(.*?)\]", body)
have = re.findall(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'', arr.group(1)) if arr else []
have = [a or b for a, b in have]
missing = [w for w in want if w not in have]

if mode == "check":
    print("rtk excludes: all present" if not missing else "rtk excludes missing: " + ", ".join(missing))
    sys.exit(1 if missing else 0)
if not missing:
    print(f"rtk excludes already present in {conf}")
    sys.exit(0)

merged = have + missing
line = "exclude_commands = [" + ", ".join('"%s"' % m.replace('"', '\\"') for m in merged) + "]"
if arr:
    s, e = hooks.end() + arr.start(), hooks.end() + arr.end()
    new = text[:s] + line + text[e:]
elif hooks:
    new = text[:hooks.end()] + "\n" + line + text[hooks.end():]
else:
    new = text + ("" if text.endswith("\n") or not text else "\n") + ("\n" if text else "") + "[hooks]\n" + line + "\n"

if mode == "dry":
    sys.stdout.write(new)
    sys.exit(0)

os.makedirs(os.path.dirname(conf), exist_ok=True)
if os.path.exists(conf):
    bak = f"{conf}.bak-{time.strftime('%Y%m%dT%H%M%S')}"
    shutil.copy2(conf, bak)          # copy2 keeps mode
    print(f"backup: {bak}")
    st = os.stat(conf)
    with open(conf, "w") as f:       # in place: same inode, same mode
        f.write(new)
    os.chmod(conf, st.st_mode)
else:
    with open(conf, "w") as f:
        f.write(new)
print(f"rtk excludes added to {conf}: " + ", ".join(missing))
PY

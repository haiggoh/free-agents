#!/usr/bin/env bash
# tests/test_backend_check_launcher.sh — the weekly launchd job is PORTABLE and actually works.
#
# Usage: bash tests/test_backend_check_launcher.sh
#
# Task 11: `manage-backend.py launchd install` writes a STABLE launcher into the live-config
# tree (never the installing copy) and points the plist at it. At RUN time the launcher puts
# Homebrew on PATH (launchd's PATH lacks it — the old job failed nightly with
# `brew: command not found`), resolves the free-agents root (FA_ROOT -> csl symlink -> newest
# plugin-cache version by `sort -V`), and runs the update check + post-upgrade check.
# Sandbox only: temp HOME, fake brew, fake plugin cache, FA_LAUNCHD_NO_LOAD=1 (no launchctl).
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$2"
          else FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$2"; fi; }
has() { printf '%s' "$2" | grep -qF -- "$1"; check $? "$3"; }
expect_rc() { local want="$1" got="$2"
    if [ "$want" -eq "$got" ]; then check 0 "$3 (got $got)"; else check 1 "$3 (got $got, want $want)"; fi; }

SB="$(mktemp -d "${TMPDIR:-/tmp}/fa-launchd-test.XXXXXX")"
SB="$(cd "$SB" && pwd)"   # canonicalize: TMPDIR ends in /, mktemp would leave a // that Python normalizes
trap 'rm -rf "$SB"' EXIT
H="$SB/home"; LIVE="$SB/live"
mkdir -p "$H" "$LIVE" "$SB/fakebin"

# --- fake brew: `brew shellenv` prints a PATH export ---
cat > "$SB/fakebin/brew" <<'STUB'
#!/usr/bin/env bash
if [ "${1:-}" = "shellenv" ]; then printf 'PATH="%s:/usr/bin:/bin"; export PATH\n' "$(dirname "$0")"; exit 0; fi
exit 0
STUB
chmod +x "$SB/fakebin/brew"

# --- run `launchd install` in the sandbox (no launchctl) ---
env -i HOME="$H" LA_LIVE_CONFIG_DIR="$LIVE" FA_LAUNCHD_NO_LOAD=1 PATH=/usr/bin:/bin \
    python3 "$REPO/install/manage-backend.py" launchd install >"$SB/install.out" 2>&1
rc=$?
expect_rc 0 $rc "launchd install exits"

LAUNCHER="$SB/launchd/backend-check.sh"     # <live>/../launchd/backend-check.sh
PLIST="$H/Library/LaunchAgents/com.haiggoh.backend-update-check.plist"
if [ -x "$LAUNCHER" ]; then check 0 "stable launcher written + executable at $LAUNCHER"; else check 1 "stable launcher written + executable"; fi
if [ -f "$PLIST" ]; then check 0 "plist written"; else check 1 "plist written"; fi

# --- plist points at the launcher, never at a plugin cache or a checkout ---
plist_check="$(python3 - "$PLIST" "$LAUNCHER" <<'PYEOF'
import plistlib, sys
plist, launcher = sys.argv[1], sys.argv[2]
with open(plist, "rb") as f:
    p = plistlib.load(f)
args = p["ProgramArguments"]
text = open(plist).read()
ok = (args == ["/bin/bash", launcher]
      and "plugins/cache" not in text
      and "ClaudeWorkspace" not in text)
print("OK" if ok else f"BAD: {args}")
PYEOF
)"
has "OK" "$plist_check" "plist ProgramArguments = [bash, stable launcher]; no cache/checkout path"

# --- fake plugin cache with 0.9.0 and 0.10.0: sort -V must pick 0.10.0 (lexical picks 0.9.0) ---
for v in 0.9.0 0.10.0; do
    d="$H/.claude/plugins/cache/haiggoh/free-agents/$v"
    mkdir -p "$d/.claude-plugin"
    printf '{"name":"free-agents","version":"%s"}\n' "$v" > "$d/.claude-plugin/plugin.json"
done
root="$(env -i HOME="$H" FA_BREW="$SB/fakebin/brew" PATH=/usr/bin:/bin bash "$LAUNCHER" --print-root 2>/dev/null)"
rc=$?
expect_rc 0 $rc "launcher --print-root exits"
has "/free-agents/0.10.0" "$root" "resolves newest cache version by sort -V (0.10.0 over 0.9.0): $root"

# --- FA_ROOT set but invalid -> exit 1 naming it ---
out="$(env -i HOME="$H" FA_BREW="$SB/fakebin/brew" FA_ROOT="$SB/nope" PATH=/usr/bin:/bin bash "$LAUNCHER" --print-root 2>&1)"
rc=$?
expect_rc 1 $rc "FA_ROOT invalid exits"
has "FA_ROOT=$SB/nope" "$out" "FA_ROOT failure names the variable and value"

# --- FA_BREW set but not executable -> exit 1 naming it ---
out="$(env -i HOME="$H" FA_BREW="$SB/no-brew" PATH=/usr/bin:/bin bash "$LAUNCHER" --print-root 2>&1)"
rc=$?
expect_rc 1 $rc "FA_BREW invalid exits"
has "FA_BREW=$SB/no-brew" "$out" "FA_BREW failure names the variable and value"

# --- full run through a fake root: both checks green -> exit 0; post-upgrade failing -> exit 1 ---
FR="$SB/fakeroot"
mkdir -p "$FR/.claude-plugin" "$FR/scripts" "$FR/bin"
printf '{"name":"free-agents"}\n' > "$FR/.claude-plugin/plugin.json"
printf '#!/usr/bin/env bash\nexit 0\n' > "$FR/scripts/check-backend-updates.sh"
printf '#!/usr/bin/env bash\nexit "${FAKE_PUC_RC:-0}"\n' > "$FR/bin/fa-post-upgrade-check.sh"
env -i HOME="$H" FA_BREW="$SB/fakebin/brew" FA_ROOT="$FR" PATH=/usr/bin:/bin bash "$LAUNCHER" >/dev/null 2>&1
rc=$?
expect_rc 0 $rc "full run, both checks green"
env -i HOME="$H" FA_BREW="$SB/fakebin/brew" FA_ROOT="$FR" FAKE_PUC_RC=1 PATH=/usr/bin:/bin bash "$LAUNCHER" >/dev/null 2>&1
rc=$?
expect_rc 1 $rc "full run, post-upgrade check broken -> launcher exit 1"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

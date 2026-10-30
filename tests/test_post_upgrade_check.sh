#!/usr/bin/env bash
# tests/test_post_upgrade_check.sh — bin/fa-post-upgrade-check.sh gates on REAL health JSON.
#
# Usage: bash tests/test_post_upgrade_check.sh
#
# The script under test calls `python3 manage-backend.py health --all --json` and (with
# --repair) `--backend X repair V`. These cases stub manage-backend.py via FA_MANAGE_BACKEND
# (a PYTHON stub — the script runs it under python3) and the notification via FA_NOTIFY_CMD.
# Nothing here touches real venvs, the live config, or launchd.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SCRIPT="$REPO/bin/fa-post-upgrade-check.sh"

PASS=0; FAIL=0
check() { if [ "$1" -eq 0 ]; then PASS=$((PASS+1)); printf '  PASS: %s\n' "$2"
          else FAIL=$((FAIL+1)); printf '  FAIL: %s\n' "$2"; fi; }
has() { printf '%s' "$2" | grep -qF -- "$1"; check $? "$3"; }
expect_rc() { # expect_rc <want> <got> <label>
    local want="$1" got="$2"
    if [ "$want" -eq "$got" ]; then check 0 "$3 (got $got)"; else check 1 "$3 (got $got, want $want)"; fi
}

SB="$(mktemp -d "${TMPDIR:-/tmp}/fa-puc-test.XXXXXX")"
trap 'rm -rf "$SB"' EXIT
H="$SB/home"
mkdir -p "$H/.cache/local-agents/rapid-runtime-manager/receipts" "$SB/bin"

# --- stub manage-backend.py (python, like the real one): logs calls, serves canned health JSON,
#     flips to clean when a repair call lands ---
cat > "$SB/bin/manage-backend-stub" <<'STUB'
#!/usr/bin/env python3
import os, sys
log = os.environ["STUB_LOG"]
with open(log, "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if args[:1] == ["health"]:
    if "--json" in args:
        with open(os.environ["STUB_HEALTH_JSON"]) as f:
            sys.stdout.write(f.read())
    rc_file = os.environ["STUB_RC_FILE"]
    rc = open(rc_file).read().strip() if os.path.exists(rc_file) else os.environ.get("STUB_HEALTH_RC", "0")
    sys.exit(int(rc))
if args[:1] == ["--backend"] and args[2:3] == ["repair"]:
    with open(log, "a") as f:
        f.write(f"repair {args[1]} {args[3]}\n")
    with open(os.environ["STUB_HEALTH_JSON"], "w") as f:
        f.write("[]")
    with open(os.environ["STUB_RC_FILE"], "w") as f:
        f.write("0")
    sys.exit(0)
sys.exit(0)
STUB
chmod +x "$SB/bin/manage-backend-stub"

printf '#!/usr/bin/env bash\necho notified >> "$STUB_NOTIFY"\n' > "$SB/bin/notify-stub"
chmod +x "$SB/bin/notify-stub"

export STUB_LOG="$SB/log" STUB_RC_FILE="$SB/rc" STUB_NOTIFY="$SB/notify"
export FA_MANAGE_BACKEND="$SB/bin/manage-backend-stub"
export FA_NOTIFY_CMD="$SB/bin/notify-stub"
export HOME="$H"

BROKEN='[{"backend":"rapid-mlx","version":"0.16.1","state":"broken","reason":"gone","durability":"n/a","pinned":false}]'
CLEAN='[{"backend":"rapid-mlx","version":"0.16.10","state":"complete","reason":"","durability":"durable","pinned":true}]'

run_case() { : > "$STUB_LOG"; : > "$STUB_NOTIFY"; printf '%s' "$1" > "$SB/health.json"
             echo "$2" > "$STUB_RC_FILE"; export STUB_HEALTH_JSON="$SB/health.json"; }

# 1. all healthy -> exit 0, report, no notification, no repair
run_case "$CLEAN" 0
out="$(bash "$SCRIPT" 2>&1)"; rc=$?
expect_rc 0 $rc "healthy: exit"
has "all registered backends healthy" "$out" "healthy: prints clean report"
if [ ! -s "$STUB_NOTIFY" ]; then check 0 "healthy: no notification"; else check 1 "healthy: no notification"; fi
if grep -q repair "$STUB_LOG"; then check 1 "healthy: no repair call"; else check 0 "healthy: no repair call"; fi

# 2. broken WITH receipt + --repair -> repair called, recheck clean, exit 0
run_case "$BROKEN" 1
touch "$H/.cache/local-agents/rapid-runtime-manager/receipts/rapid-mlx-0.16.1.json"
out="$(bash "$SCRIPT" --repair 2>&1)"; rc=$?
expect_rc 0 $rc "repair: exit after successful repair"
if grep -q "repair rapid-mlx 0.16.1" "$STUB_LOG"; then check 0 "repair: stub got repair rapid-mlx 0.16.1"; else check 1 "repair: stub got repair rapid-mlx 0.16.1"; fi
has "healthy after repair" "$out" "repair: reports clean after recheck"

# 3. broken WITHOUT receipt, no --repair -> exit 1, notification, names the version
run_case "$BROKEN" 1
rm -f "$H/.cache/local-agents/rapid-runtime-manager/receipts/rapid-mlx-0.16.1.json"
out="$(bash "$SCRIPT" 2>&1)"; rc=$?
expect_rc 1 $rc "broken no-repair: exit"
has "0.16.1" "$out" "broken no-repair: output names the broken version"
if [ -s "$STUB_NOTIFY" ]; then check 0 "broken no-repair: notification sent"; else check 1 "broken no-repair: notification sent"; fi
if grep -q "^repair" "$STUB_LOG"; then check 1 "broken no-repair: no repair attempted"; else check 0 "broken no-repair: no repair attempted"; fi

# 4. unknown flag -> exit 2, usage on stderr, manage-backend NEVER invoked (parse before action)
run_case "$CLEAN" 0
out="$(bash "$SCRIPT" --bogus 2>&1)"; rc=$?
expect_rc 2 $rc "unknown flag: exit"
has "unknown flag: --bogus" "$out" "unknown flag: names the flag"
if [ ! -s "$STUB_LOG" ]; then check 0 "unknown flag: no manage-backend call ran"; else check 1 "unknown flag: no manage-backend call ran"; fi

# 5. --help -> exit 0, usage, no manage-backend invocation
run_case "$CLEAN" 0
out="$(bash "$SCRIPT" --help 2>&1)"; rc=$?
expect_rc 0 $rc "--help: exit"
has "--repair" "$out" "--help: prints usage"
if [ ! -s "$STUB_LOG" ]; then check 0 "--help: no manage-backend call ran"; else check 1 "--help: no manage-backend call ran"; fi

# 6. checker itself fails (empty health output) -> exit 1 + notification, never "clean"
run_case "" 2
out="$(bash "$SCRIPT" 2>&1)"; rc=$?
expect_rc 1 $rc "checker crash: exit"
has "no output" "$out" "checker crash: says so"
if [ -s "$STUB_NOTIFY" ]; then check 0 "checker crash: notification sent"; else check 1 "checker crash: notification sent"; fi

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

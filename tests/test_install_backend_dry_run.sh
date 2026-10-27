#!/usr/bin/env bash
# test_install_backend_dry_run.sh — `install-backend.sh --dry-run` must not modify anything.
#
# WHY THIS EXISTS (measured 2026-10-26): line 152 did `chmod +x "$MANAGE_CUDA_BACKEND"` on EVERY
# invocation, --dry-run included, so a read-only probe left `install/manage-cuda-backend.py`
# dirty in the working tree (100644 -> 100755). That is a dry run that writes: the flag's whole
# meaning is that nothing changes. It showed up as a mystery dirty file across several sessions
# before anyone traced it.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
REPO="$PWD"

# --help prints this header and exits BEFORE anything runs.
case "${1:-}" in -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;; esac

pass=0 fail=0
ok()  { printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  ✗ %s\n' "$1"; fail=$((fail+1)); }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# A scratch copy of the repo, so the test's chmod probe cannot touch the real tree. bin/ and
# config/ are required too: the installer cd's into ../bin, then sources ../config/config-lib.sh.
cp -RL "$REPO/install" "$REPO/bin" "$REPO/config" "$WORK/" 2>/dev/null
chmod 644 "$WORK/install/manage-cuda-backend.py" "$WORK/install/manage-backend.py"

echo "install-backend.sh --dry-run must be side-effect free:"

mode_before=$(/usr/bin/stat -f %Sp "$WORK/install/manage-cuda-backend.py")
bash "$WORK/install/install-backend.sh" --all --dry-run >"$WORK/out.log" 2>&1
mode_after=$(/usr/bin/stat -f %Sp "$WORK/install/manage-cuda-backend.py")

if [ "$mode_before" = "$mode_after" ]; then
  ok "a dry run does not chmod install/manage-cuda-backend.sh"
else
  bad "a dry run changed the file's mode: $mode_before -> $mode_after"
fi
if [ -d "$WORK/.venvs" ] || [ -d "$WORK/venvs" ]; then
  bad "a dry run created a venv directory it should only have described"
else
  ok "a dry run creates no venv"
fi

# The real install path must still make the scripts executable — the fix is to MOVE the chmod
# behind the dry-run guard, not to delete it. Asserted by the observable: a NON-dry-run invocation
# chmods, a dry-run does not.
chmod 644 "$WORK/install/manage-backend.py"
bash "$WORK/install/install-backend.sh" --backend rapid-mlx --dry-run >/dev/null 2>&1
m1=$(/usr/bin/stat -f %Sp "$WORK/install/manage-backend.py")
bash "$WORK/install/install-backend.sh" --backend rapid-mlx >/dev/null 2>&1 || true
m2=$(/usr/bin/stat -f %Sp "$WORK/install/manage-backend.py")
if [ "$m1" = "-rw-r--r--" ] && [ "$m2" = "-rwxr-xr-x" ]; then
  ok "a real install chmods the manager script, while the dry run left it alone"
else
  bad "expected the real install to chmod manage-backend.py and the dry run not to (got $m1 then $m2)"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

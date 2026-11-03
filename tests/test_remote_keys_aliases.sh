#!/usr/bin/env bash
# tests/test_remote_keys_aliases.sh — 0.52.0 multi-key credential aliases in bin/remote-keys.sh.
# aliases.json maps provider -> ordered credential file names; first existing+readable wins;
# missing alias files fall through; a malformed map or an existing-but-refused alias aborts
# loudly naming it; no entry -> the old direct-file behavior. Key material is never copied.

set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SCRIPT="$REPO/bin/remote-keys.sh"

PASS=0
FAIL=0
check() {
    local status="$1" label="$2"
    if [ "$status" -eq 0 ]; then PASS=$((PASS + 1)); printf '  PASS: %s\n' "$label"
    else FAIL=$((FAIL + 1)); printf '  FAIL: %s\n' "$label"; fi
}
expect_eq() {  # expect_eq <label> <expected> <actual>
    if [ "$2" = "$3" ]; then check 0 "$1"; else check 1 "$1 (want '$2' got '$3')"; fi
}
expect_rc() {  # expect_rc <label> <want_rc> <cmd...>
    local label="$1" want="$2"; shift 2
    "$@" >/dev/null 2>&1
    local got=$?
    if [ "$got" -eq "$want" ]; then check 0 "$label"; else check 1 "$label (want rc=$want got rc=$got)"; fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export LA_API_KEYS_DIR="$TMP/keys"
mkdir -p "$LA_API_KEYS_DIR"

# --- no aliases.json -> old direct behavior ---------------------------------------
printf 'sk-direct' > "$LA_API_KEYS_DIR/nvidia"
out="$("$SCRIPT" --resolve nvidia)"
expect_eq "no aliases.json -> direct file" "nvidia" "$out"
out="$("$SCRIPT" --env nvidia)"
expect_eq "no aliases.json -> env carries the direct key" "NVIDIA_API_KEY=sk-direct" "$out"

# --- alias order: first existing+readable wins --------------------------------------
printf '{"nvidia": ["nvidia-backup", "nvidia"]}' > "$LA_API_KEYS_DIR/aliases.json"
printf 'sk-backup' > "$LA_API_KEYS_DIR/nvidia-backup"
out="$("$SCRIPT" --resolve nvidia)"
expect_eq "both present -> first alias wins" "nvidia-backup" "$out"
out="$("$SCRIPT" --env nvidia)"
expect_eq "env carries the alias key value" "NVIDIA_API_KEY=sk-backup" "$out"
rm "$LA_API_KEYS_DIR/nvidia-backup"
out="$("$SCRIPT" --resolve nvidia)"
expect_eq "first alias missing -> falls through to the next" "nvidia" "$out"

# --- all aliases missing -> default file, then the normal no-credential error --------
rm "$LA_API_KEYS_DIR/nvidia"
expect_rc "all keys missing -> --check fails" 1 "$SCRIPT" --check nvidia
out="$("$SCRIPT" --env nvidia 2>&1)"; rc=$?
[ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q 'no credential for nvidia'
check $? "all missing -> --env exits 1 naming the provider"

# --- malformed aliases.json -> loud abort naming the file -----------------------------
printf 'not json' > "$LA_API_KEYS_DIR/aliases.json"
printf 'sk-x' > "$LA_API_KEYS_DIR/nvidia"
out="$("$SCRIPT" --resolve nvidia 2>&1)"; rc=$?
[ "$rc" -eq 3 ] && printf '%s' "$out" | grep -q 'aliases.json'
check $? "corrupt aliases.json -> rc=3 naming the file (got rc=$rc out=$out)"

# --- wrong-shaped entry -> loud abort naming the entry --------------------------------
printf '{"nvidia": "nvidia"}' > "$LA_API_KEYS_DIR/aliases.json"
out="$("$SCRIPT" --check nvidia 2>&1)"; rc=$?
[ "$rc" -eq 3 ] && printf '%s' "$out" | grep -q "entry for 'nvidia'"
check $? "non-list entry -> rc=3 naming the entry (got rc=$rc out=$out)"

# --- existing-but-refused alias (symlink) -> loud abort, no silent fallthrough ---------
rm -f "$LA_API_KEYS_DIR/aliases.json"
ln -s /etc/hosts "$LA_API_KEYS_DIR/nvidia-evil"   # existing target: the symlink itself is the refusal
printf '{"nvidia": ["nvidia-evil", "nvidia"]}' > "$LA_API_KEYS_DIR/aliases.json"
out="$("$SCRIPT" --resolve nvidia 2>&1)"; rc=$?
[ "$rc" -eq 3 ] && printf '%s' "$out" | grep -q 'nvidia-evil'
check $? "symlinked alias -> rc=3 naming the alias, not silent fallthrough (got rc=$rc)"

# --- multi-file provider: alias covers the primary file, extras stay direct ----------
rm -f "$LA_API_KEYS_DIR/nvidia-evil"
printf 'sk-cf' > "$LA_API_KEYS_DIR/cloudflare"
printf 'acct-1' > "$LA_API_KEYS_DIR/cloudflare-account-id"
printf 'sk-cf-alt' > "$LA_API_KEYS_DIR/cloudflare-alt"
printf '{"cloudflare": ["cloudflare-alt", "cloudflare"]}' > "$LA_API_KEYS_DIR/aliases.json"
out="$("$SCRIPT" --env cloudflare)"
expect_eq "cloudflare alias applies to the token, account-id stays direct" \
    "$(printf 'CLOUDFLARE_API_TOKEN=sk-cf-alt\nCLOUDFLARE_ACCOUNT_ID=acct-1')" "$out"

# --- aliases.json itself is never exported into the env -------------------------------
out="$("$SCRIPT" --env nvidia 2>/dev/null || true)"
if printf '%s' "$out" | grep -q 'aliases'; then check 1 "aliases.json content never appears in --env output (got: $out)"
else check 0 "aliases.json content never appears in --env output"; fi

# --- --help documents the alias map ----------------------------------------------------
"$SCRIPT" --help 2>/dev/null | grep -q 'aliases.json'
check $? "--help documents aliases.json"
expect_rc "unknown flag exits non-zero" 2 "$SCRIPT" --bogus

printf '%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

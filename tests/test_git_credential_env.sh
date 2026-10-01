#!/usr/bin/env bash
# test_git_credential_env.sh — the session git credential overrides must keep github.com
# authenticated while silencing the bare osxkeychain helper.
#
# Guards the defect shipped in c4da19e (2026-09-24): remote-session.sh exported ONLY
# `credential.helper=""`, which reset every helper including the github.com gh helper,
# so sandboxed sessions failed to push ("could not read Username ... Device not
# configured") and models worked around it by writing the PAT into the remote URL.
#
# Hermetic: temp HOME with its own .gitconfig, a stub `gh` that answers the credential
# protocol with a fake token, GIT_CONFIG_NOSYSTEM=1, no network, no Keychain.

set -uo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
HELPER="$ROOT/bin/la-git-credential-env.sh"
PASS=0; FAIL=0

check() { # check <actual> <expected> <description>
    if [ "$1" = "$2" ]; then echo "✅ PASS: $3"; PASS=$((PASS+1))
    else echo "❌ FAIL: $3"; echo "     expected: $2"; echo "     actual:   $1"; FAIL=$((FAIL+1)); fi
}

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'GH'
#!/usr/bin/env bash
# stub gh: only `gh auth git-credential get` is implemented
if [ "$1 $2 $3" = "auth git-credential get" ]; then
    cat >/dev/null; printf 'protocol=https\nhost=github.com\nusername=x-access-token\npassword=FAKE-TEST-TOKEN\n'
fi
GH
chmod +x "$TMP/bin/gh"
# Mirrors the user's ~/.gitconfig shape: bare osxkeychain-like helper + github.com gh helper.
cat > "$TMP/.gitconfig" <<CFG
[credential]
	helper = /bin/false
[credential "https://github.com"]
	helper =
	helper = !$TMP/bin/gh auth git-credential
CFG

# fill <env assignments...> — run `git credential fill` for github.com in an isolated env
fill() {
    printf 'protocol=https\nhost=github.com\n\n' | env -i HOME="$TMP" PATH="$TMP/bin:/usr/bin:/bin" \
        GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 "$@" git credential fill 2>/dev/null \
        | sed -n 's/^password=//p'
}

# Baseline: the user's config alone finds the credential.
check "$(fill)" "FAKE-TEST-TOKEN" "baseline: ~/.gitconfig gh helper supplies the github.com credential"

# Regression guard: the OLD override (reset only) loses it. Keeps the test honest — if this
# ever passes with a token, the fixture no longer reproduces the bug.
check "$(fill GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=)" "" \
    "old reset-only override reproduces the bug (no credential)"

# The fix: what sourcing the helper exports.
eval "$(env -i HOME="$TMP" PATH="$TMP/bin:/usr/bin:/bin" LA_GH_BIN="$TMP/bin/gh" bash -c \
    '. "$1"; for v in ${!GIT_CONFIG_@}; do printf "export %s=%q\n" "$v" "${!v}"; done' _ "$HELPER")"
check "$GIT_CONFIG_COUNT" "3" "helper exports three overrides"
check "$GIT_CONFIG_KEY_0=$GIT_CONFIG_VALUE_0" "credential.helper=" "override 0 resets the bare helper"
check "$GIT_CONFIG_VALUE_2" "!$TMP/bin/gh auth git-credential" "override 2 restores the gh helper"
check "$(fill GIT_CONFIG_COUNT="$GIT_CONFIG_COUNT" \
    GIT_CONFIG_KEY_0="$GIT_CONFIG_KEY_0" GIT_CONFIG_VALUE_0="$GIT_CONFIG_VALUE_0" \
    GIT_CONFIG_KEY_1="$GIT_CONFIG_KEY_1" GIT_CONFIG_VALUE_1="$GIT_CONFIG_VALUE_1" \
    GIT_CONFIG_KEY_2="$GIT_CONFIG_KEY_2" GIT_CONFIG_VALUE_2="$GIT_CONFIG_VALUE_2")" \
    "FAKE-TEST-TOKEN" "with the fix, git still gets the github.com credential"

# Non-github hosts get NO helper (that reset is what silences the 100001 store failure).
other=$(printf 'protocol=http\nhost=localhost:4141\n\n' | env -i HOME="$TMP" PATH="$TMP/bin:/usr/bin:/bin" \
    GIT_CONFIG_NOSYSTEM=1 GIT_TERMINAL_PROMPT=0 GIT_CONFIG_COUNT="$GIT_CONFIG_COUNT" \
    GIT_CONFIG_KEY_0="$GIT_CONFIG_KEY_0" GIT_CONFIG_VALUE_0="$GIT_CONFIG_VALUE_0" \
    GIT_CONFIG_KEY_1="$GIT_CONFIG_KEY_1" GIT_CONFIG_VALUE_1="$GIT_CONFIG_VALUE_1" \
    GIT_CONFIG_KEY_2="$GIT_CONFIG_KEY_2" GIT_CONFIG_VALUE_2="$GIT_CONFIG_VALUE_2" \
    git config --get-urlmatch credential.helper http://localhost:4141 2>/dev/null)
check "$other" "" "non-github hosts resolve to an empty helper (100001 stays silenced)"

# No gh available: falls back to reset-only, warns, still exports a consistent count.
out=$(env -i HOME="$TMP" PATH="/usr/bin:/bin" LA_GH_FALLBACK=/nonexistent/gh bash -c '. "$1" 2>&1; echo "COUNT=$GIT_CONFIG_COUNT"' _ "$HELPER")
check "$(printf '%s' "$out" | grep -c 'gh not found')" "1" "missing gh prints a warning"
check "$(printf '%s' "$out" | sed -n 's/^COUNT=//p')" "1" "missing gh exports only the reset"

# Executed (not sourced): --help works, bare run refuses, nothing exported.
"$HELPER" --help >/dev/null 2>&1; check "$?" "0" "--help exits 0"
"$HELPER" >/dev/null 2>&1; check "$?" "2" "executing without --help/--print exits 2"
"$HELPER" --bogus >/dev/null 2>&1; check "$?" "2" "unknown option exits 2"

# remote-session.sh must source the helper and no longer carry the reset-only block.
check "$(grep -c 'la-git-credential-env.sh"' "$ROOT/bin/remote-session.sh")" "1" "remote-session.sh sources the helper"
check "$(grep -c '^export GIT_CONFIG_COUNT=1$' "$ROOT/bin/remote-session.sh")" "0" "remote-session.sh has no reset-only block"

echo; echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]

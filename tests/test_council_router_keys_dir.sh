#!/usr/bin/env bash
# council-router.sh must look for API keys in $LA_API_KEYS_DIR, so its tests can use a temp dir.
#
# Why (2026-10-05): a session tested the router's "key present / key absent" branches by running
# `echo test-key > ~/.api_keys/nvidia` and then `rm ~/.api_keys/nvidia`. That overwrote and then
# deleted the user's real NVIDIA key, because the router read only $HOME/.api_keys. This suite
# runs the router in a sandbox repo copy with HOME and LA_API_KEYS_DIR both pointed at temp dirs,
# and checks the real key directory is never touched.
set -uo pipefail

HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PASS=0; FAIL=0
check() { if [ "$2" = "$3" ]; then PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1";
          else FAIL=$((FAIL + 1)); printf 'FAIL  %s -- expected [%s] got [%s]\n' "$1" "$3" "$2"; fi; }

SB="$(mktemp -d)"; trap 'rm -rf "$SB"' EXIT
mkdir -p "$SB/repo/bin" "$SB/repo/config" "$SB/home/.models/LocalModel" "$SB/keys" "$SB/home/.api_keys"
cp "$REPO/bin/council-router.sh" "$SB/repo/bin/"
cp "$REPO/config/config-lib.sh" "$SB/repo/config/"
# A weight-sized file so la_on_disk accepts the local fallback.
dd if=/dev/zero of="$SB/home/.models/LocalModel/model.safetensors" bs=1048576 count=2 2>/dev/null
cat > "$SB/repo/config/config.local.sh" <<'CONFIG'
LA_MODELS_DIR="$HOME/.models"
la_register cloudy  nvidia      api   auto "" false "" high
la_register localy  LocalModel  rapid auto "" false "" high
la_role reasoner cloudy high
la_role reasoner localy high
CONFIG

# config-lib.sh needs bash 4+ (associative arrays); macOS /bin/bash is 3.2.
BASH4=""
for b in "$(command -v bash)" /opt/homebrew/bin/bash /usr/local/bin/bash; do
  [ -x "$b" ] && "$b" -c '[ "${BASH_VERSINFO[0]}" -ge 4 ]' 2>/dev/null && { BASH4="$b"; break; }
done
[ -n "$BASH4" ] || { echo "SKIP: needs bash 4+ (brew install bash)"; exit 0; }
route() { env -i PATH="/usr/bin:/bin" HOME="$SB/home" "$@" \
            "$BASH4" "$SB/repo/bin/council-router.sh" --role reasoner 2>/dev/null; }

out="$(route LA_API_KEYS_DIR="$SB/keys")"
check "no key in LA_API_KEYS_DIR: falls back to the local model" "$out" "localy"

printf 'test-key\n' > "$SB/keys/nvidia"
out="$(route LA_API_KEYS_DIR="$SB/keys")"
check "key in LA_API_KEYS_DIR: picks the api model" "$out" "cloudy"

# The directory decides, not $HOME: a key only under $HOME/.api_keys is ignored when the
# variable points elsewhere, and is still found when it is unset (the default is unchanged).
rm "$SB/keys/nvidia"; printf 'test-key\n' > "$SB/home/.api_keys/nvidia"
out="$(route LA_API_KEYS_DIR="$SB/keys")"
check "key only in \$HOME/.api_keys is ignored when LA_API_KEYS_DIR points elsewhere" "$out" "localy"
out="$(route)"
check "unset LA_API_KEYS_DIR still defaults to \$HOME/.api_keys" "$out" "cloudy"

out="$("$BASH4" "$SB/repo/bin/council-router.sh" --help 2>&1)"
case "$out" in *LA_API_KEYS_DIR*) check "--help documents LA_API_KEYS_DIR" ok ok ;;
  *) check "--help documents LA_API_KEYS_DIR" "missing" ok ;; esac

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

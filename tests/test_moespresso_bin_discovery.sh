#!/usr/bin/env bash
# test_moespresso_bin_discovery.sh — config-lib.sh must resolve a MoEspresso binary that can
# actually LOAD a MoEspresso-packaged model, and `moespresso` must be a legal serve value.
#
# WHY THIS EXISTS (measured 2026-10-25): Homebrew links `moespresso` (4.0.0) to
# /opt/homebrew/bin/moespresso, and it stays there even after `brew install moespresso@3` — Homebrew
# says so out loud ("the following moespresso@3 executables are shadowed by linked moespresso
# commands"). 4.0.0 cannot load a DeepSeek V4 package: it refuses with `Error: unsupported required
# package feature`. So a discovery that accepts the PATH name reports a healthy install that fails
# at load time — silent-wrong, which is the exact shape this test must catch.
# --help prints this header and exits BEFORE anything runs.
case "${1:-}" in -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;; esac
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
REPO="$PWD"

pass=0 fail=0
ok()  { printf '  ✓ %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  ✗ %s\n' "$1"; fail=$((fail+1)); }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# A stub binary that reports the version in $STUB_VERSION. 4.x must never be selected.
stub() {  # stub <dir> <version>
  mkdir -p "$1"
  cat > "$1/moespresso" <<STUB
#!/bin/sh
echo "moespresso $2"
STUB
  chmod +x "$1/moespresso"
}

echo "moespresso binary discovery:"

# --- 1. the @3 keg wins over the shadowing unversioned PATH entry -------------------------
KEG3="$WORK/Cellar/moespresso@3/3.0.0/bin"
stub "$KEG3" 3.0.0
BREW_BIN="$WORK/homebrew/bin"
stub "$BREW_BIN" 4.0.0          # what /opt/homebrew/bin/moespresso really is today

out=$(env -u LA_LIVE_CONFIG_DIR \
      LA_MOESPRESSO_CELLAR_ROOT="$WORK/Cellar/moespresso@3" \
      LA_MOESPRESSO_PATH_BIN="$BREW_BIN/moespresso" \
      bash -c '. "$1/config/config-lib.sh"; la_discover_moespresso_bin' _ "$REPO" 2>&1)
if [ "$out" = "$KEG3/moespresso" ]; then
  ok "the @3 keg is preferred over the shadowing 4.0.0 on PATH"
else
  bad "discovery did not pick the @3 keg (got: ${out:-<empty>})"
fi

# --- 2. a 4.x-only machine resolves to nothing, rather than to a binary that cannot load ----
out=$(env -u LA_LIVE_CONFIG_DIR \
      LA_MOESPRESSO_CELLAR_ROOT="$WORK/no-such-keg" \
      LA_MOESPRESSO_PATH_BIN="$BREW_BIN/moespresso" \
      bash -c '. "$1/config/config-lib.sh"; la_discover_moespresso_bin' _ "$REPO" 2>&1)
if [ -z "$out" ]; then
  ok "a 4.x-only machine yields no binary (rather than one that cannot load the model)"
else
  bad "a 4.x-only machine resolved to $out — that binary cannot load a MoEspresso package"
fi

# --- 3. an explicit pin always wins ---------------------------------------------------------
stub "$WORK/pinned" 3.0.0
out=$(env -u LA_LIVE_CONFIG_DIR LA_MOESPRESSO_BIN="$WORK/pinned/moespresso" \
      LA_MOESPRESSO_CELLAR_ROOT="$KEG3/.." \
      bash -c '. "$1/config/config-lib.sh"; la_discover_moespresso_bin' _ "$REPO" 2>&1)
if [ "$out" = "$WORK/pinned/moespresso" ]; then
  ok "an explicit LA_MOESPRESSO_BIN wins over discovery"
else
  bad "the operator pin was ignored (got: ${out:-<empty>})"
fi

# --- 4. `moespresso` is a legal serve value, and NOT a reachable MLX default -----------------
out=$(env -u LA_LIVE_CONFIG_DIR bash -c '. "$1/config/config-lib.sh"; la_load_config
  case " $LA_SERVE_BACKENDS " in *" moespresso "*) echo "PIN-OK" ;; *) echo "PIN-MISSING" ;; esac
  case " $LA_MLX_BACKENDS " in *" moespresso "*) echo "DEFAULT-LEAK" ;; *) echo "DEFAULT-OK" ;; esac' _ "$REPO" 2>/dev/null)
if printf '%s\n' "$out" | /usr/bin/grep -q 'PIN-OK'; then
  ok "moespresso is a legal serve value"
else
  bad "moespresso is missing from LA_SERVE_BACKENDS"
fi
if printf '%s\n' "$out" | /usr/bin/grep -q 'DEFAULT-OK'; then
  ok "moespresso can never be the machine MLX default (pin only)"
else
  bad "moespresso leaked into LA_MLX_BACKENDS — a generic 'mlx' could resolve to it"
fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

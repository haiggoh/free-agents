#!/usr/bin/env bash
# la-catalog-overlay.sh — where the per-user LOCAL model-catalog overlay lives, and the one-time
# migration that moves an existing overlay there (0.58.3, user 2026-11-14).
#
# The overlay (model-catalog.local.psv: the user's own local-model rows) used to sit in
# <copy>/config/, i.e. inside a git checkout, a plugin-cache VERSION dir or a Homebrew Cellar
# version — so each copy saw a different file and an update left it behind in the old version.
# It now lives in ONE per-user store that every copy reads:
#     ~/.claude/free-agents/model-catalog.local.psv
#
# Usage:
#   la-catalog-overlay.sh path    [--config-dir DIR]     print the overlay to read (or nothing)
#   la-catalog-overlay.sh migrate [--config-dir DIR] [--dry-run] [--hook]
#   la-catalog-overlay.sh --help
#
#   --config-dir DIR  the running copy's config dir (default: this script's ../config)
#   --dry-run         migrate: say what would change, change nothing
#   --hook            migrate: print the notice on STDOUT (SessionStart) and always exit 0
#
# Resolution (`path`, and the sourced function la_catalog_overlay_path <config_dir>):
#   1. $LA_MODEL_CATALOG_LOCAL  — explicit file; set but missing is an ERROR (exit 2), never a
#                                 fallthrough
#   2. the store file            — ~/.claude/free-agents/model-catalog.local.psv
#   3. <config_dir>/model-catalog.local.psv — the legacy location
#   4. nothing
# bin/catalog_overlay.py implements the same order for the Python readers (parity-tested).
#
# Migration (`migrate`; run by csl startup and by the plugin's SessionStart hook, so every user
# reaches it through the normal update path): scans the legacy locations of EVERY install —
# this copy's config dir, every plugin-cache version (.../cache/*/free-agents/*/config) and every
# Homebrew Cellar version (<prefix>/Cellar/free-agents/*/libexec/config). If they hold ONE
# distinct content, it is moved to the store and each old path becomes a symlink to it. Different
# contents, or a store that differs from one of them: nothing changes, one notice lists every
# path. Silent when there is nothing to do; idempotent.
#
# Environment:
#   LA_MODEL_CATALOG_LOCAL     explicit overlay file (resolution step 1)
#   LA_OVERLAY_SCAN_ROOTS      ':'-separated config dirs to scan INSTEAD of the defaults (tests)
#   LA_OVERLAY_BREW_PREFIXES   ':'-separated Homebrew prefixes (default /opt/homebrew:/usr/local)
#   CLAUDE_PLUGINS_DIR         the plugins dir (default ~/.claude/plugins)
#
# Exit codes: 0 ok (also every migrate outcome); 2 usage error or a bad LA_MODEL_CATALOG_LOCAL.

LA_OVERLAY_NAME="model-catalog.local.psv"

la_catalog_overlay_store() { printf '%s' "$HOME/.claude/free-agents/$LA_OVERLAY_NAME"; }

# la_catalog_overlay_path <config_dir> — prints the overlay file to read, or nothing.
la_catalog_overlay_path() {
  local cfg="${1:-}" store
  if [ -n "${LA_MODEL_CATALOG_LOCAL:-}" ]; then
    if [ -f "$LA_MODEL_CATALOG_LOCAL" ]; then
      printf '%s' "$LA_MODEL_CATALOG_LOCAL"; return 0
    fi
    printf 'la-catalog-overlay: LA_MODEL_CATALOG_LOCAL=%s does not exist\n' "$LA_MODEL_CATALOG_LOCAL" >&2
    return 2
  fi
  store="$(la_catalog_overlay_store)"
  if [ -f "$store" ]; then printf '%s' "$store"; return 0; fi
  if [ -n "$cfg" ] && [ -f "$cfg/$LA_OVERLAY_NAME" ]; then
    printf '%s' "$cfg/$LA_OVERLAY_NAME"
  fi
  return 0
}

# The legacy config dirs to scan, one per line: this copy's first, then every install's.
_la_overlay_scan_dirs() {
  local cfg="$1" d p IFS_SAVE="$IFS"
  if [ -n "${LA_OVERLAY_SCAN_ROOTS:-}" ]; then
    IFS=':'; for d in $LA_OVERLAY_SCAN_ROOTS; do [ -n "$d" ] && printf '%s\n' "$d"; done; IFS="$IFS_SAVE"
    return 0
  fi
  [ -n "$cfg" ] && printf '%s\n' "$cfg"
  for d in "${CLAUDE_PLUGINS_DIR:-$HOME/.claude/plugins}"/cache/*/free-agents/*/config; do
    [ -d "$d" ] && printf '%s\n' "$d"
  done
  IFS=':'
  for p in ${LA_OVERLAY_BREW_PREFIXES:-/opt/homebrew:/usr/local}; do
    IFS="$IFS_SAVE"
    for d in "$p"/Cellar/free-agents/*/libexec/config; do
      [ -d "$d" ] && printf '%s\n' "$d"
    done
    IFS=':'
  done
  IFS="$IFS_SAVE"
}

# _la_overlay_migrate <config_dir> <dry_run 0|1> — prints at most one notice line on stdout.
_la_overlay_migrate() {
  local cfg="$1" dry="$2" store d f first="" conflict=0 list="" moved=0
  store="$(la_catalog_overlay_store)"
  # Collect REGULAR files (a symlink is an already-migrated path); dedupe the list itself.
  local files=""
  while IFS= read -r d; do
    f="$d/$LA_OVERLAY_NAME"
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    case "
$files
" in *"
$f
"*) continue ;; esac
    files="${files:+$files
}$f"
  done <<EOF
$(_la_overlay_scan_dirs "$cfg")
EOF
  [ -n "$files" ] || return 0

  # Group by content: every file must equal the first (and the store, if it exists).
  while IFS= read -r f; do
    list="${list:+$list, }$f"
    if [ -z "$first" ]; then first="$f"
    elif ! cmp -s "$first" "$f"; then conflict=1
    fi
  done <<EOF
$files
EOF
  if [ -f "$store" ] && ! cmp -s "$store" "$first"; then conflict=1; fi
  if [ "$conflict" = 1 ]; then
    printf 'free-agents: your local model catalog exists in several places with DIFFERENT contents (%s); nothing was changed — merge them into %s and remove the others.\n' \
      "$list" "$store"
    return 0
  fi

  if [ "$dry" = 1 ]; then
    printf 'free-agents: would move your local model catalog to %s and leave a symlink at: %s\n' "$store" "$list"
    return 0
  fi

  mkdir -p "$(dirname "$store")" || return 0
  if [ ! -f "$store" ]; then
    mv "$first" "$store" || return 0
    if ! ln -s "$store" "$first"; then
      mv "$store" "$first"        # never leave neither path holding the data
      return 0
    fi
    moved=1
  fi
  while IFS= read -r f; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    cmp -s "$store" "$f" || continue
    rm -f "$f" && ln -s "$store" "$f"
  done <<EOF
$files
EOF
  if [ "$moved" = 1 ]; then
    printf 'free-agents: moved your local model catalog to %s (it is now shared by every copy of free-agents; a symlink stays at %s)\n' \
      "$store" "$list"
  else
    printf 'free-agents: your local model catalog is in %s; the identical copies at %s are now symlinks to it\n' \
      "$store" "$list"
  fi
}

_la_overlay_usage() {
  sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed -n 's/^# \{0,1\}//p'
}

_la_overlay_main() {
  local cmd="" cfg="" dry=0 hook=0
  cfg="$(cd "$(dirname "${BASH_SOURCE[0]}")/../config" 2>/dev/null && pwd)"
  while [ $# -gt 0 ]; do
    case "$1" in
      -h|--help) _la_overlay_usage; return 0 ;;
      path|migrate) [ -z "$cmd" ] || { echo "usage: la-catalog-overlay.sh {path|migrate} [options]" >&2; return 2; }; cmd="$1" ;;
      --config-dir) [ $# -ge 2 ] || { echo "usage: --config-dir needs a directory" >&2; return 2; }; cfg="$2"; shift ;;
      --dry-run) dry=1 ;;
      --hook) hook=1 ;;
      *) printf 'la-catalog-overlay.sh: unrecognised argument %s\nusage: la-catalog-overlay.sh {path|migrate} [--config-dir DIR] [--dry-run] [--hook] | --help\n' "$1" >&2; return 2 ;;
    esac
    shift
  done
  case "$cmd" in
    path) la_catalog_overlay_path "$cfg" ;;
    migrate)
      local out
      out="$(_la_overlay_migrate "$cfg" "$dry")"
      [ -n "$out" ] || return 0
      if [ "$hook" = 1 ]; then printf '%s\n' "$out"; else printf '%s\n' "$out" >&2; fi
      return 0 ;;
    *) echo "usage: la-catalog-overlay.sh {path|migrate} [--config-dir DIR] [--dry-run] [--hook] | --help" >&2; return 2 ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  _la_overlay_main "$@"
  exit $?
fi

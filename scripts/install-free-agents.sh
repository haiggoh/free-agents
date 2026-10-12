#!/usr/bin/env bash
# install-free-agents.sh — standalone installer for free-agents (macOS, Linux, WSL2).
#
# Installs the plugin through Claude Code's own CLI (the haiggoh marketplace), puts `csl`,
# `lowkey` and `lk` on your PATH as wrappers that always run the NEWEST installed plugin copy,
# then optionally sets up the remote lane (LiteLLM, picker UI, API keys) and local inference.
#
# Usage:
#   curl -fsSL https://github.com/haiggoh/free-agents/releases/latest/download/install-free-agents.sh | bash
#   curl -fsSL …/install-free-agents.sh | bash -s -- --remote-only --yes
#   bash install-free-agents.sh [options]
#
# Options:
#   --remote-only   skip local inference (backend + model downloads)
#   --local         include local inference without asking
#   --yes           accept every default without prompting (implies no local setup unless --local)
#   --bin-dir DIR   where the PATH wrappers go (default ~/.local/bin)
#   --no-wrappers   do not write csl/lowkey/lk wrappers
#   --help          print this help and exit
#
# Environment:
#   CLAUDE_CONFIG_DIR   Claude Code config dir (default ~/.claude); the plugin cache is read from it
#   FREE_AGENTS_ROOT    honoured by the wrappers: run that checkout instead of the plugin cache
#
# Prompts are read from /dev/tty, so they work under `curl … | bash`. With no terminal at all the
# installer takes the defaults and skips the optional heavy steps. Existing commands named csl,
# lowkey or lk that this installer did not write are left alone.

# Everything runs inside main(), called on the last line, so a truncated download executes nothing.

set -uo pipefail

REPO="haiggoh/free-agents"
MARKETPLACE_SRC="haiggoh/get-haiggoh"
PLUGIN_REF="free-agents@haiggoh"
WRAPPER_MARK="# managed by free-agents install-free-agents.sh"

MODE_LOCAL=ask
ASSUME_YES=0
BIN_DIR="$HOME/.local/bin"
WRAPPERS=1

if [ -t 1 ]; then
    C_B=$'\033[1m'; C_OK=$'\033[0;32m'; C_WARN=$'\033[1;33m'; C_ERR=$'\033[0;31m'; C_DIM=$'\033[0;36m'; C_0=$'\033[0m'
else
    C_B=; C_OK=; C_WARN=; C_ERR=; C_DIM=; C_0=
fi
step() { printf '\n%s▶ %s%s\n' "$C_B" "$1" "$C_0"; }
ok()   { printf '%s✓ %s%s\n' "$C_OK" "$1" "$C_0"; }
warn() { printf '%s⚠ %s%s\n' "$C_WARN" "$1" "$C_0"; }
err()  { printf '%s✗ %s%s\n' "$C_ERR" "$1" "$C_0" >&2; }
info() { printf '%s  %s%s\n' "$C_DIM" "$1" "$C_0"; }

# The help text lives in a function, not only in the header comment: under `curl … | bash` there is
# no file on disk to read the header from.
usage() {
    cat <<'HELP'
install-free-agents.sh — standalone installer for free-agents (macOS, Linux, WSL2).

Installs the plugin through Claude Code's own CLI (the haiggoh marketplace), puts csl, lowkey and lk
on your PATH as wrappers that always run the newest installed plugin copy, then optionally sets up the
remote lane (LiteLLM, picker UI, API keys) and local inference.

Usage:
  curl -fsSL https://github.com/haiggoh/free-agents/releases/latest/download/install-free-agents.sh | bash
  curl -fsSL …/install-free-agents.sh | bash -s -- --remote-only --yes
  bash install-free-agents.sh [options]

Options:
  --remote-only   skip local inference (backend + model downloads)
  --local         include local inference without asking
  --yes           accept every default without prompting (no local setup unless --local)
  --bin-dir DIR   where the PATH wrappers go (default ~/.local/bin)
  --no-wrappers   do not write csl/lowkey/lk wrappers
  --help          print this help and exit

Environment:
  CLAUDE_CONFIG_DIR   Claude Code config dir (default ~/.claude); the plugin cache is read from it
  FREE_AGENTS_ROOT    honoured by the wrappers: run that checkout instead of the plugin cache
HELP
}

HAVE_TTY=0
if [ -r /dev/tty ] && { : </dev/tty; } 2>/dev/null; then HAVE_TTY=1; fi

# ask "question" default(y|n) -> 0 for yes
ask() {
    local q=$1 def=$2 reply hint="[Y/n]"
    [ "$def" = n ] && hint="[y/N]"
    if [ "$ASSUME_YES" = 1 ] || [ "$HAVE_TTY" = 0 ]; then
        [ "$def" = y ]; return
    fi
    printf '%s%s %s %s' "$C_WARN" "$q" "$hint" "$C_0" >/dev/tty
    IFS= read -r reply </dev/tty || reply=
    case "$reply" in
        [yY]|[yY][eE][sS]) return 0 ;;
        [nN]|[nN][oO]) return 1 ;;
        *) [ "$def" = y ] ;;
    esac
}

# The newest installed plugin copy (same rule as the Homebrew wrappers).
plugin_root() {
    local cache="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache/haiggoh/free-agents" newest
    [ -d "$cache" ] || return 1
    # shellcheck disable=SC2010  # version-number dir names only; same rule as the Homebrew wrapper
    newest=$(ls -1 "$cache" 2>/dev/null | grep -E '^[0-9]+[.][0-9]+[.][0-9]+$' |
             sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
    [ -n "$newest" ] && [ -f "$cache/$newest/.claude-plugin/plugin.json" ] || return 1
    printf '%s' "$cache/$newest"
}

check_prereqs() {
    step "Checking prerequisites"
    case "$(uname -s)" in
        Darwin) ok "macOS $(sw_vers -productVersion 2>/dev/null) on $(uname -m)" ;;
        Linux)  if grep -qi microsoft /proc/version 2>/dev/null; then ok "Linux (WSL2)"; else ok "Linux"; fi ;;
        *) err "Unsupported platform: $(uname -s). Native Windows needs WSL2."; return 1 ;;
    esac
    command -v claude >/dev/null 2>&1 || {
        err "Claude Code (the 'claude' command) is not on PATH."
        info "Install it first: https://claude.com/download, then re-run this installer."
        return 1
    }
    ok "Claude Code $(claude --version 2>/dev/null | head -1)"
    command -v python3 >/dev/null 2>&1 || { err "python3 not found (macOS: brew install python)"; return 1; }
    ok "Python $(python3 --version 2>&1 | cut -d' ' -f2)"
}

install_plugin() {
    step "Installing the plugin through Claude Code"
    if claude plugin marketplace list 2>/dev/null | grep -q '❯ haiggoh$\|haiggoh/get-haiggoh'; then
        claude plugin marketplace update haiggoh >/dev/null 2>&1 || true
        ok "haiggoh marketplace already added (refreshed)"
    elif claude plugin marketplace add "$MARKETPLACE_SRC" >/dev/null 2>&1; then
        ok "haiggoh marketplace added"
    else
        err "Could not add the marketplace ($MARKETPLACE_SRC)."
        info "Inside Claude Code run:  /plugin marketplace add $MARKETPLACE_SRC"
        return 1
    fi
    if plugin_root >/dev/null; then
        if claude plugin update "$PLUGIN_REF" >/dev/null 2>&1; then ok "plugin already installed (update checked)"
        else warn "plugin already installed; the update check did not run"; fi
    elif claude plugin install "$PLUGIN_REF" --scope user >/dev/null 2>&1; then
        ok "plugin installed"
    else
        err "'claude plugin install $PLUGIN_REF' failed."
        info "Inside Claude Code run:  /plugin install $PLUGIN_REF"
        return 1
    fi
    FA=$(plugin_root) || { err "The plugin was installed but its directory was not found."; return 1; }
    ok "plugin $(cat "$FA/VERSION" 2>/dev/null) at $FA"
}

write_wrappers() {
    [ "$WRAPPERS" = 1 ] || return 0
    step "Putting csl, lowkey and lk on your PATH ($BIN_DIR)"
    mkdir -p "$BIN_DIR" || { err "cannot create $BIN_DIR"; return 1; }
    local name rel target
    for name in csl lowkey lk; do
        case "$name" in csl) rel=bin/csl ;; *) rel=bin/lowkey ;; esac
        target="$BIN_DIR/$name"
        if { [ -e "$target" ] || [ -L "$target" ]; } && ! grep -qF "$WRAPPER_MARK" "$target" 2>/dev/null; then
            warn "$target exists and was not written by this installer — left alone"
            continue
        fi
        rm -f "$target"
        cat >"$target" <<EOF
#!/usr/bin/env bash
$WRAPPER_MARK
# Runs the NEWEST free-agents copy Claude Code has installed; FREE_AGENTS_ROOT overrides.
root="\${FREE_AGENTS_ROOT:-}"
if [ -z "\$root" ]; then
    cache="\${CLAUDE_CONFIG_DIR:-\$HOME/.claude}/plugins/cache/haiggoh/free-agents"
    v=\$(ls -1 "\$cache" 2>/dev/null | grep -E '^[0-9]+[.][0-9]+[.][0-9]+\$' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
    [ -n "\$v" ] && root="\$cache/\$v"
fi
[ -n "\$root" ] && [ -x "\$root/$rel" ] || {
    echo "$name: free-agents is not installed in Claude Code (claude plugin install $PLUGIN_REF)" >&2
    exit 1
}
exec "\$root/$rel" "\$@"
EOF
        chmod 755 "$target"
        ok "$target"
    done
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) warn "$BIN_DIR is not on your PATH. Add this line to your shell rc file:"
           info "export PATH=\"$BIN_DIR:\$PATH\"" ;;
    esac
}

setup_remote() {
    step "Remote lane (free cloud APIs)"
    if command -v litellm >/dev/null 2>&1; then
        ok "litellm found"
    elif command -v pipx >/dev/null 2>&1; then
        if ask "Install LiteLLM (the proxy remote sessions run through) with pipx?" y; then
            if pipx install 'litellm[proxy]' >/dev/null 2>&1; then ok "litellm installed"
            else warn "pipx install failed; run: pipx install 'litellm[proxy]'"; fi
        fi
    else
        warn "litellm not found, and pipx is not available to install it"
        info "macOS: brew install pipx && pipx install 'litellm[proxy]'"
    fi
    if "$FA/install/setup-session-picker.sh" --check >/dev/null 2>&1; then
        ok "session picker UI ready"
    elif ask "Set up the session picker UI (a small private Python venv)?" y; then
        if "$FA/install/setup-session-picker.sh"; then ok "session picker UI ready"
        else warn "picker setup failed; re-run: $FA/install/setup-session-picker.sh"; fi
    fi
    if [ "$HAVE_TTY" = 1 ] && [ "$ASSUME_YES" = 0 ] && ask "Store a free-tier API key now (no API calls are made)?" y; then
        python3 "$FA/install/setup-api-keys.py" </dev/tty || warn "key setup did not finish; later: csl setup-remote"
    else
        info "Add provider keys later with:  csl setup-remote"
    fi
}

setup_local() {
    case "$MODE_LOCAL" in
        no) return 0 ;;
        ask) ask "Set up LOCAL inference too (backends + model downloads, tens of GB)?" n || {
                 info "Skipped. Later: $FA/install/install-backend.sh, then csl → d"; return 0; } ;;
    esac
    step "Local inference"
    local args=()
    [ "$(uname -s)" = Linux ] && command -v nvidia-smi >/dev/null 2>&1 && args=(--all)
    "$FA/install/install-backend.sh" ${args[@]+"${args[@]}"} || { warn "backend install failed; re-run: $FA/install/install-backend.sh"; return 0; }
    ok "backends installed"
    if [ "$HAVE_TTY" = 1 ]; then
        info "You do not need every model. --group default fetches the recommended set (~36 GB)."
        "$FA/install/download-models.sh" </dev/tty || warn "model download did not finish; later: csl download-models"
    else
        info "Download models later: csl download-models"
    fi
}

main() {
    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help) usage; exit 0 ;;
            --remote-only) MODE_LOCAL=no ;;
            --local) MODE_LOCAL=yes ;;
            --yes|-y) ASSUME_YES=1 ;;
            --bin-dir) [ $# -ge 2 ] || { echo "--bin-dir needs a directory" >&2; exit 2; }; BIN_DIR=$2; shift ;;
            --no-wrappers) WRAPPERS=0 ;;
            *) echo "usage: install-free-agents.sh [--remote-only|--local] [--yes] [--bin-dir DIR] [--no-wrappers] [--help]" >&2; exit 2 ;;
        esac
        shift
    done
    [ "$ASSUME_YES" = 1 ] && [ "$MODE_LOCAL" = ask ] && MODE_LOCAL=no

    printf '%sfree-agents installer%s — Claude Code on free inference\n' "$C_B" "$C_0"
    check_prereqs || exit 1
    install_plugin || exit 1
    write_wrappers
    setup_remote
    setup_local

    step "Done"
    ok "Restart Claude Code (or run /reload-plugins) to load the skills and hooks."
    info "csl                 open the session picker"
    info "csl remote          launch a free remote session"
    info "README: https://github.com/$REPO#readme"
}

main "$@"

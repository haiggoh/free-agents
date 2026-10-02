#!/usr/bin/env bash
# la-git-credential-env.sh — session-scoped git credential overrides for free-agents sessions.
#
# SOURCE this file (do not execute it): it exports GIT_CONFIG_COUNT / GIT_CONFIG_KEY_n /
# GIT_CONFIG_VALUE_n into the caller's environment, so the overrides apply only to the
# session's own process tree and never edit ~/.gitconfig.
#
#   . "$SCRIPT_DIR/la-git-credential-env.sh"     # from a launcher
#   bin/la-git-credential-env.sh --help          # usage
#   bin/la-git-credential-env.sh --print         # show what sourcing would export
#
# What it does, and why both halves are needed:
#   1. `credential.helper = ""` RESETS the helper list. That silences the harmless
#      `fatal: failed to store: 100001` that the bare osxkeychain helper prints when a
#      sandboxed git tries to store the sandbox proxy's throwaway credential.
#   2. That reset also wipes the URL-scoped github.com helper from ~/.gitconfig, so it is
#      put back explicitly: `gh auth git-credential`, which hands git the PAT from GH_TOKEN
#      (exported from ~/.api_keys/github) or gh's keyring. Without step 2 git finds NO
#      credential and fails with "could not read Username for 'https://github.com': Device
#      not configured" — the push failure seen in remote sessions from 0.19.x onward.
#
# Environment:
#   LA_GH_BIN   path to gh used for the github.com helper (default: `command -v gh`,
#               then LA_GH_FALLBACK). If gh is not found, only step 1 is applied
#               and a warning is printed, so the session still starts.
#   LA_GH_FALLBACK  fallback gh path when gh is not on PATH (default /opt/homebrew/bin/gh)

_la_git_cred_usage() {
    sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed -e '$d' -e 's/^# \{0,1\}//'
}

_la_git_cred_apply() {
    local gh_bin="${LA_GH_BIN:-}"
    if [[ -z "$gh_bin" ]]; then
        gh_bin="$(command -v gh 2>/dev/null || true)"
        local fallback="${LA_GH_FALLBACK:-/opt/homebrew/bin/gh}"
        [[ -z "$gh_bin" && -x "$fallback" ]] && gh_bin="$fallback"
    fi

    export GIT_CONFIG_KEY_0="credential.helper"
    export GIT_CONFIG_VALUE_0=""
    if [[ -n "$gh_bin" ]]; then
        # Empty first entry resets the github.com list, then gh is the only helper for it.
        export GIT_CONFIG_KEY_1="credential.https://github.com.helper"
        export GIT_CONFIG_VALUE_1=""
        export GIT_CONFIG_KEY_2="credential.https://github.com.helper"
        export GIT_CONFIG_VALUE_2="!$gh_bin auth git-credential"
        export GIT_CONFIG_COUNT=3
    else
        echo "la-git-credential-env: gh not found; github.com pushes will have no credential helper" >&2
        export GIT_CONFIG_COUNT=1
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    # Executed, not sourced: never change anything, only describe.
    case "${1:-}" in
        -h|--help) _la_git_cred_usage; exit 0 ;;
        --print)
            _la_git_cred_apply
            for ((i = 0; i < GIT_CONFIG_COUNT; i++)); do
                k="GIT_CONFIG_KEY_$i"; v="GIT_CONFIG_VALUE_$i"
                printf '%s=%s\n' "${!k}" "${!v}"
            done
            exit 0 ;;
        "") echo "la-git-credential-env.sh: source this file; see --help" >&2; exit 2 ;;
        *) echo "la-git-credential-env.sh: unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
fi

_la_git_cred_apply

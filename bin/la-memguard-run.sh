# shellcheck shell=bash
# la-memguard-run.sh — sourced by the local launchers (not executable on its own).
#
#   la_memguard_claude <backend> <port> <model> -- <claude args...>
#
# Runs `claude <args>` registered with the memory guard (bin/la-memguard-registry.py) and, when it
# can, inside a private tmux session attached in this same terminal, so the guard can later type
# `/compact` into it (tmux send-keys is the only injection route that can stash a draft first).
# Returns claude's exit status either way.
#
# Falls back to running claude directly (registered, but with no injection target) when tmux is
# missing, stdin/stdout is not a terminal, or LA_MEMGUARD_TMUX=0. Already inside tmux: the current
# pane is the target and nothing is nested. LA_MEMGUARD=0 skips the guard entirely.
# Env: LA_MEMGUARD, LA_MEMGUARD_TMUX, LA_MEMGUARD_STATE_DIR, LA_MEMGUARD_TMUX_BIN (tests).

la_memguard_claude() {
    local backend="$1" port="$2" model="$3"
    shift 3
    [ "${1:-}" = "--" ] && shift
    if [ "${LA_MEMGUARD:-1}" = "0" ]; then
        claude "$@"
        return $?
    fi
    local reg="$LAUNCH_DIR/la-memguard-registry.py"
    local tmux_bin="${LA_MEMGUARD_TMUX_BIN:-$(command -v tmux 2>/dev/null)}"
    local state="${LA_MEMGUARD_STATE_DIR:-$HOME/.local/state/free-agents/memguard}"
    local mode=direct socket="" target="" sid rc
    if [ -n "${TMUX:-}" ] && [ -n "${TMUX_PANE:-}" ]; then
        mode=inside socket="${TMUX%%,*}" target="$TMUX_PANE"
    elif [ "${LA_MEMGUARD_TMUX:-1}" != "0" ] && [ -n "$tmux_bin" ] && [ -t 0 ] && [ -t 1 ]; then
        mode=tmux
    fi
    if [ "$mode" = tmux ]; then
        mkdir -p "$state/tmux" && chmod 700 "$state" "$state/tmux" 2>/dev/null
        socket="$state/tmux/$backend-$port-$$.sock" target="fa-$backend-$port-$$"
        # macOS caps a unix-socket path at 104 bytes; past that tmux cannot start at all. Fall back
        # to a short private dir (ours, mode 700, not a symlink) rather than lose injection.
        if [ "${#socket}" -gt 100 ]; then
            local short
            short="/tmp/fa-memguard-$(id -u)"
            mkdir -m 700 "$short" 2>/dev/null
            if [ -d "$short" ] && [ ! -L "$short" ] && [ -O "$short" ]; then
                chmod 700 "$short"
                socket="$short/$backend-$port-$$.sock"
            else
                mode=direct socket="" target=""
            fi
        fi
    fi
    sid="$(python3 "$reg" register --backend "$backend" --port "$port" --model "$model" \
        --tmux-socket "$socket" --tmux-target "$target" --launcher-pid "$$" 2>/dev/null)" || sid=""
    export LA_MEMGUARD_SESSION_ID="$sid"
    # One watchdog per machine; it exits by itself once no local session is registered.
    [ -n "$sid" ] && python3 "$LAUNCH_DIR/la-memguard.py" ensure >/dev/null 2>&1

    if [ "$mode" != tmux ]; then
        claude "$@"
        rc=$?
    else
        local runner="$state/tmux/$target.run" status="$state/tmux/$target.status" a
        {
            printf '#!/bin/bash\ncd %q || exit 1\nclaude' "$PWD"
            for a in "$@"; do printf ' %q' "$a"; done
            printf '\necho $? > %q\n' "$status"
        } >"$runner"
        chmod 700 "$runner"
        rm -f "$status"
        # Private server (own socket, no user tmux.conf). Claude Code owns C-b (background task) and
        # needs Esc without delay, so: no prefix, escape-time 0, no status bar; mouse scrolls history.
        "$tmux_bin" -S "$socket" -f /dev/null new-session -d -s "$target" -x "$(tput cols 2>/dev/null || echo 120)" \
            -y "$(tput lines 2>/dev/null || echo 40)" "$runner" \
            \; set -g prefix None \; unbind-key C-b \; set -g escape-time 0 \; set -g status off \
            \; set -g mouse on \; set -g focus-events on \; set -g history-limit 50000 \; set -g default-terminal tmux-256color \
            \; set -ga terminal-features ',*:RGB' \; set -g remain-on-exit off >/dev/null 2>&1
        if "$tmux_bin" -S "$socket" has-session -t "$target" 2>/dev/null; then
            "$tmux_bin" -S "$socket" attach-session -t "$target" >/dev/null 2>&1
            # Detached by accident (no prefix key exists, but a client can still drop): keep the
            # launcher waiting while the session lives, and say how to get back.
            while "$tmux_bin" -S "$socket" has-session -t "$target" 2>/dev/null; do
                printf '\nℹ️  Session still running in tmux — reattach: tmux -S %q attach -t %s\n' "$socket" "$target" >&2
                sleep 5
                [ -t 0 ] && "$tmux_bin" -S "$socket" attach-session -t "$target" >/dev/null 2>&1
            done
            rc="$(cat "$status" 2>/dev/null)"
            rc="${rc:-1}"
        else
            # tmux could not start the session: run plainly rather than not at all.
            python3 "$reg" unregister "$sid" >/dev/null 2>&1
            sid="$(python3 "$reg" register --backend "$backend" --port "$port" --model "$model" \
                --launcher-pid "$$" 2>/dev/null)" || sid=""
            export LA_MEMGUARD_SESSION_ID="$sid"
            claude "$@"
            rc=$?
        fi
        rm -f "$runner" "$status" "$socket"
    fi
    [ -n "$sid" ] && python3 "$reg" unregister "$sid" >/dev/null 2>&1
    unset LA_MEMGUARD_SESSION_ID
    return "$rc"
}

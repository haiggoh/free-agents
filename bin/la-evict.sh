#!/bin/bash
# la-evict.sh — eviction fallback for local model servers.
#
# WHY THIS EXISTS
# When too many model servers are resident, the machine runs out of RAM, and the failure
# mode is not a clean error: the system becomes unresponsive, so there is no way left to
# type a kill command. The only remaining exit is a hardware reboot.
#
# So this script has one job: free the most memory it can while touching the fewest
# things, from a single non-interactive invocation that needs no terminal.
#
# THE ESCALATION LADDER — advanced ONLY by an argument-less run:
#   tier 0  sweep EVERY unattached (orphaned) server in one pass, and WARN which ports
#           are still attached to a live session. Dead servers are safe to evict and
#           there is no reason to tier them, so they all go in a single pass.
#   tier 1  evict ONE server under the existing ranking rules. If the best candidate is
#           attached, hold back instead (exit 3): a live session's backend is never cut
#           out from under it by the second invocation.
#   tier 2+ evict ONE server, still by ranking, but the attached guard is lifted and
#           unrecognised backends are eligible. This is the emergency tier — in an OOM
#           the machine stalls and cannot continue until at least one port is freed.
# From tier 1 onward each invocation evicts exactly one server; only tier 0 sweeps.
#
# --port N names an explicit target and is ORTHOGONAL to the ladder: any number of --port
# runs leave the tier exactly where the plain runs left it, and a --port run never writes
# escalation state. N must be inside the configured model-server range; a port outside it
# is refused (exit 2). That refusal is what keeps the proxy band live sessions depend on
# out of reach of a typo. --port may be repeated and is additive.
#
# SUPPORTS MACOS (Apple Silicon MLX) AND LINUX/WSL (CUDA)
# Uses /proc on Linux for reliable process inspection; uses ps on macOS.
# Supports Rapid-MLX, vllm-mlx, mlx_lm.server, llama.cpp, and CUDA vLLM/llama-server.
#
# Usage: la-evict.sh [--status] [--dry-run] [--tier N] [--port N]... [--all] [--allow-unknown] [--reset]
#        [--help]   (--reset clears the saved escalation state and exits, evicting nothing)
#
# Exit: 0 done · 1 internal failure · 2 usage · 3 tier 1 held back an attached server
#       4 a --port request found no listener (or one of several) · 5 a --port request
#       stopped nothing because every candidate was refused
# The LiteLLM proxy band (4141-4151) is NEVER scanned, ranked or evicted, and no
# environment override widens past it.

set -u

PORT_START="${LA_PORT_START:-8000}"
PORT_MAX="${LA_PORT_MAX:-8010}"
# `-` not `:-`: an explicitly EMPTY value must stay empty. With `:-`, setting
# LA_EVICT_EXTRA_PORTS="" silently restored the 8080 default, so "no extra ports"
# was not expressible and every range check that used it was wrong.
EXTRA_PORTS="${LA_EVICT_EXTRA_PORTS-8080}"
STATE_FILE="${LA_EVICT_STATE:-$HOME/.claude/logs/la-evict.state}"
LOG_DIR="${LA_EVICT_LOG_DIR:-$HOME/.claude/logs}"
WINDOW_S="${LA_EVICT_WINDOW:-600}"      # repeat inside this window escalates
YOUNG_S="${LA_EVICT_YOUNG:-120}"        # "just launched" — prime suspect
TERM_WAIT="${LA_EVICT_TERM_WAIT:-8}"    # seconds to wait for SIGTERM before SIGKILL

# HARD EXCLUSION — NOT CONFIGURABLE. These are the LiteLLM proxy ports that live
# Claude Code sessions are served through on this machine. No env override widens
# past them: an operator (or a stale profile) pointing LA_PORT_START at 4141 must
# not be able to make this script kill a proxy that sessions depend on. Killing one
# reads as "Connection refused" from every session at once and is unrecoverable
# without relaunching each one.
PROXY_LO=4141
PROXY_HI=4151

usage() {
    awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0" >&2
}

# The tier ladder advances ONLY on an argument-less run. Anything else — --port,
# --tier, --all, --allow-unknown, --dry-run, --status — leaves the tier exactly
# where it was, both for reading and for writing.
BARE_RUN=1; [ $# -gt 0 ] && BARE_RUN=0

DRY_RUN=0; STATUS_ONLY=0; FORCE_TIER=""; KILL_ALL=0; ALLOW_UNKNOWN=0
PORTS=""; PORT_MODE=0; DO_RESET=0
while [ $# -gt 0 ]; do
    case "$1" in
        --status)        STATUS_ONLY=1 ;;
        --dry-run|-n)    DRY_RUN=1 ;;
        --tier)
            shift
            _want="${1:-}"
            case "$_want" in
                ''|*[!0-9]*) printf 'la-evict.sh: --tier needs a non-negative integer, got %s\n' "'${_want}'" >&2
                              usage; exit 2 ;;
            esac
            FORCE_TIER="$_want"
            ;;
        --all)           KILL_ALL=1 ;;
        --allow-unknown) ALLOW_UNKNOWN=1 ;;
        --port)
            shift
            _want="${1:-}"
            case "$_want" in
                ''|*[!0-9]*)
                    printf 'la-evict.sh: --port needs a numeric port, got %s\n' "'${_want}'" >&2
                    usage; exit 2 ;;
            esac
            PORTS="$PORTS $_want"
            PORT_MODE=1
            ;;
        --reset)         DO_RESET=1 ;;
        -h|--help)       awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
        *)               echo "unknown argument: $1" >&2; usage; exit 2 ;;
    esac
    shift
done

# --port names a known target, so it is meaningless to combine it with the ladder's
# own controls. Refusing beats silently picking one interpretation.
if [ "$PORT_MODE" = 1 ] && { [ "$KILL_ALL" = 1 ] || [ -n "$FORCE_TIER" ]; }; then
    printf 'la-evict.sh: --port is orthogonal to the tier ladder: combining it with --tier or --all is not supported\n' >&2
    usage; exit 2
fi

in_proxy_band() {
    _want="$1"
    [ "$_want" -ge "$PROXY_LO" ] 2>/dev/null && [ "$_want" -le "$PROXY_HI" ] 2>/dev/null
}

in_scan_range() {
    _want="$1"
    if [ "$_want" -ge "$PORT_START" ] 2>/dev/null && [ "$_want" -le "$PORT_MAX" ] 2>/dev/null; then
        return 0
    fi
    for _extra in $EXTRA_PORTS; do
        [ "$_extra" = "$_want" ] && return 0
    done
    return 1
}

if [ "$PORT_MODE" = 1 ]; then
    for _want in $PORTS; do
        if in_proxy_band "$_want"; then
            printf 'la-evict.sh: refusing --port %s: %s-%s is the LiteLLM proxy band live sessions use\n' \
                "$_want" "$PROXY_LO" "$PROXY_HI" >&2
            usage; exit 2
        fi
        if ! in_scan_range "$_want"; then
            printf 'la-evict.sh: --port %s is outside the local-model port range %s-%s (extra: %s)\n' \
                "$_want" "$PORT_START" "$PORT_MAX" "${EXTRA_PORTS:+$EXTRA_PORTS}" >&2
            printf '  refusing: a port outside this range is never evicted\n' >&2
            usage; exit 2
        fi
    done
fi

# --reset runs AFTER validation, so a bad flag combination cannot clear state as a
# side effect of a usage error.
if [ "$DO_RESET" = 1 ]; then
    rm -f "$STATE_FILE"
    echo "escalation state cleared"
    exit 0
fi

mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || true

# --- which ports are backing a live Claude Code session -----------------------
# A server with a session attached is the expensive one to evict, so it ranks last.
# ANTHROPIC_BASE_URL in the session's own environment is the authoritative signal
# (ps exposes it for same-user processes); the port named in the injected system
# prompt is a second, weaker signal kept as a fallback.
# OS-specific: on Linux we use /proc/environ; on macOS we use ps -Eww
attached_ports() {
    for cpid in $(pgrep -x claude 2>/dev/null); do
        if [ -r "/proc/$cpid/environ" ]; then
            # Linux: read from /proc/PID/environ (null-separated)
            tr '\0' '\n' < /proc/"$cpid"/environ 2>/dev/null | sed -n \
                -e 's|^ANTHROPIC_BASE_URL=http://localhost:\([0-9]*\).*|\1|p' \
                -e 's|^ANTHROPIC_BASE_URL=http://127\.0\.0\.1:\([0-9]*\).*|\1|p'
        else
            # macOS: use ps -Eww
            ps -Eww -p "$cpid" 2>/dev/null | tr ' ' '\n' \
                | sed -n \
                    -e 's|^ANTHROPIC_BASE_URL=http://localhost:\([0-9]*\).*|\1|p' \
                    -e 's|^ANTHROPIC_BASE_URL=http://127\.0\.0\.1:\([0-9]*\).*|\1|p'
        fi
        ps -o command= -p "$cpid" 2>/dev/null \
            | sed -n 's/.*served by a local server process on port \([0-9]*\).*/\1/p'
    done | sort -u
}

ATTACHED="$(attached_ports | tr '\n' ' ')"
is_attached() { case " $ATTACHED " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

# --- elapsed "[[DD-]HH:]MM:SS" -> seconds -------------------------------------
etime_to_s() {
    awk -v e="$1" 'BEGIN{
        d=0; n=split(e,a,"-"); if(n==2){d=a[1]; e=a[2]}
        m=split(e,b,":")
        if(m==3){print d*86400+b[1]*3600+b[2]*60+b[3]}
        else if(m==2){print d*86400+b[1]*60+b[2]}
        else{print 0}
    }'
}

# --- restart-loop heuristic ---------------------------------------------------
# Best-effort and never decisive on its own: a server that is both very young and has
# several startup banners in its recent log is almost certainly cycling rather than
# serving. Log naming has changed before, so every candidate log for the port is read
# and a missing log simply scores zero.
loop_score() {
    port="$1"; age="$2"
    [ "$age" -gt "$YOUNG_S" ] && { echo 0; return; }
    starts=0
    for lf in "$LOG_DIR"/*_"$port".log "$LOG_DIR"/*"$port"*.log; do
        [ -f "$lf" ] || continue
        n=$(tail -n 400 "$lf" 2>/dev/null | grep -c -i -E "uvicorn running|application startup|loading model|started server|Traceback|out of memory" 2>/dev/null)
        [ "$n" -gt "$starts" ] && starts="$n"
    done
    [ "$starts" -ge 3 ] && echo 2 && return
    [ "$starts" -ge 1 ] && echo 1 && return
    echo 0
}

# --- discover the servers -----------------------------------------------------
ROWS="$(mktemp -t laevict)"; trap 'rm -f "$ROWS"' EXIT

# OS detection for process inspection
if [[ -d /proc ]] && [[ -r /proc/1/stat ]]; then
    # Linux/WSL: use /proc for reliable process inspection
    OS_TYPE="linux"
else
    # macOS: use ps
    OS_TYPE="mac"
fi

for port in $(seq "$PORT_START" "$PORT_MAX") $EXTRA_PORTS; do
    # The proxy band is never scanned, so it can never be ranked, selected or
    # killed — even if a range override happens to include it.
    in_proxy_band "$port" && continue
    pid=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)
    [ -n "${pid:-}" ] || continue
    [ "$pid" -gt 1 ] 2>/dev/null || continue

    # OS-specific process inspection
    if [ "$OS_TYPE" = "linux" ]; then
        # Linux: use /proc for reliable inspection
        cmd=$(tr '\0' ' ' < /proc/"$pid"/cmdline 2>/dev/null | sed 's/\x0$//')
        rss_kb=$(awk '/VmRSS/{print $2}' /proc/"$pid"/status 2>/dev/null || echo 0)
        # etime from /proc/PID/stat field 22 (starttime) - complex, fallback to ps
        age=$(etime_to_s "$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')")
    else
        # macOS: use ps
        cmd=$(ps -o command= -p "$pid" 2>/dev/null)
        rss_kb=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' ')
        age=$(etime_to_s "$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')")
    fi
    [ -n "$cmd" ] || continue
    rss_kb=${rss_kb:-0}
    age=${age:-0}

    # Initialize band; the real value is set once attachment is known below.
    band=5

    case "$cmd" in
        *rapid-mlx*)      backend=rapid-mlx ;;
        *"omlx serve"*|*omlx.cli*) backend=omlx ;;
        *mlx_lm.server*)  backend=mlx-lm ;;
        *llama-server*)   backend=llama.cpp ;;
        *vllm-mlx*)       backend=vllm-mlx ;;
        *vllm*)           backend=vllm-cuda ;;     # CUDA vLLM
        *)                backend=unknown ;;
    esac

    if is_attached "$port"; then att=yes; else att=no; fi
    loop=$(loop_score "$port" "$age")

    # Attached genuinely sorts LAST, as the ranking has always claimed: the tier-1
    # "one server" pick must never be an attached one while an unattached one is
    # still available. band was a constant 5 for every row before, which made the
    # sort key meaningless and the comment untrue.
    if [ "$att" = yes ]; then band=9; else band=1; fi

    printf '%d|%012d|%012d|%s|%s|%s|%s|%s\n' \
        "$band" "$age" "$((99999999 - rss_kb/1024))" "$port" "$pid" "$backend" "$att" "$loop" >> "$ROWS"
done

if [ ! -s "$ROWS" ]; then
    # A --port request is a specific question and must not be answered by the
    # generic "nothing to evict": the caller asked for one port, so it gets exit 4.
    if [ "$PORT_MODE" = 1 ]; then
        echo "=== no model server on :${PORTS# } ==="
        echo "RESULT: nothing evicted — the requested port(s) hold no listener."
        exit 4
    fi
    echo "No local model servers are listening on ${PORT_START}-${PORT_MAX} ${EXTRA_PORTS}."
    echo "RESULT: nothing to evict — no model server is running."
    exit 0
fi

RANKED="$(sort -t'|' -k1,1n -k2,2n -k3,3n "$ROWS")"
COUNT=$(printf '%s\n' "$RANKED" | wc -l | tr -d ' ')
# Snapshot of what was listening before, so "freed" can be measured against the live
# system afterwards instead of trusting counters that cannot survive a pipe.
BEFORE_PORTS="$(printf '%s\n' "$RANKED" | awk -F'|' '{printf "%s ", $4}')"

human_gb() { awk -v k="$1" 'BEGIN{printf "%.1fGB", k/1048576}'; }

show_table() {
    printf '%-6s %-7s %-11s %-9s %-9s %-8s %s\n' PORT PID BACKEND RSS AGE ATTACHED SUSPECT
    printf '%s\n' "$RANKED" | while IFS='|' read -r _ age _ port pid backend att loop; do
        age=$((10#$age))   # stored zero-padded for sorting; display unpadded
        rss_kb=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' '); [ -n "${rss_kb:-}" ] || rss_kb=0
        case "$loop" in 2) s="cycling" ;; 1) s="log-noise" ;; *) s="-" ;; esac
        [ "$age" -le "$YOUNG_S" ] && [ "$s" = "-" ] && s="just-launched"
        printf '%-6s %-7s %-11s %-9s %-9s %-8s %s\n' \
            ":$port" "$pid" "$backend" "$(human_gb "$rss_kb")" "${age}s" "$att" "$s"
    done
}

echo "=== local model servers (eviction order, top first) ==="
show_table
echo
free_mb=$(vm_stat 2>/dev/null | awk '/Pages free/{gsub(/\./,"",$3); printf "%d", $3*16384/1048576}')
echo "Free RAM now: ${free_mb:-?}MB"

if [ "$STATUS_ONLY" = 1 ]; then
    echo "RESULT: status only — ${COUNT} server(s) up, nothing evicted."
    exit 0
fi

# --- escalation tier ----------------------------------------------------------
# The default is tier 0 (the orphan sweep). ONLY an argument-less run climbs the
# ladder, and only from state a bare run itself wrote: --port, --tier, --all and the
# modifier flags neither read nor write it, so a stray invocation can never push the
# next bare run onto a tier more dangerous than the one the operator reached.
now=$(date +%s)
tier=0
if [ -n "$FORCE_TIER" ]; then
    tier="$FORCE_TIER"
elif [ "$BARE_RUN" = 1 ] && [ -f "$STATE_FILE" ]; then
    last_epoch=$(awk 'END{print $1}' "$STATE_FILE" 2>/dev/null)
    last_tier=$(awk 'END{print $2}' "$STATE_FILE" 2>/dev/null)
    case "${last_epoch:-}" in ''|*[!0-9]*) last_epoch=0 ;; esac
    case "${last_tier:-}" in ''|*[!0-9]*) last_tier=0 ;; esac
    if [ $((now - last_epoch)) -le "$WINDOW_S" ]; then
        tier=$((last_tier + 1))
        echo "Repeat invocation inside ${WINDOW_S}s — escalating to tier ${tier}."
    fi
fi

# Which servers this run takes:
#   --port   exactly the named ports, in the order given (never tier-ranked)
#   --all    everything
#   tier 0   every unattached server — dead servers are safe to evict and there is
#            no reason to tier them, so they all go in one pass
#   tier >=1 ONE server under the existing ranking rules, clamped to the list
MISSING_PORTS=""
if [ "$PORT_MODE" = 1 ]; then
    targets=""
    _seen=""
    for _want in $PORTS; do
        # Repeated --port values are deduplicated: evicting the same pid twice only
        # printed a second, misleading "evicting" line.
        case " $_seen " in *" $_want "*) continue ;; esac
        _seen="$_seen $_want"
        _row="$(printf '%s\n' "$RANKED" | awk -F'|' -v p="$_want" '$4 == p {print; exit}')"
        if [ -n "$_row" ]; then
            if [ -z "$targets" ]; then targets="$_row"; else targets="$targets
$_row"; fi
        else
            MISSING_PORTS="$MISSING_PORTS $_want"
        fi
    done
elif [ "$KILL_ALL" = 1 ]; then
    targets="$RANKED"
elif [ "$tier" -eq 0 ]; then
    targets="$(printf '%s\n' "$RANKED" | awk -F'|' '$7 == "no"')"
else
    # ONE server per invocation, from tier 1 up. The target is the BEST remaining
    # candidate (row 1), not the Nth row: when tier 1 held back it stopped nothing,
    # so counting rows would silently evict a different server from the one the
    # operator was just told to override.
    targets="$(printf '%s\n' "$RANKED" | sed -n '1p')"
fi

# A first invocation must never take the backend out from under a live session. When
# the only candidate left is attached, stop and make the caller ask again: pressing the
# button a second time escalates past this guard, so the escalating-tier state is still
# recorded here even though nothing was stopped.
TOP_ATT="$(printf '%s\n' "$targets" | head -1 | cut -d'|' -f7)"
TOP_PORT="$(printf '%s\n' "$targets" | head -1 | cut -d'|' -f4)"
# Tier 1 — however it was reached, including an explicit `--tier 1` — holds back when
# the best candidate is attached. A forced tier used to bypass this guard, which made
# `--tier 1` mean something different from tier 1.
if [ "$tier" = 1 ] && [ "$TOP_ATT" = yes ] && [ "$KILL_ALL" != 1 ] \
   && [ "$PORT_MODE" != 1 ]; then
    if [ "$DRY_RUN" != 1 ] && [ "$BARE_RUN" = 1 ]; then
        printf '%s %s %s\n' "$now" "$tier" "guard" >> "$STATE_FILE"
    fi
    echo "=== held back ==="
    echo "  The best candidate (:$TOP_PORT) has a live Claude Code session attached, and no"
    echo "  unattached server is available to take instead. Stopping it would cut that"
    echo "  session's backend out from under it, so nothing was stopped."
    echo
    echo "RESULT: held back — the only candidate :$TOP_PORT is in use by a live session. Run again to override."
    exit 3
fi

# --- evict ---------------------------------------------------------------------
evict_one() {
    port="$1"; pid="$2"; backend="$3"; att="$4"
    # An unrecognised listener is refused outside the emergency tier: it may belong to
    # something this script does not know how to restart. From tier 2 on, leaving it
    # resident is the bigger risk, so the emergency tier takes it.
    if [ "$backend" = unknown ] && [ "$ALLOW_UNKNOWN" != 1 ] && [ "$tier" -lt 2 ]; then
        echo "  ⏭  :$port pid $pid — unrecognised listener, refusing without --allow-unknown"
        return 1
    fi
    # PID-reuse guard. The pid was captured during the scan; between then and now it
    # may have exited and been reused by an unrelated same-user process. kill -0
    # cannot detect that, so re-read the port's CURRENT owner and refuse unless it is
    # still this pid.
    _now_pid="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | head -1)"
    if [ -z "$_now_pid" ] || [ "$_now_pid" != "$pid" ]; then
        echo "  ⏭  :$port pid $pid no longer owns the port — refusing (already gone, or the pid was reused)"
        return 1
    fi
    if ! ps -o user= -p "$pid" 2>/dev/null | grep -qx "$(id -un)"; then
        echo "  ⏭  :$port pid $pid — not owned by $(id -un), refusing"
        return 1
    fi
    rss_kb=$(ps -o rss= -p "$pid" 2>/dev/null | tr -d ' '); [ -n "${rss_kb:-}" ] || rss_kb=0
    note=""; [ "$att" = yes ] && note=" (a live session is attached — it will lose its backend)"
    if [ "$DRY_RUN" = 1 ]; then
        echo "  [dry-run] would evict $backend on :$port (pid $pid, $(human_gb "$rss_kb"))$note"
        return 0
    fi
    echo "  → evicting $backend on :$port (pid $pid, $(human_gb "$rss_kb"))$note"
    kill -TERM "$pid" 2>/dev/null
    waited=0
    while [ "$waited" -lt "$TERM_WAIT" ]; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1; waited=$((waited + 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
        echo "    SIGTERM ignored after ${TERM_WAIT}s — sending SIGKILL"
        kill -9 "$pid" 2>/dev/null
        sleep 1
    fi
    if kill -0 "$pid" 2>/dev/null; then
        echo "    ⚠️  pid $pid still alive"
        return 1
    fi
    echo "    ✅ gone, ~$(human_gb "$rss_kb") released"
    EVICT_MB=$((EVICT_MB + rss_kb / 1024))
    EVICT_N=$((EVICT_N + 1))
    return 0
}

EVICT_MB=0; EVICT_N=0
if [ "$tier" -ge 2 ] && [ "$PORT_MODE" != 1 ] && [ "$KILL_ALL" != 1 ]; then
    echo "=== evicting (tier ${tier}) — EMERGENCY: attached and unrecognised servers are eligible ==="
else
    echo "=== evicting (tier ${tier}) ==="
fi
# An empty target list must not feed an empty line to the loop below: read would
# hand evict_one a set of empty fields and it would print a spurious "not owned by
# ... refusing" for a server that does not exist.
if [ -n "$targets" ]; then
    printf '%s\n' "$targets" | while IFS='|' read -r _ _ _ port pid backend att _; do
        evict_one "$port" "$pid" "$backend" "$att"
    done
fi

# The while-loop above runs in a subshell, so its counters cannot survive the pipe.
# Recount from the live system: only a port that was listening before and is not
# listening now counts as freed, so a refusal or a failed kill is never reported as
# a success.
sleep 1
still_up=0; freed=0
for port in $(seq "$PORT_START" "$PORT_MAX") $EXTRA_PORTS; do
    in_proxy_band "$port" && continue
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
        still_up=$((still_up + 1))
    else
        case " $BEFORE_PORTS " in *" $port "*) freed=$((freed + 1)) ;; esac
    fi
done
free_after=$(vm_stat 2>/dev/null | awk '/Pages free/{gsub(/\./,"",$3); printf "%d", $3*16384/1048576}')

# Only a BARE run advances the ladder. It counts as a press even when it stopped
# nothing: the ladder is meant to be three presses deep (sweep, one, emergency), so
# a machine where every listener is attached still escalates on the third press.
# What the BARE_RUN gate buys is that --port, --tier, --all and the modifier flags
# can never push the next bare run onto a tier the operator did not reach.
if [ "$DRY_RUN" != 1 ] && [ "$BARE_RUN" = 1 ]; then
    printf '%s %s %s\n' "$now" "$tier" "$(printf '%s' "$targets" | head -1 | cut -d'|' -f5)" >> "$STATE_FILE"
fi

# Tier 0 sweeps orphans; whatever is still attached is reported so the operator can
# see which ports were deliberately NOT taken and why.
if [ "$PORT_MODE" != 1 ] && [ "$tier" -le 1 ]; then
    _attached_left="$(printf '%s\n' "$RANKED" | awk -F'|' '$7 == "yes" {
        printf "  :%s pid %s %s\n", $4, $5, $6
    }')"
    if [ -n "$_attached_left" ]; then
        echo
        echo "=== still attached to a live session — NOT evicted ==="
        printf '%s\n' "$_attached_left"
        echo "  (a live Claude Code session is using these ports; run again to escalate)"
    fi
fi

echo
if [ "$DRY_RUN" = 1 ]; then
    echo "RESULT: dry run at tier ${tier} — nothing was stopped, ${still_up} server(s) still up."
    [ -z "$MISSING_PORTS" ] || echo "NOTE: no listener on :${MISSING_PORTS# }."
    exit 0
fi
echo "Free RAM after: ${free_after:-?}MB (was ${free_mb:-?}MB)   servers still up: ${still_up}"
if [ "$still_up" -gt 0 ]; then
    echo "RESULT: tier ${tier} done; ${still_up} server(s) still up, free RAM ${free_after:-?}MB. Run again for the next one."
else
    echo "RESULT: tier ${tier} done; no model servers left, free RAM ${free_after:-?}MB."
fi

# A --port run asks one specific question and must not end in the generic success
# line: if the caller asked for a port nothing was stopped on, it needs to know.
if [ "$PORT_MODE" = 1 ]; then
    if [ -n "$MISSING_PORTS" ]; then
        echo "REQUESTED BUT ABSENT: no model server on :${MISSING_PORTS# }."
        echo "RESULT: the requested port(s) that DID have a listener were evicted; :${MISSING_PORTS# } had nothing to stop."
        exit 4
    fi
    if [ "$freed" -eq 0 ]; then
        echo "RESULT: nothing was actually stopped — see the lines above for why each candidate was refused."
        exit 5
    fi
fi

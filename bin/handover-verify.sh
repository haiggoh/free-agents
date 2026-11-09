#!/usr/bin/env bash
# handover-verify — prove a launched session STARTED WORK, not that its port listens.
#
# C0b (v0.46.0, measured 2026-10-25): a launch's --verify used to check
# `lsof -sTCP:LISTEN` — true for an idle session, so it reported success on the
# failure. The verify that matters proves the FIRST PROMPT ARRIVED: a transcript
# for the new session's sessionId whose content contains the handoff path.
#
# Resolution: the sessionId comes from --session-id, or from the launcher's
# registry entry ~/.claude/sessions/<pid>.json (--pid). The transcript is found by
# globbing <projects-dir>/*/<sessionId>.jsonl — never by guessing the project
# directory from the launch cwd, which would read as NOT_VERIFIED for the wrong
# reason. Polls once a second until the timeout, then reports.
#
# Usage:
#   handover-verify.sh --expect SUBSTRING [--session-id ID | --pid PID]
#                      [--timeout S] [--sessions-dir D] [--projects-dir D]
#   handover-verify.sh --help
# Prints VERIFIED (exit 0) once the transcript exists AND contains the substring;
# NOT_VERIFIED (exit 1) when the timeout expires without that. Read-only.
set -u
usage() {
    cat <<'USAGE'
handover-verify.sh — prove a launched session received its first prompt.

Usage:
  handover-verify.sh --expect SUBSTRING [--session-id ID | --pid PID]
                     [--timeout S] [--sessions-dir D] [--projects-dir D]

  --expect SUBSTRING   text the new session's transcript must contain (the
                       handoff path is the usual choice). Required.
  --session-id ID      the session id to look up directly.
  --pid PID            resolve the session id from ~/.claude/sessions/<pid>.json
                       (the launcher's registry entry for the new process).
  --timeout S          seconds to poll before giving up (default 60).
  --sessions-dir D     registry directory override (tests; default ~/.claude/sessions).
  --projects-dir D     transcript root override (tests; default ~/.claude/projects).

Prints VERIFIED (exit 0) once the transcript exists AND contains the substring;
NOT_VERIFIED (exit 1) when the timeout expires without that. Read-only.
USAGE
}
expect=""; sid=""; pid=""; tmo=60; sdir=""; pdir=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)      usage; exit 0 ;;
        --expect)       [[ $# -ge 2 ]] || { echo 'handover-verify: --expect needs a substring' >&2; exit 2; }
                        expect="$2"; shift 2 ;;
        --session-id)   [[ $# -ge 2 ]] || { echo 'handover-verify: --session-id needs an id' >&2; exit 2; }
                        sid="$2"; shift 2 ;;
        --pid)          [[ $# -ge 2 ]] || { echo 'handover-verify: --pid needs a pid' >&2; exit 2; }
                        pid="$2"; shift 2 ;;
        --timeout)      [[ $# -ge 2 ]] || { echo 'handover-verify: --timeout needs seconds' >&2; exit 2; }
                        tmo="$2"; shift 2 ;;
        --sessions-dir) [[ $# -ge 2 ]] || { echo 'handover-verify: --sessions-dir needs a path' >&2; exit 2; }
                        sdir="$2"; shift 2 ;;
        --projects-dir) [[ $# -ge 2 ]] || { echo 'handover-verify: --projects-dir needs a path' >&2; exit 2; }
                        pdir="$2"; shift 2 ;;
        *)              echo "handover-verify: unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done
[[ -n "$expect" ]] || { echo 'handover-verify: --expect is required' >&2; exit 2; }
[[ -n "$sid" || -n "$pid" ]] || { echo 'handover-verify: one of --session-id / --pid is required' >&2; exit 2; }
sdir="${sdir:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/sessions}"
pdir="${pdir:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects}"
if [[ -z "$sid" ]]; then
    regf="$sdir/$pid.json"
    if [[ ! -r "$regf" ]]; then
        echo "NOT_VERIFIED: no registry entry for pid $pid ($regf)"
        exit 1
    fi
    sid="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("sessionId",""))' "$regf" 2>/dev/null || true)"
    if [[ -z "$sid" ]]; then
        echo "NOT_VERIFIED: the registry entry for pid $pid carries no sessionId"
        exit 1
    fi
    # 0.50.0 F5: the registry is keyed by pid — a RECYCLED pid must not verify another
    # session's transcript. Compare the recorded procStart with the live process.
    pstart="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("procStart",""))' "$regf" 2>/dev/null || true)"
    startedat="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("startedAt",""))' "$regf" 2>/dev/null || true)"
    if [[ -n "$pstart" || -n "$startedat" ]]; then
        # 2026-11-08 route-watcher Task 4: compare EPOCH SECONDS, not strings — the registry's
        # procStart is written in UTC while `ps lstart` prints LOCAL time, so a string compare
        # reports "recycled" for the very same instant (18:44:10 vs 19:44:10 on a CET box).
        # startedAt (epoch millis) is authoritative; procStart parsed as UTC is the fallback.
        # ±2 s tolerance for clock read jitter.
        check="$(python3 - "$pid" "$startedat" "$pstart" <<'PY' 2>/dev/null || true
import os, subprocess, sys, time
pid, startedat, pstart = sys.argv[1], sys.argv[2], sys.argv[3]
r = subprocess.run(["ps", "-o", "lstart=", "-p", pid], capture_output=True, text=True,
                   env={**os.environ, "LC_ALL": "C"})
live = " ".join(r.stdout.split())
if r.returncode != 0 or not live:
    print("NOT_RUNNING")
    raise SystemExit(0)
live_epoch = time.mktime(time.strptime(live, "%a %b %d %H:%M:%S %Y"))      # lstart is LOCAL
if startedat:
    reg_epoch = float(startedat) / 1000.0                                 # startedAt: epoch millis
elif pstart:
    reg_epoch = time.timegm(time.strptime(pstart, "%a %b %d %H:%M:%S %Y")) # procStart: UTC
else:
    raise SystemExit(0)
print("OK" if abs(live_epoch - reg_epoch) <= 2 else "RECYCLED")
PY
)"
        case "$check" in
            OK) ;;
            NOT_RUNNING)
                echo "NOT_VERIFIED: pid $pid is not running (registry procStart '$pstart')"
                exit 1 ;;
            RECYCLED)
                echo "NOT_VERIFIED: pid $pid was recycled (start epochs differ by more than 2s: registry ${startedat:-$pstart})"
                exit 1 ;;
            *)
                echo "NOT_VERIFIED: could not compare the start time of pid $pid"
                exit 1 ;;
        esac
    fi
fi
deadline=$(( $(date +%s) + tmo ))
while :; do
    for f in "$pdir"/*/"$sid.jsonl"; do
        if [[ -f "$f" ]] && /usr/bin/grep -q -- "$expect" "$f" 2>/dev/null; then
            echo "VERIFIED: $f contains the expected text"
            exit 0
        fi
    done
    [[ $(date +%s) -lt $deadline ]] || {
        echo "NOT_VERIFIED: no transcript for session $sid containing the expected text within ${tmo}s"
        exit 1
    }
    sleep 1
done

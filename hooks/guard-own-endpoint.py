#!/usr/bin/env python3
"""PreToolUse guard: refuse a Bash command that would kill this session's own endpoint.

WHY THIS EXISTS (measured incident, 2026-09-14/15)
--------------------------------------------------
Three consecutive sessions on the remote LiteLLM lane died with

    API Error: Connection refused - a firewall or proxy may be blocking it

In every case the last tool call before death was `pkill -f "litellm.*4141"` while
proxy-4141 was serving that same session's `/v1/messages` (proxy-4141.log shows
POST /v1/messages right up to `Shutting down`). The session killed its own API
mid-turn. Each replacement session then ran /resume-interrupted, read the tail,
reached the same pkill, and died the same way - a resume loop that burned four
sessions and left 56 unanswered prompts.

The system prompt ALREADY told those sessions not to do this, naming the exact
port ("Never kill, restart, or otherwise disturb processes on ports 4141-4151").
It rendered correctly and they did it anyway. Prose in a system prompt is not a
guard; a PreToolUse deny is. This hook is the mechanical half.

WHAT IT DOES
------------
Reads the PreToolUse payload on stdin. For a Bash tool call, it looks for a
process-killing command aimed at the port or process backing $ANTHROPIC_BASE_URL
and denies it with an explanation naming the port and the safe alternative.

Deliberately narrow, because a false deny is its own failure:
  * only Bash;
  * only when the endpoint is local (localhost/127.0.0.1) - a cloud endpoint
    cannot be killed from here, so there is nothing to protect;
  * only kill-family verbs (kill, pkill, killall, kill_a_port helpers, and
    launchctl/brew-services stops), not arbitrary mentions of the port. Reading
    a log, curling the endpoint, or grepping for the pid stays allowed.

Exit codes follow the PreToolUse contract: it emits a JSON decision on stdout
and exits 0. On any internal error it fails OPEN (allows) rather than wedging
the session - a guard that blocks everything when it breaks is worse than the
bug it prevents.

Environment:
  ANTHROPIC_BASE_URL        the endpoint to protect (required for a deny)
  LA_GUARD_EXTRA_PORTS      comma-separated extra ports to protect
  LA_GUARD_DISABLE=1        turn the guard off entirely
"""

import json
import os
import re
import sys

USAGE = """guard-own-endpoint.py - PreToolUse guard against killing your own API endpoint

Reads a PreToolUse hook payload as JSON on stdin and, for Bash commands, denies
any attempt to kill the process or port serving $ANTHROPIC_BASE_URL.

Usage:
  guard-own-endpoint.py              read a hook payload on stdin (normal use)
  guard-own-endpoint.py --help       show this help
  guard-own-endpoint.py --self-test  run built-in cases, print PASS/FAIL, exit 1 on failure

Environment:
  ANTHROPIC_BASE_URL     endpoint to protect; a non-local URL is never guarded
  LA_GUARD_EXTRA_PORTS   comma-separated additional ports to protect
  LA_GUARD_DISABLE=1     disable the guard (always allow)

Exits 0 and allows the call on any internal error: failing open is safer than
wedging a session.
"""

# Verbs that end a process. `kill` matches as a word so that "killall" and a
# bare "kill" both hit, but a filename like "killer.log" does not.
KILL_RE = re.compile(
    r"(?:^|[\s;&|(`])(?:sudo\s+)?"
    r"(?:pkill|killall|kill|fuser|lsof\s+[^|;]*-t[^|;]*\|\s*xargs\s+kill)\b",
    re.IGNORECASE,
)
# Service managers that stop a daemon without the word "kill".
SERVICE_STOP_RE = re.compile(
    r"(?:launchctl\s+(?:stop|unload|bootout)|brew\s+services\s+(?:stop|restart))\b",
    re.IGNORECASE,
)
# Our own supported stopper, which takes the proxy down deliberately.
REMOTE_STOP_RE = re.compile(r"remote-session(?:\.sh)?\b[^;|&]*--stop", re.IGNORECASE)

LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1")


def endpoint_port(url):
    """Return the port of a LOCAL endpoint, or None if it is not local/parseable."""
    if not url:
        return None
    m = re.match(r"^https?://([^/:]+|\[[^\]]+\])(?::(\d+))?", url.strip())
    if not m:
        return None
    host, port = m.group(1), m.group(2)
    if host.lower() not in LOCAL_HOSTS:
        return None
    if not port:
        return None
    return port


def protected_ports():
    ports = set()
    p = endpoint_port(os.environ.get("ANTHROPIC_BASE_URL", ""))
    if p:
        ports.add(p)
    for extra in (os.environ.get("LA_GUARD_EXTRA_PORTS") or "").split(","):
        extra = extra.strip()
        if extra.isdigit():
            ports.add(extra)
    return ports


def offending_port(command, ports):
    """Return the protected port this command would kill, or None.

    A command qualifies only if it BOTH uses a process-ending verb AND names a
    protected port (or the proxy config file carrying that port). Naming the
    port alone -- tailing its log, curling it -- is not enough.
    """
    if not command or not ports:
        return None
    kills = bool(KILL_RE.search(command)) or bool(SERVICE_STOP_RE.search(command))
    if not kills and not REMOTE_STOP_RE.search(command):
        return None
    for port in sorted(ports):
        # The port as a standalone number, or embedded in the runtime artifact
        # names we generate (proxy-4141.yaml / .pid / .log).
        if re.search(r"(?<!\d)" + re.escape(port) + r"(?!\d)", command):
            return port
    return None


def decide(payload):
    """Return (allow: bool, reason: str)."""
    if os.environ.get("LA_GUARD_DISABLE") == "1":
        return True, ""
    if payload.get("tool_name") != "Bash":
        return True, ""
    command = (payload.get("tool_input") or {}).get("command", "")
    ports = protected_ports()
    port = offending_port(command, ports)
    if not port:
        return True, ""
    return False, (
        f"BLOCKED: this command would kill the endpoint serving THIS session.\n\n"
        f"ANTHROPIC_BASE_URL points at port {port}, so stopping that process ends "
        f"the current turn with a misleading "
        f'"API Error: Connection refused - a firewall or proxy may be blocking it". '
        f"It looks like a network fault but is self-inflicted. This happened three "
        f"times on 2026-09-14/15 and cost four sessions.\n\n"
        f"Instead:\n"
        f"  - to apply a new config, start a SECOND proxy on a free port and switch "
        f"to it, rather than restarting this one;\n"
        f"  - to restart this endpoint, do it from a different session or an "
        f"independent terminal window;\n"
        f"  - if freeing port {port} really is required, stop and ask the user.\n\n"
        f"Override for this one command by prefixing LA_GUARD_DISABLE=1 only if you "
        f"are certain the port is NOT your own endpoint."
    )


SELF_TESTS = [
    # (env_base_url, command, expect_allowed)
    ("http://localhost:4141", 'pkill -f "litellm.*4141"', False),
    ("http://localhost:4141", "kill $(cat /tmp/x/proxy-4141.pid)", False),
    ("http://localhost:4141", "killall -9 litellm; sleep 2", True),  # no port named
    ("http://localhost:4141", "tail -50 /tmp/x/proxy-4141.log", True),
    ("http://localhost:4141", "curl -s http://localhost:4141/v1/models", True),
    ("http://localhost:4141", "remote-session.sh --stop 4141", False),
    ("http://localhost:8000", "pkill -f 'rapid-mlx.*8000'", False),
    ("http://localhost:8000", "pkill -f 'rapid-mlx.*8001'", True),  # a different port
    ("https://llmgw.joyia.p7s1.io", 'pkill -f "litellm.*4141"', True),  # cloud: nothing to kill
    ("", 'pkill -f "litellm.*4141"', True),  # unknown endpoint: fail open
    ("http://localhost:4141", "lsof -nP -iTCP:4141 -sTCP:LISTEN", True),  # inspection only
    ("http://localhost:4141", "grep -n 4141 notes.txt", True),
]


def self_test():
    saved = os.environ.get("ANTHROPIC_BASE_URL")
    failures = 0
    for base, cmd, expect_allow in SELF_TESTS:
        os.environ["ANTHROPIC_BASE_URL"] = base
        allow, _ = decide({"tool_name": "Bash", "tool_input": {"command": cmd}})
        ok = allow == expect_allow
        failures += 0 if ok else 1
        verdict = "allow" if allow else "DENY "
        print(f"{'PASS' if ok else 'FAIL'}  [{verdict}] base={base or '<unset>'!s:34} {cmd}")
    # A non-Bash tool is never guarded.
    os.environ["ANTHROPIC_BASE_URL"] = "http://localhost:4141"
    allow, _ = decide({"tool_name": "Read", "tool_input": {"file_path": "/x/4141"}})
    print(f"{'PASS' if allow else 'FAIL'}  [allow] non-Bash tool is not guarded")
    failures += 0 if allow else 1
    if saved is None:
        os.environ.pop("ANTHROPIC_BASE_URL", None)
    else:
        os.environ["ANTHROPIC_BASE_URL"] = saved
    print(f"\n{len(SELF_TESTS) + 1 - failures} passed, {failures} failed")
    return 1 if failures else 0


def main(argv):
    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    if "--self-test" in argv:
        return self_test()
    unknown = [a for a in argv if a.startswith("-")]
    if unknown:
        sys.stderr.write(f"guard-own-endpoint.py: unknown option {unknown[0]}\n")
        sys.stderr.write("Try --help.\n")
        return 2
    try:
        payload = json.load(sys.stdin)
        allow, reason = decide(payload)
    except Exception:
        # Fail OPEN: never wedge a session because the guard itself broke.
        return 0
    if allow:
        return 0
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

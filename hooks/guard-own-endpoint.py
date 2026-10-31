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
  LA_GUARD_ROSTER           remote roster to read aliases from (default: the
                            plugin's config/remote-agents.sh)
  LA_GUARD_DISABLE=1        turn the guard off entirely
"""

import json
import os
import re
import sys


def lane_mark() -> str:
    """🦾 local / 📡 remote lane mark for the credit-moment prefix (fail-safe)."""
    try:
        bin_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin")
        if bin_dir not in sys.path:
            sys.path.insert(0, bin_dir)
        from emoji_constants import lane_mark as _lm
        return _lm(os.environ)
    except Exception:
        return "📡"

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
  LA_GUARD_ROSTER        remote roster whose aliases count as launches (default:
                         config/remote-agents.sh beside this hook; unreadable =
                         every alias-shaped word counts)
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
# C0 (v0.46.0): an agent-side LAUNCH of a remote session. The alias position is what is
# detected, so every launch route matches (direct, sudo-in-terminal.sh --command,
# osascript, open -a Terminal). The user's own picker/shell launches never pass through
# this hook, so they are unaffected with no env plumbing.
LAUNCH_RE = re.compile(r"\bremote-session(?:\.sh)?\s+(\S+)", re.IGNORECASE)
# Subcommands that do NOT start a session: they list, verify, dry-run, or stop.
LAUNCH_PASS_SUBCOMMANDS = ("--list", "--help", "-h", "--dry-run", "--verify",
                           "--models", "--inventory", "--stop", "--swap-model")
# A word after the script name that is a FILE, not an alias: the script was named as an
# argument (git show/diff, cat), not run. Aliases have no path separators and no file
# extension (gemini-3.8-flash has a dot, so the extension list stays explicit).
PATH_ARG_RE = re.compile(r"[/:]|\.(?:py|sh|md|json|ya?ml|txt|log|toml|cfg)\b", re.IGNORECASE)
# Names remote-session.sh's _entry_for accepts outside the roster. Any other word is an
# unknown alias: the launcher exits 2 before starting anything, so prose that merely
# MENTIONS the script ("remote-session.sh as a file") cannot produce an idle session.
EXTRA_ALIASES = {"github", "github-models", "cerebras-legacy"}
ROSTER_ENTRY_RE = re.compile(r'^\s*"([^"|\s]+)\|', re.MULTILINE)


def roster_aliases():
    """Aliases from the generated remote roster, or None when it cannot be read (the
    caller then treats every alias-shaped word as a launch: stricter, never looser)."""
    path = os.environ.get("LA_GUARD_ROSTER") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "config", "remote-agents.sh")
    try:
        with open(path, encoding="utf-8") as f:
            found = set(ROSTER_ENTRY_RE.findall(f.read()))
    except OSError:
        return None
    return (found | EXTRA_ALIASES) if found else None

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


def mask_quoted(command):
    """Mask the CONTENTS of quoted regions, keeping the quote chars themselves.

    Verb regexes run on the result, so a kill/stop word inside a quoted argument of an
    UNRELATED command (prose like "never ... remote-session.sh --stop", an echo) does
    not count — false positive measured 2026-10-31 on a waypoints.py --point string.
    The port search still runs on the RAW command: `pkill -f "litellm.*4141"` carries
    the port inside quotes and must keep denying. Launch detection likewise stays on
    the raw command (a launch wrapped in sudo-in-terminal.sh --command "..." is real).
    """
    out = []
    quote = None
    i = 0
    while i < len(command):
        c = command[i]
        if quote:
            if c == "\\" and i + 1 < len(command):
                out.append("\x00\x00")
                i += 2
                continue
            out.append(c if c == quote else "\x00")
            if c == quote:
                quote = None
            i += 1
        else:
            if c in "\"'":
                quote = c
            out.append(c)
            i += 1
    return "".join(out)


def offending_port(command, ports):
    """Return the protected port this command would kill, or None.

    A command qualifies only if it BOTH uses a process-ending verb at command
    position (quoted contents masked) AND names a protected port anywhere in the
    raw command (the port may legitimately sit inside a quoted pkill pattern).
    Naming the port alone -- tailing its log, curling it -- is not enough.
    """
    if not command or not ports:
        return None
    code = mask_quoted(command)
    kills = bool(KILL_RE.search(code)) or bool(SERVICE_STOP_RE.search(code))
    if not kills and not REMOTE_STOP_RE.search(code):
        return None
    for port in sorted(ports):
        # The port as a standalone number, or embedded in the runtime artifact
        # names we generate (proxy-4141.yaml / .pid / .log).
        if re.search(r"(?<!\d)" + re.escape(port) + r"(?!\d)", command):
            return port
    return None


def bare_word(word):
    """The word without shell quoting around it: `"alias"` / `alias\\"'` -> `alias`."""
    return re.split(r"[\"'\\;|&)]", word.lstrip("\"'\\"))[0]


def launch_without_prompt(command):
    """True for an agent Bash command that would start a remote session without carrying
    its first prompt. The alias position is what is detected, so any launch route matches
    (direct, wrapped in sudo-in-terminal.sh --command, osascript, open -a Terminal). The
    documented non-launching subcommands pass, as does any --prompt/--prompt-file, and
    a word that is not a roster alias (a file path, prose)."""
    known = None
    for m in LAUNCH_RE.finditer(command):
        first = m.group(1)
        if first.startswith("-"):
            for sub in LAUNCH_PASS_SUBCOMMANDS:
                if first.lower().startswith(sub):
                    break
            else:
                continue  # an unknown flag-first invocation: narrow guard, let it pass
        elif PATH_ARG_RE.search(first):
            continue  # the script named as a file among other files, not launched
        elif (known := known or roster_aliases()) is not None and \
                bare_word(first) not in known:
            continue  # not an alias: the launcher would refuse it before starting
        elif not re.search(r"--prompt(?:-file)?\b", command):
            return True  # an alias position and no prompt flag anywhere: an idle launch
    return False


LAUNCH_BLOCK_MESSAGE = (
    "BLOCKED: this launch starts a remote session WITHOUT its first prompt.\n\n"
    "A launch with no initial prompt yields an IDLE session: the launch's verify\n"
    "reported success while nothing was working (measured twice on 2026-10-25 —\n"
    "the Step 5 launch, then the GLM launch that motivated handoff item C0).\n\n"
    "Instead:\n"
    "  - pass the handoff:  remote-session.sh <alias> --prompt-file <handoff.md>\n"
    "    (the file must exist and be non-empty; an empty one is refused too);\n"
    "  - or:  remote-session.sh <alias> --prompt 'first instruction';\n"
    "  - --list/--help/--dry-run/--verify/--models/--inventory/--stop/--swap-model\n"
    "    stay allowed: none of them starts a session.\n"
)


def decide(payload):
    """Return (allow: bool, reason: str)."""
    if os.environ.get("LA_GUARD_DISABLE") == "1":
        return True, ""
    if payload.get("tool_name") != "Bash":
        return True, ""
    command = (payload.get("tool_input") or {}).get("command", "")
    ports = protected_ports()
    port = offending_port(command, ports)
    if port:
        return False, (
            f"{lane_mark()} free-agents: BLOCKED: this command would kill the endpoint serving THIS session.\n\n"
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
    if launch_without_prompt(command):
        return False, f"{lane_mark()} free-agents: {LAUNCH_BLOCK_MESSAGE}"
    return True, ""


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
    # C0 (v0.46.0): an agent-side launch WITHOUT its first prompt starts an idle session
    # (measured twice on 2026-10-25 — the Step 5 launch, then this GLM launch). The user's
    # own picker/shell launches never pass through this hook, so they are unaffected.
    ("http://localhost:4141", "remote-session.sh nvidia-glm53-full-thinking", False),
    ("http://localhost:4141",
     'sudo-in-terminal.sh --command "remote-session.sh nvidia-glm53-thinking --effort high"', False),
    ("http://localhost:4141", "open -a Terminal remote-session.sh nvidia-glm53-thinking", False),
    ("http://localhost:4141",
     "osascript -e 'do script \"remote-session.sh nvidia-glm53-thinking\"'", False),
    ("http://localhost:4141",
     "remote-session.sh nvidia-glm53-thinking --prompt-file ~/.claude/plans/handoff.md", True),
    ("http://localhost:4141",
     "remote-session.sh nvidia-glm53-thinking --prompt 'read the handoff first'", True),
    ("http://localhost:4141", "remote-session.sh --list", True),
    ("http://localhost:4141", "remote-session.sh --help", True),
    ("http://localhost:4141", "remote-session.sh --dry-run glm-thinking", True),
    ("http://localhost:4141", "remote-session.sh --verify glm-thinking", True),
    ("http://localhost:4141", "remote-session.sh --models glm-thinking", True),
    ("http://localhost:4141", "remote-session.sh --inventory", True),
    ("http://localhost:4141", "remote-session.sh --stop 4145", True),  # not this session's port
    ("http://localhost:4141", "remote-session.sh --swap-model 4145 other-alias", True),
    # The script NAMED as a file, not run: the word after it is a path, not an alias
    # (false positive measured 2026-10-27 on a read-only `git show ... -- <paths>`).
    ("http://localhost:4141",
     "git show 7087cf6 -- bin/remote-session.sh tests/test_swap_model.py", True),
    ("http://localhost:4141",
     'git show "origin/main:bin/remote-session.sh" "origin/main:tests/x.py"', True),
    ("http://localhost:4141", "cat bin/remote-session.sh README.md", True),
    ("http://localhost:4141", "git diff bin/remote-session.sh hooks/guard.py", True),
    # ...while a real launch by path, or with a dotted alias, is still caught.
    ("http://localhost:4141",
     "$HOME/x/bin/remote-session.sh gemini-3.8-flash", False),
    ("http://localhost:4141", "cd /tmp && bin/remote-session.sh nvidia-glm53-thinking", False),
    # Prose that MENTIONS the script (a commit message, an echo): the next word is not a
    # roster alias, and an unknown alias exits 2 before anything starts, so it cannot
    # produce an idle session (false positive measured 2026-10-27 on a commit message).
    ("http://localhost:4141",
     'git commit -m "fix: naming remote-session.sh as a file is not a launch"', True),
    ("http://localhost:4141", "echo 'then remote-session.sh launches it'", True),
    # A QUOTED alias is still a launch (and must not crash the check into failing open).
    ("http://localhost:4141", 'remote-session.sh "nvidia-glm53-thinking"', False),
    # FP measured 2026-10-31: the rule TEXT inside a quoted argument of an UNRELATED
    # command is not a kill/stop. Verb regexes must see only command-position words,
    # so quoted contents are masked before matching (the port search stays on the raw
    # command: pkill -f "litellm.*4141" carries the port inside quotes and must still deny).
    ("http://localhost:4141",
     'waypoints.py add --point "never touch ports 4141-4151 or remote-session.sh --stop"', True),
    ("http://localhost:4141", 'echo "pkill -f 4141"', True),
    ("http://localhost:4141", "echo 'remote-session.sh --stop 4141'", True),
    ("http://localhost:4141", "remote-session.sh --stop 4141", False),  # unquoted: still denied
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

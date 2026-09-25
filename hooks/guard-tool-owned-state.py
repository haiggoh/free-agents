#!/usr/bin/env python3
"""PreToolUse guard: refuse hand-edits to Claude Code's tool-owned plugin state.

WHY THIS EXISTS (measured incident, 2026-09-25)
-----------------------------------------------
A remote free-API session pushed a plugin change without a version bump. Because
the install path is keyed on the version (~/.claude/plugins/cache/<owner>/<name>/<version>/),
`claude plugin update` had nowhere new to land and left the old copy in place. The
session misread that as "the sandbox blocks the plugin cache". It then rewrote
~/.claude/plugins/installed_plugins.json with the Write tool, using a file rebuilt from
partial `grep -A 10` views. Everything it had not seen was dropped (86 -> 30 entries). Its
"restore" was a second Write of the same reconstruction. The CLI later re-filled the file
from disk, so it LOOKED healthy (valid JSON, plausible count) while recording stale
versions, resurrected uninstalled plugins and lost install history.

The right move was never an edit: bump the version in the source repo, ship it, then
`claude plugin update` / `get-haiggoh apply`. Prose rules said as much and were not
followed; a PreToolUse deny is the mechanical half.

WHAT IT DOES
------------
Protected (under $CLAUDE_CONFIG_DIR or ~/.claude):
  plugins/installed_plugins.json, plugins/known_marketplaces.json,
  plugins/cache/**, plugins/marketplaces/**

  * Write / Edit / MultiEdit / NotebookEdit whose target resolves into a protected path -> deny.
  * Bash whose command names a protected path AND contains a write-shaped verb
    (redirect, cp, mv, rm, tee, sed -i, python open(...,'w'), json.dump, ...) -> deny.
  * Reads stay allowed (cat, grep, ls, json.load). A command that is ENTIRELY
    `claude plugin ...` or a get-haiggoh invocation is allowed - those are the owners.

Exit codes follow the PreToolUse contract: a JSON decision on stdout, exit 0. On any
internal error it fails OPEN (allows) - a guard that wedges every tool call when it
breaks is worse than the bug it prevents.

Environment:
  CLAUDE_CONFIG_DIR           config root to protect (default ~/.claude)
  LA_STATE_GUARD_DISABLE=1    turn the guard off (a deliberate, human-driven repair)
"""

import json
import os
import re
import sys

USAGE = """guard-tool-owned-state.py - PreToolUse guard against hand-editing plugin state

Reads a PreToolUse hook payload as JSON on stdin and denies Write/Edit/Bash calls that
would modify installed_plugins.json, known_marketplaces.json, plugins/cache or
plugins/marketplaces. Those are owned by `claude plugin` (and get-haiggoh).

Usage:
  guard-tool-owned-state.py              read a hook payload on stdin (normal use)
  guard-tool-owned-state.py --help       show this help
  guard-tool-owned-state.py --self-test  run built-in cases, print PASS/FAIL, exit 1 on failure

Environment:
  CLAUDE_CONFIG_DIR           config root to protect (default ~/.claude)
  LA_STATE_GUARD_DISABLE=1    turn the guard off
"""

FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Matched against the raw command text, so ~, $HOME and absolute spellings all hit.
PROTECTED_IN_CMD = re.compile(
    r"plugins/(installed_plugins\.json|known_marketplaces\.json|cache(/|\b)|marketplaces(/|\b))"
)
WRITE_SHAPED = re.compile(
    r"(>|\btee\b|\bcp\b|\bmv\b|\brm\b|\brsync\b|\bln\b|\bsed\s+(-\w*\s+)*-i|\bperl\s+-\w*i"
    r"|\btruncate\b|\bchmod\b|\bchown\b|\binstall\b|\bgit\s+(checkout|restore|reset|clean)\b"
    r"|open\([^)]*['\"][wax+]|\.write_text\(|\.write\(|json\.dump\(|shutil\.|os\.(remove|unlink|rename|replace))"
)
# The owners. Only allowed when they are the WHOLE command, so `claude plugin list; cp x ...` fails.
SANCTIONED = re.compile(
    r"^\s*(claude\s+plugin\b|\S*get-haiggoh(\.py)?\s|\S*python3?\s+\S*get-haiggoh\.py\b)"
)
CHAINING = re.compile(r"[;&|`\n]|\$\(")
# Redirects that write nowhere protected: fd dups (2>&1) and /dev/null. Stripped before the
# write check so `cat installed_plugins.json 2>/dev/null` stays a read.
HARMLESS_REDIRECT = re.compile(r"\d*>>?\s*(&\d+|/dev/null)")

REASON = (
    "Blocked by free-agents guard-tool-owned-state: {what} is Claude Code's tool-owned plugin "
    "state and is written only by `claude plugin ...` (or get-haiggoh). Never hand-edit it: a "
    "rewrite from a partial view silently drops entries (incident 2026-09-25). If an update did "
    "not land, the usual cause is a MISSING VERSION BUMP - bump it in the source repo, commit, "
    "push/merge/tag, then run `get-haiggoh apply` or `claude plugin update <name>` and confirm "
    "the recorded version moved. A sandbox refusal here is a sign the approach is wrong, not an "
    "obstacle to route around. If the registry is genuinely corrupt, stop and ask the user."
)


def config_root():
    return os.path.realpath(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))


def protected_file(path):
    """True if an absolute/~ file path falls inside protected plugin state."""
    if not path:
        return False
    p = os.path.realpath(os.path.expanduser(path))
    plugins = os.path.join(config_root(), "plugins")
    if p in (os.path.join(plugins, "installed_plugins.json"),
             os.path.join(plugins, "known_marketplaces.json")):
        return True
    for sub in ("cache", "marketplaces"):
        d = os.path.join(plugins, sub)
        if p == d or p.startswith(d + os.sep):
            return True
    return False


def bash_writes_state(cmd):
    if not PROTECTED_IN_CMD.search(cmd):
        return False
    if SANCTIONED.match(cmd) and not CHAINING.search(cmd):
        return False
    return bool(WRITE_SHAPED.search(HARMLESS_REDIRECT.sub(" ", cmd)))


def decide(payload):
    """Return (allow, reason)."""
    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    if tool in FILE_TOOLS:
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if protected_file(path):
            return False, REASON.format(what=path)
        return True, ""
    if tool == "Bash":
        cmd = ti.get("command") or ""
        if bash_writes_state(cmd):
            return False, REASON.format(what="this command's target")
        return True, ""
    return True, ""


# (tool, input, expect_allow). The first four are the incident's literal shapes.
SELF_TESTS = [
    ("Write", {"file_path": "~/.claude/plugins/installed_plugins.json", "content": "{}"}, False),
    ("Bash", {"command": "! cp /tmp/installed_plugins.json ~/.claude/plugins/installed_plugins.json"}, False),
    ("Bash", {"command": "cat ~/.claude/plugins/installed_plugins.json | python -c \"import json,sys; "
                         "d=json.load(sys.stdin); json.dump(d, open('/tmp/x','w'))\" "
                         "&& cp /tmp/x ~/.claude/plugins/installed_plugins.json"}, False),
    ("Bash", {"command": "rm -rf ~/.claude/plugins/cache/haiggoh/audit-loose-ends/0.8.0"}, False),
    ("Edit", {"file_path": "~/.claude/plugins/cache/haiggoh/x/1.0.0/scripts/a.py"}, False),
    ("Edit", {"file_path": "~/.claude/plugins/known_marketplaces.json"}, False),
    ("Bash", {"command": "echo {} > $HOME/.claude/plugins/known_marketplaces.json"}, False),
    ("Bash", {"command": "sed -i '' s/0.8.0/0.8.1/ ~/.claude/plugins/installed_plugins.json"}, False),
    ("Bash", {"command": "claude plugin list; cp x ~/.claude/plugins/installed_plugins.json"}, False),
    ("Bash", {"command": "cat ~/.claude/plugins/installed_plugins.json | grep -A 10 audit"}, True),
    ("Bash", {"command": "python3 -c \"import json; print(len(json.load(open("
                         "'/Users/u/.claude/plugins/installed_plugins.json'))['plugins']))\""}, True),
    ("Bash", {"command": "ls -la ~/.claude/plugins/cache/haiggoh"}, True),
    ("Bash", {"command": "cat ~/.claude/plugins/installed_plugins.json 2>/dev/null | head"}, True),
    ("Bash", {"command": "grep -r version ~/.claude/plugins/cache/haiggoh 2>&1 | head"}, True),
    ("Bash", {"command": "cat ~/.claude/plugins/installed_plugins.json 2>&1 > ~/.claude/plugins/installed_plugins.json"}, False),
    ("Bash", {"command": "claude plugin update audit-loose-ends@haiggoh"}, True),
    ("Bash", {"command": "python3 ~/ClaudeWorkspace/get-haiggoh/bin/get-haiggoh.py apply --only x"}, True),
    ("Write", {"file_path": "~/ClaudeWorkspace/audit-loose-ends/.claude-plugin/plugin.json"}, True),
    ("Read", {"file_path": "~/.claude/plugins/installed_plugins.json"}, True),
]


def self_test():
    failures = 0
    for tool, ti, expect_allow in SELF_TESTS:
        allow, _ = decide({"tool_name": tool, "tool_input": ti})
        ok = allow == expect_allow
        failures += 0 if ok else 1
        shown = ti.get("command") or ti.get("file_path")
        print(f"{'PASS' if ok else 'FAIL'}  [{'allow' if allow else 'DENY '}] {tool:5} {shown}")
    print(f"\n{len(SELF_TESTS) - failures} passed, {failures} failed")
    return 1 if failures else 0


def main(argv):
    if "--help" in argv or "-h" in argv:
        print(USAGE)
        return 0
    if "--self-test" in argv:
        return self_test()
    unknown = [a for a in argv if a.startswith("-")]
    if unknown:
        sys.stderr.write(f"guard-tool-owned-state.py: unknown option {unknown[0]}\nTry --help.\n")
        return 2
    if os.environ.get("LA_STATE_GUARD_DISABLE") == "1":
        return 0
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

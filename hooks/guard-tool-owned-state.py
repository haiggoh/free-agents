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
  * Bash: the command is split into simple commands (quote-aware), and each is judged by its
    WRITE TARGET - a redirect target, the destination of cp/install/rsync/ln, every path of
    rm/mv/tee/chmod/..., the files of sed -i, git checkout/restore/reset in a protected repo, or an
    inline `python -c` that both names and opens protected state for writing. Relative paths are
    resolved against any `cd` earlier in the same command. Merely NAMING the cache - running a
    cached script, reading or copying out of it - is allowed (0.19.7; 0.19.5-0.19.6 denied any
    command containing a protected path and a write verb anywhere, which blocked harmless work).
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
import shlex
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


# Verbs whose DESTINATION is the last non-option argument (or `-t DIR`).
DEST_VERBS = {"cp", "install", "rsync", "ln", "ditto"}
# Verbs that modify EVERY path argument they are given. `mv` is here, not above: moving a file
# OUT of the cache removes it from the cache, so its sources count too.
MODIFY_VERBS = {"rm", "rmdir", "unlink", "mv", "truncate", "chmod", "chown", "touch", "mkdir", "tee"}
INPLACE_VERBS = {"sed", "perl", "ruby"}          # only with -i
INLINE_INTERPRETERS = {"python", "python3", "perl", "ruby", "node", "sh", "bash", "zsh"}
PREFIX_WORDS = {"!", "sudo", "command", "builtin", "exec", "nohup", "time", "env"}
OPERATORS = {";", "&&", "||", "|", "&", "\n", "(", ")", "|&"}
INLINE_WRITE = re.compile(
    r"open\([^)]*['\"][wax+]|\.write_text\(|\.write\(|json\.dump\(|shutil\.|os\.(remove|unlink|rename|replace)"
    r"|>|\bcp\b|\bmv\b|\brm\b|\btee\b|-i\b"
)


def _protected(tok, cwd=""):
    """A token names protected state: directly, or as a relative path resolved against a cd'd dir."""
    if PROTECTED_IN_CMD.search(tok):
        return True
    if cwd and tok and not tok.startswith(("/", "~", "$")):
        return bool(PROTECTED_IN_CMD.search(cwd.rstrip("/") + "/" + tok))
    return False


def _segments(cmd):
    """Split into simple commands, quote-aware. Returns None when the text cannot be tokenised."""
    try:
        lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=";&|()<>")
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return None
    segs, cur = [], []
    for t in toks:
        if t in OPERATORS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def _segment_writes(seg, cwd):
    """True if this one simple command writes protected state. Also returns the new cwd."""
    # Redirect targets: `> file`, `>> file`, `2> file` (shlex splits the operator off).
    words = []
    i = 0
    while i < len(seg):
        t = seg[i]
        if t in (">", ">>", ">|") or re.fullmatch(r"\d*>>?", t):
            target = seg[i + 1] if i + 1 < len(seg) else ""
            if target.startswith("&") or target == "/dev/null":
                i += 2
                continue
            if _protected(target, cwd):
                return True, cwd
            i += 2
            continue
        if t in ("<", "<<", "<<<"):
            i += 2
            continue
        words.append(t)
        i += 1
    # Strip prefixes and VAR=value assignments to find the real verb.
    while words and (words[0] in PREFIX_WORDS or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0])):
        words = words[1:]
    if not words:
        return False, cwd
    verb, args = os.path.basename(words[0]), words[1:]
    line = " ".join(words)
    if SANCTIONED.match(line):
        return False, cwd
    if verb == "cd":
        # Track the directory as text; only the "plugins/..." suffix matters for the check.
        tgt = args[0] if args else ""
        if not tgt or tgt.startswith(("/", "~", "$")):
            return False, tgt
        return False, (cwd.rstrip("/") + "/" + tgt) if cwd else tgt
    paths = [a for a in args if not a.startswith("-")]

    def hits(cands):
        return any(_protected(a, cwd) for a in cands)

    if verb in DEST_VERBS:
        if "-t" in args and args.index("-t") + 1 < len(args):
            return hits([args[args.index("-t") + 1]]), cwd
        return (hits(paths[-1:]) if len(paths) >= 2 else False), cwd
    if verb in MODIFY_VERBS:
        return hits(paths), cwd
    if verb in INPLACE_VERBS and any(re.fullmatch(r"-\w*i\w*", a) or a.startswith("-i") for a in args):
        return hits(paths), cwd
    if verb == "git" and any(a in ("checkout", "restore", "reset", "clean", "rm", "mv") for a in args):
        return hits(args), cwd
    if verb in INLINE_INTERPRETERS and any(a in ("-c", "-e") for a in args):
        code = " ".join(args)
        return bool(_protected(code) and INLINE_WRITE.search(code)), cwd
    return False, cwd


# Cheap pre-filter: a command can only touch plugin state if it names it, or names a directory
# ABOVE it that a later relative path could reach (`cd ~/.claude/plugins && rm -rf cache/x`).
MENTIONS_STATE = re.compile(r"\.claude\b|plugins\b|installed_plugins|known_marketplaces|CLAUDE_CONFIG_DIR")


def bash_writes_state(cmd):
    if not MENTIONS_STATE.search(cmd):
        return False
    segs = _segments(cmd)
    if segs is None:
        # Untokenisable (unbalanced quotes): fall back to the conservative whole-text check.
        return bool(WRITE_SHAPED.search(HARMLESS_REDIRECT.sub(" ", cmd)))
    cwd = ""
    for seg in segs:
        writes, cwd = _segment_writes(seg, cwd)
        if writes:
            return True
    return False


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
    # 0.19.7: false positives measured live. Naming the cache is not writing it.
    ("Bash", {"command": "python3 ~/.claude/plugins/cache/o/p/1.0/scripts/scan.py > /tmp/out.txt"}, True),
    ("Bash", {"command": "~/.claude/plugins/cache/o/p/1.0/scripts/redact.py f.txt; rm /tmp/x"}, True),
    ("Bash", {"command": "cp ~/.claude/plugins/cache/o/p/1.0/a.py ~/work/a.py"}, True),
    ("Bash", {"command": "~/.claude/plugins/cache/o/p/1.0/audit.py --dir ~/m 2>&1 | tail -1; rm -f /tmp/p.py"}, True),
    ("Bash", {"command": "diff ~/.claude/plugins/installed_plugins.json /tmp/x > /tmp/d.txt"}, True),
    ("Bash", {"command": "echo 'mv is not run here: ~/.claude/plugins/cache/x'"}, True),
    # ...while the real write shapes stay denied, including ones hidden behind chaining or cd.
    ("Bash", {"command": "cp ~/work/a.py ~/.claude/plugins/cache/o/p/1.0/a.py"}, False),
    ("Bash", {"command": "cp -t ~/.claude/plugins/cache/o/p/1.0 ~/work/a.py"}, False),
    ("Bash", {"command": "mv ~/.claude/plugins/cache/o/p/1.0 /tmp/gone"}, False),
    ("Bash", {"command": "ls /tmp; tee ~/.claude/plugins/installed_plugins.json < /tmp/x"}, False),
    ("Bash", {"command": "cd ~/.claude/plugins/cache/o/p/1.0 && echo hi > a.py"}, False),
    ("Bash", {"command": "cd ~/.claude/plugins && rm -rf cache/o"}, False),
    ("Bash", {"command": "cd ~/.claude && cp /tmp/x plugins/installed_plugins.json"}, False),
    ("Bash", {"command": "cd ~/.claude/plugins && cat installed_plugins.json > /tmp/copy.json"}, True),
    ("Bash", {"command": "python3 -c \"open('/Users/u/.claude/plugins/installed_plugins.json','w').write('{}')\""}, False),
    ("Bash", {"command": "sudo rm -rf ~/.claude/plugins/cache/o"}, False),
    ("Bash", {"command": "git -C ~/.claude/plugins/marketplaces/h checkout main"}, False),
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

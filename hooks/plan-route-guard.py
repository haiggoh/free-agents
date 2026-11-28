#!/usr/bin/env python3
"""plan-route-guard — PreToolUse guard: a PAID session cannot write a plan file whose tasks
lack a route tag.

Plan file: a .md under any docs/superpowers/plans/ or under $HOME/.claude/plans/ (any depth)
with >= 2 headings matching ^#{2,4} (Task|Step|Phase). Tags: free:dispatch | free:session |
free:handover | cloud:<checkable reason>, optional /<role> suffix; legacy local:/remote: count
as free:. A plan with zero free: tasks needs a `Routing:` line whose text holds no invalid
reason. Invalid cloud reasons (case-insensitive substring): tool use, tool-use, tools,
too hard, too complex, local is slow, slow, not delegatable.

The guard fires only on a paid session (ANTHROPIC_BASE_URL not localhost) with plan_check ON
(bin/cloud_session_env.py, FA_PLAN_CHECK; default ON). Every internal error FAILS OPEN (one
stderr line, exit 0) — the guard never blocks on its own failure.

Hook I/O: PreToolUse JSON on stdin; deny = the reason on stderr + exit 2.
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TAG_TOKEN_RE = re.compile(
    r"\b(free:dispatch|free:session|free:handover|local:[A-Za-z0-9_-]+|remote:[A-Za-z0-9_-]+"
    r"|cloud:)(/[A-Za-z0-9_-]+)?")
HEADING_RE = re.compile(r"^#{2,4} (?:Task|Step|Phase)\b")
ROUTING_RE = re.compile(r"^Routing:\s*(.*)$")
INVALID_CLOUD = ("tool use", "tool-use", "tools", "too hard", "too complex",
                 "local is slow", "slow", "not delegatable")


@dataclass
class PlanReport:
    tasks: list                 # list[(heading, tag|None)]
    free_count: int
    routing_line: Optional[str]


def _tag_in_line(line: str) -> Optional[str]:
    m = TAG_TOKEN_RE.search(line)
    if not m:
        return None
    tag = m.group(0)
    if tag.startswith("cloud:"):
        reason = line[m.end(1):].strip()
        if m.group(2) and m.group(2) in reason:
            reason = reason[:reason.find(m.group(2))]
        reason = reason.rstrip(".,;:)]`'\"")
        tag = "cloud:" + reason.strip()
    return tag


def _tag_kind(tag: Optional[str]) -> str:
    if tag is None:
        return "none"
    if tag.startswith(("free:", "local:", "remote:")):
        return "free"
    reason = tag[len("cloud:"):].lower()
    for bad in INVALID_CLOUD:
        if bad in reason:
            return "invalid"
    return "cloud"


def parse_plan(text: str) -> Optional[PlanReport]:
    """Tasks = headings + the tag found in the section's first non-empty lines (heading line
    plus the first block, so a tag in a list item directly under the heading counts)."""
    lines = text.splitlines()
    tasks = []
    routing_line = None
    for idx, line in enumerate(lines):
        if HEADING_RE.match(line):
            block = [line]
            j = idx + 1
            while j < len(lines) and lines[j].strip():
                block.append(lines[j])
                j += 1
            tag = None
            for bl in block:
                tag = _tag_in_line(bl)
                if tag:
                    break
            tasks.append((line.strip(), tag))
        elif routing_line is None:
            rm = ROUTING_RE.match(line)
            if rm:
                routing_line = rm.group(1).strip()
    if len(tasks) < 2:
        return None
    return PlanReport(tasks=tasks,
                      free_count=sum(1 for _h, t in tasks if _tag_kind(t) == "free"),
                      routing_line=routing_line)


def is_plan_path(path: str, home: str) -> bool:
    if not path or not path.endswith(".md"):
        return False
    p = os.path.realpath(os.path.abspath(path)) + os.sep
    plans_dir = os.path.join(home, ".claude", "plans") + os.sep
    if p.startswith(plans_dir):
        return True
    return ("docs" + os.sep + "superpowers" + os.sep + "plans" + os.sep) in p


def _is_free(base_url) -> bool:
    """Same rule as hooks/cloud-agent-intercept.py:is_free_session (the parity test pins both;
    hook files have dashes, so they are loaded by path, not imported)."""
    if not base_url:
        return False
    try:
        from urllib.parse import urlparse
        host = urlparse(str(base_url)).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    except Exception:
        return False


def _plugin_root(env) -> str:
    return env.get("CLAUDE_PLUGIN_ROOT") or str(Path(__file__).resolve().parent.parent)


def _plan_check_on(env) -> bool:
    """ON by default; FA_PLAN_CHECK=0 turns it off. A missing cloud_session_env module means ON;
    a corrupt settings file FAILS OPEN (one stderr line, treated as off — never block)."""
    try:
        bin_dir = os.path.join(_plugin_root(env), "bin")
        if bin_dir not in sys.path:
            sys.path.insert(0, bin_dir)
        import cloud_session_env as cse
    except Exception:
        return True
    try:
        cse._read_strict()
    except Exception as exc:
        print(f"plan-route-guard: settings unreadable ({exc.__class__.__name__}); failing open",
              file=sys.stderr)
        return False
    try:
        return bool(cse.plan_check(env))
    except Exception as exc:
        print(f"plan-route-guard: plan_check failed ({exc.__class__.__name__}); failing open",
              file=sys.stderr)
        return False


def _deny(variant: str, offending: list, env) -> str:
    lines = [f"🧭 free-agents plan_check: {variant}"]
    for h in offending:
        lines.append(f"  - {h}")
    lines.append("Tag each task: free:dispatch | free:session | free:handover "
                 "| cloud:<checkable reason>")
    lines.append(f"Opt out: python3 {_plugin_root(env)}/bin/cloud_session_env.py set plan_check 0")
    return "\n".join(lines)


def decide(path: str, text: str, env) -> Optional[str]:
    """The deny reason for writing `text` to `path` in this session, or None to allow."""
    if _is_free(env.get("ANTHROPIC_BASE_URL")):
        return None
    if not _plan_check_on(env):
        return None
    home = os.path.realpath(os.path.expanduser(str(env.get("HOME") or "~")))
    if not is_plan_path(path, home):
        return None
    report = parse_plan(text)
    if report is None:
        return None
    offending = []
    saw_invalid = False
    for heading, tag in report.tasks:
        kind = _tag_kind(tag)
        if kind == "invalid":
            saw_invalid = True
            offending.append(heading)
        elif kind == "none":
            offending.append(heading)
    if offending:
        variant = ("this plan uses an invalid cloud reason" if saw_invalid
                   else "this plan has tasks without a route tag")
        return _deny(variant, offending, env)
    if report.free_count == 0:
        rl = (report.routing_line or "").lower()
        if report.routing_line is None or any(bad in rl for bad in INVALID_CLOUD):
            return _deny("this plan has no free: task and no valid Routing: line",
                         [h for h, _t in report.tasks], env)
    return None


def _apply_edits(file_path, edits) -> Optional[str]:
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except Exception:
        return None
    for e in edits:
        old, new = e.get("old_string"), e.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if e.get("replace_all"):
            text = text.replace(old, new)
        else:
            if old not in text:
                return None
            text = text.replace(old, new, 1)
    return text


def resulting_text(tool_name: str, tool_input: dict) -> Optional[str]:
    """The file content AFTER the call: Write -> content; Edit/MultiEdit -> current file with
    the edits applied (replace_all honoured). Unreadable or irrelevant -> None (allow)."""
    if tool_name == "Write":
        c = tool_input.get("content")
        return c if isinstance(c, str) else None
    if tool_name == "Edit":
        return _apply_edits(tool_input.get("file_path"), [tool_input])
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return None
        return _apply_edits(tool_input.get("file_path"), edits)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="plan-route-guard.py",
        description="PreToolUse guard: a paid session cannot write a plan file whose tasks "
                    "lack a free:/cloud: route tag (plan_check setting, default ON). Fails "
                    "open on any internal error; deny = reason on stderr + exit 2.")
    ap.parse_args()
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
        inp = payload.get("tool_input") or {}
        if not isinstance(inp, dict):
            return 0
        text = resulting_text(str(payload.get("tool_name") or ""), inp)
        if text is None:
            return 0
        reason = decide(str(inp.get("file_path") or ""), text, dict(os.environ))
        if reason:
            print(reason, file=sys.stderr)
            return 2
        return 0
    except Exception as exc:
        print(f"plan-route-guard: internal error ({exc.__class__.__name__}); failing open",
              file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())

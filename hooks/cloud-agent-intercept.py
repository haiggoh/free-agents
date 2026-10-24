#!/usr/bin/env python3
"""
Claude Code PreToolUse hook: route the native `Agent` tool per the session's routing mode.

WHY: On paid cloud sessions (ANTHROPIC_BASE_URL not localhost), the native `Agent` tool uses
expensive Sonnet subagents. free-agents routes it per path (cloud_session_env.py): free-first
(deny + redirect to free-agent-tool.py; ONE native call is allowed once THIS session's free
attempts are honestly exhausted — the fallback mark), free-only (deny, never falls back), paid
(pass through). FREE sessions (localhost base URL) always pass through.

Fail-open: any error — including a corrupt settings layer — passes the call through; the hook
must never block the user because its own configuration is unreadable.
"""

import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

_BIN = Path(__file__).resolve().parent.parent / "bin"
if str(_BIN) not in sys.path:
    sys.path.insert(0, str(_BIN))
try:
    import cloud_session_env as cse
    import fa_fallback_mark as fm
except ImportError:                                   # standalone deploy without bin/: fail open
    cse = None
    fm = None


def is_free_session(base_url: Optional[str]) -> bool:
    """Return True if base_url points to localhost (free/local session)."""
    if not base_url:
        return False  # unset = cloud (native Anthropic)
    try:
        parsed = urllib.parse.urlparse(base_url)
        host = parsed.hostname or ""
        return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    except Exception:
        return False


def build_deny_reason(tool_input: Dict[str, Any], plugin_root: str, mode: str) -> str:
    """Build the deny reason: the exact Bash invocation, then the mode's tail."""
    prompt = tool_input.get("prompt", "")
    subagent_type = tool_input.get("subagent_type", "general-purpose")
    description = tool_input.get("description", "")

    free_tool = os.path.join(plugin_root, "bin", "free-agent-tool.py")
    payload = {"prompt": prompt, "subagent_type": subagent_type, "description": description}
    payload_json = json.dumps(payload, separators=(",", ":"))

    # A QUOTED heredoc delimiter passes the JSON verbatim: no quote in the prompt can end the
    # string early or be expanded by the shell (an inline '...' broke on any apostrophe).
    delim = "FA_AGENT_JSON"
    while delim in payload_json:
        delim += "_"
    if mode == "free-only":
        tail = (
            "This lane is set to Free only — it NEVER falls back to paid for Agent calls. "
            "Change it in csl -> Cloud Session Configuration, or "
            "`bin/cloud_session_env.py set route agent free-first` / `paid`.\n"
        )
    else:
        tail = (
            "Free-first lane: after this session's free attempts are honestly exhausted "
            "(the dispatch result then says \"paid_fallback_allowed\": true), ONE native Agent "
            "call is let through automatically. Check the attempts in free-agent-tool.py's "
            "output; force the mode with `bin/cloud_session_env.py set route agent paid`.\n"
        )
    return (
        f"Free-agents replacement: run this subagent for FREE via Bash instead of paid Sonnet.\n"
        f"  python3 \"{free_tool}\" <<'{delim}'\n{payload_json}\n{delim}\n"
        f"Returns JSON: {{\"content\": \"...\", \"model_used\": \"...\", \"route\": \"...\"}}.\n"
        f"Before running it, tell the user which model it goes to (`python3 \"{free_tool}\" --list` "
        f"shows the preference order); free models are not equal.\n"
        f"It is a STATELESS dispatch: the model cannot read files or run commands. Add the files "
        f"it needs as \"files\": [paths] (their contents are attached). Work that must drive tools "
        f"belongs in a free session (remote-session.sh / csl), not here.\n"
        f"Independent tasks may be launched as several parallel Bash calls.\n"
        f"Original description: {description}\n"
        f"{tail}"
    )


def decide(payload: Dict[str, Any], env: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Pure decision function for testing. Returns hookSpecificOutput dict or None (pass through).
    """
    tool_name = payload.get("tool_name")
    if tool_name != "Agent":
        return None

    if is_free_session(env.get("ANTHROPIC_BASE_URL")):
        return None

    # Resolve the mode; an unreadable settings layer never blocks the user (fail open).
    try:
        mode = cse.route("agent", env) if cse else "free-first"
    except Exception:
        return None

    if mode == "paid":
        return None

    tool_input = payload.get("tool_input", {})
    plugin_root = env.get("CLAUDE_PLUGIN_ROOT", "<plugin>")

    if mode == "free-first" and fm is not None:
        try:
            mark = fm.consume_mark(str(payload.get("session_id") or ""))
        except Exception:
            mark = None
        if mark:
            return None               # the ONE native paid call: this session earned it

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": build_deny_reason(tool_input, plugin_root, mode),
        }
    }


def print_usage() -> None:
    """Print usage to stdout."""
    print(
        "Usage: hooks/cloud-agent-intercept.py [--help|-h|--self-test]\n"
        "\n"
        "Claude Code PreToolUse hook: route the native Agent tool per the session's mode.\n"
        "free-first (default): deny + redirect to free-agent-tool.py; one native call is let\n"
        "  through once this session's free attempts are honestly exhausted (the mark).\n"
        "free-only: deny, never falls back. paid: pass through (free-agents stays out).\n"
        "\n"
        "Environment variables:\n"
        "  ANTHROPIC_BASE_URL    - Base URL; localhost = free session, else cloud\n"
        "  FA_ROUTE_AGENT        - free-first | free-only | paid (settings.json env; the\n"
        "                          legacy FA_REPLACE_AGENTS 1/0 maps onto it)\n"
        "  FA_FALLBACK_MARK_DIR  - Override the mark dir (default ~/.claude/free-agents/paid-fallback)\n"
        "  CLAUDE_PLUGIN_ROOT    - Plugin root for free-agent-tool.py path (default \"<plugin>\")\n"
        "\n"
        "Arguments:\n"
        "  --help, -h      Show this help and exit\n"
        "  --self-test     Run built-in tests and exit with 0/1"
    )


def run_self_test() -> int:
    """Run built-in test cases. Return 0 if all pass, 1 otherwise."""
    all_pass = True

    def run_case(name: str, payload: Dict[str, Any], env: Dict[str, str], expected: Optional[Dict[str, Any]]) -> bool:
        nonlocal all_pass
        result = decide(payload, dict(env))
        if result == expected:
            print(f"PASS: {name}")
            return True
        print(f"FAIL: {name}")
        print(f"  Expected: {json.dumps(expected)}")
        print(f"  Got:      {json.dumps(result)}")
        all_pass = False
        return False

    CLOUD = {"ANTHROPIC_BASE_URL": "https://gateway.example.com"}

    # a) non-Agent tool -> None
    run_case("non-Agent tool", {"tool_name": "Bash", "tool_input": {}, "session_id": "s1"}, {}, None)

    # b) free session -> None even with free-only
    run_case("free session + free-only",
             {"tool_name": "Agent", "tool_input": {"prompt": "hi"}, "session_id": "s1"},
             {**CLOUD, "ANTHROPIC_BASE_URL": "http://localhost:4141", "FA_ROUTE_AGENT": "free-only"}, None)

    # c) paid mode -> None; the legacy FA_REPLACE_AGENTS=0 maps onto paid
    run_case("paid mode", {"tool_name": "Agent", "tool_input": {"prompt": "hi"}, "session_id": "s1"},
             {**CLOUD, "FA_ROUTE_AGENT": "paid"}, None)
    run_case("legacy replace=0", {"tool_name": "Agent", "tool_input": {"prompt": "hi"}, "session_id": "s1"},
             {**CLOUD, "FA_REPLACE_AGENTS": "0"}, None)

    # d) free-first (also via the legacy 1) -> deny; the reason carries the heredoc + the mode tail
    result_d = decide(
        {"tool_name": "Agent", "tool_input": {"prompt": "test prompt", "subagent_type": "coder",
                                              "description": "desc"}, "session_id": "s1"},
        {"ANTHROPIC_BASE_URL": "https://api.anthropic.com", "FA_REPLACE_AGENTS": "1",
         "CLAUDE_PLUGIN_ROOT": "/my/plugin"})
    if result_d and result_d.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
        reason = result_d["hookSpecificOutput"].get("permissionDecisionReason", "")
        if "free-agent-tool.py" in reason and "test prompt" in reason:
            print("PASS: free-first -> deny with the redirect")
        else:
            print(f"FAIL: free-first -> deny but reason missing expected content: {reason}")
            all_pass = False
    else:
        print(f"FAIL: free-first -> expected deny, got {result_d}")
        all_pass = False

    # e) undecided (no env at all) -> deny, NEVER ask
    result_e = decide({"tool_name": "Agent", "tool_input": {"prompt": "hi", "description": "task 1"},
                       "session_id": "s1"}, dict(CLOUD))
    if result_e and result_e["hookSpecificOutput"]["permissionDecision"] == "deny":
        print("PASS: undecided -> deny (no ask-once prompt)")
    else:
        print(f"FAIL: undecided -> expected deny, got {result_e}")
        all_pass = False

    # f) free-only -> deny, and the reason says it never falls back
    result_f = decide({"tool_name": "Agent", "tool_input": {"prompt": "hi", "description": "task 1"},
                       "session_id": "s1"}, {**CLOUD, "FA_ROUTE_AGENT": "free-only"})
    if result_f and result_f["hookSpecificOutput"]["permissionDecision"] == "deny" \
            and "never falls back" in result_f["hookSpecificOutput"]["permissionDecisionReason"]:
        print("PASS: free-only -> deny, never falls back")
    else:
        print(f"FAIL: free-only -> expected deny with the never-fallback tail, got {result_f}")
        all_pass = False

    # g) free-first with an ARMED mark -> None (the one native call)
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        old = os.environ.get("FA_FALLBACK_MARK_DIR")
        os.environ["FA_FALLBACK_MARK_DIR"] = tmpdir
        try:
            if fm is not None:
                fm.write_mark("s1", [{"id": "g", "provider": "gemini", "ok": False}])
            result_g = decide({"tool_name": "Agent", "tool_input": {"prompt": "cloud prompt",
                                                                   "description": "cloud task"},
                               "session_id": "s1"},
                              {"FA_ROUTE_AGENT": "free-first", "CLAUDE_PLUGIN_ROOT": "/plugin"})
            if result_g is None:
                print("PASS: armed mark lets the one native call through")
            else:
                print(f"FAIL: armed mark -> expected pass-through, got {result_g}")
                all_pass = False
            result_g2 = decide({"tool_name": "Agent", "tool_input": {"prompt": "again"}, "session_id": "s1"},
                               {"FA_ROUTE_AGENT": "free-first", "CLAUDE_PLUGIN_ROOT": "/plugin"})
            if result_g2 is not None and result_g2["hookSpecificOutput"]["permissionDecision"] == "deny":
                print("PASS: the mark is single use — the next call is denied again")
            else:
                print(f"FAIL: consumed mark -> expected deny, got {result_g2}")
                all_pass = False
        finally:
            if old is None:
                os.environ.pop("FA_FALLBACK_MARK_DIR", None)
            else:
                os.environ["FA_FALLBACK_MARK_DIR"] = old

    return 0 if all_pass else 1


def main() -> int:
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        print_usage()
        return 0
    if "--self-test" in args:
        return run_self_test()
    if args:
        print(f"Unknown argument: {args[0]}", file=sys.stderr)
        print_usage()
        return 2

    try:
        stdin_data = sys.stdin.read()
        if not stdin_data:
            return 0
        payload = json.loads(stdin_data)
    except Exception:
        return 0

    if payload.get("tool_name") != "Agent":
        return 0

    result = decide(payload, dict(os.environ))

    if result:
        print(json.dumps(result))

    return 0


if __name__ == "__main__":
    sys.exit(main())

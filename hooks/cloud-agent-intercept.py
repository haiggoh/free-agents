#!/usr/bin/env python3
"""
Claude Code PreToolUse hook: intercept paid Sonnet subagents on cloud sessions.

WHY: On paid cloud sessions (ANTHROPIC_BASE_URL not localhost), the native `Agent`
tool uses expensive Sonnet subagents. This hook lets users opt in to replace those
with FREE models (NVIDIA Nemotron, Gemini, local) via `free-agent-tool.py`.

Behavior:
- FREE sessions (localhost base URL): always pass through (native Agent).
- FA_REPLACE_AGENTS=0: pass through (user chose native).
- FA_REPLACE_AGENTS=1: deny native Agent, instruct model to call free-agent-tool.py via Bash.
- Undecided: ask ONCE per machine (state file), then default to pass-through.

Fail-open: any error -> silent pass-through.
"""

import json
import os
import sys
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


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


def get_state_path(env: Dict[str, str]) -> Path:
    """Get the state file path for 'ask once' tracking."""
    state_env = env.get("FA_INTERCEPT_STATE")
    if state_env:
        return Path(state_env)
    home = Path.home()
    return home / ".claude" / "free-agents" / "agent-intercept-asked"


def read_state(state_path: Path) -> bool:
    """Return True if state file exists (already asked)."""
    return state_path.exists()


def write_state(state_path: Path) -> None:
    """Create state file with ISO timestamp, mode 0600."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(datetime.now().isoformat())
    state_path.chmod(0o600)


def build_deny_reason(tool_input: Dict[str, Any], plugin_root: str) -> str:
    """Build the deny reason with exact Bash invocation instructions."""
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
    return (
        f"Free-agents replacement: run this subagent for FREE via Bash instead of paid Sonnet.\n"
        f"  python3 \"{free_tool}\" <<'{delim}'\n{payload_json}\n{delim}\n"
        f"Returns JSON: {{\"content\": \"...\", \"model_used\": \"...\"}}.\n"
        f"Independent tasks may be launched as several parallel Bash calls.\n"
        f"Original description: {description}\n"
        f"Configure: csl -> Cloud Session Configuration -> Replace subagents with free agents, "
        f"or `bin/cloud_session_env.py set replace_agents 0` to turn off."
    )


def build_ask_reason(tool_input: Dict[str, Any]) -> str:
    """Build the ask reason for first-time prompt."""
    description = tool_input.get("description", "")
    return (
        f"free-agents can replace paid Sonnet subagents with FREE models "
        f"(NVIDIA Nemotron / Gemini / local) on cloud sessions. "
        f"Approve to run THIS subagent natively. "
        f"To decide permanently: csl -> Cloud Session Configuration -> Replace subagents with free agents, "
        f"or run `bin/cloud_session_env.py set replace_agents 1` (free) / `0` (native). "
        f"You will not be asked again.\n"
        f"Original description: {description}"
    )


def decide(payload: Dict[str, Any], env: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Pure decision function for testing.
    Returns hookSpecificOutput dict or None (pass through).
    """
    tool_name = payload.get("tool_name")
    if tool_name != "Agent":
        return None

    tool_input = payload.get("tool_input", {})
    base_url = env.get("ANTHROPIC_BASE_URL")

    # Rule 3: free session -> pass through
    if is_free_session(base_url):
        return None

    # Rule 4: read FA_REPLACE_AGENTS
    fa_replace = env.get("FA_REPLACE_AGENTS", "")

    # Rule 5: explicitly native
    if fa_replace == "0":
        return None

    plugin_root = env.get("CLAUDE_PLUGIN_ROOT", "<plugin>")

    # Rule 6: explicitly replace
    if fa_replace == "1":
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": build_deny_reason(tool_input, plugin_root),
            }
        }

    # Rule 7: undecided -> ask once
    state_path = get_state_path(env)
    if not read_state(state_path):
        write_state(state_path)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": build_ask_reason(tool_input),
            }
        }

    # Already asked -> pass through
    return None


def print_usage() -> None:
    """Print usage to stdout."""
    print(
        "Usage: hooks/cloud-agent-intercept.py [--help|-h|--self-test]\n"
        "\n"
        "Claude Code PreToolUse hook: intercept paid Sonnet subagents on cloud sessions.\n"
        "Replaces native Agent tool with free-agent-tool.py (opt-in).\n"
        "\n"
        "Environment variables:\n"
        "  ANTHROPIC_BASE_URL    - Base URL; localhost = free session, else cloud\n"
        "  FA_REPLACE_AGENTS     - \"1\" = replace with free, \"0\" = native, unset = ask once\n"
        "  FA_INTERCEPT_STATE    - Override state file path (default ~/.claude/free-agents/agent-intercept-asked)\n"
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
        with tempfile.TemporaryDirectory() as tmpdir:
            test_env = dict(env)
            test_env["FA_INTERCEPT_STATE"] = os.path.join(tmpdir, "state")
            result = decide(payload, test_env)
            if result == expected:
                print(f"PASS: {name}")
                return True
            else:
                print(f"FAIL: {name}")
                print(f"  Expected: {json.dumps(expected)}")
                print(f"  Got:      {json.dumps(result)}")
                all_pass = False
                return False

    # a) non-Agent tool -> None
    run_case(
        "non-Agent tool",
        {"tool_name": "Bash", "tool_input": {}, "session_id": "s1"},
        {},
        None,
    )

    # b) Agent + localhost + FA_REPLACE_AGENTS=1 -> None (free session)
    run_case(
        "Agent + localhost + replace=1",
        {"tool_name": "Agent", "tool_input": {"prompt": "hi"}, "session_id": "s1"},
        {"ANTHROPIC_BASE_URL": "http://localhost:4141", "FA_REPLACE_AGENTS": "1"},
        None,
    )

    # c) Agent + cloud + FA_REPLACE_AGENTS=0 -> None
    run_case(
        "Agent + cloud + replace=0",
        {"tool_name": "Agent", "tool_input": {"prompt": "hi"}, "session_id": "s1"},
        {"ANTHROPIC_BASE_URL": "https://gateway.example.com", "FA_REPLACE_AGENTS": "0"},
        None,
    )

    # d) Agent + cloud + FA_REPLACE_AGENTS=1 -> deny, reason contains free-agent-tool.py and prompt
    result_d = decide(
        {"tool_name": "Agent", "tool_input": {"prompt": "test prompt", "subagent_type": "coder", "description": "desc"}, "session_id": "s1"},
        {"ANTHROPIC_BASE_URL": "https://api.anthropic.com", "FA_REPLACE_AGENTS": "1", "CLAUDE_PLUGIN_ROOT": "/my/plugin"},
    )
    if result_d and result_d.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
        reason = result_d["hookSpecificOutput"].get("permissionDecisionReason", "")
        if "free-agent-tool.py" in reason and "test prompt" in reason:
            print("PASS: Agent + cloud + replace=1 -> deny with correct reason")
        else:
            print(f"FAIL: Agent + cloud + replace=1 -> deny but reason missing expected content: {reason}")
            all_pass = False
    else:
        print(f"FAIL: Agent + cloud + replace=1 -> expected deny, got {result_d}")
        all_pass = False

    # e) Agent + cloud + undecided + no state file -> ask, state file created
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state"
        env_e = {"ANTHROPIC_BASE_URL": "https://api.anthropic.com", "FA_INTERCEPT_STATE": str(state_file)}
        result_e = decide(
            {"tool_name": "Agent", "tool_input": {"prompt": "hi", "description": "task 1"}, "session_id": "s1"},
            env_e,
        )
        if result_e and result_e.get("hookSpecificOutput", {}).get("permissionDecision") == "ask":
            if state_file.exists():
                print("PASS: undecided + no state -> ask, state created")
            else:
                print("FAIL: undecided + no state -> ask but state not created")
                all_pass = False
        else:
            print(f"FAIL: undecided + no state -> expected ask, got {result_e}")
            all_pass = False

    # f) same as e again -> None (asked once)
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "state"
        state_file.write_text("2024-01-01T00:00:00")
        env_f = {"ANTHROPIC_BASE_URL": "https://api.anthropic.com", "FA_INTERCEPT_STATE": str(state_file)}
        result_f = decide(
            {"tool_name": "Agent", "tool_input": {"prompt": "hi", "description": "task 1"}, "session_id": "s1"},
            env_f,
        )
        if result_f is None:
            print("PASS: undecided + state exists -> None")
        else:
            print(f"FAIL: undecided + state exists -> expected None, got {result_f}")
            all_pass = False

    # g) Agent + base url unset (native Anthropic login) + FA_REPLACE_AGENTS=1 -> deny
    result_g = decide(
        {"tool_name": "Agent", "tool_input": {"prompt": "cloud prompt", "description": "cloud task"}, "session_id": "s1"},
        {"FA_REPLACE_AGENTS": "1", "CLAUDE_PLUGIN_ROOT": "/plugin"},
    )
    if result_g and result_g.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
        reason = result_g["hookSpecificOutput"].get("permissionDecisionReason", "")
        if "cloud prompt" in reason:
            print("PASS: unset base url + replace=1 -> deny")
        else:
            print(f"FAIL: unset base url + replace=1 -> deny but missing prompt in reason: {reason}")
            all_pass = False
    else:
        print(f"FAIL: unset base url + replace=1 -> expected deny, got {result_g}")
        all_pass = False

    return 0 if all_pass else 1


def main() -> int:
    # Rule 1: argument handling
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

    # Rule 9: fail open wrapper
    try:
        # Rule 2: read stdin
        stdin_data = sys.stdin.read()
        if not stdin_data:
            return 0
        payload = json.loads(stdin_data)
    except Exception:
        return 0

    # Rule 2 continued: non-Agent -> pass through
    if payload.get("tool_name") != "Agent":
        return 0

    # Decide using pure function
    result = decide(payload, dict(os.environ))

    if result:
        print(json.dumps(result))

    return 0


if __name__ == "__main__":
    sys.exit(main())
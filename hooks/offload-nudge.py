#!/usr/bin/env python3
"""SessionStart hook — put the offload-by-role rule IN CONTEXT every session.

Why this exists: local-agents was the only behavioral rule in the family without an in-context
nudge. A skill (trigger-dependent) or a buried CLAUDE.md line underfires — verified by probe: a
fresh session planned a textbook bulk task entirely on cloud despite the rule being present. Every
sibling plugin (no-hidden-changes, measure-twice, audit-loose-ends, run-to-completion, brief-agents)
injects its nudge at SessionStart; this brings offload up to that bar.

A PAID session gets the free-offload-by-default nudge; a FREE one (localhost base URL) gets the
council nudge instead: no cost reason to offload, but a cross-family review still adds quality.

Fail-safe: any error → no output, exit 0. Never break or slow a session start.
"""
import json
import os
import sys


def is_free_session(base_url: str) -> bool:
    """A localhost base URL is a free session (local model or a free-API proxy)."""
    from urllib.parse import urlparse
    try:
        return (urlparse(base_url).hostname or "") in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    except ValueError:
        return False


def paid_nudge(root: str) -> str:
    """A PAID session: free offloading is the DEFAULT for delegated work, fan-out included."""
    tool = f"{root}/bin/free-agent-tool.py" if root else "the plugin's bin/free-agent-tool.py"
    return (
        "free-agents is installed and this session runs on PAID inference, so FREE OFFLOADING IS "
        "THE DEFAULT for delegated work. Before ANY Agent/Task call, Workflow, fan-out, parallel "
        "subagents, /ultracode-style runs or a review agent, route the work to a free model "
        "INSTEAD of a paid subagent: the native Agent tool can only start a paid model. Pick the "
        "shape first: (1) self-contained text work (review, analysis, extraction, drafting, a "
        "second opinion) → STATELESS dispatch: "
        f"`python3 {tool} <<'JSON'` with {{\"prompt\": ..., \"files\": [paths]}} — attach the "
        "files, because a dispatch cannot read them; it walks the maintainer's preference order "
        "(`--list` shows it: Gemini 3.8 Flash, DeepSeek V4.1 Flash, GLM 5.3, GLM 5.3 Flash, Nemotron 3 Ultra, "
        "then local DeepSeek V4 Flash, Laguna S, KAT Coder) and fails over by itself. Several "
        "independent pieces = several parallel Bash calls. (2) Work that must read files or run "
        "commands → a free SESSION (remote-session.sh <alias>, or csl for local), never a "
        "stateless dispatch; session models differ from dispatch ones (Gemini quota runs out fast in "
        "a session, DeepSeek is slow; GLM 5.3 is the session workhorse) — see the "
        "free-session-handover skill, which also covers overnight handovers. ALWAYS ANNOUNCE the model and route to the user BEFORE offloading — "
        "free models are not equal. A paid subagent needs a stated reason (security-critical, "
        "final verification of the free result, or a free lane measured as failing now). Keep "
        "judgment, integration and the final check on this session. Full guidance: the "
        "offload-to-local skill."
    )


def free_nudge(root: str) -> str:
    """A FREE session: offloading saves no money here, but a second model family still buys quality."""
    tool = f"{root}/bin/free-agent-tool.py" if root else "the plugin's bin/free-agent-tool.py"
    return (
        "free-agents: this session already runs on FREE inference, so offloading saves no money "
        "here; do not delegate to cut cost. A dispatch still pays off for QUALITY: an adversarial "
        "review or an independent cross-check from a DIFFERENT model family often finds what this "
        "session's own model misses. For that, run a stateless dispatch: "
        f"`python3 {tool}` with {{\"prompt\": ..., \"files\": [paths], \"role\": \"validator\", "
        "\"avoid_family\": \"<this session's model family>\"} (attach the files; a dispatch cannot "
        "read the disk). Announce the model and route before offloading. Guidance: the offload-to-local "
        "skill (council routing)."
    )

def main():
    try:
        root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
        # A FREE session saves nothing by offloading, so it gets the council nudge instead of the
        # cost one: a different model family is the reason to dispatch there (user 2026-10-23).
        free = is_free_session(os.environ.get("ANTHROPIC_BASE_URL", ""))
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": (free_nudge if free else paid_nudge)(root)}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

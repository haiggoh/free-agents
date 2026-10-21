#!/usr/bin/env python3
"""SessionStart hook — put the offload-by-role rule IN CONTEXT every session.

Why this exists: local-agents was the only behavioral rule in the family without an in-context
nudge. A skill (trigger-dependent) or a buried CLAUDE.md line underfires — verified by probe: a
fresh session planned a textbook bulk task entirely on cloud despite the rule being present. Every
sibling plugin (no-hidden-changes, measure-twice, audit-loose-ends, run-to-completion, brief-agents)
injects its nudge at SessionStart; this brings offload up to that bar.

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


def main():
    try:
        root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
        if not is_free_session(os.environ.get("ANTHROPIC_BASE_URL", "")):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                     "additionalContext": paid_nudge(root)}}))
            return 0
        resolver = f"{root}/bin/la-roles.sh" if root else "the plugin's bin/la-roles.sh (or `csl roles`)"
        nudge = (
            "local-agents (offload-to-local): before executing a multi-step or file-touching task, "
            "decide offload PER STEP up front — annotate each planned step `local:<role>` or "
            "`cloud:<reason>`. DEFAULT each delegatable step to `local:operator` (free local compute); "
            "keep a step on cloud with a named reason — quality/capability (frontier reasoning, "
            "security-critical, final review/verification) OR plain cost-benefit: for small or "
            "one-off work where spin-up/briefing overhead "
            "would exceed the saving, `cloud:not worth offloading` is a perfectly good reason. Offload "
            "is the DEFAULT, not a mandate. Roles are a spectrum: operator (broad default) · reasoner · "
            "validator · utility — one model fills several by varying effort. Resolve on-disk models "
            f"(and effort) with `{resolver}`; dispatch via curl, or bin/librarian-dispatch.py (SSE) "
            "for long/open-ended generations (stall watchdog, no timeout death). Tool use is NOT a "
            "cloud reason — a local SESSION drives tools fine (only a stateless dispatch can't); "
            "coupled tool-work usually stays on cloud, and a parallel local session is a deliberate "
            "option for a big isolated chunk (see the skill). The smell is reflexively doing BULK/"
            "mechanical work yourself on the paid model — not skipping offload on trivia. Full "
            "guidance: the offload-to-local skill."
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": nudge,
            }
        }))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

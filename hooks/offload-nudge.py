#!/usr/bin/env python3
"""SessionStart hook — put the offload-by-role rule IN CONTEXT every session.

Why this exists: local-agents was the only behavioral rule in the family without an in-context
nudge. A skill (trigger-dependent) or a buried CLAUDE.md line underfires — verified by probe: a
fresh session planned a textbook bulk task entirely on cloud despite the rule being present. Every
sibling plugin (no-hidden-changes, measure-twice, audit-loose-ends, run-to-completion, brief-agents)
injects its nudge at SessionStart; this brings offload up to that bar.

A PAID session gets the free-offload-by-default nudge; a FREE one (localhost base URL) gets the
council nudge instead: no cost reason to offload, but a cross-family review still adds quality.
Either way, guards_section() appends each PreToolUse guard's PRIMER (one line each) so the model
knows the traps at session start; the Agent/Workflow intercept's line follows the route modes and
is omitted when both paths are paid (the menu opt-out turns that hook off too).

Fail-safe: any error → no output, exit 0. Never break or slow a session start.
"""
import json
import os
import sys
from pathlib import Path


def is_free_session(base_url: str) -> bool:
    """A localhost base URL is a free session (local model or a free-API proxy)."""
    from urllib.parse import urlparse
    try:
        return (urlparse(base_url).hostname or "") in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    except ValueError:
        return False


def lane_mark() -> str:
    """🦾 local / 📡 remote lane mark for the credit-moment prefix (fail-safe: never
    break session start over a branding mark)."""
    try:
        bin_dir = str(Path(__file__).resolve().parent.parent / "bin")
        if bin_dir not in sys.path:
            sys.path.insert(0, bin_dir)
        from emoji_constants import lane_mark as _lm
        return _lm(os.environ)
    except Exception:
        return "📡"


def _routing_block(root: str) -> str:
    """0.54.0 Task 9: the routing text comes from the marked block in the offload skill —
    never a second copy here (a test goes RED if they diverge). Falls back to this checkout
    when CLAUDE_PLUGIN_ROOT points at a partial copy (tests); the loud MISSING line only when
    neither has the block."""
    try:
        bin_dir = str(Path(__file__).resolve().parent.parent / "bin")
        if bin_dir not in sys.path:
            sys.path.insert(0, bin_dir)
        import offload_routing
        own = str(Path(__file__).resolve().parent.parent)
        block = offload_routing.routing_block(root or own)
        if block == offload_routing.MISSING and root and Path(root).resolve() != Path(own).resolve():
            block = offload_routing.routing_block(own)
        return block
    except Exception:
        return offload_routing.MISSING if "offload_routing" in sys.modules else ""


def paid_nudge(root: str, modes: dict | None = None) -> str:
    """A PAID session, per the Agent path's routing mode (cloud_session_env.route).

    free-first (default): free offloading is THE DEFAULT for delegated work, and one honest
    paid escape exists after exhaustion. free-only: every Agent call is redirected, never
    paid. paid: the native Agent runs as usual — free models only when the user asks.
    `modes` is {"agent": mode} (other paths pass through unchanged here)."""
    tool = f"{root}/bin/free-agent-tool.py" if root else "the plugin's bin/free-agent-tool.py"
    modes = modes or {}
    agent_mode = modes.get("agent", "free-first")
    if agent_mode == "paid" and modes.get("workflow", "free-first") == "paid":
        # Both paths opted out via the menu (route=paid): the intercept hook is off, so the
        # start hook says nothing about it either — guards_section still adds the other guards.
        return ""
    if agent_mode == "paid":
        return (
            "free-agents is installed, but the Agent path is set to PAID: the native Agent tool "
            "runs paid subagents AS USUAL, and free models are used ONLY WHEN YOU ASK — run the "
            f"stateless dispatch yourself when you want one: `python3 {tool} <<'JSON'` with "
            '{{"prompt": ..., "files": [paths]}} (attach the files; a dispatch cannot read them; '
            "`--list` shows the preference order). Nothing here changes how the native Agent "
            "behaves. Change the mode in csl -> Cloud Session Configuration, or "
            "`python3 "
            f"{tool.rsplit('/', 2)[0]}/cloud_session_env.py set route agent free-first`."
        )
    if agent_mode == "free-only":
        return (
            "free-agents is installed and the Agent path is set to FREE ONLY: EVERY Agent/Task/"
            "Workflow call is redirected to a free model and the hook NEVER FALLS BACK to paid. "
            "Change the mode in csl -> Cloud Session Configuration.\n"
            + _routing_block(root)
        )
    return (
        "free-agents is installed and this session runs on PAID inference, so FREE OFFLOADING IS "
        "THE DEFAULT for delegated work — before ANY Agent/Task/Workflow call, fan-out or "
        "parallel subagents, route the work to a free model instead of a paid subagent (the "
        "native Agent tool can only start a paid model).\n"
        + _routing_block(root)
    )


def free_nudge(root: str) -> str:
    """A FREE session: offloading saves no money here, but a second model family still buys quality."""
    tool = f"{root}/bin/free-agent-tool.py" if root else "the plugin's bin/free-agent-tool.py"
    return (
        f"{lane_mark()} free-agents: this session already runs on FREE inference, so offloading saves no money "
        "here; do not delegate to cut cost. A dispatch still pays off for QUALITY: an adversarial "
        "review or an independent cross-check from a DIFFERENT model family often finds what this "
        "session's own model misses. For that, run a stateless dispatch: "
        f"`python3 {tool}` with {{\"prompt\": ..., \"files\": [paths], \"role\": \"validator\", "
        "\"avoid_family\": \"<this session's model family>\"} (attach the files; a dispatch cannot "
        "read the disk). Announce the model and route before offloading. Guidance: the offload "
        "skill (council routing)."
    )


GUARD_HOOKS = ("guard-own-endpoint.py", "guard-tool-owned-state.py", "read-pages-guard.py")


def _load_primer(hooks_dir: Path, name: str) -> str:
    """The PRIMER constant of a hook file, read via AST — a session-start hook must never
    EXECUTE a guard module just to read a string (a future module-level import could hang
    the start). "" on any problem (fail-open)."""
    import ast
    try:
        tree = ast.parse((hooks_dir / name).read_text(encoding="utf-8"))
        for node in tree.body:
            # plain Assign and annotated AnnAssign both carry the PRIMER constant
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                if node.value is None:
                    continue  # bare annotation (PRIMER: str) carries no value
                targets, value = [node.target], node.value
            else:
                continue
            if any(isinstance(t, ast.Name) and t.id == "PRIMER" for t in targets):
                return ast.literal_eval(value)
        return ""
    except Exception:
        return ""


def guards_section(modes: dict | None, free: bool) -> str:
    """One line per PreToolUse guard's PRIMER, injected once at SessionStart (prevention, so the
    guards stay a safety net). The Agent/Workflow intercept line names only paths NOT routed
    paid — route=paid is the menu opt-out that turns that hook off, so its text goes with it;
    on a FREE session the intercept passes through, so no agent line (the three guards still
    apply — own-endpoint matters most there). Fail-open: any error -> ""."""
    try:
        hooks_dir = Path(__file__).resolve().parent
        lines = [f"  - {p}" for p in (_load_primer(hooks_dir, n) for n in GUARD_HOOKS) if p]
        modes = modes or {}
        guarded = [p for p in ("agent", "workflow") if modes.get(p, "free-first") != "paid"]
        if not free and guarded:
            lines.append(f"  - Agent/Workflow calls are intercepted on: {', '.join(guarded)} "
                         f"(route=paid opts that path out).")
        if not lines:
            return ""
        return ("🛡 free-agents PreToolUse guards — know these before they fire:\n"
                + "\n".join(lines))
    except Exception:
        return ""


def main():
    # 0.50.0 help sweep: --help prints usage and never emits hook output.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("offload-nudge.py — SessionStart hook: prints the offload/council nudge plus each "
              "PreToolUse guard's primer as hook JSON on stdout. Usage: python3 offload-nudge.py [--help]")
        return 0
    try:
        root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
        # A FREE session saves nothing by offloading, so it gets the council nudge instead of the
        # cost one: a different model family is the reason to dispatch there (user 2026-10-23).
        free = is_free_session(os.environ.get("ANTHROPIC_BASE_URL", ""))
        modes = None
        if not free:
            try:
                # The repo's own bin first (always real for this deploy), then the plugin root.
                own_bin = str(Path(__file__).resolve().parent.parent / "bin")
                if own_bin not in sys.path:
                    sys.path.insert(0, own_bin)
                if root and os.path.isdir(os.path.join(root, "bin")):
                    sys.path.insert(0, os.path.join(root, "bin"))
                import cloud_session_env as cse
                modes = {path: cse.route(path, dict(os.environ)) for path in cse.ROUTE_PATHS}
            except Exception:
                modes = None                       # fail open to the free-first nudge
        text = free_nudge(root) if free else paid_nudge(root, modes)
        section = guards_section(modes, free)
        if section:
            text = f"{text}\n{section}" if text else section
        if text:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                     "additionalContext": text}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

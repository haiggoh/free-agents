#!/usr/bin/env python3
"""local-queue-stop-hook.py — Stop hook for queued prompt recovery.

Uses the shared queue_replay core for replay/classification and queue_state
for persistent acknowledgment tracking across transcript lag.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_replay as qr  # noqa: E402

# Backward-compatible exports for test_queue_marker_contract.sh
build_queue_groups = qr.build_queue_groups
_content_hash = qr._content_hash
_extract_answered_markers = qr._extract_answered_markers
_text_addresses_prompt = qr._text_addresses_prompt


# Emoji from shared constants
EMOJI_STOP_HOOK = "🪝"


def read_hook_input() -> dict:
    """Return the Stop-hook stdin payload as a dict, or {} if there is none."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def is_launcher_session() -> bool:
    """True iff this session was launched by one of THIS PROJECT'S launchers."""
    override = os.environ.get("LA_QUEUE_STOP_HOOK")
    if override == "0":
        return False
    if override == "1":
        return True
    return os.environ.get("LA_SESSION_LAUNCHER", "").strip() in KNOWN_LAUNCHERS


KNOWN_LAUNCHERS = frozenset({"csl", "launch-claude-agent.sh", "remote-session.sh"})


def session_transcripts(payload: dict | None = None):
    """Yield the transcript path for THIS session (own transcript only)."""
    tp = (payload or {}).get("transcript_path")
    if isinstance(tp, str) and tp:
        tp = os.path.expanduser(tp)
        if os.path.isfile(tp):
            yield os.path.realpath(tp)
        return

    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if not sid:
        return
    cwd = os.environ.get("CLAUDE_CWD") or os.getcwd()
    slug = "".join(c if c.isalnum() else "-" for c in cwd)
    project_dir = os.path.join(os.path.expanduser("~"), ".claude", "projects", slug)
    path = os.path.join(project_dir, f"{sid}.jsonl")
    if os.path.isfile(path):
        yield os.path.realpath(path)


def get_session_id(payload: dict | None = None) -> str:
    """Get or derive session ID."""
    # Try from payload first
    sid = (payload or {}).get("session_id")
    if isinstance(sid, str) and sid:
        return sid

    # Try from environment
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    if sid:
        return sid

    # Derive from transcript path
    for path in session_transcripts(payload):
        return os.path.basename(path).replace(".jsonl", "")

    return "unknown-session"


def format_block_reason(count: int, prompt_details: list[dict], hook_type: str, emoji: str = "🪝") -> str:
    """Format a polished, human-readable block reason with collapsible full content."""
    summary_lines = []
    for i, p in enumerate(prompt_details, 1):
        summary_lines.append(f"  {i}. {p['display'][:120]}")
    summary = "\n".join(summary_lines)

    full_content_section = "\n".join(
        f"  <details>\n"
        f"    <summary>Prompt {i}: {p['display'][:80]}</summary>\n"
        f"    <pre>{p['content']}</pre>\n"
        f"  </details>"
        for i, p in enumerate(prompt_details, 1)
    )

    helper_calls = "\n".join(
        f"  python3 bin/queue-marker-helper.py {json.dumps(p['content'])}"
        for i, p in enumerate(prompt_details, 1)
    )

    if hook_type == "undelivered":
        action = "Read the queued prompt(s) below and act on them now"
        extra = "If you see no such prompt in your context, acknowledge that you stopped with a pending request and ask the user to resend it."
    else:
        action = "Respond to each unaddressed queued prompt now"
        extra = "If you believe you already addressed a prompt, confirm by including its marker in your response."

    reason = (
        f"<system-reminder>\n"
        f"{emoji} <b>Stop Hook: Queued Prompts Detected</b>\n"
        f"\n"
        f"You just finished a turn, but {count} queued prompt(s) from the user were "
        f"{'never delivered to you' if hook_type == 'undelivered' else 'delivered but not meaningfully addressed'}.\n"
        f"\n"
        f"<b>Summary:</b>\n"
        f"{summary}\n"
        f"\n"
        f"<b>Action:</b> {action}, then stop. {extra}\n"
        f"\n"
        f"<details>\n"
        f"  <summary><b>Full prompt content (for helper script)</b></summary>\n"
        f"  <pre>{full_content_section}</pre>\n"
        f"</details>\n"
        f"\n"
        f"<b>Helper script calls:</b>\n"
        f"{helper_calls}\n"
        f"\n"
        f"To prevent this notification from reappearing, embed the agreed marker "
        f"`[[QUEUE_ANSWERED:<8-char-hash>]]` in your response for EACH queued prompt.\n"
        f"</system-reminder>"
    )
    return reason


def check_stop_hook(payload: dict, transcript_path: str) -> tuple[bool, str | None]:
    """Main stop hook logic using queue_replay core.

    Returns: (should_block, reason_json_or_none)
    """
    session_id = get_session_id(payload)

    # Full replay and classification
    result = qr.replay_and_classify(transcript_path, session_id, stdin_payload=payload)

    # Check for undelivered prompts (enqueued but never drained)
    if result.undelivered:
        count = len(result.undelivered)
        prompt_details = []
        for g in result.undelivered:
            ct = g.occurrence.content
            display_ct = ct[:300].replace("\n", " ")
            prompt_details.append({"content": ct, "display": display_ct})

        reason = format_block_reason(count, prompt_details, "undelivered", EMOJI_STOP_HOOK)
        return True, reason

    # Check for unaddressed but delivered prompts
    if result.unaddressed:
        count = len(result.unaddressed)
        prompt_details = []
        for g in result.unaddressed:
            ct = g.occurrence.content
            display_ct = ct[:300].replace("\n", " ")
            prompt_details.append({"content": ct, "display": display_ct})

        reason = format_block_reason(count, prompt_details, "unaddressed", EMOJI_STOP_HOOK)
        return True, reason

    return False, None


def main() -> int:
    if not is_launcher_session():
        return 0

    payload = read_hook_input()

    # Per plan: reconcile BEFORE checking stop_hook_active
    # (but still respect stop_hook_active as a hard bypass for safety)
    if payload.get("stop_hook_active"):
        return 0

    best_path = None
    for path in session_transcripts(payload):
        best_path = path
        break

    if not best_path:
        return 0

    should_block, reason = check_stop_hook(payload, best_path)

    if should_block and reason:
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    return 0


USAGE = """Usage: local-queue-stop-hook.py [--help]

Stop hook: notice prompts you queued that the model never answered.

Key features:
- Shared queue_replay core for reliable replay/classification
- Persistent acknowledgment tracking via queue_state (survives transcript lag)
- Per-occurrence acknowledgment with disposition (answered_now/answered_earlier/clarification)
- Legacy marker migration
- No-progress budget (max 3 consecutive continuations without new acceptance)

Environment:
  LA_SESSION_LAUNCHER   set by the launcher; must name a known launcher to enable the
                        hook. Anything else (including empty) leaves it off.
  LA_QUEUE_STOP_HOOK    0 = force OFF, 1 = force ON. Overrides the launcher marker;
                        the csl `s` toggle sets 0 when you switch the hook off.
  CLAUDE_CODE_SESSION_ID / CLAUDE_PROJECT_DIR
                        fallback transcript resolution when stdin carries no
                        transcript_path.

Exit status is always 0: a hook that fails must end the turn, never loop.
"""

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--help", "-h"):
            print(USAGE)
            sys.exit(0)
        print("local-queue-stop-hook.py: unrecognised argument %r" % sys.argv[1],
              file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
#!/usr/bin/env python3
"""Improved Local-session Stop-hook: notice queued prompts the model stopped before receiving.

This is a REFACTORED VERSION of local-queue-stop-hook.py with:
- 🪝 emoji header for clear identification
- Collapsible full-content sections (human-friendly)
- Concise main message (no repetitive full content dumps)
- Single full-content block at end for helper script

This file is for REVIEW. Apply to /Users/bra0002h/.claude/plugins/cache/haiggoh/free-agents/0.20.5/bin/local-queue-stop-hook.py after review.
"""
import json
import os
import sys

# Source emoji from emoji.sh constants
EMOJI_STOP_HOOK = "🪝"

# The marker contract lives in ONE module both this hook and queue-marker-helper.py import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_marker  # noqa: E402


def read_hook_input():
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


def session_transcripts(payload=None):
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


def build_queue_groups(transcript_path):
    """Parse the transcript and build a list of queue groups."""
    try:
        fh = open(transcript_path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return []

    with fh:
        queue_ops = []
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") == "queue-operation":
                queue_ops.append((i, o.get("operation"), o.get("content", "")))

    if not queue_ops:
        return []

    groups = []
    pending = []  # [(enqueue_line, content)] — oldest first

    def _take(drained_content):
        if drained_content:
            for i, (_, ct) in enumerate(pending):
                if ct == drained_content:
                    return pending.pop(i)
        return pending.pop(0)

    for ln, op, ct in queue_ops:
        if op == "enqueue":
            pending.append((ln, ct))
        elif op in ("remove", "dequeue"):
            if pending:
                enq_ln, enq_ct = _take(ct)
                groups.append({
                    "enqueue_line": enq_ln,
                    "content": enq_ct,
                    "drain_line": ln,
                    "drain_type": "remove",
                    "assistant_texts": [],
                    "assistant_tools": [],
                })
        elif op == "popAll":
            while pending:
                enq_ln, enq_ct = pending.pop(0)
                groups.append({
                    "enqueue_line": enq_ln,
                    "content": enq_ct,
                    "drain_line": ln,
                    "drain_type": "popAll",
                    "assistant_texts": [],
                    "assistant_tools": [],
                })

    return groups


def _extract_assistant_entry(line_idx, line_str):
    """Return (has_text, text_content, tool_names) from one assistant transcript line."""
    try:
        o = json.loads(line_str)
    except Exception:
        return False, "", []

    msg = o.get("message", None)
    if not isinstance(msg, dict):
        return False, "", []

    texts = []
    tools = []
    for b in msg.get("content", []):
        if not isinstance(b, dict):
            continue
        bt = b.get("type", "")
        if bt == "text" and b.get("text", ""):
            texts.append(b["text"])
        elif bt == "tool_use":
            tools.append(b.get("name", ""))

    return bool(texts), " ".join(texts), tools


def format_block_reason(count, prompt_details, hook_type, emoji="🪝"):
    """Format a polished, human-readable block reason with collapsible full content.

    Args:
        count: Number of unaddressed prompts
        prompt_details: List of dicts with 'content' (full) and 'display' (truncated)
        hook_type: "undelivered" or "unaddressed"
        emoji: Emoji to prefix the message

    Returns:
        Formatted string with system-reminder wrapper
    """
    # Build concise summary lines
    summary_lines = []
    for i, p in enumerate(prompt_details, 1):
        summary_lines.append(f"  {i}. {p['display'][:120]}")

    summary = "\n".join(summary_lines)

    # Build full-content section (collapsible-style)
    full_content_section = "\n".join(
        f"  <details>\n"
        f"    <summary>Prompt {i}: {p['display'][:80]}</summary>\n"
        f"    <pre>{p['content']}</pre>\n"
        f"  </details>"
        for i, p in enumerate(prompt_details, 1)
    )

    # Build helper script calls
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


def check_unanswered_queues(transcript_path, queue_groups, stdin_payload=None):
    """Check whether each queue group was actually addressed by the assistant."""
    if not transcript_path:
        return False, None

    try:
        lines = open(transcript_path, "r", encoding="utf-8", errors="replace").readlines()
    except OSError:
        return False, None

    assistant_entries = []
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") != "assistant":
            continue
        has_text, text, tools = _extract_assistant_entry(i, line)
        if has_text or tools:
            assistant_entries.append((i, has_text, text, tools))

    stdin_hashes = set()
    if stdin_payload:
        msg = stdin_payload.get("message")
        if isinstance(msg, dict):
            content_blocks = msg.get("content", [])
            if isinstance(content_blocks, list):
                for block in content_blocks:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            stdin_hashes.update(queue_marker.extract_hashes([text]))

    if not queue_groups:
        return False, None

    unaddressed = []

    for g in queue_groups:
        enq = g["enqueue_line"]
        drain = g["drain_line"]
        content = g["content"]
        if not content or enq >= drain:
            continue

        g_texts = []
        g_tools = []
        for _a_ln, a_has_text, a_text, a_tools in assistant_entries:
            if _a_ln > enq and _a_ln <= drain:
                if a_has_text:
                    g_texts.append(a_text)
                g_tools.extend(a_tools)
            elif _a_ln > drain:
                if a_has_text:
                    g_texts.append(a_text)
                g_tools.extend(a_tools)

        answered_hashes = _extract_answered_markers(g_texts)
        combined_hashes = answered_hashes | stdin_hashes

        prompt_addressed = _text_addresses_prompt(g_texts, content, combined_hashes)

        if prompt_addressed:
            continue

        post_drain_entries = [
            (a_ln, a_has_text, a_text, a_tools)
            for a_ln, a_has_text, a_text, a_tools in assistant_entries
            if a_ln > drain
        ]

        if not post_drain_entries:
            unaddressed.append((content, "zero response (popAll or remove with no assistant entry)"))
            continue

        text_turns_since_drain = 0
        tool_turns_since_drain = 0
        MAX_TOOL_TURNS_WITHOUT_TEXT = 10
        for a_ln, a_has_text, a_text, a_tools in post_drain_entries:
            if not a_has_text:
                tool_turns_since_drain += 1
                if tool_turns_since_drain >= MAX_TOOL_TURNS_WITHOUT_TEXT:
                    unaddressed.append((
                        content,
                        f"prompt unaddressed after {tool_turns_since_drain} tool turns with no natural pause"
                    ))
                    break
                continue

            tool_turns_since_drain = 0
            turn_hashes = _extract_answered_markers([a_text])
            if _text_addresses_prompt([a_text], content, turn_hashes):
                text_turns_since_drain = 0
            else:
                text_turns_since_drain += 1
                if text_turns_since_drain >= 2:
                    unaddressed.append((
                        content,
                        f"prompt ignored across {text_turns_since_drain} natural pauses"
                    ))
                    break

    if unaddressed:
        count = len(unaddressed)
        prompt_details = []
        for ct, reason in unaddressed:
            display_ct = ct[:300].replace("\n", " ")
            prompt_details.append({
                "content": ct,
                "display": display_ct,
                "reason": reason
            })

        reason = format_block_reason(count, prompt_details, "unaddressed", "🪝")
        return True, reason

    return False, None


def _content_hash(content):
    return queue_marker.content_hash(content)


def _extract_answered_markers(texts):
    if not texts:
        return set()
    return queue_marker.extract_hashes(texts)


def _text_addresses_prompt(texts, queued_content, answered_hashes=None):
    if not texts or not queued_content:
        return False

    if answered_hashes is not None:
        q_hash = _content_hash(queued_content)
        if q_hash in answered_hashes:
            return True

    text_lower = " ".join(texts).lower()
    q_lower = queued_content.lower()

    q_words = set(w for w in q_lower.replace(",", "").replace(".", "").replace('"', "").split() if len(w) >= 4)
    if not q_words:
        return False

    text_words = set(w for w in text_lower.split() if len(w) >= 4)

    overlap = q_words & text_words
    return len(overlap) >= 2


def scan_final_pending_contents(path):
    pending = []
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") != "queue-operation":
                continue
            op = o.get("operation")
            if op == "enqueue":
                content = o.get("content", "")
                if content:
                    pending.append(content)
            elif op in ("remove", "dequeue"):
                if pending:
                    pending.pop(0)
            elif op == "popAll":
                pending = []
    return pending


def main() -> int:
    if not is_launcher_session():
        return 0

    payload = read_hook_input()

    if payload.get("stop_hook_active"):
        return 0

    best_path = None
    stale_pending_contents = []
    all_queue_groups = []
    for path in session_transcripts(payload):
        best_path = path
        stale_pending_contents.extend(scan_final_pending_contents(path))
        all_queue_groups.extend(build_queue_groups(path))

    if stale_pending_contents:
        count = len(stale_pending_contents)
        prompt_details = []
        for ct in stale_pending_contents:
            display_ct = ct[:300].replace("\n", " ")
            prompt_details.append({"content": ct, "display": display_ct})

        reason = format_block_reason(count, prompt_details, "undelivered", "🪝")
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    if all_queue_groups:
        answered, warning = check_unanswered_queues(
            best_path, all_queue_groups,
            stdin_payload=payload,
        )
        if warning:
            print(json.dumps({"decision": "block", "reason": warning}))
            return 0

    return 0


USAGE = """improved-queue-stop-hook.py — Stop hook: notice prompts you queued that the model never answered.

This is a REFACTORED VERSION for review. Key improvements:
- 🪝 emoji header for clear identification
- Collapsible full-content sections (human-friendly)
- Concise main message (no repetitive full content dumps)
- Single full-content block at end for helper script

Apply to /Users/bra0002h/.claude/plugins/cache/haiggoh/free-agents/0.20.5/bin/local-queue-stop-hook.py after review.
"""

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--help", "-h"):
            print(USAGE)
            sys.exit(0)
        print("improved-queue-stop-hook.py: unrecognised argument %r" % sys.argv[1],
              file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
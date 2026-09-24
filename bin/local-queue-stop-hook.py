#!/usr/bin/env python3
"""Local-session Stop-hook: notice queued prompts the model stopped before receiving.

WHY THIS EXISTS (the gap)
  Claude Code tracks a user's queued prompts (submitted while the model was mid-run)
  IN MEMORY, per session, and delivers them over the session's Unix socket. The queue
  is drained AUTOMATICALLY at a turn boundary — but the transcript records the lifecycle
  explicitly as `queue-operation` entries:
      {"type":"queue-operation","operation":"enqueue"|"remove"|"dequeue",
       "timestamp":...,"content":...}
  A well-behaved queue drains cleanly (removes/dequeues >= enqueues). The problem case
  is a prompt that is STILL queued when the model STOPS (no pending turn) — the harness
  does not re-invoke the model to act on it. Measured on a real local session (2026-08-31):
  18 queue-operation records, 9 enqueue / 9 drain, all consumed; the model never once
  told the user it had received those prompts, because drained prompts carry
  promptSource=None and are indistinguishable from a fresh turn. The tail case —
  enqueue with no matching drain — is what this hook catches.

WHAT IT DOES
  Fires on the `Stop` event (the model just finished a turn). Resolves THIS session's
  own transcript (deterministically, via CLAUDE_CODE_SESSION_ID + cwd-slug — the same
  mechanism budget-tally.py uses) and scans it for an UNCONSUMED queue-operation.
  Own-transcript only: this is a plugin-scoped hook (local-agents), so it only fires
  for sessions using the local-agents plugin. It must not sweep the project dir's
  sibling sessions (their dead queue history is not this session's queue — see
  session_transcripts).

    * clean queue  (final pending == 0) -> scan for UNADDRESSED queues (see below).
      If every queue drain had a genuine text response to the queued content,
      print nothing, exit 0. The turn ends.
    * pending queue (final pending > 0)  -> print {"decision":"block","reason":...}.
      Claude Code treats a Stop hook's `decision:"block"` as "the turn is not done —
      feed `reason` back to the model as the next input." So the model wakes and acts
      on whatever the user queued, instead of sitting idle while prompts pile up.

    UNADDRESSED QUEUES (the second half of the equation)
      Just because `pending == 0` (the queue drained) does NOT mean the user's
      queued prompts were addressed. Three failure modes exist even when pending == 0:

      1. **Zero response** — a popAll or remove cleared the queue but there was NO
         assistant entry between the enqueue and the drain. The model's next turn
         happened to land after the drain, but had no text at all for that queue item.

      2. **Tool-use only, no text** — the assistant entries between enqueue and remove
         contain tool_use blocks (Bash, Read, Glob, etc.) but NO text blocks. The model
         executed tools but never acknowledged the queued prompt in words. This is not
         a response — it's the model doing its own thing after the queue was delivered.

      3. **Coincidental text** — the assistant produced text between enqueue and remove,
         but the text does not reference or act on the queued prompt's content. The next
         turn from the model after a queued prompt landing in the model's context window
         is often just what the model was going to say anyway; the queued prompt was
         delivered by the harness at a turn boundary, and the model continued processing
         regular turns. Coincidence in time is not a response.

      The stop hook now checks all three: if any queue group falls into category 1 or 2,
      or if text exists but does not semantically address the queued content (category 3),
      it blocks and tells the model to respond to the queued prompt.

GATING — launcher-started sessions only (the user's requirement)
  This is a PLUGIN-SCOPED Stop hook (local-agents). It only fires for sessions using
  the local-agents plugin (local/remote sessions), not for regular cloud/gateway
  sessions. We still gate on the ENDPOINT as defense-in-depth:
    * `CLAUDE_IS_LOCAL` LEAKS — it is exported by the launcher and can persist into a
      later `claude` launched from the same shell that is actually a gateway session,
      so it is a false-positive source. (Memory: local-session-self-identification.)
    * `ANTHROPIC_BASE_URL` is the authoritative signal: local sessions run against
      http://localhost:<port> / http://127.0.0.1:<port>; gateway sessions point at a
      public host (or unset, which inherits the org gateway). A localhost endpoint is
      unambiguously local.
  An env override (LA_QUEUE_STOP_HOOK=0) forces OFF for debugging; =1 forces ON.

SAFETY / NO-OP-FIRST
  * Any failure to read/parse a transcript is treated as "no pending queue" (fail-open
    to ending the turn, never to an infinite re-block).
  * The endpoint gate is the first branch; cloud sessions exit 0 immediately.
  * `stop_hook_active` IS HONOURED — see the loop guard below. Without it this hook
    blocks forever.
  * We never kill processes, never touch files, never loop — one read + one print.
  * The reason we emit names the queued prompt COUNT and the oldest queued timestamp,
    not the prompt bodies, so a giant queue cannot blow up the injected context.

LOOP GUARD — `stop_hook_active` (added 2026-09-07; the hook previously ignored it)
  A Stop hook that answers `decision:"block"` gets re-invoked when the model next tries to
  stop. Our pending count comes from the transcript's queue-operation records, which only
  gain a drain when the HARNESS delivers the queue — so if the block prevents that from
  happening, the count never falls and we block again, unbounded. Claude Code sets
  `stop_hook_active: true` on stdin for exactly this case ("this stop already followed a
  hook block"). We read stdin and return 0 immediately when it is set: block AT MOST once
  per turn, then get out of the way. Measured before the fix: piping
  {"stop_hook_active":true} still produced an identical block.

TRANSCRIPT RESOLUTION — prefer the payload, fall back to the slug
  Claude Code hands us `transcript_path` on stdin; use it. The old slug derivation
  (~/.claude/projects/<cwd-slug>/<session-id>.jsonl, mirroring budget-tally.py's
  current_session_path, with <cwd-slug> = absolute cwd with every non-alphanumeric run
  replaced by '-') is kept ONLY as a fallback for a bare invocation with no stdin.
  Rebuilding a path we were handed is how you resolve to the wrong file.

  ⚠️ KNOWN RACE, not fixed by either path: the docs state the transcript "is written
  asynchronously and may lag the in-memory conversation, so it may not yet include the
  current turn's most recent messages when a hook fires." Our verdict is derived entirely
  from that file, so the newest enqueue/dequeue of the current turn may be absent when we
  decide. The loop guard is what keeps that race from compounding — a stale read can cost
  one spurious block, never a spin.

  Own transcript only — this is a plugin-scoped hook (local-agents), so it only fires
  for sessions using the local-agents plugin (local/remote sessions). It must NOT sweep
  the project dir's sibling sessions (their dead queue history is not this session's
  queue; see session_transcripts()).

This file is part of the local-agents plugin. It is installed via the plugin system
and referenced from hooks/hooks.json using ${CLAUDE_PLUGIN_ROOT}/bin/local-queue-stop-hook.py.
"""
import json
import os
import sys

# The marker contract (hash, format, parse regex) lives in ONE module both this hook and
# queue-marker-helper.py import, so the two can never drift apart. The hook is invoked by
# absolute path from hooks.json, and in the installed plugin cache the CWD is the user's
# project, not here — so make this file's own directory importable rather than relying on
# sys.path happening to contain it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import queue_marker  # noqa: E402


def read_hook_input():
    """Return the Stop-hook stdin payload as a dict, or {} if there is none.

    Fail-open by design: a bare invocation (no stdin, closed stdin, non-JSON) yields {},
    which makes stop_hook_active falsy and transcript_path absent, so the caller falls
    back to the slug derivation and behaves exactly as the pre-stdin version did.
    """
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
    """True iff this session was launched by one of THIS PROJECT'S launchers.

    WHY A LAUNCHER MARKER RATHER THAN THE ENDPOINT (the user's requirement, 2026-09-16).
      The requirement is that the hook fires ONLY for sessions started through `csl`,
      `launch-claude-agent.sh`, or `remote-session.sh` — never in an ordinary cloud/gateway
      or direct-Anthropic session. The endpoint sniff this replaces could not express that:

        * it MISSED remote sessions. `remote-session.sh` points Claude Code at a LiteLLM
          proxy on http://127.0.0.1:<port>, which the old check read as "local" — correct by
          accident — while a future non-loopback proxy would have silently disabled the hook.
        * it could FIRE for a session this project did not launch. Any other tool pointing
          Claude Code at localhost inherited the hook.
        * it answers the wrong question. "Is the endpoint loopback?" is a proxy for "did one
          of our launchers start this?", and a proxy for the real predicate is exactly how
          the gate drifts from the requirement.

      Each launcher stamps LA_SESSION_LAUNCHER with its own name, so the gate now tests the
      thing it actually cares about. A plain `claude` never sets it and exits at branch one.

    THE LEAK CAVEAT STILL APPLIES, and is why the marker is not merely "is it set".
      `CLAUDE_IS_LOCAL` leaks: a launcher exports it, and a LATER gateway `claude` started
      from the same shell inherits it (memory: local-session-self-identification). The same
      hazard applies to any exported marker. It is mitigated, not ignored: the launchers set
      LA_SESSION_LAUNCHER on the `claude` process itself (env prefix / export immediately
      before exec), and the value must match a KNOWN launcher name — an inherited empty or
      unrecognised value does not open the gate.

    TOGGLE PRECEDENCE (highest first):
      1. LA_QUEUE_STOP_HOOK=0|1 — explicit override, wins over everything (debugging, and
         the csl `s` toggle sets it to 0 when the user turns the hook off).
      2. LA_SESSION_LAUNCHER in KNOWN_LAUNCHERS — the normal path.
      3. anything else -> False. Fail CLOSED for the gate (never fire where unwanted),
         which is the opposite of the transcript-read failure mode below (fail OPEN to
         ending the turn, never to an infinite re-block).
    """
    override = os.environ.get("LA_QUEUE_STOP_HOOK")
    if override == "0":
        return False
    if override == "1":
        return True
    return os.environ.get("LA_SESSION_LAUNCHER", "").strip() in KNOWN_LAUNCHERS


# The launchers permitted to enable this hook. A value outside this set — including the
# empty string an inherited-but-unset variable produces — does NOT enable it.
KNOWN_LAUNCHERS = frozenset({"csl", "launch-claude-agent.sh", "remote-session.sh"})


def session_transcripts(payload=None):
    """Yield the transcript path for THIS session.

    PREFERS payload["transcript_path"] — the harness tells us which file is ours, so
    re-deriving it is a chance to be wrong. The slug derivation below is the fallback for
    a bare invocation with no stdin.

    OWN transcript ONLY. This hook is a PLUGIN-SCOPED Stop hook (local-agents), so it
    fires only for sessions using the local-agents plugin. Resolving to the session's
    own <session-id>.jsonl is therefore the only correct scope: a queued prompt in this
    session's queue is this session's state.

    Deliberately NOT globbing sibling *.jsonl: each session logs its OWN queue lifecycle,
    and a session that was KILLED or /clear'd mid-queue leaves queue-ops that were never
    drained — permanently. Summing the whole project dir would resurrect every such
    historical tail as a "pending" prompt for the current (unrelated) session. Measured on
    a real session 2026-09-01: the dir held 54 transcripts; own was 4 enq / 4 drain (clean)
    but 9 other finished sessions carried 17 undrained net, and the global Stop hook
    reported a false "17 prompts queued" — waking a session that had nothing pending.
    (The sibling glob was originally meant for a resumed/compacted session whose on-disk
    id differs from CLAUDE_CODE_SESSION_ID, but for a plugin-scoped hook that benefit is
    outweighed many times over by the false-positive blast radius.)
    """
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
    """Parse the transcript and build a list of queue groups.

    A queue group is one enqueue ... matching drain (remove / popAll).  Each group
    captures the queue content, the enqueue line, and the drain line.

    Groups are used by check_unanswered_queues() to find what the assistant did
    between the enqueue and the drain.  A group with no assistant text, or with
    only tool_use (no text blocks), counts as unaddressed.

    Returns: list[dict] each with keys:
        enqueue_line, content, drain_line, drain_type (remove/popAll),
        assistant_texts (list[str]), assistant_tools (list[str])
    """
    try:
        fh = open(transcript_path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return []

    # 1. Extract all queue operations
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

    # 2. Build groups: match each enqueue to its drain (remove or popAll)
    #
    # PENDING IS FIFO, NOT A STACK. A drain op also CARRIES the content it drained, so
    # prefer that ground truth and fall back to FIFO order only when it is absent or
    # names something not pending. The previous implementation popped the most RECENT
    # enqueue (LIFO) and ignored the content field entirely, so with two or more queued
    # prompts every drain was attributed the wrong content — and since the marker hash is
    # computed from that content, a correct [[QUEUE_ANSWERED:...]] could never match. The
    # bug was invisible with a single queued prompt, where LIFO and FIFO coincide.
    groups = []
    pending = []  # [(enqueue_line, content)] — oldest first

    def _take(drained_content):
        """Remove and return the pending entry this drain refers to."""
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
            # popAll clears all currently-pending enqueues, oldest first so the groups
            # come out in the order the prompts were queued.
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


def check_unanswered_queues(transcript_path, queue_groups, stdin_payload=None):
    """Check whether each queue group was actually addressed by the assistant.

    For each queue group, examine the assistant entries that appear between
    the enqueue line and the drain line.  A group is unaddressed if:

    1. **Zero response** — no assistant entries at all between enqueue and drain
       (common with popAll: the queue is cleared but the model never responds).

    2. **Tool-use only** — all assistant entries between enqueue and drain contain
       tool_use blocks but NO text blocks. The model executed tools but never
       acknowledged the queued prompt in words. This is not a response — it is
       the model's own next turn happening to land after the queue was delivered.

    3. **Coincidental text** — text exists but does not address the queued prompt's
       content. The model's next turn after any queued prompt is often just what
       it was going to say anyway; the queued prompt was delivered by the harness
       at a turn boundary, and the model continued processing regular turns.
       Coincidence in time is not a response.

    RACE FIX: The transcript file is written ASYNCHRONOUSLY and may lag the
    in-memory conversation. The Stop hook receives the current turn's data on
    stdin (payload["message"]). Check THAT for QUEUE_ANSWERED markers FIRST,
    before falling back to the transcript read. This eliminates the race where
    a correctly-marked response triggers re-notification because the transcript
    hasn't caught up yet.

    Returns (has_issue, message) where message is a human-readable block reason
    (or None if no issue was detected).
    """
    if not transcript_path:
        return False, None

    # Build a line cache for the transcript (only need specific ranges)
    try:
        lines = open(transcript_path, "r", encoding="utf-8", errors="replace").readlines()
    except OSError:
        return False, None

    # Extract all assistant entries: (line_index, has_text, text, tools)
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

    # RACE FIX: Extract marker hashes from stdin payload (current turn) FIRST.
    # The transcript lags; the stdin payload has the just-completed turn.
    stdin_hashes = set()
    if stdin_payload:
        # payload["message"] is the current assistant message (dict with "content": [...])
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

    unaddressed = []  # (group_content, reason)

    for g in queue_groups:
        enq = g["enqueue_line"]
        drain = g["drain_line"]
        content = g["content"]
        if not content or enq >= drain:
            continue

        # Find assistant entries between enqueue and drain, AND after drain (all subsequent turns)
        # The queue is delivered at a turn boundary, so the model's response comes
        # in the turn AFTER the drain. We check ALL assistant entries after drain
        # because the model might not include the marker in the immediate response
        # but add it in a subsequent turn after being prompted by the hook.
        g_texts = []
        g_tools = []
        for a_ln, a_has_text, a_text, a_tools in assistant_entries:
            if a_ln > enq and a_ln <= drain:
                # Between enqueue and drain (original logic)
                if a_has_text:
                    g_texts.append(a_text)
                g_tools.extend(a_tools)
            elif a_ln > drain:
                # After drain - check ALL assistant entries (model may add marker later)
                if a_has_text:
                    g_texts.append(a_text)
                g_tools.extend(a_tools)
                # No break - check all subsequent turns for the marker

        # Extract explicit answer markers from all texts in this group
        answered_hashes = _extract_answered_markers(g_texts)

        # Combine transcript markers with stdin (current turn) markers for race fix
        combined_hashes = answered_hashes | stdin_hashes

        # Check if ANY text in the group addresses the prompt (via marker or semantic overlap)
        prompt_addressed = _text_addresses_prompt(g_texts, content, combined_hashes)

        if prompt_addressed:
            # Prompt was addressed - nothing to do for this group
            continue

        # Prompt NOT addressed. Analyze post-drain turns to find the natural "seam" for nudging.
        # The harness drains queues at turn boundaries (natural pauses). We piggyback on this:
        # nudge at the SECOND text-only turn after drain. The first text turn is the
        # harness's natural pause detection (benefit of doubt - might be coincidental).
        # The second text turn without addressing = pattern of ignoring.
        post_drain_entries = [
            (a_ln, a_has_text, a_text, a_tools)
            for a_ln, a_has_text, a_text, a_tools in assistant_entries
            if a_ln > drain
        ]

        if not post_drain_entries:
            # Zero response - no turns at all after drain
            unaddressed.append((content, "zero response (popAll or remove with no assistant entry)"))
            continue

        # Count text turns after drain (turns with text content)
        # A text turn that addresses the prompt resets the count.
        # Emergency safeguard: if 10+ tool-only turns with no text turns, force nudge.
        text_turns_since_drain = 0
        tool_turns_since_drain = 0
        MAX_TOOL_TURNS_WITHOUT_TEXT = 10
        for a_ln, a_has_text, a_text, a_tools in post_drain_entries:
            if not a_has_text:
                # Tool-only turn - doesn't count as a "pause"
                tool_turns_since_drain += 1
                if tool_turns_since_drain >= MAX_TOOL_TURNS_WITHOUT_TEXT:
                    # Emergency: model has been tooling for too long without a natural pause
                    unaddressed.append((
                        content,
                        f"prompt unaddressed after {tool_turns_since_drain} tool turns with no natural pause (assistant used tools but never produced text to address the queued prompt)"
                    ))
                    break
                continue

            # Reset tool turn counter on text turn (natural pause occurred)
            tool_turns_since_drain = 0

            turn_hashes = _extract_answered_markers([a_text])
            if _text_addresses_prompt([a_text], content, turn_hashes):
                # This text turn addressed the prompt - reset counter
                text_turns_since_drain = 0
            else:
                # Text turn that didn't address the prompt
                text_turns_since_drain += 1
                if text_turns_since_drain >= 2:
                    # Second text turn ignoring the prompt - this is the natural seam to nudge
                    unaddressed.append((
                        content,
                        f"prompt ignored across {text_turns_since_drain} natural pauses (assistant produced text but did not address the queued prompt's content)"
                    ))
                    break  # Stop checking this group - we've found the nudge point

    if unaddressed:
        count = len(unaddressed)
        # Build detailed list with FULL prompt content so the model can run the helper script
        prompt_details = []
        for ct, reason in unaddressed:
            display_ct = ct[:300].replace("\n", " ")
            prompt_details.append({
                "content": ct,  # FULL content for helper script input
                "display": display_ct,
                "reason": reason
            })

        reason_str = "; ".join(
            f'"{p["display"][:120]}" — {p["reason"]}'
            for p in prompt_details[:3]
        )

        # Build the explicit prompt list with FULL CONTENT for the helper script
        prompt_list = "\n".join(
            f'  - Full content for helper script: """{p["content"]}"""\n'
            f'    Run: python3 bin/queue-marker-helper.py """{p["content"]}"""'
            for p in prompt_details
        )

        reason = (
            f"<system-reminder>You just finished a turn, but your transcript shows "
            f"that {count} of your queued prompts were delivered while you were running "
            f"and were not meaningfully addressed. {reason_str}. "
            f"To prevent this notification from reappearing, embed the agreed marker "
            f"`[[QUEUE_ANSWERED:<8-char-hash>]]` in your response for EACH queued prompt.\n\n"
            f"UN addressed queued prompts (use the helper script with the EXACT content below):\n"
            f"{prompt_list}\n\n"
            f"Respond to each queued prompt now, then include the marker the helper script "
            f"outputs in your response, then stop. If you believe you already addressed a prompt, "
            f"confirm by including its marker in your response.</system-reminder>"
        )
        return True, reason

    return False, None


def _content_hash(content):
    """Return a short hash of the queued content for marker matching."""
    return queue_marker.content_hash(content)


def _extract_answered_markers(texts):
    """Extract [[QUEUE_ANSWERED:<hash>]] markers from assistant texts.

    Returns a set of content hashes that have been explicitly marked as answered.
    """
    if not texts:
        return set()
    return queue_marker.extract_hashes(texts)


def _text_addresses_prompt(texts, queued_content, answered_hashes=None):
    """Heuristic: does ANY text semantically overlap with the queued content?

    Returns True if the text contains meaningful words/shapes from the queued
    prompt that would not normally appear in generic assistant text.  If the
    text is about the same topic (keywords, phrase fragments, or the user's
    specific concern), we count it as addressed.

    This is intentionally conservative: if the text mentions the queued content's
    subject matter even partially, the model responded.  If the text is generic
    session-continuation prose with no traces of the queued content, it is
    coincidental.

    Also checks for explicit [[QUEUE_ANSWERED:<hash>]] markers.
    """
    if not texts or not queued_content:
        return False

    # Check for explicit answer marker first
    if answered_hashes is not None:
        q_hash = _content_hash(queued_content)
        if q_hash in answered_hashes:
            return True

    text_lower = " ".join(texts).lower()
    q_lower = queued_content.lower()

    # Split both into words (alphanumeric tokens, length >= 4 chars to avoid noise)
    q_words = set(w for w in q_lower.replace(",", "").replace(".", "").replace('"', "").split() if len(w) >= 4)
    if not q_words:
        return False  # queued content has no extractable words

    text_words = set(w for w in text_lower.split() if len(w) >= 4)

    # Check: do >= 2 content words from the queued prompt appear in the text?
    overlap = q_words & text_words
    return len(overlap) >= 2


def main() -> int:
    # First branch: only sessions started by one of this project's launchers engage.
    # A plain cloud/gateway `claude` returns here having done nothing. (Also: a disabled hook.)
    if not is_launcher_session():
        return 0

    payload = read_hook_input()

    # LOOP GUARD: this stop already followed a hook block. Block at most once per turn,
    # then stand down — our pending count cannot fall until the harness drains the queue,
    # and blocking is what prevents that. See the module docstring.
    if payload.get("stop_hook_active"):
        return 0

    best_path = None
    stale_pending_contents = []  # un-drained enqueue contents (old check)
    all_queue_groups = []  # all groups for the new check
    for path in session_transcripts(payload):
        best_path = path  # take the last one (most likely the only one)
        # 1. Old check: final pending > 0 (un-drained enqueues)
        stale_pending_contents.extend(scan_final_pending_contents(path))
        # 2. New check: all queue groups for unaddressed detection
        all_queue_groups.extend(build_queue_groups(path))

    # Check 1: stale queue (prompts never drained by the harness)
    if stale_pending_contents:
        count = len(stale_pending_contents)
        prompt_details = []
        for ct in stale_pending_contents:
            display_ct = ct[:300].replace("\n", " ")
            prompt_details.append({
                "content": ct,  # FULL content for helper script
                "display": display_ct
            })

        prompt_list = "\n".join(
            f'  - Full content for helper script: """{p["content"]}"""\n'
            f'    Run: python3 bin/queue-marker-helper.py """{p["content"]}"""'
            for p in prompt_details
        )

        reason = (
            f"<system-reminder>You just finished a turn, but {count} prompt(s) are still "
            f"queued from the user (submitted while you were running, not yet delivered to "
            f"you). Read the queued prompt(s) below and act on them now, "
            f"then stop. If you see no such prompt in your context, acknowledge that you "
            f"stopped with a pending request and ask the user to resend it.\n\n"
            f"Queued prompts not yet delivered (use the helper script with the EXACT content below):\n"
            f"{prompt_list}\n\n"
            f"Respond to each queued prompt now, then include the marker the helper script "
            f"outputs in your response, then stop.</system-reminder>"
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    # Check 2: queue drained but responses were unaddressed (zero response, tool-use only,
    # or coincidental text).  This catches what the old hook missed: prompts that were
    # "pending == 0" but never meaningfully answered.
    if all_queue_groups:
        answered, warning = check_unanswered_queues(
            best_path, all_queue_groups,
            stdin_payload=payload,
        )
        if warning:
            print(json.dumps({"decision": "block", "reason": warning}))
            return 0

    return 0


def scan_final_pending_contents(path):
    """Return list of un-drained enqueue contents for one transcript.

    Used alongside the new queue-groups check.  If the queue has not yet been
    drained (stale pending > 0), that takes priority.
    """
    # Track pending enqueues with their content
    pending = []  # list of enqueue contents
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
                    pending.pop(0)  # FIFO: remove oldest
            elif op == "popAll":
                pending = []
    return pending


USAGE = """local-queue-stop-hook.py — Stop hook: notice prompts you queued that the model
never answered.

Reads the Stop-hook JSON payload on stdin and, if this session left a queued prompt
un-drained or drained-but-unanswered, prints {"decision":"block","reason":...} so the
harness feeds the prompt back to the model instead of ending the turn.

Usage:
  local-queue-stop-hook.py            # normal operation (Stop hook; reads stdin)
  local-queue-stop-hook.py --help     # this message

Gating — it engages ONLY for sessions started by this project's launchers:
  csl · launch-claude-agent.sh · remote-session.sh
A plain cloud/gateway `claude` exits immediately having done nothing.

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
    # Parse arguments BEFORE doing any work. A script that ignores --help and just RUNS is
    # worse than one with no help at all: the probe becomes an unintended execution whose
    # output looks like help. (This script previously exited 0 printing NOTHING for --help,
    # while still running the hook.)
    if len(sys.argv) > 1:
        if sys.argv[1] in ("--help", "-h"):
            print(USAGE)
            sys.exit(0)
        print("local-queue-stop-hook.py: unrecognised argument %r" % sys.argv[1],
              file=sys.stderr)
        print("Usage: local-queue-stop-hook.py [--help]   (normally invoked as a Stop hook)",
              file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(main())
    except Exception:
        # Fail open: any unexpected error ends the turn rather than looping.
        sys.exit(0)
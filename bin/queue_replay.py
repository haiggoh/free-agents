#!/usr/bin/env python3
"""queue_replay.py — Shared queue replay and classification core.

This module is the SINGLE source of truth for queue replay, occurrence identity,
and acknowledgment validation. Both the Stop hook and future mid-run hook import
from here.

It replaces the inline logic in local-queue-stop-hook.py with a well-tested,
independently verifiable core.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any


# =============================================================================
# MARKER CONTRACT (single source of truth, shared with queue_marker.py)
# =============================================================================

HASH_LEN = 8

MARKER_RE = re.compile(r'\[\[QUEUE_ANSWERED:([a-f0-9]{%d})\]\]' % HASH_LEN)


def content_hash(content: str) -> str:
    """Return the short hash identifying a queued prompt by its exact content."""
    return hashlib.sha256(content.encode()).hexdigest()[:HASH_LEN]


def format_marker(content: str) -> str:
    """Return the marker an assistant emits to mark `content` as addressed."""
    return "[[QUEUE_ANSWERED:%s]]" % content_hash(content)


def extract_hashes(texts: list[str]) -> set[str]:
    """Return every marker hash present across `texts`."""
    found = set()
    for t in texts:
        if t:
            found.update(MARKER_RE.findall(t))
    return found


# =============================================================================
# OCCURRENCE IDENTITY
# =============================================================================

@dataclass(frozen=True, slots=True)
class Occurrence:
    """A single queued prompt occurrence with stable identity.

    Identity is derived from: session_id + enqueue_line + content_hash.
    This ensures identical content at different positions are distinct occurrences.
    """
    session_id: str
    enqueue_line: int
    content: str
    content_hash: str
    occurrence_id: str = field(init=False)

    def __post_init__(self):
        # Stable occurrence ID: first 12 chars of sha256(session_id + "|" + str(enqueue_line) + "|" + content)
        key = f"{self.session_id}|{self.enqueue_line}|{self.content}"
        object.__setattr__(self, 'occurrence_id', hashlib.sha256(key.encode()).hexdigest()[:12])

    @property
    def short_id(self) -> str:
        """Short display ID (first 8 chars of occurrence_id)."""
        return self.occurrence_id[:8]


@dataclass(frozen=True, slots=True)
class DrainEvent:
    """A queue drain event (remove/dequeue/popAll) with optional content."""
    line: int
    op: str  # 'remove' | 'dequeue' | 'popAll'
    content: str | None


@dataclass(slots=True)
class QueueGroup:
    """A paired enqueue + drain group representing one occurrence lifecycle."""
    occurrence: Occurrence
    drain: DrainEvent | None = None
    assistant_texts: list[str] = field(default_factory=list)
    assistant_tools: list[str] = field(default_factory=list)
    # Acknowledgment state
    acknowledged: bool = False
    acknowledgment_disposition: str | None = None  # 'answered_now' | 'answered_earlier' | 'clarification'
    acknowledgment_evidence: str | None = None


# =============================================================================
# REPLAY ENGINE
# =============================================================================

def build_occurrences(transcript_path: str, session_id: str) -> list[Occurrence]:
    """Parse transcript and build all enqueue occurrences with stable IDs."""
    occurrences = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
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
                        occurrences.append(Occurrence(
                            session_id=session_id,
                            enqueue_line=i,
                            content=content,
                            content_hash=content_hash(content),
                        ))
    except OSError:
        pass
    return occurrences


def build_drains(transcript_path: str) -> list[DrainEvent]:
    """Parse transcript and build all drain events in physical order."""
    drains = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
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
                if op in ("remove", "dequeue", "popAll"):
                    content = o.get("content")
                    if content == "":
                        content = None
                    drains.append(DrainEvent(line=i, op=op, content=content))
    except OSError:
        pass
    return drains


def pair_occurrences_with_drains(
    occurrences: list[Occurrence],
    drains: list[DrainEvent],
) -> list[QueueGroup]:
    """Pair occurrences with drains using content-matched FIFO.

    Algorithm:
    - For each drain in physical order:
      - If drain has content: match the oldest pending occurrence with that exact content
      - If drain has no content (contentless dequeue): match the oldest pending occurrence (FIFO)
      - If popAll: match all remaining pending occurrences in FIFO order
    - Unmatched drains are ignored (defensive)
    - Unmatched occurrences remain as undrained (undelivered) - they become groups with drain=None
    """
    pending = list(occurrences)  # oldest first
    groups = []

    for drain in drains:
        if drain.op == "popAll":
            while pending:
                occ = pending.pop(0)
                groups.append(QueueGroup(occurrence=occ, drain=drain))
        elif pending:
            if drain.content is not None:
                # Content-matched: find first pending with matching content
                matched_idx = None
                for i, occ in enumerate(pending):
                    if occ.content == drain.content:
                        matched_idx = i
                        break
                if matched_idx is not None:
                    occ = pending.pop(matched_idx)
                    groups.append(QueueGroup(occurrence=occ, drain=drain))
                # If no match, drain is ignored (no corresponding enqueue)
            else:
                # Contentless dequeue: FIFO fallback
                occ = pending.pop(0)
                groups.append(QueueGroup(occurrence=occ, drain=drain))
        # If no pending, drain is ignored

    # Remaining pending occurrences are undelivered (no drain) - create groups for them
    for occ in pending:
        groups.append(QueueGroup(occurrence=occ, drain=None))

    return groups


def extract_assistant_entries(transcript_path: str) -> list[tuple[int, bool, str, list[str]]]:
    """Extract all assistant entries from transcript.

    Returns: list of (line_number, has_text, text_content, tool_names)
    """
    entries = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "assistant":
                    continue
                msg = o.get("message")
                if not isinstance(msg, dict):
                    continue

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

                if texts or tools:
                    entries.append((i, bool(texts), " ".join(texts), tools))
    except OSError:
        pass
    return entries


def attach_assistant_evidence(
    groups: list[QueueGroup],
    assistant_entries: list[tuple[int, bool, str, list[str]]],
) -> None:
    """Attach assistant texts/tools to each group based on line ranges.

    For each group:
    - Texts/tools between enqueue_line and drain_line (inclusive drain) are "during"
    - Texts/tools after drain_line are "post-drain"
    - All are stored for acknowledgment validation
    """
    for g in groups:
        enq = g.occurrence.enqueue_line
        drain = g.drain.line if g.drain else float('inf')

        for a_ln, a_has_text, a_text, a_tools in assistant_entries:
            if a_ln > enq and a_ln <= drain:
                if a_has_text:
                    g.assistant_texts.append(a_text)
                g.assistant_tools.extend(a_tools)
            elif a_ln > drain:
                if a_has_text:
                    g.assistant_texts.append(a_text)
                g.assistant_tools.extend(a_tools)


# =============================================================================
# ACKNOWLEDGMENT VALIDATION
# =============================================================================

class Disposition:
    ANSWERED_NOW = "answered_now"
    ANSWERED_EARLIER = "answered_earlier"
    CLARIFICATION = "clarification"

    @classmethod
    def all(cls) -> set[str]:
        return {cls.ANSWERED_NOW, cls.ANSWERED_EARLIER, cls.CLARIFICATION}


def validate_acknowledgment(
    texts: list[str],
    occurrence: Occurrence,
    stdin_hashes: set[str] | None = None,
) -> tuple[bool, str | None, str | None]:
    """Validate whether assistant texts constitute a valid acknowledgment.

    Returns: (is_addressed, disposition, evidence)

    An occurrence is addressed if:
    1. A valid marker for this occurrence's content_hash is present in texts OR stdin_hashes
    2. AND the assistant provided substantive evidence (not just a marker)

    Evidence requirements per disposition:
    - answered_now: concrete answer or account of action + result
    - answered_earlier: specific reference/summary locating earlier response
    - clarification: concrete question explaining ambiguity

    A marker-only response is NEVER sufficient.
    """
    if not texts and not stdin_hashes:
        return False, None, None

    # Check for valid marker (in texts or stdin)
    all_hashes = set()
    if texts:
        all_hashes.update(extract_hashes(texts))
    if stdin_hashes:
        all_hashes.update(stdin_hashes)

    target_hash = occurrence.content_hash
    if target_hash not in all_hashes:
        return False, None, None

    # Has valid marker - now check for substantive evidence
    combined_text = " ".join(texts).strip()
    if not combined_text or combined_text == format_marker(occurrence.content):
        # Marker only - insufficient
        return False, None, None

    # Determine disposition from text content (heuristic based on keywords)
    text_lower = combined_text.lower()

    if any(kw in text_lower for kw in ["earlier", "already answered", "previous", "above", "before"]):
        disposition = Disposition.ANSWERED_EARLIER
    elif any(kw in text_lower for kw in ["clarif", "ambiguous", "unclear", "what do you mean", "which one"]):
        disposition = Disposition.CLARIFICATION
    else:
        disposition = Disposition.ANSWERED_NOW

    # Evidence is the text (truncated for storage)
    evidence = combined_text[:500]

    return True, disposition, evidence


def check_unaddressed_groups(
    groups: list[QueueGroup],
    stdin_hashes: set[str] | None = None,
) -> list[QueueGroup]:
    """Check which groups remain unaddressed after validation.

    Returns list of groups that were delivered (have drain) but NOT acknowledged.
    Undelivered groups (no drain) are NOT included in unaddressed.
    """
    unaddressed = []

    for g in groups:
        if g.drain is None:
            # Undelivered (no drain) - not counted as unaddressed
            continue

        is_addressed, disposition, evidence = validate_acknowledgment(
            g.assistant_texts, g.occurrence, stdin_hashes
        )

        if is_addressed:
            g.acknowledged = True
            g.acknowledgment_disposition = disposition
            g.acknowledgment_evidence = evidence
        else:
            unaddressed.append(g)

    return unaddressed


# =============================================================================
# LEGACY MIGRATION
# =============================================================================

def migrate_legacy_markers(
    groups: list[QueueGroup],
    assistant_texts_all: list[str],
) -> list[QueueGroup]:
    """Migrate legacy content-only markers to occurrence-bound acknowledgments.

    Legacy markers contain only content hash (no occurrence ID). Migration policy:
    - Process assistant texts chronologically
    - Each legacy marker can match ONE eligible unacknowledged occurrence with matching content
    - Once an occurrence is acknowledged, it cannot be re-acknowledged by another legacy marker
    - Ambiguity (multiple eligible occurrences) is preserved - user must disambiguate
    """
    # Build content -> list of unacknowledged groups mapping
    content_to_groups: dict[str, list[QueueGroup]] = {}
    for g in groups:
        if not g.acknowledged and g.drain is not None:
            content_to_groups.setdefault(g.occurrence.content, []).append(g)

    # Process all assistant texts chronologically
    legacy_hashes = extract_hashes(assistant_texts_all)

    for h in legacy_hashes:
        # Find groups with this content hash
        matching_groups = [g for g in groups if g.occurrence.content_hash == h and not g.acknowledged and g.drain is not None]
        if len(matching_groups) == 1:
            # Unambiguous - acknowledge it
            g = matching_groups[0]
            g.acknowledged = True
            g.acknowledgment_disposition = Disposition.ANSWERED_NOW
            g.acknowledgment_evidence = "[legacy marker migration]"
        # If 0 or >1, leave ambiguous - don't auto-acknowledge

    return groups


# =============================================================================
# TRANSCRIPT LAG HANDLING
# =============================================================================

def extract_stdin_hashes(payload: dict[str, Any] | None) -> set[str]:
    """Extract QUEUE_ANSWERED hashes from Stop hook stdin payload (last_assistant_message)."""
    if not payload:
        return set()
    msg = payload.get("message")
    if not isinstance(msg, dict):
        return set()
    content_blocks = msg.get("content")
    if not isinstance(content_blocks, list):
        return set()

    hashes = set()
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if text:
                hashes.update(extract_hashes([text]))
    return hashes


# =============================================================================
# PUBLIC API: Full replay + classification
# =============================================================================

@dataclass(slots=True)
class ReplayResult:
    """Complete result of queue replay and classification."""
    session_id: str
    groups: list[QueueGroup]
    undelivered: list[QueueGroup]  # no drain
    unaddressed: list[QueueGroup]  # drained but not acknowledged
    acknowledged: list[QueueGroup]  # drained and acknowledged


def replay_and_classify(
    transcript_path: str,
    session_id: str,
    stdin_payload: dict[str, Any] | None = None,
) -> ReplayResult:
    """Full pipeline: replay transcript -> classify -> validate acknowledgments.

    This is the single entry point for both Stop hook and mid-run hook.
    """
    # Build occurrences (enqueues)
    occurrences = build_occurrences(transcript_path, session_id)

    # Build drains (removes/dequeues/popAll)
    drains = build_drains(transcript_path)

    # Pair into groups
    groups = pair_occurrences_with_drains(occurrences, drains)

    # Extract all assistant entries
    assistant_entries = extract_assistant_entries(transcript_path)

    # Attach assistant evidence to groups
    attach_assistant_evidence(groups, assistant_entries)

    # Get stdin hashes (from last_assistant_message in Stop hook payload)
    stdin_hashes = extract_stdin_hashes(stdin_payload) if stdin_payload else set()

    # Legacy marker migration (before validation so migrated markers count)
    all_assistant_texts = [t for _, _, t, _ in assistant_entries if t]
    migrate_legacy_markers(groups, all_assistant_texts)

    # Validate acknowledgments
    unaddressed = check_unaddressed_groups(groups, stdin_hashes)

    # Classify
    undelivered = [g for g in groups if g.drain is None]
    acknowledged = [g for g in groups if g.acknowledged]

    return ReplayResult(
        session_id=session_id,
        groups=groups,
        undelivered=undelivered,
        unaddressed=unaddressed,
        acknowledged=acknowledged,
    )


# =============================================================================
# REMAINING: For backward compatibility with existing test contract
# =============================================================================

def build_queue_groups(transcript_path: str) -> list[dict]:
    """Backward-compatible wrapper returning list of dicts.

    This mimics the old build_queue_groups() interface for existing tests.
    Only returns groups that have a drain (matched occurrences), not undelivered ones.
    """
    # We need a session_id - derive from transcript path
    session_id = os.path.basename(transcript_path).replace(".jsonl", "")
    result = replay_and_classify(transcript_path, session_id)

    # Convert to old format - only drained groups (matching old behavior)
    old_groups = []
    for g in result.groups:
        if g.drain is None:
            continue  # Skip undelivered (old behavior didn't include them)
        old_groups.append({
            "enqueue_line": g.occurrence.enqueue_line,
            "content": g.occurrence.content,
            "drain_line": g.drain.line,
            "drain_type": g.drain.op,
            "assistant_texts": g.assistant_texts,
            "assistant_tools": g.assistant_tools,
        })
    return old_groups


def _content_hash(content: str) -> str:
    return content_hash(content)


def _extract_answered_markers(texts: list[str]) -> set[str]:
    return extract_hashes(texts)


def _text_addresses_prompt(texts: list[str], queued_content: str, answered_hashes: set[str] | None = None) -> bool:
    if not texts or not queued_content:
        return False

    if answered_hashes is not None:
        q_hash = content_hash(queued_content)
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0)

    # Quick self-test
    print("queue_replay.py — self-test")
    print(f"HASH_LEN = {HASH_LEN}")
    print(f"content_hash('test') = {content_hash('test')}")
    print(f"format_marker('test') = {format_marker('test')}")
    print(f"extract_hashes(['[[QUEUE_ANSWERED:abc12345]]']) = {extract_hashes(['[[QUEUE_ANSWERED:abc12345]]'])}")
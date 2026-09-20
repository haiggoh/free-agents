#!/usr/bin/env python3
"""queue_marker.py — the single source of truth for the QUEUE_ANSWERED marker contract.

Both `local-queue-stop-hook.py` (which PARSES markers) and `queue-marker-helper.py`
(which EMITS them) import from here. Before this module the contract was restated in
three independent forms across those two files — the hash algorithm, the marker syntax,
and the regex that parses it. They happened to agree, but nothing enforced it: changing
`[:8]` to `[:12]` on one side would have produced markers the other side silently
rejects, which is indistinguishable from the marker mechanism being broken.

Import this rather than re-deriving any of the three.
"""

import hashlib
import re

# Length of the hex digest carried in a marker. The parse pattern is derived from this
# value, so the two can never disagree.
HASH_LEN = 8

MARKER_RE = re.compile(r'\[\[QUEUE_ANSWERED:([a-f0-9]{%d})\]\]' % HASH_LEN)


def content_hash(content: str) -> str:
    """Return the short hash identifying a queued prompt by its exact content."""
    return hashlib.sha256(content.encode()).hexdigest()[:HASH_LEN]


def format_marker(content: str) -> str:
    """Return the marker an assistant emits to mark `content` as addressed."""
    return "[[QUEUE_ANSWERED:%s]]" % content_hash(content)


def extract_hashes(texts) -> set:
    """Return every marker hash present across `texts`."""
    found = set()
    for t in texts:
        if t:
            found.update(MARKER_RE.findall(t))
    return found

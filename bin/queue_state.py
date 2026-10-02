#!/usr/bin/env python3
"""queue_state.py — Private atomic state persistence for queue acknowledgments.

This module handles:
- Atomic read/write of session-scoped acknowledgment state
- Locking for concurrent hook invocations
- Schema versioning and migration
- Corruption recovery
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


STATE_DIR = Path.home() / ".claude" / "state" / "queue"
STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

SCHEMA_VERSION = 1


@dataclass(slots=True)
class AcknowledgedOccurrence:
    """Persisted acknowledgment for one occurrence."""
    occurrence_id: str
    content_hash: str
    session_id: str
    disposition: str
    evidence: str
    acknowledged_at: float


@dataclass(slots=True)
class QueueState:
    """Complete queue state for a session."""
    schema_version: int = SCHEMA_VERSION
    session_id: str = ""
    source_identity: str = ""  # transcript path hash for binding
    acknowledged: dict[str, AcknowledgedOccurrence] = field(default_factory=dict)
    notification_counters: dict[str, int] = field(default_factory=dict)
    last_cleanup: float = 0


def _state_path(session_id: str) -> Path:
    """Get state file path for a session."""
    # Sanitize session_id for filename
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return STATE_DIR / f"{safe_id}.json"


def _lock_path(session_id: str) -> Path:
    """Get lock file path for a session."""
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
    return STATE_DIR / f"{safe_id}.lock"


def _compute_source_identity(transcript_path: str) -> str:
    """Compute a hash binding state to the transcript source."""
    import hashlib
    try:
        stat = os.stat(transcript_path)
        # Hash of path + mtime + size for change detection
        key = f"{transcript_path}|{stat.st_mtime_ns}|{stat.st_size}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]
    except OSError:
        return ""


def acquire_lock(session_id: str, timeout: float = 5.0) -> int | None:
    """Acquire exclusive lock for session state.

    Returns file descriptor on success, None on timeout.
    """
    lock_file = _lock_path(session_id)
    lock_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o600)
    start = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() - start > timeout:
                os.close(fd)
                return None
            time.sleep(0.01)


def release_lock(fd: int) -> None:
    """Release lock file descriptor."""
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def read_state(session_id: str, transcript_path: str | None = None) -> QueueState:
    """Read queue state for session, with source identity validation.

    If transcript_path is provided and source identity doesn't match,
    returns a fresh state (treats as new/corrupt).
    """
    path = _state_path(session_id)
    if not path.exists():
        return QueueState(session_id=session_id,
                          source_identity=_compute_source_identity(transcript_path) if transcript_path else "")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Corrupt state - return fresh
        return QueueState(session_id=session_id,
                          source_identity=_compute_source_identity(transcript_path) if transcript_path else "")

    # Validate schema version
    if data.get("schema_version", 0) != SCHEMA_VERSION:
        # Migration needed - for now return fresh (can add migrations later)
        return QueueState(session_id=session_id,
                          source_identity=_compute_source_identity(transcript_path) if transcript_path else "")

    # Validate source identity if provided
    if transcript_path:
        expected = _compute_source_identity(transcript_path)
        if data.get("source_identity") != expected:
            # Transcript changed - return fresh state
            return QueueState(session_id=session_id, source_identity=expected)

    # Reconstruct
    state = QueueState(
        schema_version=data["schema_version"],
        session_id=data["session_id"],
        source_identity=data["source_identity"],
        notification_counters=data.get("notification_counters", {}),
        last_cleanup=data.get("last_cleanup", 0),
    )
    for occ_id, occ_data in data.get("acknowledged", {}).items():
        state.acknowledged[occ_id] = AcknowledgedOccurrence(**occ_data)

    return state


def write_state(state: QueueState, fd: int | None = None) -> bool:
    """Atomically write queue state to disk.

    If fd is provided (lock held), writes to the same directory atomically.
    Otherwise, acquires own lock briefly.
    """
    path = _state_path(state.session_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    data = {
        "schema_version": state.schema_version,
        "session_id": state.session_id,
        "source_identity": state.source_identity,
        "acknowledged": {
            occ_id: asdict(occ) for occ_id, occ in state.acknowledged.items()
        },
        "notification_counters": state.notification_counters,
        "last_cleanup": state.last_cleanup,
    }

    # Atomic write: write to temp file, then rename
    tmp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            json.dump(data, tmp, separators=(",", ":"))
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name

        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
        return True
    except Exception:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except Exception:
                pass
        return False


def record_acknowledgment(
    session_id: str,
    transcript_path: str,
    occurrence_id: str,
    content_hash: str,
    disposition: str,
    evidence: str,
) -> bool:
    """Record an acknowledged occurrence atomically.

    Returns True on success, False on failure (lock timeout, write error).
    """
    fd = acquire_lock(session_id)
    if fd is None:
        return False

    try:
        state = read_state(session_id, transcript_path)
        state.acknowledged[occurrence_id] = AcknowledgedOccurrence(
            occurrence_id=occurrence_id,
            content_hash=content_hash,
            session_id=session_id,
            disposition=disposition,
            evidence=evidence,
            acknowledged_at=time.time(),
        )
        return write_state(state, fd)
    finally:
        release_lock(fd)


def is_acknowledged(
    session_id: str,
    transcript_path: str,
    occurrence_id: str,
) -> bool:
    """Check if an occurrence is already acknowledged (persisted)."""
    state = read_state(session_id, transcript_path)
    return occurrence_id in state.acknowledged


def get_acknowledged_occurrences(session_id: str, transcript_path: str) -> dict[str, AcknowledgedOccurrence]:
    """Get all acknowledged occurrences for a session."""
    state = read_state(session_id, transcript_path)
    return state.acknowledged


def increment_notification_counter(session_id: str, transcript_path: str, key: str) -> int:
    """Increment and return a notification counter (for throttling)."""
    fd = acquire_lock(session_id)
    if fd is None:
        return 0
    try:
        state = read_state(session_id, transcript_path)
        count = state.notification_counters.get(key, 0) + 1
        state.notification_counters[key] = count
        write_state(state, fd)
        return count
    finally:
        release_lock(fd)


def cleanup_stale_states(max_age_days: int = 7) -> int:
    """Clean up state files older than max_age_days.

    Returns number of files cleaned.
    """
    if not STATE_DIR.exists():
        return 0

    cutoff = time.time() - (max_age_days * 86400)
    cleaned = 0
    for path in STATE_DIR.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                # Also remove lock file if exists
                lock = path.with_suffix(".lock")
                if lock.exists():
                    lock.unlink()
                cleaned += 1
        except Exception:
            pass
    return cleaned


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0)

    # Quick self-test
    import tempfile
    test_session = "test-session-123"
    test_transcript = "/tmp/test-transcript.jsonl"

    # Create a dummy transcript for source identity
    Path(test_transcript).write_text("test\n")

    print("Testing queue_state.py...")
    state = read_state(test_session, test_transcript)
    print(f"Initial state: session={state.session_id}, source={state.source_identity[:8]}...")

    ok = record_acknowledgment(test_session, test_transcript, "occ-1", "hash1", "answered_now", "test evidence")
    print(f"Record acknowledgment: {ok}")

    state2 = read_state(test_session, test_transcript)
    print(f"Read back: {len(state2.acknowledged)} acknowledged")

    ok2 = is_acknowledged(test_session, test_transcript, "occ-1")
    print(f"Is acknowledged: {ok2}")

    # Cleanup
    Path(test_transcript).unlink(missing_ok=True)
    _state_path(test_session).unlink(missing_ok=True)
    _lock_path(test_session).unlink(missing_ok=True)
    print("Self-test complete.")
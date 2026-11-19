#!/usr/bin/env python3
"""
transcript-identity.py — SessionStart hook for Free Agents transcript identity.

This hook:
1. Reads the session identity from environment (set by launchers)
2. Checks for existing transcript marker to detect transitions
3. Appends transition marker if session kind changed from previous session
4. Ensures idempotent SessionStart handling (no duplicate markers)
5. Never mutates existing transcripts - only appends new markers

The hook receives the transcript path via stdin JSON:
{
  "transcript_path": "/path/to/session.jsonl",
  "session_id": "...",  # optional
  ...
}

Environment variables available:
- LA_SESSION_IDENTITY: Full identity JSON from la-session-identity.sh
- LA_SESSION_KIND: local|free_api|cloud|unknown
- LA_SESSION_ID: Stable session ID
- LA_TRANSCRIPT_MARKER_VERSION: Version of marker format
"""

import json
import os
import sys
import fcntl
from pathlib import Path
from datetime import datetime, timezone

# The marker parser lives in bin/fa_transcript.py — one parser, shared with the takeover
# pipeline (plan v2 Task 1.1 Step 5). bin/ is the plugin root's sibling in every layout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import fa_transcript


def log(msg):
    """Log to stderr for debugging."""
    print(f"[transcript-identity] {msg}", file=sys.stderr)


def read_input():
    """Read JSON input from stdin."""
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError as e:
        log(f"Failed to parse stdin JSON: {e}")
        return {}


def find_transcript_marker(transcript_path):
    """
    Search transcript for FREE_AGENTS_SESSION_IDENTITY marker.
    Returns (marker_json, line_number) or (None, None) if not found.
    Parsing is delegated to fa_transcript.identity_marker (plan v2 Task 1.1 Step 5);
    message.content as a LIST is handled there via the single message_text accessor.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return None, None
    try:
        records, _malformed = fa_transcript.load_records(transcript_path)
    except Exception as e:
        log(f"Error reading transcript: {e}")
        return None, None
    marker = fa_transcript.identity_marker(records)
    if marker is None:
        return None, None
    return marker.raw, marker.line


def resume_warning(payload, env, marker, has_prior_turns):
    """Warn-only free->paid resume warning (plan v2 Task 1.5; user decision 2026-11-19).

    Warns when LA_SESSION_IDENTITY is absent (not a launcher session) AND the transcript
    carries a free-lane identity marker AND (the SessionStart source is "resume" OR the
    transcript already has turns). Never fires on a loopback endpoint (probes) unless
    FA_RESUME_WARNING_ALLOW_LOOPBACK=1 (hermetic delivery probe only). Returns the fixed
    warning copy, or None. When LA_SESSION_IDENTITY is set this returns None and the
    caller's behaviour stays byte-identical.
    """
    if env.get("LA_SESSION_IDENTITY"):
        return None
    if not marker or marker.kind not in ("free_api", "local"):
        return None
    if not (payload.get("source") == "resume" or has_prior_turns):
        return None
    url = env.get("ANTHROPIC_BASE_URL", "")
    if fa_transcript.is_loopback_url(url):
        if env.get("FA_RESUME_WARNING_ALLOW_LOOPBACK") != "1":
            return None
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "") if url else ""
    host = host or "an unknown route"
    kind = marker.kind
    model = marker.model or "unknown"
    sid = payload.get("session_id") or "unknown"
    return (f"⚠️ free-agents: this conversation started on a free lane ({kind}, {model}). "
            f"It now runs on {host} — a paid route — and its full history is sent there. "
            f"To continue free: fa-takeover capture {sid}")


def get_session_kind_from_marker(marker):
    """Extract session_kind from marker JSON."""
    if marker and isinstance(marker, dict):
        return marker.get('session_kind')
    return None


def get_session_id_from_marker(marker):
    """Extract session_id from marker JSON."""
    if marker and isinstance(marker, dict):
        return marker.get('session_id')
    return None


def get_marker_type(marker):
    """Extract marker_type from marker JSON (defaults to 'identity' for backward compat)."""
    if marker and isinstance(marker, dict):
        return marker.get('marker_type', 'identity')
    return 'identity'


def create_transition_marker(prev_kind, new_kind, new_session_id, new_identity):
    """Create a transition marker JSON."""
    transition_types = {
        ('cloud', 'local'): 'cloud-to-local',
        ('local', 'cloud'): 'local-to-cloud',
        ('cloud', 'free_api'): 'cloud-to-free-api',
        ('free_api', 'cloud'): 'free-api-to-cloud',
        ('local', 'free_api'): 'local-to-free-api',
        ('free_api', 'local'): 'free-api-to-local',
        ('unknown', 'local'): 'unknown-to-local',
        ('unknown', 'free_api'): 'unknown-to-free-api',
        ('unknown', 'cloud'): 'unknown-to-cloud',
        ('local', 'unknown'): 'local-to-unknown',
        ('free_api', 'unknown'): 'free-api-to-unknown',
        ('cloud', 'unknown'): 'cloud-to-unknown',
    }
    transition_type = transition_types.get((prev_kind, new_kind), f'{prev_kind}-to-{new_kind}')

    return {
        "schema_version": 1,
        "marker_type": "transition",
        "from_kind": prev_kind,
        "to_kind": new_kind,
        "transition_type": transition_type,
        "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        "new_session_id": new_session_id,
        "new_identity": new_identity
    }


def append_to_transcript(transcript_path, marker_json, marker_version):
    """Append a marker to the transcript file with file locking."""
    marker_line = f"FREE_AGENTS_SESSION_IDENTITY_V{marker_version}|{json.dumps(marker_json, separators=(',', ':'))}"
    try:
        # Open in append mode with locking
        with open(transcript_path, 'a', encoding='utf-8') as f:
            # Try to get an exclusive lock (non-blocking)
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                # Could not get lock, another process is writing
                log("Could not acquire lock on transcript, skipping append")
                return False

            # Write the marker as a system message entry
            # The transcript format is JSONL with message objects
            # We need to write it as a valid JSONL line
            # The marker should be embedded in a system message
            system_msg = {
                "type": "system",
                "message": {
                    "role": "system",
                    "content": marker_line
                },
                "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
            }
            f.write(json.dumps(system_msg, separators=(',', ':')) + '\n')
            f.flush()
            # Lock released automatically on close
        log(f"Appended marker to transcript: {transcript_path}")
        return True
    except Exception as e:
        log(f"Error appending to transcript: {e}")
        return False


def main():
    # 0.50.0 help sweep: --help prints usage and never reads stdin or emits hook output.
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print("transcript-identity.py — SessionStart hook: stamps session identity into the "
              "transcript. Usage: python3 transcript-identity.py [--help]")
        return 0
    # Read hook input
    input_data = read_input()
    transcript_path = input_data.get('transcript_path')

    # Get current session identity from environment
    la_session_identity = os.environ.get('LA_SESSION_IDENTITY')
    if not la_session_identity:
        # Task 1.5 (warn only): a free-lane conversation resumed on a paid route warns once.
        # Emits SessionStart JSON with BOTH systemMessage and additionalContext. When
        # LA_SESSION_IDENTITY is set this block is skipped entirely (byte-identical path).
        try:
            marker = None
            has_prior = False
            if transcript_path and os.path.exists(transcript_path):
                records, _malformed = fa_transcript.load_records(transcript_path)
                marker = fa_transcript.identity_marker(records)
                has_prior = bool(fa_transcript.user_instructions(records))
            warning = resume_warning(input_data, os.environ, marker, has_prior)
            if warning:
                print(json.dumps({
                    "systemMessage": warning,
                    "hookSpecificOutput": {"hookEventName": "SessionStart",
                                           "additionalContext": warning},
                }))
        except Exception as e:
            log(f"resume warning failed: {e}")
        log("No LA_SESSION_IDENTITY in environment, skipping")
        return 0

    try:
        current_identity = json.loads(la_session_identity)
    except json.JSONDecodeError as e:
        log(f"Failed to parse LA_SESSION_IDENTITY: {e}")
        return 0

    current_kind = current_identity.get('session_kind', 'unknown')
    current_session_id = current_identity.get('session_id', 'unknown')
    marker_version = current_identity.get('transcript_marker_version', 1)

    log(f"Current session: kind={current_kind}, id={current_session_id}")

    # Find existing marker in transcript
    existing_marker, marker_line = find_transcript_marker(transcript_path)

    if existing_marker:
        prev_kind = get_session_kind_from_marker(existing_marker)
        prev_session_id = get_session_id_from_marker(existing_marker)
        prev_marker_type = get_marker_type(existing_marker)

        log(f"Found existing marker at line {marker_line}: kind={prev_kind}, id={prev_session_id}, type={prev_marker_type}")

        # Check if this is a transition (different session kind)
        if prev_kind and prev_kind != current_kind:
            log(f"Transition detected: {prev_kind} -> {current_kind}")
            transition_marker = create_transition_marker(
                prev_kind, current_kind, current_session_id, current_identity
            )
            # Append transition marker to transcript
            if transcript_path:
                append_to_transcript(transcript_path, transition_marker, marker_version)
            return 0

        # Same session kind - check if it's the same session (idempotent)
        if prev_session_id == current_session_id:
            log("Same session ID - idempotent SessionStart, no marker needed")
            return 0
        else:
            log(f"Different session ID ({prev_session_id} vs {current_session_id}) but same kind - treating as new session")
            # Fall through to emit new marker
    else:
        log("No existing marker found - fresh session")

    # Append the session identity marker for new sessions or same-kind new sessions
    if transcript_path:
        append_to_transcript(transcript_path, current_identity, marker_version)
    return 0


if __name__ == '__main__':
    sys.exit(main())
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
import re
import fcntl
from pathlib import Path
from datetime import datetime, timezone


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
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return None, None

    pattern = re.compile(r'FREE_AGENTS_SESSION_IDENTITY_V(\d+)\|(.+)')
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                # First try direct match (for markers written directly)
                match = pattern.search(line)
                if match:
                    version = int(match.group(1))
                    marker_json = match.group(2)
                    try:
                        return json.loads(marker_json), line_num
                    except json.JSONDecodeError:
                        pass

                # Then try parsing as JSONL and checking message.content
                try:
                    entry = json.loads(line)
                    if entry.get('type') == 'system':
                        content = entry.get('message', {}).get('content', '')
                        match = pattern.search(content)
                        if match:
                            version = int(match.group(1))
                            marker_json = match.group(2)
                            try:
                                return json.loads(marker_json), line_num
                            except json.JSONDecodeError:
                                pass
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log(f"Error reading transcript: {e}")
    return None, None


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
    # Read hook input
    input_data = read_input()
    transcript_path = input_data.get('transcript_path')

    # Get current session identity from environment
    la_session_identity = os.environ.get('LA_SESSION_IDENTITY')
    if not la_session_identity:
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
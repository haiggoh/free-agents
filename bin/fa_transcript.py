#!/usr/bin/env python3
"""fa_transcript.py — pure transcript reader for the takeover pipeline (plan v2 Task 1.1).

Reads a Claude Code JSONL transcript and never raises on a bad line: malformed
lines are counted, not fatal. `message_text` is the ONLY content accessor —
every other function in this module goes through it (list content included).

Stdlib only. Python 3.9-compatible (macOS system python3 runs hooks).
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

# Pinned from plan v2 Receipt 0 / Task 0.4: the observed budget wall is an assistant
# record with isApiErrorMessage + apiErrorStatus 422 whose text reads
# "API Error: 422 Budget has been exceeded! Key=... Current cost: ... Max budget: ...".
# Key on the TEXT, not the status — the gateway has sent 422 here (status-agnostic).
BUDGET_WALL_RE = re.compile(r"Budget has been exceeded")

# Same marker format as hooks/transcript-identity.py (FREE_AGENTS_SESSION_IDENTITY_V<n>|<json>).
MARKER_RE = re.compile(r"FREE_AGENTS_SESSION_IDENTITY_V(\d+)\|(.+)")

FILE_TOOLS = ("Edit", "Write", "NotebookEdit")

# Loopback spellings the probes and the guard must all catch (final review finding 2):
# dotted 127.*, ::1, localhost, 0.0.0.0, the decimal/hex numeric forms of 127.0.0.1,
# and the IPv4-mapped ::ffff:127.0.0.1.
_LOOPBACK_HOSTS = {"localhost", "::1", "0.0.0.0", "::"}


def is_loopback_url(url) -> bool:
    """True when the URL points at loopback (probes/fakes) — never at a paid route."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").strip("[]").lower()
    except Exception:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    if host.startswith("127.") or host.startswith("::ffff:127.") or host.startswith("::ffff:0."):
        return True
    if host.isdigit():  # decimal IPv4 of 127.0.0.1 / 0.0.0.0
        n = int(host)
        return n == 0 or (0x7F000000 <= n <= 0x7FFFFFFF)
    try:
        import ipaddress
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Instruction:
    line: int
    uuid: str | None
    timestamp: str | None
    text: str


@dataclass(frozen=True)
class ToolUse:
    line: int
    tool_use_id: str
    name: str
    file_path: str | None


@dataclass(frozen=True)
class ApiError:
    line: int
    text: str
    kind: str  # budget_wall | rate_limit | overloaded | auth | other


@dataclass(frozen=True)
class IdentityMarker:
    line: int
    kind: str
    model: str | None
    raw: dict


def load_records(path: str):
    """Return ([(line_number_1_based, record_dict), ...], malformed_count).

    Never raises on a bad line; blank lines are skipped without counting.
    """
    records = []
    malformed = 0
    # errors="replace": a killed mid-write line must never raise UnicodeDecodeError.
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(data, dict):  # null / "heartbeat" / 123 are malformed records
                malformed += 1
                continue
            records.append((ln, data))
    return records, malformed


def message_text(record: dict) -> str:
    """The only TEXT accessor: string content as-is, or text blocks of a list joined.

    (Tool blocks are read structurally via _content_blocks — a text accessor cannot
    yield tool ids/inputs.)
    """
    msg = record.get("message")
    msg = msg if isinstance(msg, dict) else {}
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _content_blocks(record: dict) -> list:
    msg = record.get("message")
    msg = msg if isinstance(msg, dict) else {}
    content = msg.get("content", [])
    return content if isinstance(content, list) else []


def user_instructions(records) -> list:
    """Verbatim user instructions: skips tool_result carriers, isMeta, hook/command echoes."""
    out = []
    for ln, rec in records:
        if rec.get("type") != "user" or rec.get("isMeta"):
            continue
        text = message_text(rec)
        stripped = text.strip()
        if not stripped:
            continue
        if stripped.startswith("<command-") or stripped.startswith("<hook"):
            continue
        out.append(Instruction(line=ln, uuid=rec.get("uuid"), timestamp=rec.get("timestamp"), text=text))
    return out


def _tool_uses(records) -> list:
    out = []
    for ln, rec in records:
        for block in _content_blocks(rec):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                inp = block.get("input") or {}
                out.append(ToolUse(
                    line=ln,
                    tool_use_id=block.get("id", ""),
                    name=block.get("name", ""),
                    file_path=inp.get("file_path"),
                ))
    return out


def _tool_result_ids(records) -> set:
    ids = set()
    for _line, rec in records:
        for block in _content_blocks(rec):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                ids.add(block.get("tool_use_id"))
    return ids


def pending_tool_uses(records) -> list:
    """tool_use blocks with no matching tool_result — outcome unknown, never re-run blindly."""
    done = _tool_result_ids(records)
    return [t for t in _tool_uses(records) if t.tool_use_id not in done]


def tool_result_texts(records) -> list:
    """(line, tool_use_id, content_text) for every tool_result block, chronological.

    Used by fa-takeover verify to prove the handoff nonce entered the model's context.
    """
    out = []
    for ln, rec in records:
        for block in _content_blocks(rec):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
                elif not isinstance(content, str):
                    content = ""
                out.append((ln, block.get("tool_use_id", ""), content))
    return out


def files_touched(records) -> list:
    """File paths from Edit/Write/NotebookEdit inputs, first occurrence order, deduped."""
    seen = set()
    out = []
    for t in _tool_uses(records):
        if t.name in FILE_TOOLS and t.file_path and t.file_path not in seen:
            seen.add(t.file_path)
            out.append(t.file_path)
    return out


def last_todos(records):
    """The parsed todos list of the LAST TodoWrite tool_result, or None."""
    name_by_id = {t.tool_use_id: t.name for t in _tool_uses(records)}
    last = None
    for _line, rec in records:
        for block in _content_blocks(rec):
            if (isinstance(block, dict) and block.get("type") == "tool_result"
                    and name_by_id.get(block.get("tool_use_id")) == "TodoWrite"):
                last = block
    if last is None:
        return None
    content = last.get("content", "")
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, list) else None


def classify_api_error(text: str) -> str:
    t = text or ""
    low = t.lower()
    if BUDGET_WALL_RE.search(t):
        return "budget_wall"
    if "429" in t or "rate limit" in low or "rate_limit" in low:
        return "rate_limit"
    if "overloaded" in low or "529" in t or "503" in t:
        return "overloaded"
    if "401" in t or "403" in t or "authentication" in low or "api key" in low or "x-api-key" in low:
        return "auth"
    return "other"


def api_errors(records) -> list:
    """Engine-written API error records: assistant records carrying the error markers."""
    out = []
    for ln, rec in records:
        if rec.get("type") == "assistant" and (rec.get("isApiErrorMessage") or rec.get("apiErrorStatus")):
            text = message_text(rec)
            out.append(ApiError(line=ln, text=text, kind=classify_api_error(text)))
    return out


def identity_marker(records):
    """First FREE_AGENTS_SESSION_IDENTITY marker found in any record's text, or None."""
    for ln, rec in records:
        text = message_text(rec)
        if not text:
            continue
        m = MARKER_RE.search(text)
        if m:
            try:
                raw = json.loads(m.group(2))
            except json.JSONDecodeError:
                continue
            return IdentityMarker(line=ln, kind=raw.get("session_kind"), model=raw.get("model"), raw=raw)
    return None


def _main(argv):
    """Library module, not a CLI — but --help must still answer and unknown args must exit 2."""
    if any(a in ("-h", "--help") for a in argv):
        print("fa_transcript.py — pure transcript reader library (imported by bin/fa-takeover and tests).")
        print("Usage: python3 fa_transcript.py --help")
        print("This module takes no runtime arguments; it exposes load_records/message_text/"
              "user_instructions/pending_tool_uses/last_todos/files_touched/api_errors/"
              "classify_api_error/identity_marker.")
        return 0
    if argv:
        print(f"fa_transcript.py: unknown argument: {argv[0]}", file=sys.stderr)
        print("Usage: python3 fa_transcript.py --help", file=sys.stderr)
        return 2
    print("fa_transcript.py: library module — import it; no CLI.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

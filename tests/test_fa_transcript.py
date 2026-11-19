"""Tests for bin/fa_transcript.py — the pure transcript reader (plan v2 Task 1.1)."""
import json
from pathlib import Path

import pytest

import fa_transcript as ft

FIX = Path(__file__).parent / "fixtures" / "transcripts"


def load(name):
    return ft.load_records(str(FIX / name))


# --- Step 1 pinned tests -----------------------------------------------------

def test_marker_in_list_content():
    records, malformed = load("list_content.jsonl")
    assert malformed == 0
    m = ft.identity_marker(records)
    assert m is not None
    assert m.line == 1
    assert m.kind == "free_api"
    assert m.raw["session_id"] == "sess-list"


def test_identical_prompts_kept():
    records, _ = load("duplicates.jsonl")
    instrs = ft.user_instructions(records)
    assert [i.text for i in instrs].count("do the same thing") == 2
    assert len({i.line for i in instrs}) == 2


def test_tool_result_not_an_instruction():
    records, _ = load("pending_tool.jsonl")
    instrs = ft.user_instructions(records)
    assert [i.text for i in instrs] == ["edit the file"]


def test_pending_tool_detected():
    records, _ = load("pending_tool.jsonl")
    pending = ft.pending_tool_uses(records)
    assert [(t.tool_use_id, t.name) for t in pending] == [("toolu_pending", "Bash")]


def test_budget_wall_classified():
    text = ("API Error: 422 Budget has been exceeded! Key=Test-Key (sk-...test) "
            "Current cost: 40.00000000000001, Max budget: 40.0")
    assert ft.classify_api_error(text) == "budget_wall"
    records, _ = load("budget_wall.jsonl")
    errs = ft.api_errors(records)
    assert len(errs) == 1
    assert errs[0].kind == "budget_wall"
    assert errs[0].line == 2


def test_overloaded_not_budget():
    text = 'API Error: 529 {"error":{"message":"Service temporarily overloaded"}}'
    assert ft.classify_api_error(text) == "overloaded"


def test_malformed_line_counted():
    records, malformed = load("malformed.jsonl")
    assert malformed == 1
    assert [ln for ln, _ in records] == [1, 3]


# --- remaining interface coverage --------------------------------------------

def test_load_records_shape_and_message_text():
    records, malformed = load("basic.jsonl")
    assert malformed == 0 and len(records) == 3
    assert ft.message_text(records[0][1]) == "fix the bug"
    # list content is joined
    lrecords, _ = load("list_content.jsonl")
    assert ft.message_text(lrecords[1][1]) == "hello world"


def test_user_instructions_exclude_meta_echoes():
    records, _ = load("basic.jsonl")
    instrs = ft.user_instructions(records)
    assert [i.text for i in instrs] == ["fix the bug"]
    assert instrs[0].uuid == "u1"
    assert instrs[0].timestamp == "2026-11-19T03:00:00.000Z"


def test_files_touched_from_edit_write_inputs():
    records, _ = load("pending_tool.jsonl")
    assert ft.files_touched(records) == ["/tmp/x.py"]


def test_last_todos_parses_latest_todowrite_result():
    todos = [{"content": "ship it", "status": "pending", "activeForm": "shipping"}]
    records = [
        (1, {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "TodoWrite", "input": {"todos": []}}]}}),
        (2, {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps(todos)}]}}),
        (3, {"type": "user", "message": {"role": "user", "content": "next"}}),
    ]
    assert ft.last_todos(records) == todos
    assert ft.last_todos([(1, {"type": "user", "message": {"role": "user", "content": "hi"}})]) is None


def test_identity_marker_absent_when_no_marker():
    records, _ = load("basic.jsonl")
    assert ft.identity_marker(records) is None


# --- hardening (independent review findings, 2026-11-19) ----------------------

def test_load_records_counts_non_dict_json_as_malformed(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        '{"type":"user","uuid":"u1","message":{"role":"user","content":"ok"}}\n'
        "null\n"
        '"heartbeat"\n'
        "123\n"
    )
    records, malformed = ft.load_records(str(t))
    assert malformed == 3
    assert [r.get("uuid") for _line, r in records] == ["u1"]


def test_load_records_survives_non_utf8_bytes(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_bytes(
        b'{"type":"user","uuid":"u1","message":{"role":"user","content":"ok"}}\n'
        b'{"type":"user","uuid":"u2","message":{"role":"user","content":"\xff\xfe bad"}}\n'
    )
    records, malformed = ft.load_records(str(t))  # never raises
    assert len(records) == 2
    assert malformed == 0
    assert "�?" in ft.message_text(records[1][1]) or "bad" in ft.message_text(records[1][1])


def test_string_message_field_is_not_crashing():
    rec = {"type": "user", "uuid": "u1", "message": "hello"}
    assert ft.message_text(rec) == ""
    assert ft.user_instructions([(1, rec)]) == []
    assert ft.pending_tool_uses([(1, rec)]) == []
    # an error marker on a non-assistant record is not an API error record
    assert ft.api_errors([(1, dict(rec, isApiErrorMessage=True))]) == []


def test_tool_result_texts():
    records, _ = load("pending_tool.jsonl")
    assert ft.tool_result_texts(records) == [(4, "toolu_done", "ok")]


# --- shared loopback detector (final review finding 2) -----------------------

@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:4147", True),
    ("http://127.5.5.5:9", True),
    ("https://localhost/v1", True),
    ("http://[::1]:8080/v1", True),
    ("http://0.0.0.0:4147", True),
    ("http://2130706433:4147", True),          # decimal 127.0.0.1
    ("http://[::ffff:127.0.0.1]:4147", True),   # IPv4-mapped loopback
    ("https://llmgw.joyia.example/v1", False),
    ("http://localhost.example.com/v1", False),  # hostname ENDING in localhost is not loopback
    ("", False),
    (None, False),
])
def test_is_loopback_url(url, expected):
    assert ft.is_loopback_url(url) is expected

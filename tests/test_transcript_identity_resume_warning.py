"""Tests for the free->paid resume warning in hooks/transcript-identity.py (plan v2 Task 1.5).

resume_warning(payload, env, marker, has_prior_turns) -> str | None — the warning copy is
fixed; conditions pinned by the plan. Warn only (user decision 2026-11-19).
"""
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import fa_transcript as ft

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "hooks" / "transcript-identity.py"

_loader = importlib.machinery.SourceFileLoader("transcript_identity_t", str(HOOK))
spec = importlib.util.spec_from_file_location("transcript_identity_t", HOOK, loader=_loader)
assert spec is not None and spec.loader is not None
tid = importlib.util.module_from_spec(spec)
sys.modules["transcript_identity_t"] = tid
spec.loader.exec_module(tid)

PAID = {"ANTHROPIC_BASE_URL": "https://llmgw.joyia.example/v1"}


def marker(kind="free_api", model="z-ai/glm-5.3-flash"):
    return ft.IdentityMarker(line=1, kind=kind, model=model,
                             raw={"session_kind": kind, "session_id": "s1", "model": model})


def payload(source="resume", session_id="sess-1"):
    return {"hook_event_name": "SessionStart", "session_id": session_id,
            "source": source, "transcript_path": "/tmp/t.jsonl", "cwd": "/tmp"}


# --- Step 1 pinned tests -----------------------------------------------------

def test_resumed_free_on_paid_warns():
    w = tid.resume_warning(payload("resume"), PAID, marker(), has_prior_turns=True)
    assert w is not None
    assert "free lane" in w and "free_api" in w and "z-ai/glm-5.3-flash" in w
    assert "llmgw.joyia.example" in w and "paid route" in w
    assert "fa-takeover capture sess-1" in w


def test_fresh_session_silent():
    assert tid.resume_warning(payload("startup"), PAID, marker(), has_prior_turns=False) is None


def test_launcher_session_unchanged():
    # LA_SESSION_IDENTITY set -> resume_warning is not even consulted; output identical
    env = dict(PAID)
    env["LA_SESSION_IDENTITY"] = '{"session_kind":"free_api","session_id":"s9"}'
    assert tid.resume_warning(payload("resume"), env, marker(), has_prior_turns=True) is None


def test_loopback_silent():
    env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:4147"}
    assert tid.resume_warning(payload("resume"), env, marker(), has_prior_turns=True) is None


def test_unknown_endpoint_warns():
    w = tid.resume_warning(payload("resume"), {}, marker(), has_prior_turns=True)
    assert w is not None and "an unknown route" in w


def test_list_content_marker_warns():
    # the marker arrives inside a list content block — message_text is the single accessor
    recs = [(1, {"type": "system", "message": {"role": "system", "content": [
        {"type": "text", "text": 'FREE_AGENTS_SESSION_IDENTITY_V1|{"session_kind":"local","session_id":"s2","model":"qwen3"}'}]}})]
    m = ft.identity_marker(recs)
    assert m is not None and m.kind == "local"
    w = tid.resume_warning(payload("resume"), PAID, m, has_prior_turns=True)
    assert w is not None and "local" in w and "qwen3" in w


def test_local_kind_warns():
    w = tid.resume_warning(payload("resume"), PAID, marker(kind="local", model="qwen3-30b"), True)
    assert w is not None and "local" in w


# --- conditions matrix -------------------------------------------------------

def test_prior_turns_alone_triggers_even_when_source_is_startup():
    w = tid.resume_warning(payload("startup"), PAID, marker(), has_prior_turns=True)
    assert w is not None


def test_cloud_marker_silent():
    assert tid.resume_warning(payload("resume"), PAID, marker(kind="cloud", model="claude-opus-5-5"), True) is None


def test_no_marker_silent():
    assert tid.resume_warning(payload("resume"), PAID, None, True) is None

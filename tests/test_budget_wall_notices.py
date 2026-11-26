"""Tests for the budget-wall NOTICE contract (R4 plan Task 1 — NEW file, split playbook).

D1 one notice per wall (an already-claimed wall is silent on stdout) · D2 the 📡
free-agents mark, never 💸 · identical lines dedupe within one run · repeat walls of
one session supersede (newest package wins) · fa-takeover refuses/lists superseded ids ·
the secret_findings refusal names the manual override.

Run: pytest tests/test_budget_wall_notices.py -v
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))
HOOK = REPO / "bin" / "budget-wall-capture.py"
FIX = REPO / "tests" / "fixtures" / "transcripts"

_loader = importlib.machinery.SourceFileLoader("budget_wall_capture", str(HOOK))
spec = importlib.util.spec_from_file_location("budget_wall_capture", HOOK, loader=_loader)
assert spec is not None and spec.loader is not None
bwc = importlib.util.module_from_spec(spec)
sys.modules["budget_wall_capture"] = bwc
spec.loader.exec_module(bwc)

import emoji_constants  # noqa: E402
import fa_capture  # noqa: E402

BUDGET_TEXT = ("API Error: 422 Budget has been exceeded! Key=Test-Key (sk-...test) "
               "Current cost: 40.00000000000001, Max budget: 40.0")


def payload(text=BUDGET_TEXT, transcript=None, sid="sess-bw", no_transcript=False):
    return {"hook_event_name": "StopFailure", "session_id": sid,
            "transcript_path": None if no_transcript else str(transcript or FIX / "budget_wall.jsonl"),
            "last_assistant_message": text, "cwd": "/tmp",
            "effort": {"level": "medium"}, "prompt_id": "p1"}


def run_hook(pl, env, monkeypatch):
    """In-process run of the hook's capture path (pattern copied from test_budget_wall_hook.py)."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(pl)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = bwc.main([])
    return rc, out.getvalue()


def base_env(tmp_path):
    return {"HOME": str(tmp_path), "FA_TAKEOVER_DIR": str(tmp_path / "takeovers"),
            "ANTHROPIC_BASE_URL": "https://llmgw.example/v1",
            "FA_SECRET_SCANNER": str(REPO / "tests" / "fixtures" / "stub_redact_secret.py"),
            # R4: pin the MANUAL path in the Task-1 tests — auto-launch off via env override
            "FA_AUTO_TAKEOVER": "0"}


def packages(tmp_path):
    root = tmp_path / "takeovers"
    return sorted(root.glob("tk-*")) if root.is_dir() else []


def spool(tmp_path, name, pl, endpoint="#fa-endpoint https://llmgw.example/v1"):
    pend = tmp_path / "takeovers" / ".pending"
    pend.mkdir(parents=True, exist_ok=True)
    (pend / name).write_text(endpoint + "\n" + json.dumps(pl) + "\n")
    return pend


def run_pending(tmp_path, monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert bwc.main(["--pending"]) == 0
    return out.getvalue()


def msg_lines(out):
    return json.loads(out)["systemMessage"].splitlines() if out.strip() else []


def takeover_cli(tmp_path, *args):
    return subprocess.run([sys.executable, str(REPO / "bin" / "fa-takeover"), *args],
                          capture_output=True, text=True,
                          env={**os.environ, "FA_TAKEOVER_DIR": str(tmp_path / "takeovers"),
                               "HOME": str(tmp_path)}, timeout=60)


# --- D2: the mark -------------------------------------------------------------

def test_notice_uses_free_agents_mark(tmp_path, monkeypatch):
    rc, out = run_hook(payload(), base_env(tmp_path), monkeypatch)
    assert rc == 0
    (line,) = msg_lines(out)
    assert line.startswith(bwc.NOTICE_PREFIX)
    assert "💸" not in line
    assert bwc.NOTICE_PREFIX.startswith(emoji_constants.SESSION_EMOJI_FREE_API_STR)
    assert emoji_constants.SESSION_EMOJI_FREE_API_STR == "📡"  # pins the config value


# --- D1: one notice per wall ---------------------------------------------------

def test_already_claimed_wall_is_silent(tmp_path, monkeypatch):
    env = base_env(tmp_path)
    rc1, out1 = run_hook(payload(), env, monkeypatch)
    rc2, out2 = run_hook(payload(), env, monkeypatch)
    assert rc1 == rc2 == 0
    assert len(packages(tmp_path)) == 1
    assert msg_lines(out1), "first fire announces"
    assert out2 == ""  # second fire of the same wall: NOTHING on stdout
    log = (tmp_path / "takeovers" / "hook.log").read_text()
    assert "already claimed" in log


def test_two_spooled_payloads_same_wall_one_line(tmp_path, monkeypatch):
    spool(tmp_path, "1.json", payload())
    spool(tmp_path, "2.json", payload())
    out = run_pending(tmp_path, monkeypatch, base_env(tmp_path))
    assert len(msg_lines(out)) == 1
    assert len(packages(tmp_path)) == 1


def test_identical_lines_deduped_in_one_run(tmp_path, monkeypatch):
    # two DIFFERENT sessions refused with the SAME code -> 2 lines (distinct short ids);
    # a duplicate of the first -> still 2, never more
    spool(tmp_path, "a.json", payload(sid="sess-aaa", no_transcript=True))
    spool(tmp_path, "b.json", payload(sid="sess-bbb", no_transcript=True))
    spool(tmp_path, "a2.json", payload(sid="sess-aaa", no_transcript=True))
    out = run_pending(tmp_path, monkeypatch, base_env(tmp_path))
    lines = msg_lines(out)
    assert len(lines) == 2
    assert any("sess-aaa" in ln for ln in lines) and any("sess-bbb" in ln for ln in lines)


# --- repeat walls of one session: supersede ------------------------------------

def _two_walls(tmp_path, monkeypatch):
    """Wall at transcript line 2, then a repeat wall at line 4 of the SAME session."""
    env = base_env(tmp_path)
    rc1, out1 = run_hook(payload(), env, monkeypatch)
    old = packages(tmp_path)[0].name
    rc2, out2 = run_hook(payload(transcript=FIX / "budget_wall_repeat.jsonl"), env, monkeypatch)
    pkgs = packages(tmp_path)
    assert len(pkgs) == 2
    new = next(p.name for p in pkgs if p.name != old)
    return env, old, new, out1, out2


def test_repeat_wall_same_session_supersedes(tmp_path, monkeypatch):
    _env, old, new, out1, out2 = _two_walls(tmp_path, monkeypatch)
    root = tmp_path / "takeovers"
    assert fa_capture.superseded_by(root, old) == new
    assert fa_capture.superseded_by(root, new) is None
    assert fa_capture.latest_package_for_session(root, "sess-bw") == new
    assert len(msg_lines(out1)) == 1 and len(msg_lines(out2)) == 1  # one notice per run


def test_launch_refuses_superseded(tmp_path, monkeypatch):
    _env, old, new, _o1, _o2 = _two_walls(tmp_path, monkeypatch)
    r = takeover_cli(tmp_path, "launch", old)
    assert r.returncode == 4
    assert new in r.stderr


def test_list_marks_superseded(tmp_path, monkeypatch):
    _env, old, new, _o1, _o2 = _two_walls(tmp_path, monkeypatch)
    r = takeover_cli(tmp_path, "list")
    assert r.returncode == 0
    assert old in r.stdout and f"superseded by {new}" in r.stdout


# --- Review Focus 5: the refusal names the manual override ----------------------

def test_refusal_notice_names_override(tmp_path, monkeypatch):
    rc, out = run_hook(payload(transcript=FIX / "budget_wall_secret.jsonl", sid="sess-secret"),
                       base_env(tmp_path), monkeypatch)
    assert rc == 0
    assert packages(tmp_path) == []
    (line,) = msg_lines(out)
    assert "secret_findings" in line
    # the override must carry the FULL session id — _find_transcript resolves exact filenames only
    assert "fa-takeover capture sess-secret --allow-findings" in line


def test_notice_says_refused_when_auto_on_but_claimed(tmp_path, monkeypatch):
    # code-review (important): auto ON + launch refused (one-successor claim held) must NOT
    # tell the user "auto-launch off (csl)" — the real reason is in hook.log
    (tmp_path / "takeovers" / ".launched").mkdir(parents=True)
    (tmp_path / "takeovers" / ".launched" / "sess-bw").write_text("1")  # claim held
    env = base_env(tmp_path)
    env["FA_AUTO_TAKEOVER"] = "1"
    rc, out = run_hook(payload(transcript=FIX / "budget_wall.jsonl", sid="sess-bw"),
                       env, monkeypatch)
    assert rc == 0
    (line,) = msg_lines(out)
    assert "Nothing was launched" in line
    assert "auto-launch refused (see hook.log)" in line, line
    assert "off (csl)" not in line, line

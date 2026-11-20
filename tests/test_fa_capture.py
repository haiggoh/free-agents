"""Tests for bin/fa_capture.py — the capture-package builder (plan v2 Task 1.2)."""
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

import fa_capture

FIX = Path(__file__).parent / "fixtures" / "transcripts"
NOW = datetime(2026, 11, 19, 4, 0, 0, tzinfo=timezone.utc)


def build(tmp_path, fixture="duplicates.jsonl", **kw):
    kwargs = dict(
        cwd=str(tmp_path),
        dest_route="nvidia-glm53",
        dest_window_tokens=1_000_000,
        base_overhead_tokens=200,
        reason="budget wall (API error at transcript line 2)",
        scanner=None,
        now=NOW,
    )
    kwargs.update(kw)
    return fa_capture.build_package(str(FIX / fixture), **kwargs)


# --- Step 1 pinned tests -----------------------------------------------------

def test_package_keeps_duplicate_instructions(tmp_path):
    pkg = build(tmp_path, "duplicates.jsonl")
    assert pkg.manifest["counts"]["instructions"] == 2
    # §3 carries every instruction, numbered, with line refs (fixture lines 1 and 3)
    sec3 = pkg.handoff_md.split("## 3. The goal, verbatim", 1)[1].split("## 4.", 1)[0]
    assert sec3.count("do the same thing") == 2
    assert "(line 1)" in sec3 and "(line 3)" in sec3


def test_refuses_when_instructions_exceed_budget(tmp_path):
    with pytest.raises(fa_capture.CaptureRefused) as e:
        build(tmp_path, "duplicates.jsonl", dest_window_tokens=10_000, base_overhead_tokens=0)
    assert e.value.code == "over_budget"


def test_secret_finding_refuses(tmp_path):
    def scanner(text):
        return ["sk-ant-live-key-found"]
    with pytest.raises(fa_capture.CaptureRefused) as e:
        build(tmp_path, "duplicates.jsonl", scanner=scanner)
    assert e.value.code == "secret_findings"
    pkg = build(tmp_path, "duplicates.jsonl", scanner=scanner, allow_findings=True)
    assert pkg.manifest["secret_scan"] == {"ran": True, "findings": ["sk-ant-live-key-found"]}


def test_git_state_recorded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, env=env,
                   capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True, env=env)
    (repo / "tracked.txt").write_text("v1")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, env=env,
                   capture_output=True)
    (repo / "tracked.txt").write_text("v2")          # modified
    (repo / "untracked.txt").write_text("new")       # untracked
    pkg = build(tmp_path, "duplicates.jsonl", cwd=str(repo))
    g = pkg.manifest["git"]
    assert g["is_repo"] is True
    assert g["branch"] == "main"
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True, env=env).stdout.strip()
    assert g["head"] == head
    assert set(g["porcelain_paths"]) == {"tracked.txt", "untracked.txt"}


def test_not_a_repo(tmp_path):
    pkg = build(tmp_path, "duplicates.jsonl", cwd=str(tmp_path / "plain"))
    g = pkg.manifest["git"]
    assert g["is_repo"] is False
    assert g["head"] is None and g["porcelain_paths"] == []


def test_write_modes_and_no_overwrite(tmp_path):
    pkg = build(tmp_path, "duplicates.jsonl")
    root = tmp_path / "takeovers"
    out = fa_capture.write_package(pkg, root)
    assert out == root / pkg.takeover_id
    assert (out.stat().st_mode & 0o777) == 0o700
    for name in ("manifest.json", "handoff.md"):
        p = out / name
        assert p.exists()
        assert (p.stat().st_mode & 0o777) == 0o600
    assert json.loads((out / "manifest.json").read_text()) == pkg.manifest
    assert (out / "handoff.md").read_text() == pkg.handoff_md
    with pytest.raises(Exception):
        fa_capture.write_package(pkg, root)  # same id twice: refused, never overwritten


def test_tool_output_not_inlined(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        '{"type":"user","uuid":"u1","sessionId":"s","timestamp":"2026-11-19T03:00:00Z",'
        '"message":{"role":"user","content":"run it"}}\n'
        '{"type":"assistant","uuid":"a1","sessionId":"s","timestamp":"2026-11-19T03:00:01Z",'
        '"message":{"role":"assistant","content":[{"type":"tool_use","id":"t1","name":"Bash",'
        '"input":{"command":"cat secret"}}]}}\n'
        '{"type":"user","uuid":"u2","sessionId":"s","timestamp":"2026-11-19T03:00:02Z",'
        '"message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1",'
        '"content":"PLANTED-TOOL-OUTPUT-SECRET"}]}}\n'
    )
    pkg = fa_capture.build_package(str(t), cwd=str(tmp_path), dest_route="r",
                                   dest_window_tokens=1_000_000, base_overhead_tokens=200,
                                   reason="test", scanner=None, now=NOW)
    assert "PLANTED-TOOL-OUTPUT-SECRET" not in pkg.handoff_md


def test_nonce_line_present_and_hash_in_manifest(tmp_path):
    pkg = build(tmp_path, "duplicates.jsonl")
    lines = [line for line in pkg.handoff_md.splitlines() if line.startswith("TAKEOVER-NONCE: ")]
    assert len(lines) == 1
    nonce = lines[0].split(": ", 1)[1]
    assert len(nonce) == 16 and all(c in "0123456789abcdef" for c in nonce)
    assert pkg.manifest["nonce_sha256"] == hashlib.sha256(nonce.encode()).hexdigest()
    assert pkg.takeover_id.startswith("tk-20261119")
    import re
    assert re.fullmatch(r"tk-\d{14}-[0-9a-f]{8}", pkg.takeover_id)


# --- interface coverage ------------------------------------------------------

def test_transcript_missing_refused(tmp_path):
    with pytest.raises(fa_capture.CaptureRefused) as e:
        fa_capture.build_package(str(tmp_path / "nope.jsonl"), cwd=str(tmp_path),
                                 dest_route="r", dest_window_tokens=1_000_000,
                                 base_overhead_tokens=200, reason="x", now=NOW)
    assert e.value.code == "transcript_missing"


def test_no_instructions_refused(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text('{"type":"assistant","uuid":"a1","message":{"role":"assistant",'
                 '"content":[{"type":"text","text":"hi"}]}}\n')
    with pytest.raises(fa_capture.CaptureRefused) as e:
        fa_capture.build_package(str(t), cwd=str(tmp_path), dest_route="r",
                                 dest_window_tokens=1_000_000, base_overhead_tokens=200,
                                 reason="x", now=NOW)
    assert e.value.code == "no_instructions"


def test_estimate_tokens():
    assert fa_capture.estimate_tokens("") == 0
    assert fa_capture.estimate_tokens("abcd") == 1
    assert fa_capture.estimate_tokens("abcde") == 2


def test_manifest_schema_and_budget_math(tmp_path):
    pkg = build(tmp_path, "budget_wall.jsonl", base_overhead_tokens=200,
                dest_window_tokens=1_000_000)
    m = pkg.manifest
    assert m["schema_version"] == 1
    assert set(m) >= {"schema_version", "takeover_id", "nonce_sha256", "created_at", "reason",
                      "source", "dest", "counts", "git", "completeness", "secret_scan"}
    assert m["dest"]["budget_tokens"] == 1_000_000 - max(200, 40_000) - 8_000
    assert m["dest"]["route"] == "nvidia-glm53" and m["dest"]["window_tokens"] == 1_000_000
    src = m["source"]
    assert src["line_count"] == 2 and src["malformed"] == 0
    raw = (FIX / "budget_wall.jsonl").read_bytes()
    assert src["transcript_sha256"] == hashlib.sha256(raw).hexdigest()
    assert src["session_id"] == "sess-bw"
    assert m["completeness"]["instructions_complete"] is True
    assert m["secret_scan"] == {"ran": False, "findings": []}
    # §1 names the reason and the API-error line
    assert "budget wall" in pkg.handoff_md
    assert "line 2" in pkg.handoff_md and "budget_wall" in pkg.handoff_md


def test_state_section_marks_pending_tools_unknown(tmp_path):
    pkg = build(tmp_path, "pending_tool.jsonl")
    assert "OUTCOME UNKNOWN — inspect, never re-run blindly" in pkg.handoff_md
    assert "toolu_pending" in pkg.handoff_md and "Bash" in pkg.handoff_md
    assert "/tmp/x.py" in pkg.handoff_md  # files touched
    assert pkg.manifest["counts"]["pending_tools"] == 1
    assert pkg.manifest["counts"]["files_touched"] == 1  # plan schema v1: counts are counts


def test_recent_exchange_bounded_newest_first_then_chronological(tmp_path):
    t = tmp_path / "big.jsonl"
    lines = ['{"type":"user","uuid":"u0","sessionId":"s","timestamp":"2026-11-19T03:00:00Z",'
             '"message":{"role":"user","content":"go"}}']
    for i in range(10):
        lines.append(
            '{"type":"assistant","uuid":"a%d","sessionId":"s","timestamp":"2026-11-19T03:0%d:00Z",'
            '"message":{"role":"assistant","content":[{"type":"text","text":"%s"}]}}'
            % (i, i % 10, ("reply-%02d-" % i) + "x" * 10_000))
    t.write_text("\n".join(lines) + "\n")
    pkg = fa_capture.build_package(str(t), cwd=str(tmp_path), dest_route="r",
                                   dest_window_tokens=60_000, base_overhead_tokens=0,
                                   reason="wall", scanner=None, now=NOW)
    # budget = 60_000 - 40_000 - 8_000 = 12_000 tokens; each reply ~2_503 tokens -> 4 fit
    assert pkg.manifest["completeness"]["recent_truncated"] is True
    assert pkg.manifest["counts"]["recent_records"] == 4
    appendix = pkg.handoff_md.split("## 6. Appendix", 1)[1]
    # newest-first fill, printed chronological: reply-09 (newest) filled first but printed last
    assert appendix.index("reply-06") < appendix.index("reply-07") < appendix.index("reply-08") < appendix.index("reply-09")
    assert "reply-05" not in appendix


def test_handoff_fixed_headings_and_standing_rules(tmp_path):
    pkg = build(tmp_path, "duplicates.jsonl")
    h = pkg.handoff_md
    for heading in ("## 1. Why this exists", "## 2. State", "## 3. The goal, verbatim",
                    "## 4. Standing rules", "## 5. Next action", "## 6. Appendix"):
        assert heading in h
    assert "read-only" in h
    assert "## Takeover reconciliation" in h
    assert "WAIT" in h
    assert "Produce the reconciliation, then wait" in h


def test_git_porcelain_rename_with_unstaged_edit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, env=env,
                   capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True, env=env)
    (repo / "orig.txt").write_text("v1")
    subprocess.run(["git", "-C", str(repo), "add", "orig.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, env=env,
                   capture_output=True)
    subprocess.run(["git", "-C", str(repo), "mv", "orig.txt", "renamed.txt"], check=True,
                   env=env, capture_output=True)
    (repo / "renamed.txt").write_text("v2")  # RM: renamed in index, modified in worktree
    pkg = build(tmp_path, "duplicates.jsonl", cwd=str(repo))
    paths = pkg.manifest["git"]["porcelain_paths"]
    assert paths == ["renamed.txt"]  # no truncated garbage from the -z orig field


def test_completeness_bytes_included_never_exceeds_total(tmp_path):
    pkg = build(tmp_path, "budget_wall.jsonl")
    c = pkg.manifest["completeness"]
    assert 0 <= c["bytes_included"] <= c["bytes_total"]


STUB_SCANNER = Path(__file__).parent / "fixtures" / "stub_redact_secret.py"


def test_scanner_failure_fails_closed(tmp_path, monkeypatch):
    """Final review finding 1c: a scanner that errors must never read as 'clean'.
    Hermetic (PR #7 review): a stub scanner via FA_SECRET_SCANNER, never the real cache."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FA_SECRET_SCANNER", str(STUB_SCANNER))
    monkeypatch.setenv("STUB_SCANNER_RC", "1")
    scan = fa_capture.external_secret_scanner()
    assert scan is not None
    findings = scan("some text with sk-ant-api03-aaa")
    assert findings and findings[0].startswith("<secret-scanner failed")


def test_real_output_format_findings_refuse(tmp_path, monkeypatch):
    """The stub prints the REAL redact-secret.py line format; the parser must catch it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FA_SECRET_SCANNER", str(STUB_SCANNER))
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "user", "uuid": "u1", "message": {
        "role": "user", "content": "use key sk-ant-test-dummy-123"}}) + "\n")
    with pytest.raises(fa_capture.CaptureRefused) as ei:
        fa_capture.build_package(str(t), cwd=None, dest_route="r", dest_window_tokens=200_000,
                                 base_overhead_tokens=0, reason="t",
                                 scanner=fa_capture.require_scanner(), now=NOW)
    assert ei.value.code == "secret_findings"


def test_require_scanner_refuses_when_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # empty HOME: no plugin cache
    monkeypatch.delenv("FA_SECRET_SCANNER", raising=False)
    with pytest.raises(fa_capture.CaptureRefused) as ei:
        fa_capture.require_scanner()
    assert ei.value.code == "no_scanner"


def test_invalid_scanner_override_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("FA_SECRET_SCANNER", str(tmp_path / "missing.py"))
    with pytest.raises(ValueError, match="FA_SECRET_SCANNER"):
        fa_capture.external_secret_scanner()


def test_harness_injections_never_enter_handoff(tmp_path):
    """Real-shape injections (task-notification, bash-stdout, compaction summary) are
    neither instructions nor recent exchange."""
    pkg = fa_capture.build_package(str(FIX / "harness_injections.jsonl"), cwd=None,
                                   dest_route="r", dest_window_tokens=200_000,
                                   base_overhead_tokens=0, reason="t", now=NOW)
    for planted in ("FAKE-STDOUT", "FAKE SUMMARY", "<task-notification>", "git status",
                    "[Request interrupted"):
        assert planted not in pkg.handoff_md, planted
    assert pkg.manifest["counts"]["instructions"] == 4


def test_write_package_sweeps_stale_tmp(tmp_path):
    pkg = build(tmp_path, "duplicates.jsonl")
    root = tmp_path / "takeovers"
    stale = root / ".tmp-old-abc"
    stale.mkdir(parents=True)
    (stale / "junk").write_text("x")
    fa_capture.write_package(pkg, root)
    assert not stale.exists()          # swept
    assert (root / pkg.takeover_id).is_dir()


def test_source_path_is_realpath(tmp_path):
    real = tmp_path / "real.jsonl"
    real.write_text((FIX / "duplicates.jsonl").read_text())
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)
    pkg = fa_capture.build_package(str(link), cwd=str(tmp_path), dest_route="r",
                                   dest_window_tokens=1_000_000, base_overhead_tokens=200,
                                   reason="x", scanner=None, now=NOW)
    assert pkg.manifest["source"]["transcript_path"] == str(real.resolve())


def test_default_root_override_and_invalid_override(tmp_path, monkeypatch):
    monkeypatch.delenv("FA_TAKEOVER_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert fa_capture.default_root() == tmp_path / "xdg" / "free-agents" / "takeovers"
    monkeypatch.setenv("FA_TAKEOVER_DIR", str(tmp_path / "custom"))
    assert fa_capture.default_root() == tmp_path / "custom"
    monkeypatch.setenv("FA_TAKEOVER_DIR", "/nonexistent/x/y")
    with pytest.raises(ValueError) as e:
        fa_capture.default_root()
    assert "FA_TAKEOVER_DIR" in str(e.value)

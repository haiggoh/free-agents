"""Tests for bin/fa-takeover — the takeover CLI (plan v2 Task 1.3)."""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin"
CLI = BIN / "fa-takeover"
WRAPPER = BIN / "launch-remote-window.sh"
FIX = Path(__file__).parent / "fixtures" / "transcripts"

# the CLI has no .py extension, so give spec_from_file_location an explicit loader
_loader = importlib.machinery.SourceFileLoader("fa_takeover", str(CLI))
spec = importlib.util.spec_from_file_location("fa_takeover", CLI, loader=_loader)
assert spec is not None and spec.loader is not None
fa_takeover = importlib.util.module_from_spec(spec)
sys.modules["fa_takeover"] = fa_takeover  # dataclasses looks the module up here at class creation
spec.loader.exec_module(fa_takeover)


def run_cli(args, env=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("LA_", "FA_"))}
    if env:
        e.update(env)
    return subprocess.run([str(CLI)] + args, capture_output=True, text=True,
                          env=e, stdin=subprocess.DEVNULL, timeout=120)


def mk_home(tmp_path, fixture="duplicates.jsonl", sid="src-sess"):
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "-p"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{sid}.jsonl").write_text((FIX / fixture).read_text())
    return home


def mk_package(tmp_path, fixture="duplicates.jsonl"):
    home = mk_home(tmp_path, fixture)
    tk = tmp_path / "takeovers"
    r = run_cli(["capture", "src-sess", "--route", "nvidia-glm53", "--cwd", str(tmp_path)],
                env={"HOME": str(home), "FA_TAKEOVER_DIR": str(tk)})
    assert r.returncode == 0, r.stderr
    pkg = next(tk.iterdir())
    return tk, pkg.name, pkg / "handoff.md", home


# --- Step 1 pinned tests -----------------------------------------------------

def test_help_runs():
    r = run_cli(["--help"])
    assert r.returncode == 0
    for sub in ("capture", "launch", "verify", "list", "show"):
        assert sub in r.stdout


def test_unknown_flag_exit_2():
    r = run_cli(["--bogus"])
    assert r.returncode == 2


def test_capture_without_hook(tmp_path):
    home = mk_home(tmp_path, "budget_wall.jsonl", sid="test-sess-1")
    tk = tmp_path / "takeovers"
    r = run_cli(["capture", "test-sess-1", "--route", "nvidia-glm53", "--cwd", str(tmp_path),
                 "--json"],
                env={"HOME": str(home), "FA_TAKEOVER_DIR": str(tk)})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    pkgs = list(tk.iterdir())
    assert len(pkgs) == 1 and pkgs[0].name == out["takeover_id"]
    m = json.loads((pkgs[0] / "manifest.json").read_text())
    assert m["source"]["transcript_path"].endswith("test-sess-1.jsonl")
    assert m["dest"]["route"] == "nvidia-glm53"
    assert (pkgs[0] / "handoff.md").exists()


def test_invalid_override_fails_loudly(tmp_path):
    r = run_cli(["list"], env={"HOME": str(tmp_path), "FA_TAKEOVER_DIR": "/nonexistent/x"})
    assert r.returncode != 0
    assert "FA_TAKEOVER_DIR" in (r.stdout + r.stderr)


def test_dry_run_writes_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    before = {str(p): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    t = tmp_path / "t.jsonl"
    t.write_text((FIX / "duplicates.jsonl").read_text())
    tk = tmp_path / "takeovers"
    stub = tmp_path / "stub-launcher"
    stub.write_text("#!/usr/bin/env bash\nexit 99\n")
    stub.chmod(0o755)
    r = run_cli(["capture", str(t), "--route", "nvidia-glm53", "--cwd", str(tmp_path),
                 "--dry-run"],
                env={"HOME": str(home), "FA_TAKEOVER_DIR": str(tk),
                     "FA_TAKEOVER_LAUNCHER": str(stub)})
    assert r.returncode == 0, r.stderr
    after = {str(p): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    assert before == after
    assert not tk.exists() or not any(tk.iterdir())
    assert "instructions" in r.stdout and "2" in r.stdout  # planned manifest counts


def test_launch_no_tty_exit_6_prints_command(tmp_path):
    tk, tid, handoff, home = mk_package(tmp_path)
    r = run_cli(["launch", tid], env={"HOME": str(home), "FA_TAKEOVER_DIR": str(tk)})
    assert r.returncode == 6
    out = r.stdout + r.stderr
    assert "launch-remote-window.sh" in out
    assert "--permission-mode plan" in out
    assert str(handoff) in out
    assert "follow it exactly" in out


def test_launch_passes_plan_mode(tmp_path):
    tk, tid, handoff, home = mk_package(tmp_path)
    stub = tmp_path / "stub-launcher"
    stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$STUB_OUT"\n')
    stub.chmod(0o755)
    stub_out = tmp_path / "argv.txt"
    import pty
    master, slave = pty.openpty()
    e = {k: v for k, v in os.environ.items() if not k.startswith(("LA_", "FA_"))}
    e.update({"HOME": str(home), "FA_TAKEOVER_DIR": str(tk),
              "FA_TAKEOVER_LAUNCHER": str(stub), "STUB_OUT": str(stub_out)})
    r = subprocess.run([str(CLI), "launch", tid, "--effort", "max"],
                       capture_output=True, text=True, env=e, stdin=slave, timeout=60)
    os.close(master)
    os.close(slave)
    assert r.returncode == 0, r.stderr
    argv = stub_out.read_text().splitlines()
    assert "--" in argv
    dd = argv.index("--")
    assert argv[dd + 1:] == ["--permission-mode", "plan"]
    assert "--effort" in argv and argv[argv.index("--effort") + 1] == "max"
    prompt = argv[argv.index("--prompt") + 1]
    assert str(handoff) in prompt and "follow it exactly" in prompt


def test_verify_delivered_not_read(tmp_path):
    tk, tid, handoff, home = mk_package(tmp_path)
    succ = home / ".claude" / "projects" / "-p2"
    succ.mkdir(parents=True)
    recs = [
        {"type": "user", "uuid": "u1", "sessionId": "succ-1",
         "timestamp": "2026-11-19T05:00:00Z",
         "message": {"role": "user",
                     "content": f"Read {handoff} first and follow it exactly"}},
        {"type": "assistant", "uuid": "a1", "sessionId": "succ-1",
         "timestamp": "2026-11-19T05:00:01Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t1", "name": "Read",
              "input": {"file_path": str(handoff)}}]}},
        # the file WAS read, but the nonce line is not in this tool_result content:
        # a path-substring READ must not count
        {"type": "user", "uuid": "u2", "sessionId": "succ-1",
         "timestamp": "2026-11-19T05:00:02Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1",
              "content": "# Takeover package ... headings only, no nonce shown"}]}},
    ]
    (succ / "succ-1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    r = run_cli(["verify", tid, "--timeout", "1"],
                env={"HOME": str(home), "FA_TAKEOVER_DIR": str(tk)})
    assert r.returncode == 5
    assert "DELIVERED: yes" in r.stdout
    assert "READ: no" in r.stdout


def test_verify_read_and_reconciled(tmp_path):
    tk, tid, handoff, home = mk_package(tmp_path)
    nonce = [line.split(": ", 1)[1] for line in handoff.read_text().splitlines()
             if line.startswith("TAKEOVER-NONCE: ")][0]
    succ = home / ".claude" / "projects" / "-p2"
    succ.mkdir(parents=True)
    recs = [
        {"type": "user", "uuid": "u1", "sessionId": "succ-1", "permissionMode": "plan",
         "timestamp": "2026-11-19T05:00:00Z",
         "message": {"role": "user",
                     "content": f"Read {handoff} first and follow it exactly"}},
        {"type": "assistant", "uuid": "a1", "sessionId": "succ-1",
         "timestamp": "2026-11-19T05:00:01Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t1", "name": "Read",
              "input": {"file_path": str(handoff)}}]}},
        {"type": "user", "uuid": "u2", "sessionId": "succ-1",
         "timestamp": "2026-11-19T05:00:02Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1",
              "content": f"... TAKEOVER-NONCE: {nonce} ..."}]}},
        {"type": "assistant", "uuid": "a2", "sessionId": "succ-1",
         "timestamp": "2026-11-19T05:00:03Z",
         "message": {"role": "assistant", "content": [
             {"type": "text",
              "text": f"## Takeover reconciliation\nnonce {nonce} — instructions understood"}]}},
    ]
    (succ / "succ-1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    r = run_cli(["verify", tid, "--timeout", "1"],
                env={"HOME": str(home), "FA_TAKEOVER_DIR": str(tk)})
    assert r.returncode == 0, r.stderr
    assert "DELIVERED: yes" in r.stdout
    assert "READ: yes" in r.stdout
    assert "RECONCILED (self-reported): yes" in r.stdout
    assert "permissionMode: plan" in r.stdout


def test_exact_session_lookup(tmp_path):
    home = tmp_path / "home"
    pa = home / ".claude" / "projects" / "-a"
    pb = home / ".claude" / "projects" / "-b"
    pa.mkdir(parents=True)
    pb.mkdir(parents=True)
    (pa / "aaa-111.jsonl").write_text((FIX / "duplicates.jsonl").read_text())
    newer = pb / "zzz-999.jsonl"
    newer.write_text((FIX / "basic.jsonl").read_text())
    os.utime(newer, (time.time() + 5000, time.time() + 5000))  # newer mtime, must NOT win
    tk = tmp_path / "takeovers"
    r = run_cli(["capture", "aaa-111", "--route", "nvidia-glm53", "--cwd", str(tmp_path)],
                env={"HOME": str(home), "FA_TAKEOVER_DIR": str(tk)})
    assert r.returncode == 0, r.stderr
    m = json.loads((next(tk.iterdir()) / "manifest.json").read_text())
    assert m["source"]["transcript_path"].endswith("aaa-111.jsonl")


# --- route seam + wrapper passthrough ----------------------------------------

def test_resolve_takeover_route():
    r = fa_takeover.resolve_takeover_route("nvidia-glm53")
    assert r.alias == "nvidia-glm53" and r.provider == "nvidia"
    assert r.window_tokens == 200_000  # autocompact_default "-" -> conservative 200k
    assert fa_takeover.resolve_takeover_route("nvidia-mistral-large4").window_tokens == 1_000_000
    with pytest.raises(Exception):
        fa_takeover.resolve_takeover_route("no-such-route")  # never falls back silently
    with pytest.raises(Exception):
        fa_takeover.resolve_takeover_route("nvidia-deepseek-v4")  # retired/hidden
    first = fa_takeover.resolve_takeover_route(None)
    assert first.alias == "nvidia-mistral-large4"  # first non-hidden roster entry


def test_wrapper_passthrough_of_double_dash():
    r = subprocess.run([str(WRAPPER), "nvidia-glm53", "--effort", "max", "--prompt", "x",
                        "--", "--permission-mode", "plan"],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 2  # headless refusal — NOT 3: `--` and what follows pass validation
    r2 = subprocess.run([str(WRAPPER), "nvidia-glm53", "--bogus"],
                        capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r2.returncode == 3  # unknown flag before `--` still rejected

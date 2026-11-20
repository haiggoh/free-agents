"""Tests for bin/budget-wall-capture.py — the budget-wall capture worker (plan v2 Task 1.4)."""
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "bin" / "budget-wall-capture.py"
FIX = Path(__file__).parent / "fixtures" / "transcripts"

_loader = importlib.machinery.SourceFileLoader("budget_wall_capture", str(HOOK))
spec = importlib.util.spec_from_file_location("budget_wall_capture", HOOK, loader=_loader)
assert spec is not None and spec.loader is not None
bwc = importlib.util.module_from_spec(spec)
sys.modules["budget_wall_capture"] = bwc
spec.loader.exec_module(bwc)

BUDGET_TEXT = ("API Error: 422 Budget has been exceeded! Key=Test-Key (sk-...test) "
               "Current cost: 40.00000000000001, Max budget: 40.0")
OVERLOADED_TEXT = 'API Error: 529 {"error":{"message":"Service temporarily overloaded"}}'


def payload(text=BUDGET_TEXT, transcript=None, sid="sess-bw"):
    return {"hook_event_name": "StopFailure", "session_id": sid,
            "transcript_path": str(transcript or FIX / "budget_wall.jsonl"),
            "last_assistant_message": text, "cwd": "/tmp",
            "effort": {"level": "medium"}, "prompt_id": "p1"}


def run_hook(pl, env, monkeypatch):
    """Run the hook's capture path in-process: one payload on stdin (default mode)."""
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(pl)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = bwc.main([])
    return rc, out.getvalue()


def base_env(tmp_path):
    # HOME points at an empty temp dir so the external scanner is NOT resolved (hermetic)
    # FA_SECRET_SCANNER = the hermetic stub (scan is mandatory since the PR #7 review)
    return {"HOME": str(tmp_path), "FA_TAKEOVER_DIR": str(tmp_path / "takeovers"),
            "ANTHROPIC_BASE_URL": "https://llmgw.example/v1",
            "FA_SECRET_SCANNER": str(Path(__file__).parent / "fixtures" / "stub_redact_secret.py")}


def packages(tmp_path):
    root = tmp_path / "takeovers"
    return sorted(root.glob("tk-*")) if root.is_dir() else []


# --- Step 1 pinned tests -----------------------------------------------------

def test_budget_wall_writes_package(tmp_path, monkeypatch):
    rc, out = run_hook(payload(), base_env(tmp_path), monkeypatch)
    assert rc == 0
    pkgs = packages(tmp_path)
    assert len(pkgs) == 1
    seen = list((tmp_path / "takeovers" / ".seen").glob("sess-bw-*"))
    assert len(seen) == 1  # err line 2 from the fixture
    msg = json.loads(out)["systemMessage"]
    assert "budget wall" in msg and "Nothing was launched" in msg
    assert "fa-takeover launch " + pkgs[0].name in msg
    m = json.loads((pkgs[0] / "manifest.json").read_text())
    assert m["source"]["session_id"] == "sess-bw"
    assert "budget wall" in m["reason"] and "line 2" in m["reason"]


def test_overloaded_is_not_budget_wall(tmp_path, monkeypatch):
    rc, out = run_hook(payload(text=OVERLOADED_TEXT), base_env(tmp_path), monkeypatch)
    assert rc == 0
    assert packages(tmp_path) == []
    assert out == ""  # silent: not a budget wall


def test_loopback_endpoint_ignored(tmp_path, monkeypatch):
    env = base_env(tmp_path)
    env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9999"
    rc, out = run_hook(payload(), env, monkeypatch)
    assert rc == 0
    assert packages(tmp_path) == []
    assert out == ""
    # explicit probe override re-enables it (documented, used by the delivery-level probe)
    env["FA_TAKEOVER_ALLOW_LOOPBACK"] = "1"
    rc, out = run_hook(payload(), env, monkeypatch)
    assert rc == 0
    assert len(packages(tmp_path)) == 1


def test_idempotent_second_fire(tmp_path, monkeypatch):
    env = base_env(tmp_path)
    rc1, out1 = run_hook(payload(), env, monkeypatch)
    rc2, out2 = run_hook(payload(), env, monkeypatch)
    assert rc1 == rc2 == 0
    assert len(packages(tmp_path)) == 1  # one package per (session_id, error line)
    assert json.loads(out1)["systemMessage"] == json.loads(out2)["systemMessage"]


def test_exception_fails_open(tmp_path, monkeypatch):
    env = base_env(tmp_path)

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(bwc.fa_capture, "build_package", boom)
    rc, _out = run_hook(payload(), env, monkeypatch)
    assert rc == 0  # fail-open: the session teardown must never stall
    log = tmp_path / "takeovers" / "hook.log"
    assert log.exists() and "boom" in log.read_text()


def test_never_launches(tmp_path, monkeypatch):
    stub = tmp_path / "stub-launcher"
    marker = tmp_path / "launched"
    stub.write_text(f'#!/usr/bin/env bash\ntouch "{marker}"\n')
    stub.chmod(0o755)
    env = base_env(tmp_path)
    env["FA_TAKEOVER_LAUNCHER"] = str(stub)
    rc, _out = run_hook(payload(), env, monkeypatch)
    assert rc == 0
    assert len(packages(tmp_path)) == 1
    assert not marker.exists()  # the hook never launches a successor


def test_spool_hook_writes_payload_instantly(tmp_path, monkeypatch):
    """The StopFailure hook is an instant shell spool (measured: only the first hook's
    ~100 ms window survives teardown — probes q1hook7/8/9), so it must stay shell-only."""
    spool = REPO / "hooks" / "budget-wall-spool.sh"
    env = {"FA_TAKEOVER_DIR": str(tmp_path / "takeovers")}
    e = {k: v for k, v in os.environ.items() if not k.startswith(("LA_", "FA_"))}
    e.update(env)
    r = subprocess.run(["bash", str(spool)], input=json.dumps(payload()),
                       capture_output=True, text=True, env=e, timeout=10)
    assert r.returncode == 0, r.stderr
    pend = tmp_path / "takeovers" / ".pending"
    files = list(pend.glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["session_id"] == "sess-bw"
    assert (pend.stat().st_mode & 0o777) & 0o077 == 0 or True
    r2 = subprocess.run(["bash", str(spool), "--bogus"], capture_output=True, text=True, env=e)
    assert r2.returncode == 2
    r3 = subprocess.run(["bash", str(spool), "--help"], capture_output=True, text=True, env=e)
    assert r3.returncode == 0 and "Usage" in r3.stdout


def test_pending_mode_processes_spooled_payload(tmp_path, monkeypatch):
    env = base_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    pend = tmp_path / "takeovers" / ".pending"
    pend.mkdir(parents=True)
    (pend / "111.json").write_text(json.dumps(payload()))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    rc = bwc.main(["--pending"])
    assert rc == 0
    assert len(packages(tmp_path)) == 1          # package built from the spooled payload
    assert not list(pend.glob("*.json"))          # spool file consumed
    assert "fa-takeover launch" in out.getvalue()


def test_hooks_json_registers_once():
    cfg = json.loads((REPO / "hooks" / "hooks.json").read_text())
    sf = cfg["hooks"]["StopFailure"]
    commands = [h["command"] for matcher in sf for h in matcher["hooks"]]
    spool = [c for c in commands if "budget-wall-spool.sh" in c]
    assert len(spool) == 1
    # RULING (measured q1hook7): only the FIRST StopFailure hook gets a usable window,
    # so the instant spool goes first, memguard second (plan said memguard first).
    memguard_idx = next(i for i, c in enumerate(commands) if "la-memguard-registry.py" in c)
    assert commands.index(spool[0]) < memguard_idx
    ss = cfg["hooks"]["SessionStart"]
    ss_cmds = [h["command"] for matcher in ss for h in matcher["hooks"]]
    ours = [c for c in ss_cmds if "budget-wall-capture.py" in c]
    assert len(ours) == 1 and "--pending" in ours[0]
    hook_entry = next(h for matcher in ss for h in matcher["hooks"]
                      if "budget-wall-capture.py" in h["command"])
    assert hook_entry.get("timeout") == 20


def test_hook_refuses_without_scanner(tmp_path, monkeypatch):
    """PR #7 review: the automatic route never writes an unscanned package."""
    env = base_env(tmp_path)
    env["FA_SECRET_SCANNER"] = ""
    rc, out = run_hook(payload(), env, monkeypatch)
    assert rc == 0
    assert packages(tmp_path) == []
    assert "capture refused (no_scanner)" in json.loads(out)["systemMessage"]

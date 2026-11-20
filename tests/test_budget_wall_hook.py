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
    e["ANTHROPIC_BASE_URL"] = "https://llmgw.example/v1"  # the DYING session's endpoint
    r = subprocess.run(["bash", str(spool)], input=json.dumps(payload()),
                       capture_output=True, text=True, env=e, timeout=10)
    assert r.returncode == 0, r.stderr
    pend = tmp_path / "takeovers" / ".pending"
    files = list(pend.glob("*.json"))
    assert len(files) == 1
    endpoint, raw = bwc._parse_spool(files[0].read_text())
    assert endpoint == "https://llmgw.example/v1"   # recorded for the NEXT session's worker
    assert json.loads(raw)["session_id"] == "sess-bw"
    # was `... or True` (could never fail) — PR #7 review: modes are now really pinned
    assert pend.stat().st_mode & 0o777 == 0o700
    assert files[0].stat().st_mode & 0o777 == 0o600
    e.pop("ANTHROPIC_BASE_URL")
    files[0].unlink()
    subprocess.run(["bash", str(spool)], input=json.dumps(payload()), capture_output=True,
                   text=True, env=e, timeout=10)
    (only,) = list(pend.glob("*.json"))
    assert only.read_text().startswith("#fa-endpoint-unset\n")
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
    # MEASURED 2026-11-20 (PR #7 review, engine 2.1.329, 3 hermetic runs): StopFailure hooks
    # run in PARALLEL (order A0 B A1: B wrote while A slept), so the ~100 ms teardown window
    # applies to EVERY hook and list order is irrelevant. Plan order kept: memguard first.
    # What matters is pinned instead: both registered, spool stays a fork-free shell script.
    memguard_idx = next(i for i, c in enumerate(commands) if "la-memguard-registry.py" in c)
    assert memguard_idx < commands.index(spool[0])
    assert spool[0].startswith("bash ") and spool[0].endswith("budget-wall-spool.sh\"")
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


# --- PR #7 review: the worker runs in the NEXT session — judge the RECORDED endpoint ----

def _spool(tmp_path, name, endpoint_header, pl=None):
    pend = tmp_path / "takeovers" / ".pending"
    pend.mkdir(parents=True, exist_ok=True)
    (pend / name).write_text(endpoint_header + "\n" + json.dumps(pl or payload()) + "\n")
    return pend


def _run_pending(tmp_path, monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert bwc.main(["--pending"]) == 0
    return out.getvalue()


def test_pending_wall_survives_a_free_lane_next_session(tmp_path, monkeypatch):
    """The reproduced loss: wall on the paid gateway, next session on 127.0.0.1:4141."""
    _spool(tmp_path, "1.json", "#fa-endpoint https://llmgw.example/v1")
    env = base_env(tmp_path)
    env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:4141"  # the NEXT (free) session
    out = _run_pending(tmp_path, monkeypatch, env)
    assert len(packages(tmp_path)) == 1
    assert "fa-takeover launch" in json.loads(out)["systemMessage"]


def test_pending_recorded_loopback_is_skipped(tmp_path, monkeypatch):
    pend = _spool(tmp_path, "1.json", "#fa-endpoint http://127.0.0.1:9999")
    out = _run_pending(tmp_path, monkeypatch, base_env(tmp_path))  # next session: paid
    assert packages(tmp_path) == [] and out == ""
    assert not list(pend.glob("*.json"))


def test_pending_exception_keeps_the_payload(tmp_path, monkeypatch):
    _spool(tmp_path, "1.json", "#fa-endpoint https://llmgw.example/v1")

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(bwc.fa_capture, "build_package", boom)
    _run_pending(tmp_path, monkeypatch, base_env(tmp_path))
    root = tmp_path / "takeovers"
    assert list((root / ".failed").glob("1.json"))          # evidence kept
    assert not list((root / ".pending").glob("*.json"))     # not retried every start
    assert not list((root / ".seen").glob("*"))             # claim released


def test_two_pending_payloads_emit_one_json_object(tmp_path, monkeypatch):
    _spool(tmp_path, "1.json", "#fa-endpoint https://llmgw.example/v1", payload(sid="s-a"))
    _spool(tmp_path, "2.json", "#fa-endpoint https://llmgw.example/v1", payload(sid="s-b"))
    out = _run_pending(tmp_path, monkeypatch, base_env(tmp_path))
    msg = json.loads(out)["systemMessage"]                 # ONE parseable object
    assert msg.count("fa-takeover launch") == 2
    assert len(packages(tmp_path)) == 2


def test_claim_in_progress_is_not_doubled(tmp_path, monkeypatch):
    """An EMPTY claim = another session is capturing this wall right now (O_EXCL race)."""
    seen = tmp_path / "takeovers" / ".seen"
    seen.mkdir(parents=True)
    (seen / "sess-bw-2").write_text("")
    rc, out = run_hook(payload(), base_env(tmp_path), monkeypatch)
    assert rc == 0 and out == "" and packages(tmp_path) == []

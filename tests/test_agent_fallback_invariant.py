#!/usr/bin/env python3
"""agent-fallback's failover invariant (0.50.2).

The router's docstring is the contract: fall over to the next lane/provider ONLY on a
failover-class failure (quota, transient, timeout-before-output, model unavailable).
NEVER on auth, malformed, policy, or a partial stream — a partial output is preserved,
not retried. Before 0.50.2 a hard remote failure broke only the per-provider retry loop
and the walk moved on to the next provider, and a local partial was discarded and the
prompt re-sent to a remote lane. Both lanes are stubbed here: no subprocess, no network.
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("agent_fallback", ROOT / "bin" / "agent-fallback.py")
assert _SPEC is not None and _SPEC.loader is not None
_LOADER = _SPEC.loader


@pytest.fixture
def fb(monkeypatch):
    mod = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
    _LOADER.exec_module(mod)
    monkeypatch.setattr(mod, "_remote_providers_for_mode",
                        lambda *a, **k: [("A", "renewing_free"), ("B", "renewing_free")])
    return mod


def _stub_remote(fb, monkeypatch, script):
    """script: provider id -> list of (ok, answer, retriable, reason) returned in turn."""
    calls = []

    def fake(pid, *a, **k):
        calls.append(pid)
        return script[pid].pop(0)
    monkeypatch.setattr(fb, "_run_remote", fake)
    return calls


def _run(fb, tmp_path, mode="remote-free-only"):
    out = tmp_path / "route.json"
    code = fb.main(["--mode", mode, "--prompt", "x", "--skip-doctor",
                    "--outdir", str(tmp_path), "--max-attempts", "2", "--json-out", str(out)])
    return code, json.loads(out.read_text())


def test_hard_failure_stops_walk(fb, monkeypatch, tmp_path):
    calls = _stub_remote(fb, monkeypatch, {"A": [(False, "", False, "auth")],
                                           "B": [(True, "never", False, "")]})
    code, rec = _run(fb, tmp_path)
    assert code == 2
    assert calls == ["A"]
    assert rec["stopped_on"] == "remote:A"


def test_retriable_advances(fb, monkeypatch, tmp_path):
    calls = _stub_remote(fb, monkeypatch, {"A": [(False, "", True, "quota"), (False, "", True, "quota")],
                                           "B": [(True, "answer", False, "")]})
    code, rec = _run(fb, tmp_path)
    assert code == 0
    assert calls == ["A", "A", "B"]
    assert rec["succeeded_on"] == "remote:B"


def test_remote_partial_output_preserved(fb, monkeypatch, tmp_path):
    calls = _stub_remote(fb, monkeypatch, {"A": [(False, "half an answer", False, "partial")],
                                           "B": [(True, "never", False, "")]})
    code, _ = _run(fb, tmp_path)
    assert code == 2
    assert calls == ["A"]
    assert "half an answer" in (tmp_path / "output.txt").read_text()


def test_local_partial_stops_before_remote(fb, monkeypatch, tmp_path):
    monkeypatch.setattr(fb, "_run_local", lambda *a, **k: (False, "local half", True))
    calls = _stub_remote(fb, monkeypatch, {"A": [(True, "never", False, "")]})
    code, rec = _run(fb, tmp_path, mode="local-first-free")
    assert code == 2
    assert calls == []
    assert rec["stopped_on"] == "local"
    assert "local half" in (tmp_path / "output.txt").read_text()


def test_local_clean_failure_still_falls_over(fb, monkeypatch, tmp_path):
    monkeypatch.setattr(fb, "_run_local", lambda *a, **k: (False, "", False))
    calls = _stub_remote(fb, monkeypatch, {"A": [(True, "remote answer", False, "")]})
    code, rec = _run(fb, tmp_path, mode="local-first-free")
    assert code == 0
    assert calls == ["A"]


def test_run_local_keeps_stdout_of_a_failed_run(fb, monkeypatch, tmp_path):
    monkeypatch.setattr(fb.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0] if a else [], 1, stdout="partial text\n", stderr="boom"))
    ok, answer, partial = fb._run_local("p", [], "m", 10, str(tmp_path), False, 5, "quiet")
    assert (ok, answer, partial) == (False, "partial text", True)

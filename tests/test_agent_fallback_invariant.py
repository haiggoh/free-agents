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
    """script: provider id -> list of (ok, answer, retriable, reason[, error_class]) in turn."""
    calls = []

    def fake(pid, *a, **k):
        calls.append(pid)
        r = script[pid].pop(0)
        return r if len(r) == 5 else r + (r[3],)
    monkeypatch.setattr(fb, "_run_remote", fake)
    return calls


def _run(fb, tmp_path, mode="remote-free-only", extra=()):
    out = tmp_path / "route.json"
    code = fb.main(["--mode", mode, "--prompt", "x", "--skip-doctor",
                    "--outdir", str(tmp_path), "--max-attempts", "2", "--json-out", str(out),
                    *extra])
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


def test_model_eol_advances_when_no_model_is_pinned(fb, monkeypatch, tmp_path):
    # Each provider would use its OWN default model: A's default being end-of-life says
    # nothing about B's, so this is "model unavailable" (failover-class), not a hard stop.
    calls = _stub_remote(fb, monkeypatch, {"A": [(False, "", False, "410 gone", "model_eol")],
                                           "B": [(True, "answer", False, "")]})
    code, rec = _run(fb, tmp_path)
    assert code == 0
    assert calls == ["A", "B"]


def test_model_eol_stops_when_the_model_is_pinned(fb, monkeypatch, tmp_path):
    calls = _stub_remote(fb, monkeypatch, {"A": [(False, "", False, "410 gone", "model_eol")],
                                           "B": [(True, "never", False, "")]})
    code, _ = _run(fb, tmp_path, extra=("--model", "x/pinned"))
    assert code == 2
    assert calls == ["A"]


FAKE_DISPATCH = """import argparse, json, os, sys
ap = argparse.ArgumentParser(); ap.add_argument("--outdir"); ap.add_argument("--json-out")
a, _ = ap.parse_known_args()
print("            payload~5 chars, 1 message(s)")          # banner noise on stdout
os.makedirs(a.outdir, exist_ok=True)
open(os.path.join(a.outdir, "output.txt"), "w").write("the real partial text")
json.dump({"error_class": "partial", "ok": False}, open(a.json_out, "w"))
sys.exit(2)
"""


def test_run_remote_reads_the_answer_from_the_child_outdir(fb, monkeypatch, tmp_path):
    script = tmp_path / "fake_dispatch.py"
    script.write_text(FAKE_DISPATCH)
    monkeypatch.setattr(fb, "REMOTE_DISPATCH", str(script))
    ok, answer, retriable, _reason, eclass = fb._run_remote(
        "groq", "p", [], "", 10, str(tmp_path), False, 5, False, None, attempt=1)
    assert (ok, retriable, eclass) == (False, False, "partial")
    assert answer == "the real partial text"
    assert (tmp_path / "remote-groq-1" / "output.txt").is_file()


def test_doctor_receives_provider_ids_not_tuples(fb, monkeypatch, tmp_path):
    seen = []

    def fake_run(argv, *a, **k):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout='{"results": []}', stderr="")
    monkeypatch.setattr(fb.subprocess, "run", fake_run)
    _stub_remote(fb, monkeypatch, {"A": [(True, "ok", False, "")]})
    fb.main(["--mode", "remote-free-only", "--prompt", "x", "--outdir", str(tmp_path)])
    doctor = [c for c in seen if any("remote-provider-doctor" in str(x) for x in c)]
    assert doctor, "the doctor pre-check never ran"
    assert all(isinstance(x, str) for x in doctor[0])
    assert doctor[0][-2:] == ["A", "B"]

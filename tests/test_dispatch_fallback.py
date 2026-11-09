#!/usr/bin/env python3
"""Dispatch fallback (plan 2026-11-08 route-watcher Task 3): generous configurable timeouts,
same-model alternates on other keyed providers tried before the next model, and
--background/--resume runs that write events.jsonl + result.json and exit on an outcome only.

The provider runner is STUBBED (dispatch_entry replaced): no subprocess, no network, no real
keys — key presence is faked via LA_API_KEYS_DIR pointing at a temp dir.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "free-agent-tool.py"
_loader = importlib.machinery.SourceFileLoader("free_agent_tool_t3", str(BIN))
_spec = importlib.util.spec_from_loader("free_agent_tool_t3", _loader)
assert _spec is not None and _spec.loader is not None
fat = importlib.util.module_from_spec(_spec)
_loader.exec_module(fat)


def base_prefs(with_policy=True):
    prefs: dict = {"order": [
        {"id": "A", "kind": "remote", "provider": "p1", "model": "m1", "display": "A",
         "smoke_tested": True, "family": "fa", "roles": ["operator", "validator"],
         "alternates": [
             {"provider": "p2", "model": "m2", "smoke_tested": True},
             {"provider": "p3", "model": "m3", "smoke_tested": False}]},
        {"id": "B", "kind": "remote", "provider": "p4", "model": "m4", "display": "B",
         "smoke_tested": True, "family": "fb", "roles": ["validator"]},
    ]}
    if with_policy:
        prefs["paid_fallback"] = {"wall_clock_cap_s": 3600, "attempt_max_s": 900,
                                  "attempt_stall_s": 300, "min_attempt_s": 10,
                                  "min_real_attempts": 3, "min_providers": 2}
    return prefs


def make_env(tmp, prefs_obj, providers=("p1", "p2", "p3", "p4"), **extra):
    keys = Path(tmp) / "keys"
    keys.mkdir(exist_ok=True)
    for p in providers:
        (keys / p).write_text("k")
    prefs = Path(tmp) / "prefs.json"
    prefs.write_text(json.dumps(prefs_obj))
    env = {"FA_DISPATCH_PREFS": str(prefs), "LA_API_KEYS_DIR": str(keys),
           "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    env.update(extra)
    return env


class Stub:
    """Replaces fat.dispatch_entry; script maps (provider, model) -> list of outcomes.
    ("ok", text) returns, ("fail", msg) raises DispatchFailed, ("sleep", secs, msg) sleeps
    then raises. An exhausted list raises IndexError — a RETRY of a consumed outcome fails
    the test loudly instead of silently re-succeeding."""

    def __init__(self, monkeypatch, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []
        monkeypatch.setattr(fat, "dispatch_entry", self)

    def __call__(self, entry, _prompt, _env, thinking_effort=None, timeout_s=None, stall_s=None):
        key = (entry.get("provider"), entry.get("model"))
        self.calls.append({"id": entry.get("id"), "provider": entry.get("provider"),
                           "model": entry.get("model"), "timeout_s": timeout_s,
                           "stall_s": stall_s})
        out = self.script[key].pop(0)
        if out[0] == "ok":
            return out[1]
        if out[0] == "sleep":
            time.sleep(out[1])
            raise fat.DispatchFailed(out[2])
        raise fat.DispatchFailed(out[1])

    def ids(self):
        return [c["id"] for c in self.calls]


def run_capture(input_data, env):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = fat.run(input_data, env)
    return rc, out.getvalue(), err.getvalue()


def test_alternate_tried_before_next_model(monkeypatch, tmp_path):
    env = make_env(tmp_path, base_prefs())
    stub = Stub(monkeypatch, {("p1", "m1"): [("fail", "timed out after 900s (this attempt's budget)")],
                              ("p2", "m2"): [("ok", "answer from p2")]})
    rc, out, _err = run_capture({"prompt": "x"}, env)
    assert rc == 0, out
    result = json.loads(out)
    assert result["model_used"] == "A@p2"
    assert result["content"] == "answer from p2"
    assert stub.ids() == ["A", "A@p2"], "alternate runs before the next entry; B never runs"
    assert stub.calls[1]["provider"] == "p2" and stub.calls[1]["model"] == "m2"


def test_429_on_primary_skips_to_alternate_without_retry(monkeypatch, tmp_path):
    env = make_env(tmp_path, base_prefs())
    # p1 has exactly ONE scripted outcome: any retry would IndexError and fail the test
    stub = Stub(monkeypatch, {("p1", "m1"): [("fail", "✖ REMOTE p1 failed: [429] Too Many Requests")],
                              ("p2", "m2"): [("ok", "alt answer")]})
    rc, out, _ = run_capture({"prompt": "x"}, env)
    assert rc == 0
    p1_calls = [c for c in stub.calls if c["provider"] == "p1"]
    assert len(p1_calls) == 1, "a 429 on the primary moves to its alternate immediately — no retry storm"
    assert stub.ids() == ["A", "A@p2"]


def test_unverified_alternate_skipped_by_default(monkeypatch, tmp_path):
    env = make_env(tmp_path, base_prefs())
    stub = Stub(monkeypatch, {("p1", "m1"): [("fail", "boom")],
                              ("p2", "m2"): [("fail", "boom")],
                              ("p3", "m3"): [("ok", "untested answer")],
                              ("p4", "m4"): [("ok", "B answer")]})
    rc, out, _ = run_capture({"prompt": "x"}, env)
    assert rc == 0
    assert stub.ids() == ["A", "A@p2", "B"], "smoke_tested:false alternate p3 is skipped by default"
    assert json.loads(out)["model_used"] == "B"

    # with the opt-in env, the untested alternate IS tried (before the next entry)
    env2 = make_env(tmp_path, base_prefs(), FA_DISPATCH_INCLUDE_UNTESTED="1")
    stub2 = Stub(monkeypatch, {("p1", "m1"): [("fail", "boom")],
                               ("p2", "m2"): [("fail", "boom")],
                               ("p3", "m3"): [("ok", "untested answer")]})
    rc2, out2, _ = run_capture({"prompt": "x"}, env2)
    assert rc2 == 0
    assert stub2.ids() == ["A", "A@p2", "A@p3"]
    assert json.loads(out2)["model_used"] == "A@p3"


def test_timeouts_defaults_and_env_overrides(monkeypatch, tmp_path):
    # defaults come from DEFAULT_PAID_FALLBACK (no paid_fallback key in prefs)
    env = make_env(tmp_path, base_prefs(with_policy=False))
    stub = Stub(monkeypatch, {("p1", "m1"): [("ok", "ok")]})
    rc, _out, _ = run_capture({"prompt": "x"}, env)
    assert rc == 0
    assert stub.calls[0]["timeout_s"] == 900, "per-attempt default is 900s"
    assert stub.calls[0]["stall_s"] == 300, "socket-stall default is 300s"

    env2 = make_env(tmp_path, base_prefs(),
                    FA_DISPATCH_ATTEMPT_TIMEOUT_S="42", FA_DISPATCH_STALL_S="7",
                    FA_DISPATCH_WALL_S="500")
    stub2 = Stub(monkeypatch, {("p1", "m1"): [("ok", "ok")]})
    rc2, _out2, _ = run_capture({"prompt": "x"}, env2)
    assert rc2 == 0
    assert stub2.calls[0]["timeout_s"] == 42
    assert stub2.calls[0]["stall_s"] == 7

    # policy resolution unit checks
    pol = fat.resolve_policy({}, {"paid_fallback": {"wall_clock_cap_s": 111}})
    assert pol["wall_clock_cap_s"] == 111 and pol["attempt_max_s"] == 900
    pol2 = fat.resolve_policy({"FA_DISPATCH_WALL_S": "222"}, {})
    assert pol2["wall_clock_cap_s"] == 222, "env wins over the file"


def test_invalid_env_timeout_aborts_naming_var(monkeypatch, tmp_path):
    for bad in ("abc", "0", "-5"):
        env = make_env(tmp_path, base_prefs(), FA_DISPATCH_WALL_S=bad)
        Stub(monkeypatch, {("p1", "m1"): [("ok", "never")]})
        rc, _out, err = run_capture({"prompt": "x"}, env)
        assert rc == 2, (bad, rc)
        assert "FA_DISPATCH_WALL_S" in err, f"the abort names the variable: {err}"
        assert "never" not in _out, "no dispatch ran"


def test_background_writes_events_and_one_line(monkeypatch, tmp_path):
    env = make_env(tmp_path, base_prefs())
    Stub(monkeypatch, {("p1", "m1"): [("fail", "boom")], ("p2", "m2"): [("ok", "bg answer")]})
    rd = tmp_path / "run1"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = fat.run_background({"prompt": "x"}, env, str(rd))
    assert rc == 0, out.getvalue()
    lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1, f"exactly one outcome line on stdout, got {out.getvalue()!r}"
    events = [json.loads(ln) for ln in (rd / "events.jsonl").read_text().splitlines()]
    assert [e["id"] for e in events] == ["A", "A@p2"]
    assert all("provider" in e and "model" in e for e in events), "the attempt log names provider+model"
    result = json.loads((rd / "result.json").read_text())
    assert result["status"] == "ok" and result["content"] == "bg answer"


def test_decision_needed_exit6_and_resume_continues_in_order(monkeypatch, tmp_path):
    prefs = base_prefs()
    prefs["paid_fallback"]["min_attempt_s"] = 1
    env = make_env(tmp_path, prefs, FA_DISPATCH_WALL_S="1")
    stub = Stub(monkeypatch, {("p1", "m1"): [("sleep", 1.5, "timed out after 900s (this attempt's budget)")],
                              ("p2", "m2"): [("fail", "boom")]})
    rd = tmp_path / "run2"
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = fat.run_background({"prompt": "x"}, env, str(rd))
    assert rc == 6, (rc, out.getvalue())
    line = json.loads([ln for ln in out.getvalue().splitlines() if ln.strip()][0])
    assert line["status"] == "decision_needed"
    assert f"--resume {rd} --extend" in line["resume"], line.get("resume")
    events = [json.loads(ln) for ln in (rd / "events.jsonl").read_text().splitlines()]
    assert [e["id"] for e in events] == ["A"], "the wall cap stopped the run with A@p2 and B untried"

    # resume continues from the first untried attempt, in the same deterministic order
    stub.script[("p4", "m4")] = [("ok", "resumed answer")]
    out2 = io.StringIO()
    with contextlib.redirect_stdout(out2):
        rc2 = fat.run_background(None, env, str(rd), resume=True, extend="30m")
    assert rc2 == 0, out2.getvalue()
    events2 = [json.loads(ln) for ln in (rd / "events.jsonl").read_text().splitlines()]
    assert [e["id"] for e in events2] == ["A", "A@p2", "B"], "resume appends, continues in plan order"
    result2 = json.loads((rd / "result.json").read_text())
    assert result2["status"] == "ok" and result2["content"] == "resumed answer"


def test_order_is_deterministic(monkeypatch, tmp_path):
    seqs = []
    for _ in range(2):
        env = make_env(tmp_path, base_prefs())
        stub = Stub(monkeypatch, {("p1", "m1"): [("fail", "x")],
                                  ("p2", "m2"): [("fail", "x")],
                                  ("p4", "m4"): [("fail", "x")]})
        rc, _out, _err = run_capture({"prompt": "x"}, env)
        assert rc == 1
        seqs.append(stub.ids())
    assert seqs[0] == seqs[1] == ["A", "A@p2", "B"], "same scripted failures -> identical attempt sequence"


def test_parse_args_background_modes_keep_the_3_tuple_contract():
    # existing tests pin parse_args(...) == (None, "run", None) etc.; the new modes ride on
    # attributes of the same 3-tuple so those assertions keep passing
    r = fat.parse_args([])
    assert r == (None, "run", None) and r.run_dir is None and r.extend is None
    rb = fat.parse_args(["--background", "/tmp/run-x"])
    assert rb[1] == "background" and rb[2] is None and rb.run_dir == "/tmp/run-x"
    rr = fat.parse_args(["--resume", "/tmp/run-x", "--extend", "30m"])
    assert rr[1] == "resume" and rr.run_dir == "/tmp/run-x" and rr.extend == "30m"
    assert fat.parse_args(["--background"])[2] is not None          # missing value -> usage error
    assert fat.parse_args(["--extend", "30m"])[2] is not None       # --extend without --resume
    assert fat.parse_args(["--resume", "/tmp/r", "--list"])[2] is not None  # no combining with --list


def _prefs_nv_gg():
    prefs = base_prefs()
    prefs["order"] = [
        {"id": "NV", "kind": "remote", "provider": "nvidia", "model": "m-nv",
         "display": "NV", "smoke_tested": True, "family": "f1", "roles": ["validator"]},
        {"id": "GG", "kind": "remote", "provider": "gemini", "model": "m-gg",
         "display": "GG", "smoke_tested": True, "family": "f2", "roles": ["validator"]},
    ]
    return prefs


def test_tier_snappy_shortens_timeouts_and_env_still_wins(monkeypatch, tmp_path):
    env = make_env(tmp_path, base_prefs(), FA_DISPATCH_TIMEOUT_TIER="snappy")
    stub = Stub(monkeypatch, {("p1", "m1"): [("ok", "ok")]})
    rc, _out, _err = run_capture({"prompt": "x"}, env)
    assert rc == 0
    assert stub.calls[0]["timeout_s"] == 120 and stub.calls[0]["stall_s"] == 60, \
        "snappy tier: attempt 120s / stall 60s"

    env2 = make_env(tmp_path, base_prefs(), FA_DISPATCH_TIMEOUT_TIER="snappy",
                    FA_DISPATCH_ATTEMPT_TIMEOUT_S="42")
    stub2 = Stub(monkeypatch, {("p1", "m1"): [("ok", "ok")]})
    rc2, _out2, _ = run_capture({"prompt": "x"}, env2)
    assert rc2 == 0
    assert stub2.calls[0]["timeout_s"] == 42, "the explicit FA_DISPATCH_* env wins over the tier"


def test_invalid_tier_aborts_naming_var(monkeypatch, tmp_path):
    env = make_env(tmp_path, base_prefs(), FA_DISPATCH_TIMEOUT_TIER="turbo")
    Stub(monkeypatch, {("p1", "m1"): [("ok", "never")]})
    rc, _out, err = run_capture({"prompt": "x"}, env)
    assert rc == 2
    assert "FA_DISPATCH_TIMEOUT_TIER" in err


def test_rate_limited_lane_gets_patient_timeout_only_under_detected_load(monkeypatch, tmp_path):
    state = tmp_path / "throttle.json"
    state.write_text(json.dumps({"cooldown_until": time.time() + 600, "consecutive_429s": 1}))
    env = make_env(tmp_path, _prefs_nv_gg(), providers=("nvidia", "gemini"),
                   LA_NVIDIA_THROTTLE_STATE=str(state))
    stub = Stub(monkeypatch, {("nvidia", "m-nv"): [("fail", "429")], ("gemini", "m-gg"): [("ok", "gg")]})
    rc, _out, _ = run_capture({"prompt": "x"}, env)
    assert rc == 0
    nv, gg = stub.calls
    assert (nv["provider"], nv["timeout_s"], nv["stall_s"]) == ("nvidia", 1800, 600), \
        "nvidia attempt auto-gets the patient timeout while the cooldown is active"
    assert (gg["provider"], gg["timeout_s"], gg["stall_s"]) == ("gemini", 900, 300), \
        "non-rate-limited provider keeps the tier's own tighter values (no rate limit to wait out)"

    # control: cooldown expired -> the nvidia attempt gets the balanced value too
    state.write_text(json.dumps({"cooldown_until": time.time() - 10, "consecutive_429s": 0}))
    env2 = make_env(tmp_path, _prefs_nv_gg(), providers=("nvidia", "gemini"),
                    LA_NVIDIA_THROTTLE_STATE=str(state))
    stub2 = Stub(monkeypatch, {("nvidia", "m-nv"): [("ok", "ok")]})
    rc2, _out2, _ = run_capture({"prompt": "x"}, env2)
    assert rc2 == 0
    assert stub2.calls[0]["timeout_s"] == 900 and stub2.calls[0]["stall_s"] == 300

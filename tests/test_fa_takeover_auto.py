"""Tests for `fa-takeover auto` — the deterministic auto successor (R4 plan Task 3, NEW file).

D3/D6: auto launches in a real window (sink-recorded in tests), plan mode dropped for the
AUTO path only; D9: first prompt leads with the source topic (generic <username>-xy titles
never surfaced, no -n at launch); D10/D11: launch.json records requested_route vs
actual_model from the successor transcript's identity marker. One successor per source
session (O_EXCL claim). Opt-out via csl setting or FA_AUTO_TAKEOVER=0.

Run: pytest tests/test_fa_takeover_auto.py -v
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))
FIX = REPO / "tests" / "fixtures" / "transcripts"


def _load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ft = _load("fa_takeover", REPO / "bin" / "fa-takeover")
import fa_capture  # noqa: E402


def make_package(root_dir: Path, *, mode="reconcile", transcript=None):
    """Build + write one package directly (no hook), return (pkg, root)."""
    root = root_dir / "takeovers"
    route = fa_capture.resolve_takeover_route(None)  # first live alias of the order
    tr = str(transcript or FIX / "budget_wall.jsonl")
    pkg = fa_capture.build_package(
        tr, cwd="/tmp", dest_route=route.alias, dest_window_tokens=route.window_tokens,
        base_overhead_tokens=fa_capture.DEFAULT_BASE_OVERHEAD_TOKENS,
        reason="budget wall test", scanner=None, mode=mode)
    fa_capture.write_package(pkg, root)
    return pkg, root


def auto_env(tmp_path, monkeypatch, sink, *, auto: "str | None" = "1", config_dir=None, launcher=None):
    empty_lef = tmp_path / "last-effort-empty.json"
    empty_lef.write_text("{}")
    env = {"HOME": str(tmp_path),
           "FA_TAKEOVER_DIR": str(tmp_path / "takeovers"),
           "FA_NOTIFY_SINK": str(sink),
           "FA_TAKEOVER_DETACHED_VERIFY": "0",
           "FA_TAKEOVER_LAUNCHER": launcher or str(tmp_path / "stub-launcher"),
           "FA_SECRET_SCANNER": str(REPO / "tests" / "fixtures" / "stub_redact_secret.py"),
           # hermetic: never read the worktree's real config/remote-last-effort.local.json
           "LA_LAST_EFFORT_FILE": str(empty_lef)}
    if auto is None:
        monkeypatch.delenv("FA_AUTO_TAKEOVER", raising=False)
    else:
        env["FA_AUTO_TAKEOVER"] = auto
    if config_dir is not None:
        env["FREE_AGENTS_CONFIG_DIR"] = str(config_dir)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    stub = tmp_path / "stub-launcher"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(0o755)


def window_records(sink: Path):
    if not sink.exists():
        return []
    return [json.loads(ln) for ln in sink.read_text().splitlines() if ln.strip()]


def win_argv(sink: Path):
    wins = [r for r in window_records(sink) if r.get("kind") == "window"]
    assert len(wins) == 1, f"expected exactly one window record, got {wins}"
    return wins[0]["argv"]


# --- D3/D6: window open through the sink, no plan mode on the auto path ---------

def test_auto_opens_window_through_sink(tmp_path, monkeypatch):
    pkg, root = make_package(tmp_path)
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink)
    assert ft.main(["auto", pkg.takeover_id]) == 0
    argv = win_argv(sink)
    route = pkg.manifest["dest"]["route"]
    assert argv[1] == route  # argv[0] is the (stub) launcher
    assert "--effort" in argv and "--prompt" in argv
    joined = " ".join(argv)
    assert str(root / pkg.takeover_id / "handoff.md") in joined
    assert not any("--permission-mode" in a or a == "plan" for a in argv), argv
    lj = json.loads((root / pkg.takeover_id / "launch.json").read_text())
    assert lj["route"] == route == lj["requested_route"]
    assert lj["window"] == "terminal" and lj["effort"] == "max"
    assert lj["verify"] == "pending" and lj["actual_model"] == "unverified"


def test_auto_handoff_is_continue_mode(tmp_path):
    pkg_c, _ = make_package(tmp_path / "a", mode="continue")
    h = pkg_c.handoff_md
    assert "continue autonomously" in h
    assert "then wait" not in h
    pkg_m, _ = make_package(tmp_path / "b")  # manual default: reconcile-then-WAIT
    assert "WAIT" in pkg_m.handoff_md


def test_standing_rules_match_skill():
    skill = (REPO / "skills" / "free-session-handover" / "SKILL.md").read_text(encoding="utf-8")
    sec = skill.split("## Standing rules template", 1)[1].split("\n## ", 1)[0]
    # the rules are consecutive lines with wrapped continuations: start a rule on ^N. and
    # join its continuation lines, then whitespace-normalize
    rules, cur = [], None
    for ln in sec.splitlines():
        if re.match(r"^\d+\. ", ln):
            if cur is not None:
                rules.append(" ".join(cur.split()))
            cur = ln
        elif cur is not None and ln.strip():
            cur += " " + ln.strip()
    if cur is not None:
        rules.append(" ".join(cur.split()))
    assert len(rules) == 9
    assert fa_capture.HANDOVER_STANDING_RULES == rules


def test_bad_mode_raises(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="mode"):
        make_package(tmp_path / "c", mode="bogus")


# --- one successor per source session; opt-out; invalid env ----------------------

def test_one_successor_per_source_session(tmp_path, monkeypatch):
    pkg, root = make_package(tmp_path)
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink)
    assert ft.main(["auto", pkg.takeover_id]) == 0
    assert ft.main(["auto", pkg.takeover_id]) == 3  # .launched claim already held
    assert len([r for r in window_records(sink) if r.get("kind") == "window"]) == 1
    assert (root / ".launched" / "sess-bw").exists()


def test_auto_refuses_superseded(tmp_path, monkeypatch, capsys):
    pkg, root = make_package(tmp_path)
    fa_capture.mark_superseded(root, pkg.takeover_id, "tk-newer")
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink)
    assert ft.main(["auto", pkg.takeover_id]) == 3
    assert "tk-newer" in capsys.readouterr().err
    assert window_records(sink) == []


def test_auto_refuses_when_opted_out(tmp_path, monkeypatch, capsys):
    import session_menu_state as sms
    pkg, root = make_package(tmp_path)
    cfg = tmp_path / "cfg"
    assert sms.save_auto_takeover(cfg, False) is None
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink, auto=None, config_dir=cfg)
    assert ft.main(["auto", pkg.takeover_id]) == 3
    err = capsys.readouterr().err
    assert "auto-launch off (csl)" in err and "fa-takeover launch" in err
    assert window_records(sink) == []


def test_invalid_env_aborts_loudly(tmp_path, monkeypatch, capsys):
    pkg, _root = make_package(tmp_path)
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink, auto="yes")
    assert ft.main(["auto", pkg.takeover_id]) == 3
    assert "FA_AUTO_TAKEOVER" in capsys.readouterr().err
    assert window_records(sink) == []


# --- effort resolution: last-launch for the route if recorded, else max -----------

def test_effort_resolution(tmp_path, monkeypatch):
    pkg, root = make_package(tmp_path)
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink)
    assert ft.main(["auto", pkg.takeover_id]) == 0
    lj = json.loads((root / pkg.takeover_id / "launch.json").read_text())
    assert lj["effort"] == "max"  # nothing recorded -> max
    # a recorded last-launch effort for the route wins over max (existing launcher behaviour)
    pkg2, root2 = make_package(tmp_path / "b")
    lef = tmp_path / "last-effort.json"
    lef.write_text(json.dumps({pkg2.manifest["dest"]["route"]: "high"}))
    monkeypatch.setenv("LA_LAST_EFFORT_FILE", str(lef))
    monkeypatch.setenv("FA_TAKEOVER_DIR", str(root2))
    assert ft.main(["auto", pkg2.takeover_id]) == 0
    lj2 = json.loads((root2 / pkg2.takeover_id / "launch.json").read_text())
    assert lj2["effort"] == "high"


# --- the hook notice after an auto launch (D3 end-to-end through the worker) ------

def _hook_env(tmp_path, sink, *, auto="1"):
    return {"HOME": str(tmp_path),
            "FA_TAKEOVER_DIR": str(tmp_path / "takeovers"),
            "ANTHROPIC_BASE_URL": "https://llmgw.example/v1",
            "FA_SECRET_SCANNER": str(REPO / "tests" / "fixtures" / "stub_redact_secret.py"),
            "FA_NOTIFY_SINK": str(sink),
            "FA_TAKEOVER_DETACHED_VERIFY": "0",
            "FA_TAKEOVER_LAUNCHER": str(tmp_path / "stub-launcher"),
            "FA_AUTO_TAKEOVER": auto}


def test_auto_notice_line_through_hook(tmp_path, monkeypatch):
    bwc = _load("budget_wall_capture", REPO / "bin" / "budget-wall-capture.py")
    import io
    sink = tmp_path / "sink.jsonl"
    pl = {"hook_event_name": "StopFailure", "session_id": "sess-bw",
          "transcript_path": str(FIX / "budget_wall.jsonl"),
          "last_assistant_message": "API Error: 422 Budget has been exceeded! Key=Test-Key",
          "cwd": "/tmp"}
    for k, v in _hook_env(tmp_path, sink).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(pl)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert bwc.main([]) == 0
    (line,) = json.loads(out.getvalue())["systemMessage"].splitlines()
    root = tmp_path / "takeovers"
    (pkg_dir,) = list(root.glob("tk-*"))
    route = fa_capture.resolve_takeover_route(None).alias
    assert line == (bwc.NOTICE_PREFIX
                    + f"successor launched ({route}) for session sess-bw. "
                      f"Verify: fa-takeover verify {pkg_dir.name}")
    assert len([r for r in window_records(sink) if r.get("kind") == "window"]) == 1
    # D6: the AUTO-launched package is built in continue mode on disk (the successor must
    # not be told to WAIT by its own handoff)
    handoff = (pkg_dir / "handoff.md").read_text()
    assert "continue autonomously" in handoff and "then wait" not in handoff


# --- D9: the first prompt leads with the source topic ------------------------------

def test_first_prompt_leads_with_source_topic(tmp_path, monkeypatch):
    pkg, root = make_package(tmp_path, transcript=FIX / "budget_wall_titled.jsonl")
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink)
    assert ft.main(["auto", pkg.takeover_id]) == 0
    argv = win_argv(sink)
    prompt = argv[argv.index("--prompt") + 1]
    assert prompt.startswith('Continue "Fix parser timeout" — ')
    assert str(root / pkg.takeover_id / "handoff.md") in prompt
    # no recoverable title -> the `session <8>` fallback, rendered without quotes
    pkg2, _r2 = make_package(tmp_path / "b", transcript=FIX / "budget_wall.jsonl")
    sink2 = tmp_path / "sink2.jsonl"
    auto_env(tmp_path, monkeypatch, sink2)
    monkeypatch.setenv("FA_TAKEOVER_DIR", str(tmp_path / "b" / "takeovers"))
    assert ft.main(["auto", pkg2.takeover_id]) == 0
    argv2 = win_argv(sink2)
    prompt2 = argv2[argv2.index("--prompt") + 1]
    assert prompt2.startswith("Continue session sess-bw — ")


def test_generic_username_title_is_not_surfaced(tmp_path, monkeypatch):
    import getpass
    user = getpass.getuser()
    tr = tmp_path / "generic.jsonl"
    tr.write_text(json.dumps({"type": "user", "sessionId": "sess-bw", "customTitle": f"{user}-05",
                              "message": {"role": "user", "content": "keep going"}}) + "\n"
                   + json.dumps({"type": "assistant", "sessionId": "sess-bw", "isApiErrorMessage": True,
                                 "apiErrorStatus": 422,
                                 "message": {"role": "assistant", "content": [{"type": "text",
                                     "text": "API Error: 422 Budget has been exceeded!"}]}}) + "\n")
    pkg, _root = make_package(tmp_path / "g", transcript=tr)
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink)
    monkeypatch.setenv("FA_TAKEOVER_DIR", str(tmp_path / "g" / "takeovers"))
    assert ft.main(["auto", pkg.takeover_id]) == 0
    argv = win_argv(sink)
    prompt = argv[argv.index("--prompt") + 1]
    assert prompt.startswith("Continue session sess-bw — ")
    # the generic title form must not leak into argv (the tmp PATH may contain the username,
    # so assert on the title, not the bare name)
    assert f'"{user}-05"' not in prompt and f"{user}-05" not in prompt.split(" — ")[0]
    # a title that merely CONTAINS the username is kept intact (no partial stripping)
    tr2 = tmp_path / "partial.jsonl"
    tr2.write_text(json.dumps({"type": "user", "sessionId": "sess-bw", "customTitle": f"Fix {user} auth",
                               "message": {"role": "user", "content": "keep going"}}) + "\n"
                   + json.dumps({"type": "assistant", "sessionId": "sess-bw", "isApiErrorMessage": True,
                                 "apiErrorStatus": 422,
                                 "message": {"role": "assistant", "content": [{"type": "text",
                                     "text": "API Error: 422 Budget has been exceeded!"}]}}) + "\n")
    pkg2, _r = make_package(tmp_path / "p", transcript=tr2)
    sink2 = tmp_path / "sink2.jsonl"
    auto_env(tmp_path, monkeypatch, sink2)
    monkeypatch.setenv("FA_TAKEOVER_DIR", str(tmp_path / "p" / "takeovers"))
    assert ft.main(["auto", pkg2.takeover_id]) == 0
    argv2 = win_argv(sink2)
    prompt2 = argv2[argv2.index("--prompt") + 1]
    assert f'Continue "Fix {user} auth" — ' == prompt2[: len(f'Continue "Fix {user} auth" — ')]
    # exact-match boundary: "<user>-05 notes" is NOT a whole-string match -> kept intact
    tr3 = tmp_path / "boundary.jsonl"
    tr3.write_text(json.dumps({"type": "user", "sessionId": "sess-bw", "customTitle": f"{user}-05 notes",
                               "message": {"role": "user", "content": "keep going"}}) + "\n"
                   + json.dumps({"type": "assistant", "sessionId": "sess-bw", "isApiErrorMessage": True,
                                 "apiErrorStatus": 422,
                                 "message": {"role": "assistant", "content": [{"type": "text",
                                     "text": "API Error: 422 Budget has been exceeded!"}]}}) + "\n")
    pkg3, _r3 = make_package(tmp_path / "bd", transcript=tr3)
    sink3 = tmp_path / "sink3.jsonl"
    auto_env(tmp_path, monkeypatch, sink3)
    monkeypatch.setenv("FA_TAKEOVER_DIR", str(tmp_path / "bd" / "takeovers"))
    assert ft.main(["auto", pkg3.takeover_id]) == 0
    argv3 = win_argv(sink3)
    prompt3 = argv3[argv3.index("--prompt") + 1]
    assert prompt3.startswith(f'Continue "{user}-05 notes" — ')


# --- D9/D10: no launch-time name; session-title-prefix untouched --------------------

def test_no_launch_time_name(tmp_path, monkeypatch):
    pkg, _root = make_package(tmp_path)
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink)
    assert ft.main(["auto", pkg.takeover_id]) == 0
    argv = win_argv(sink)
    assert not any(a in ("-n", "--name") for a in argv)
    r = subprocess.run(["git", "-C", str(REPO), "diff", "--quiet", "origin/main", "--",
                        "hooks/session-title-prefix.py"], capture_output=True)
    assert r.returncode == 0, "hooks/session-title-prefix.py must stay byte-identical to origin/main"


# --- D11: launch.json records the ACTUAL model from the successor's marker ----------

def _write_successor(tmp_path, handoff: Path, nonce: str, *, marker_model=None):
    succ_dir = tmp_path / ".claude" / "projects" / "x"
    succ_dir.mkdir(parents=True, exist_ok=True)
    recs = [{"type": "user", "sessionId": "succ",
             "message": {"role": "user", "content": f"read {handoff} first and follow it"}}]
    if marker_model:
        recs.append({"type": "system", "sessionId": "succ",
                     "message": {"role": "system", "content":
                                 'FREE_AGENTS_SESSION_IDENTITY_V1|{"session_kind":"remote_free","model":"%s"}' % marker_model}})
    recs += [
        {"type": "assistant", "sessionId": "succ",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": str(handoff)}}]}},
        {"type": "user", "sessionId": "succ",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t1", "content": f"TAKEOVER-NONCE: {nonce}"}]}},
    ]
    (succ_dir / "succ.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n")


def test_launch_json_records_actual_model(tmp_path, monkeypatch, capsys):
    pkg, root = make_package(tmp_path)
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink)
    assert ft.main(["auto", pkg.takeover_id]) == 0
    handoff = root / pkg.takeover_id / "handoff.md"
    nonce = next(ln.split(": ", 1)[1] for ln in handoff.read_text().splitlines()
                 if ln.startswith("TAKEOVER-NONCE: "))
    _write_successor(tmp_path, handoff, nonce, marker_model="fake-model-x")
    assert ft.main(["verify", pkg.takeover_id, "--timeout", "5"]) == 0
    lj = json.loads((root / pkg.takeover_id / "launch.json").read_text())
    assert lj["requested_route"] == pkg.manifest["dest"]["route"]
    assert lj["actual_model"] == "fake-model-x"  # from the marker, never copied from the route
    assert lj["verify"] == "DELIVERED+READ"
    assert lj["successor_session_id"] == "succ"
    capsys.readouterr()
    assert ft.main(["show", pkg.takeover_id]) == 0
    shown = capsys.readouterr().out
    assert "fake-model-x" in shown and pkg.manifest["dest"]["route"] in shown


def test_launch_json_unverified_without_marker(tmp_path, monkeypatch):
    pkg, root = make_package(tmp_path)
    sink = tmp_path / "sink.jsonl"
    auto_env(tmp_path, monkeypatch, sink)
    assert ft.main(["auto", pkg.takeover_id]) == 0
    handoff = root / pkg.takeover_id / "handoff.md"
    nonce = next(ln.split(": ", 1)[1] for ln in handoff.read_text().splitlines()
                 if ln.startswith("TAKEOVER-NONCE: "))
    _write_successor(tmp_path, handoff, nonce, marker_model=None)
    assert ft.main(["verify", pkg.takeover_id, "--timeout", "5"]) == 0
    lj = json.loads((root / pkg.takeover_id / "launch.json").read_text())
    assert lj["actual_model"] == "unverified"
    assert lj["verify"] == "DELIVERED+READ"


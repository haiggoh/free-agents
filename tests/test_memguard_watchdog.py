import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

script_path = Path(__file__).resolve().parents[1] / "bin" / "la-memguard.py"
spec = importlib.util.spec_from_file_location("la_memguard", script_path)
memguard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memguard)


def test_ok_to_soft_transition():
    state = {"level": "ok", "last_compact_at": {}, "pending": {}, "inflight": None}
    reading = (86.0, 100.0, 86)
    sessions = [{"id": "s1", "idle": True, "idle_since": 100.0}]
    footprints = {"s1": 5.0}
    now = 1000.0
    cfg = {"soft_pct": 85, "hard_pct": 93, "cooldown_s": 300}

    actions, new_state = memguard.decide(reading, sessions, footprints, state, now, cfg)

    assert len(actions) == 2
    assert actions[0] == {"kind": "level", "from": "ok", "to": "soft", "pct": 86}
    assert actions[1]["kind"] == "compact"
    assert actions[1]["session"] == "s1"
    assert actions[1]["level"] == "soft"
    assert new_state["level"] == "soft"
    assert new_state["last_compact_at"]["s1"] == 1000.0
    assert new_state["inflight"] == {"id": "s1", "at": 1000.0}


def test_two_idle_sessions_largest_footprint_first():
    state1 = {"level": "soft", "last_compact_at": {}, "pending": {}, "inflight": None}
    reading = (86.0, 100.0, 86)
    sessions = [
        {"id": "s1", "idle": True, "idle_since": 100.0},
        {"id": "s2", "idle": True, "idle_since": 100.0},
    ]
    footprints = {"s1": 2.0, "s2": 10.0}
    now1 = 1000.0
    cfg = {"soft_pct": 85, "hard_pct": 93, "cooldown_s": 300}

    # Tick 1: s2 is largest, so s2 should be chosen
    actions1, state2 = memguard.decide(reading, sessions, footprints, state1, now1, cfg)
    compact_actions1 = [a for a in actions1 if a["kind"] == "compact"]
    assert len(compact_actions1) == 1
    assert compact_actions1[0]["session"] == "s2"

    # Tick 2: s2 is now in inflight and cooldown, so s1 should be chosen
    now2 = 1005.0
    actions2, state3 = memguard.decide(reading, sessions, footprints, state2, now2, cfg)
    compact_actions2 = [a for a in actions2 if a["kind"] == "compact"]
    assert len(compact_actions2) == 1
    assert compact_actions2[0]["session"] == "s1"


def test_busy_session_compact_after_turn():
    state1 = {"level": "ok", "last_compact_at": {}, "pending": {}, "inflight": None}
    reading = (86.0, 100.0, 86)
    busy_session = [{"id": "s1", "idle": False}]
    footprints = {"s1": 5.0}
    cfg = {"soft_pct": 85, "hard_pct": 93, "cooldown_s": 300}

    # Tick 1: soft with busy session -> compact-after-turn emitted once
    actions1, state2 = memguard.decide(reading, busy_session, footprints, state1, 1000.0, cfg)
    assert any(a["kind"] == "compact-after-turn" for a in actions1)
    assert not any(a["kind"] == "compact" for a in actions1)
    assert "s1" in state2["pending"]

    # Tick 2: still busy -> no duplicate compact-after-turn or compact
    actions2, state3 = memguard.decide(reading, busy_session, footprints, state2, 1001.0, cfg)
    assert not any(a["kind"] == "compact-after-turn" for a in actions2)
    assert not any(a["kind"] == "compact" for a in actions2)

    # Tick 3: session turns idle -> compacted and removed from pending
    idle_session = [{"id": "s1", "idle": True, "idle_since": 1002.0}]
    actions3, state4 = memguard.decide(reading, idle_session, footprints, state3, 1002.0, cfg)
    assert any(a["kind"] == "compact" and a["session"] == "s1" for a in actions3)
    assert "s1" not in state4["pending"]


def test_cooldown_prevents_recompact():
    state1 = {"level": "soft", "last_compact_at": {"s1": 1000.0}, "pending": {}, "inflight": None}
    reading = (86.0, 100.0, 86)
    sessions = [{"id": "s1", "idle": True, "idle_since": 100.0}]
    footprints = {"s1": 5.0}
    cfg = {"soft_pct": 85, "hard_pct": 93, "cooldown_s": 300}

    # At t=1100 (within 300s cooldown), should NOT compact
    actions, state2 = memguard.decide(reading, sessions, footprints, state1, 1100.0, cfg)
    assert not any(a["kind"] == "compact" for a in actions)


def test_hard_transition_notify_once():
    state1 = {"level": "ok", "last_compact_at": {}, "pending": {}, "inflight": None}
    reading = (95.0, 100.0, 95)
    busy_sessions = [{"id": "s1", "idle": False}]
    footprints = {"s1": 5.0}
    cfg = {"soft_pct": 85, "hard_pct": 93, "cooldown_s": 300}

    # Tick 1: Transition into hard with no idle candidates -> notify action emitted once
    actions1, state2 = memguard.decide(reading, busy_sessions, footprints, state1, 1000.0, cfg)
    notify_actions1 = [a for a in actions1 if a["kind"] == "notify"]
    assert len(notify_actions1) == 1
    assert notify_actions1[0]["level"] == "hard"

    # Tick 2: Still in hard with no idle candidates -> no notify action
    actions2, state3 = memguard.decide(reading, busy_sessions, footprints, state2, 1001.0, cfg)
    notify_actions2 = [a for a in actions2 if a["kind"] == "notify"]
    assert len(notify_actions2) == 0


def test_reading_none_no_actions():
    state1 = {"level": "soft", "last_compact_at": {}, "pending": {}, "inflight": None}
    sessions = [{"id": "s1", "idle": True}]
    footprints = {"s1": 5.0}
    cfg = {"soft_pct": 85, "hard_pct": 93, "cooldown_s": 300}

    actions, new_state = memguard.decide(None, sessions, footprints, state1, 1000.0, cfg)
    assert actions == []
    assert new_state == state1


def test_threshold_parsing(monkeypatch):
    monkeypatch.setenv("LA_MEMGUARD_SOFT_PCT", "70")
    monkeypatch.setenv("LA_MEMGUARD_HARD_PCT", "80")

    cfg = memguard.load_cfg()
    assert cfg["soft_pct"] == 70.0
    assert cfg["hard_pct"] == 80.0


def test_segment_parsing(tmp_path, monkeypatch):
    segment_script = tmp_path / "fake-segment.sh"
    segment_script.write_text(
        '#!/bin/sh\necho \'{"label":"ram","text":"97.1/103.9G 93%","level":"crit"}\'\n'
    )
    segment_script.chmod(0o755)

    monkeypatch.setenv("LA_MEMGUARD_SEGMENT", str(segment_script))
    res = memguard.read_ram()
    assert res == (97.1, 103.9, 93)

    segment_script.write_text("#!/bin/sh\necho ''\n")
    res_empty = memguard.read_ram()
    assert res_empty is None


def test_run_once_end_to_end(tmp_path):
    state_dir = tmp_path / "state"
    sessions_dir = state_dir / "sessions"
    sessions_dir.mkdir(parents=True)

    session_file = sessions_dir / "test-sess.json"
    session_data = {
        "id": "test-sess",
        "backend": "rapid",
        "port": "8123",
        "model": "qwen",
        "launcher_pid": os.getpid(),
        "tmux_socket": "/path.sock",
        "tmux_target": "fa-test-sess",
        "idle": True,
        "idle_since": 100.0,
    }
    session_file.write_text(json.dumps(session_data))

    fake_segment = tmp_path / "segment.sh"
    fake_segment.write_text(
        '#!/bin/sh\necho \'{"label":"ram","text":"90.0/100.0G 90%","level":"warn"}\'\n'
    )
    fake_segment.chmod(0o755)

    fake_footprint = tmp_path / "footprint.sh"
    fake_footprint.write_text('#!/bin/sh\necho "5.0"\n')
    fake_footprint.chmod(0o755)

    inj_out = tmp_path / "injector.out"
    fake_injector = tmp_path / "injector.sh"
    fake_injector.write_text(f'#!/bin/sh\necho "$@" >> "{inj_out}"\n')
    fake_injector.chmod(0o755)

    env = os.environ.copy()
    env["LA_MEMGUARD_STATE_DIR"] = str(state_dir)
    env["LA_MEMGUARD_SEGMENT"] = str(fake_segment)
    env["LA_MEMGUARD_FOOTPRINT_CMD"] = str(fake_footprint)
    env["LA_MEMGUARD_INJECT_CMD"] = str(fake_injector)
    env["LA_MEMGUARD_NOTIFY"] = "0"
    env["LA_MEMGUARD_SOFT_PCT"] = "85"

    res = subprocess.run(
        [sys.executable, str(script_path), "run", "--once"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0

    assert inj_out.exists()
    inj_text = inj_out.read_text()
    assert "test-sess /compact OOM protection: RAM at 90% of the 100 GB ceiling" in inj_text

    log_file = state_dir / "watchdog.log"
    assert log_file.exists()
    log_lines = log_file.read_text().splitlines()
    actions = [json.loads(line) for line in log_lines if line.strip()]
    assert any(a.get("kind") == "compact" and a.get("session") == "test-sess" for a in actions)


def _abort_tick(tmp_path, auto_continue):
    """One `run --once` tick on an idle session whose last turn hit the backend memory limit."""
    state_dir = tmp_path / "state"
    (state_dir / "sessions").mkdir(parents=True)
    (state_dir / "sessions" / "s1.json").write_text(json.dumps({
        "id": "s1", "backend": "omlx", "port": "8123", "model": "m", "launcher_pid": os.getpid(),
        "tmux_socket": "/x.sock", "tmux_target": "t", "idle": True, "memory_abort_at": 100.0}))
    seg = tmp_path / "seg.sh"
    seg.write_text('#!/bin/sh\necho \'{"label":"ram","text":"10.0/100.0G 10%","level":"ok"}\'\n')
    seg.chmod(0o755)
    out = tmp_path / "inj.out"
    inj = tmp_path / "inj.sh"
    inj.write_text(f'#!/bin/sh\necho "$@" >> "{out}"\n')
    inj.chmod(0o755)
    env = dict(os.environ, LA_MEMGUARD_STATE_DIR=str(state_dir), LA_MEMGUARD_SEGMENT=str(seg),
               LA_MEMGUARD_INJECT_CMD=str(inj), LA_MEMGUARD_NOTIFY="0",
               LA_MEMGUARD_FOOTPRINT_CMD="true", LA_MEMGUARD_AUTO_CONTINUE=auto_continue)
    subprocess.run([sys.executable, str(script_path), "run", "--once"], env=env, timeout=10, check=True)
    state = json.loads((state_dir / "watchdog.json").read_text())
    return out.read_text(), state


def test_run_honours_auto_continue_end_to_end(tmp_path):
    # The real `run` loop must hand LA_MEMGUARD_AUTO_CONTINUE to the abort logic: with it OFF the
    # session is compacted but never queued for the automatic `continue`.
    on_out, on_state = _abort_tick(tmp_path / "on", "1")
    off_out, off_state = _abort_tick(tmp_path / "off", "0")
    assert "s1 /compact" in on_out and "s1 /compact" in off_out
    assert "s1" in on_state.get("await_continue", {})
    assert "s1" not in off_state.get("await_continue", {})
    assert "continuing automatically" in on_out and "continuing automatically" not in off_out


def test_run_empty_registry(tmp_path):
    state_dir = tmp_path / "empty_state"
    env = os.environ.copy()
    env["LA_MEMGUARD_STATE_DIR"] = str(state_dir)

    res = subprocess.run(
        [sys.executable, str(script_path), "run"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert res.returncode == 0


def test_help_and_unknown_subcommand(tmp_path):
    state_dir = tmp_path / "state"
    env = os.environ.copy()
    env["LA_MEMGUARD_STATE_DIR"] = str(state_dir)

    res_help = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_help.returncode == 0
    assert "run" in res_help.stdout
    assert "ensure" in res_help.stdout
    assert "status" in res_help.stdout
    assert not state_dir.exists()

    res_unknown = subprocess.run(
        [sys.executable, str(script_path), "unknown-cmd"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_unknown.returncode == 2


def test_phys_footprint_parsing_not_rss():
    """The default footprint reads macOS `footprint`, whose phys_footprint counts Metal memory."""
    out = ("Python [11406]: 64-bit    Footprint: 53 GB (16384 bytes per page)\n"
           "  53 GB      77 MB       146 MB       6536    TOTAL\n"
           "    phys_footprint: 53 GB\n    phys_footprint_peak: 74 GB\n")
    assert memguard.parse_phys_footprint(out) == 53.0
    assert memguard.parse_phys_footprint("    phys_footprint: 512 MB\n") == 0.5
    assert memguard.parse_phys_footprint("") == 0.0


# ---- aborted turn -> compact -> continue (phase 3) ---------------------------------------------

def _abort_sess(**kw):
    s = {"id": "s1", "idle": True, "memory_abort_at": 100.0}
    s.update(kw)
    return s


def test_abort_compacts_then_continues_after_the_compact():
    acts, st = memguard.decide_abort([_abort_sess()], {}, 101.0)
    assert [a["kind"] for a in acts] == ["compact"] and acts[0]["session"] == "s1"
    acts, st = memguard.decide_abort([_abort_sess()], st, 120.0)
    assert acts == [], "no continue before the compact has finished"
    done = _abort_sess(compacted_at=150.0)
    acts, st = memguard.decide_abort([done], st, 151.0)
    assert acts == [], "grace period lets a queued user prompt go first"
    acts, st = memguard.decide_abort([done], st, 160.0)
    assert [a["kind"] for a in acts] == ["continue"]
    acts, st = memguard.decide_abort([done], st, 170.0)
    assert acts == [], "one abort, one compact, one continue"


def test_auto_continue_off_compacts_but_never_types_continue(monkeypatch):
    acts, st = memguard.decide_abort([_abort_sess()], {}, 101.0, auto_continue=False)
    assert [a["kind"] for a in acts] == ["compact"]
    assert "continuing automatically" not in acts[0]["reason"]
    done = _abort_sess(compacted_at=150.0)
    for t in (160.0, 200.0, 400.0):
        acts, st = memguard.decide_abort([done], st, t, auto_continue=False)
        assert acts == [], "no continue when the user turned it off"
    monkeypatch.setenv("LA_MEMGUARD_AUTO_CONTINUE", "0")
    assert memguard.load_cfg()["auto_continue"] is False
    monkeypatch.delenv("LA_MEMGUARD_AUTO_CONTINUE")
    assert memguard.load_cfg()["auto_continue"] is True


def test_user_prompt_after_the_compact_skips_continue():
    _, st = memguard.decide_abort([_abort_sess()], {}, 101.0)
    s = _abort_sess(compacted_at=150.0, last_prompt_at=152.0, idle=False)
    acts, st = memguard.decide_abort([s], st, 160.0)
    assert [a["kind"] for a in acts] == ["skip-continue"]
    acts, _ = memguard.decide_abort([_abort_sess(compacted_at=150.0, last_prompt_at=152.0)], st, 200.0)
    assert acts == []


def test_no_abort_no_action_and_busy_abort_waits():
    assert memguard.decide_abort([{"id": "s1", "idle": True}], {}, 10.0)[0] == []
    acts, st = memguard.decide_abort([_abort_sess(idle=False)], {}, 101.0)
    assert acts == [], "never type into a running turn"
    acts, _ = memguard.decide_abort([_abort_sess()], st, 102.0)
    assert [a["kind"] for a in acts] == ["compact"]


def test_registry_flags_only_memory_aborts(tmp_path, monkeypatch):
    monkeypatch.setenv("LA_MEMGUARD_STATE_DIR", str(tmp_path))
    reg = memguard.load_registry_module()
    sid = reg.register("omlx", "8000", "m", launcher_pid=os.getpid())
    omlx = ("Request aborted: process memory limit exceeded (usage 98.9 GB, abort threshold "
            "(hard watermark) 98.5 GB, ceiling 103.9 GB). Reduce context length.")
    reg.mark(sid, "StopFailure", {"hook_event_name": "StopFailure", "error": "rate_limit",
                                  "error_details": "429 Too Many Requests"})
    assert "memory_abort_at" not in reg.read(sid)
    reg.mark(sid, "StopFailure", {"hook_event_name": "StopFailure", "error": "api_error",
                                  "error_details": omlx})
    assert reg.read(sid)["memory_abort_at"] > 0
    reg.mark(sid, "UserPromptSubmit", {})
    reg.mark(sid, "PostCompact", {"trigger": "auto"})
    assert reg.read(sid)["idle"] is False
    reg.mark(sid, "PostCompact", {"trigger": "manual"})
    rec = reg.read(sid)
    assert rec["idle"] is True and rec["compacted_at"] > 0

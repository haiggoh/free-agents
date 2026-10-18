"""Memory guard Phase 1: the session registry, its hook entry, and the launcher wrapper.

Everything runs against a temp LA_MEMGUARD_STATE_DIR and a stub `claude` on PATH — never the
real state dir, never a real Claude Code.
"""
import json
import os
import pty
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "bin" / "la-memguard-registry.py"
RUN = ROOT / "bin" / "la-memguard-run.sh"
HOOKS = ROOT / "hooks" / "hooks.json"


@pytest.fixture
def env(tmp_path):
    e = dict(os.environ)
    # The wrapper reads saved picker settings: point it at an empty private store, and drop any
    # LA_MEMGUARD_* the developer exported, so the real preferences never decide a test.
    e["FREE_AGENTS_CONFIG_DIR"] = str(tmp_path / "prefs")
    for k in [k for k in e if k.startswith("LA_MEMGUARD") or k == "LA_REGISTRY_AUTO_SYNC"]:
        e.pop(k)
    e["LA_MEMGUARD_STATE_DIR"] = str(tmp_path / "state")
    e.pop("LA_MEMGUARD_SESSION_ID", None)
    e.pop("TMUX", None)
    e.pop("TMUX_PANE", None)
    return e


def reg(env, *args, stdin=None):
    return subprocess.run([sys.executable, str(REG), *args], env=env, input=stdin,
                          capture_output=True, text=True, timeout=20)


def records(env):
    d = Path(env["LA_MEMGUARD_STATE_DIR"]) / "sessions"
    return {f.stem: json.loads(f.read_text()) for f in sorted(d.glob("*.json"))} if d.exists() else {}


def hook(env, sid, event):
    e = dict(env, LA_MEMGUARD_SESSION_ID=sid) if sid else env
    return reg(e, "hook", stdin=json.dumps({"hook_event_name": event, "session_id": "x"}))


def test_register_writes_record_under_state_dir(env):
    r = reg(env, "register", "--backend", "rapid", "--port", "8123", "--model", "qwen", "--launcher-pid", str(os.getpid()))
    assert r.returncode == 0, r.stderr
    sid = r.stdout.strip()
    rec = records(env)[sid]
    assert (rec["backend"], rec["port"], rec["model"], rec["idle"]) == ("rapid", "8123", "qwen", True)
    mode = (Path(env["LA_MEMGUARD_STATE_DIR"]) / "sessions" / f"{sid}.json").stat().st_mode & 0o777
    assert mode == 0o600


def test_hook_events_flip_idle_and_record_prompt_time(env):
    sid = reg(env, "register", "--backend", "omlx", "--port", "8000", "--model", "m",
              "--launcher-pid", str(os.getpid())).stdout.strip()
    assert hook(env, sid, "UserPromptSubmit").returncode == 0
    rec = records(env)[sid]
    assert rec["idle"] is False and "last_prompt_at" in rec
    hook(env, sid, "Stop")
    assert records(env)[sid]["idle"] is True
    hook(env, sid, "UserPromptSubmit")
    hook(env, sid, "StopFailure")
    rec = records(env)[sid]
    assert rec["idle"] is True and rec["last_event"] == "StopFailure"
    hook(env, sid, "SessionEnd")
    assert records(env)[sid]["ended"] is True


def test_hook_without_session_id_is_a_silent_noop(env):
    sid = reg(env, "register", "--backend", "omlx", "--port", "8000", "--model", "m",
              "--launcher-pid", str(os.getpid())).stdout.strip()
    r = hook(env, "", "UserPromptSubmit")
    assert r.returncode == 0 and r.stdout == ""
    assert records(env)[sid]["idle"] is True


def test_hook_never_fails_the_session_on_bad_input(env):
    e = dict(env, LA_MEMGUARD_SESSION_ID="omlx-1-1")
    r = reg(e, "hook", stdin="not json")
    assert r.returncode == 0


def test_two_sessions_two_files_and_dead_launcher_is_pruned(env):
    a = reg(env, "register", "--backend", "rapid", "--port", "8001", "--model", "a",
            "--launcher-pid", str(os.getpid())).stdout.strip()
    dead = subprocess.Popen(["true"]); dead.wait()
    b = reg(env, "register", "--backend", "vllm", "--port", "8002", "--model", "b",
            "--launcher-pid", str(dead.pid)).stdout.strip()
    assert set(records(env)) == {a, b}
    live = json.loads(reg(env, "list").stdout)
    assert [s["id"] for s in live] == [a]
    assert set(records(env)) == {a}


def test_unregister_removes_record(env):
    sid = reg(env, "register", "--backend", "rapid", "--port", "8001", "--model", "a",
              "--launcher-pid", str(os.getpid())).stdout.strip()
    reg(env, "unregister", sid)
    assert records(env) == {}


def test_help_prints_and_bad_usage_exits_2(env):
    r = reg(env, "--help")
    assert r.returncode == 0 and "register" in r.stdout
    assert reg(env, "frobnicate").returncode == 2
    assert reg(env, "register", "--bogus", "x").returncode == 2
    assert records(env) == {}


def test_hooks_json_registers_the_hook_for_all_four_events():
    d = json.loads(HOOKS.read_text())["hooks"]
    for ev in ("UserPromptSubmit", "Stop", "StopFailure", "SessionEnd"):
        cmds = [h["command"] for grp in d[ev] for h in grp["hooks"]]
        assert any("la-memguard-registry.py\" hook" in c for c in cmds), ev


def test_hooks_json_command_reaches_the_registry(env, tmp_path):
    """Run the registered command string itself, so a typo in the path or the env guard fails here."""
    cmd = next(h["command"] for grp in json.loads(HOOKS.read_text())["hooks"]["Stop"]
               for h in grp["hooks"] if "la-memguard" in h["command"])
    sid = reg(env, "register", "--backend", "rapid", "--port", "8001", "--model", "a",
              "--launcher-pid", str(os.getpid())).stdout.strip()
    hook(env, sid, "UserPromptSubmit")
    e = dict(env, CLAUDE_PLUGIN_ROOT=str(ROOT), LA_MEMGUARD_SESSION_ID=sid)
    subprocess.run(["bash", "-c", cmd], env=e, input=json.dumps({"hook_event_name": "Stop"}),
                   text=True, timeout=20, check=True)
    assert records(env)[sid]["idle"] is True


# ---- the launcher wrapper ------------------------------------------------------------------

def stub_claude(tmp_path, rc=7):
    bindir = tmp_path / "stubbin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "claude.log"
    (bindir / "claude").write_text(
        "#!/bin/bash\n"
        f"{{ printf 'ARGS'; printf ' [%s]' \"$@\"; echo; echo \"SID=$LA_MEMGUARD_SESSION_ID\"; "
        f"echo \"TMUX=${{TMUX:+yes}}\"; echo \"PWD=$PWD\"; "
        f"ls \"$LA_MEMGUARD_STATE_DIR/sessions\"; }} >> {log}\n"
        f"exit {rc}\n")
    (bindir / "claude").chmod(0o755)
    return bindir, log


def wrapper_cmd():
    return (f'LAUNCH_DIR={str(ROOT / "bin")!r}; . {str(RUN)!r}; '
            'la_memguard_claude rapid 8123 qwen -- --model "a b" --flag; echo "RC=$?"')


def test_wrapper_direct_mode_registers_passes_args_and_status_then_cleans_up(env, tmp_path):
    bindir, log = stub_claude(tmp_path)
    e = dict(env, PATH=f"{bindir}:{env['PATH']}")
    r = subprocess.run(["bash", "-c", wrapper_cmd()], env=e, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    assert "RC=7" in r.stdout, r.stdout + r.stderr
    out = log.read_text()
    assert "ARGS [--model] [a b] [--flag]" in out
    assert "SID=rapid-8123-" in out and "TMUX=\n" in out
    assert "rapid-8123-" in out.split("SID=")[1]  # its record existed while claude ran
    assert records(env) == {}


def test_wrapper_opt_out_runs_claude_unregistered(env, tmp_path):
    bindir, log = stub_claude(tmp_path, rc=0)
    e = dict(env, PATH=f"{bindir}:{env['PATH']}", LA_MEMGUARD="0")
    r = subprocess.run(["bash", "-c", wrapper_cmd()], env=e, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    assert "RC=0" in r.stdout
    assert "SID=\n" in log.read_text()


def _save_prefs(env, values):
    sms_dir = env["FREE_AGENTS_CONFIG_DIR"]
    code = ("import json,sys; sys.path.insert(0, sys.argv[1]); import session_menu_state as s; "
            "print(s.save_memguard(sys.argv[2], json.loads(sys.argv[3])))")
    subprocess.run([sys.executable, "-c", code, str(ROOT / "bin"), sms_dir, json.dumps(values)],
                   check=True, capture_output=True)


def test_wrapper_honours_saved_guard_off(env, tmp_path):
    # The picker's "Memory guard: OFF" reaches the launcher: claude runs unregistered.
    _save_prefs(env, {"enabled": False})
    bindir, log = stub_claude(tmp_path, rc=0)
    e = dict(env, PATH=f"{bindir}:{env['PATH']}")
    r = subprocess.run(["bash", "-c", wrapper_cmd()], env=e, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    assert "RC=0" in r.stdout, r.stdout + r.stderr
    assert "SID=\n" in log.read_text()


def test_explicit_env_beats_saved_setting_and_settings_reach_the_child(env, tmp_path):
    _save_prefs(env, {"enabled": False, "soft_pct": 80})
    bindir, log = stub_claude(tmp_path, rc=0)
    probe = (f'LAUNCH_DIR={str(ROOT / "bin")!r}; . {str(RUN)!r}; _la_memguard_settings; '
             'echo "G=$LA_MEMGUARD S=$LA_MEMGUARD_SOFT_PCT H=$LA_MEMGUARD_HARD_PCT"')
    r = subprocess.run(["bash", "-c", probe], env=dict(env, LA_MEMGUARD="1"), capture_output=True,
                       text=True, timeout=30)
    assert "G=1 S=80 H=93" in r.stdout, r.stdout + r.stderr     # export wins; saved + defaults fill in
    e = dict(env, PATH=f"{bindir}:{env['PATH']}", LA_MEMGUARD="1")
    subprocess.run(["bash", "-c", wrapper_cmd()], env=e, capture_output=True, text=True,
                   stdin=subprocess.DEVNULL, timeout=30)
    assert "SID=rapid-8123-" in log.read_text()                 # explicit LA_MEMGUARD=1 ran it


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_wrapper_tmux_mode_on_a_real_terminal(env, tmp_path):
    """On a tty the wrapper runs claude INSIDE a private tmux session, records the injection target,
    returns claude's status, and leaves no session, socket or record behind."""
    bindir, log = stub_claude(tmp_path)
    e = dict(env, PATH=f"{bindir}:{env['PATH']}", TERM="xterm-256color")
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(tmp_path)
        os.execvpe("bash", ["bash", "-c", wrapper_cmd()], e)
    buf = b""
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
    _, status = os.waitpid(pid, 0)
    out = log.read_text()
    assert b"RC=7" in buf, buf[-2000:]
    assert "TMUX=yes" in out, out
    assert f"PWD={tmp_path}" in out
    assert "ARGS [--model] [a b] [--flag]" in out
    assert records(env) == {}
    tdir = Path(env["LA_MEMGUARD_STATE_DIR"]) / "tmux"
    assert not list(tdir.glob("*.sock")) and not list(tdir.glob("*.run"))

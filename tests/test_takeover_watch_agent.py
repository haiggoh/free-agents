"""Tests for bin/takeover_watch.py — the launchd WatchPaths agent (R4 plan Task 4, NEW file).

The agent runs the capture worker the moment a spool lands in <root>/.pending/ (D5).
Tests use a temp HOME and a launchctl PATH stub — the real launchctl is never called.

Run: pytest tests/test_takeover_watch_agent.py -v
"""
import importlib.machinery
import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import time
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


try:
    tw = _load("takeover_watch", REPO / "bin" / "takeover_watch.py")
except FileNotFoundError:
    tw = None  # RED until bin/takeover_watch.py exists; plist tests assert tw is not None
bwc = _load("budget_wall_capture", REPO / "bin" / "budget-wall-capture.py")

LABEL = "com.haiggoh.free-agents.takeover-watch"


LAUNCHCTL_STUB = """#!/usr/bin/env bash
# test stub: logs argv; bootstrap/bootout toggle a loaded marker; list prints it
echo "$@" >> "$LAUNCHCTL_STUB_LOG"
case "${1:-}" in
  bootstrap) touch "$LAUNCHCTL_STUB_LOADED" ;;
  bootout) rm -f "$LAUNCHCTL_STUB_LOADED" ;;
  list) [ -f "$LAUNCHCTL_STUB_LOADED" ] && echo "-\\t0\\t%s" ;;
esac
exit 0
""" % LABEL


def watcher_env(tmp_path, monkeypatch, sink=None, extra=None):
    """temp HOME + launchctl stub on PATH + takeover root; returns (env dict, stubdir)."""
    stubdir = tmp_path / "stub-bin"
    stubdir.mkdir()
    (stubdir / "launchctl").write_text(LAUNCHCTL_STUB)
    (stubdir / "launchctl").chmod(0o755)
    env = {"HOME": str(tmp_path),
           "PATH": f"{stubdir}:{os.environ.get('PATH', '')}",
           "LAUNCHCTL_STUB_LOG": str(tmp_path / "launchctl-calls.log"),
           "LAUNCHCTL_STUB_LOADED": str(tmp_path / "loaded.marker"),
           "FA_TAKEOVER_DIR": str(tmp_path / "takeovers"),
           "FA_SECRET_SCANNER": str(REPO / "tests" / "fixtures" / "stub_redact_secret.py"),
           "FA_TAKEOVER_DETACHED_VERIFY": "0"}
    if sink is not None:
        env["FA_NOTIFY_SINK"] = str(sink)
    if extra:
        env.update(extra)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env, stubdir


def stub_calls(tmp_path):
    p = tmp_path / "launchctl-calls.log"
    return p.read_text().splitlines() if p.exists() else []


# --- plist shape ----------------------------------------------------------------

def test_plist_has_no_version_or_checkout_path(tmp_path):
    assert tw is not None, "bin/takeover_watch.py missing"
    root = tmp_path / "takeovers"
    runner = tmp_path / "watch-runner.sh"
    xml = tw.render_plist(root, runner, tmp_path)
    assert "/plugins/cache/" not in xml
    assert "ClaudeWorkspace" not in xml
    assert str(tmp_path) in xml  # the literal home is allowed ONLY inside the generated plist
    assert LABEL in xml


def test_plist_watchpaths_and_throttle(tmp_path):
    assert tw is not None, "bin/takeover_watch.py missing"
    root = tmp_path / "takeovers"
    runner = tmp_path / "watch-runner.sh"
    xml = tw.render_plist(root, runner, tmp_path)
    d = plistlib.loads(xml.encode())
    assert d["Label"] == LABEL
    assert d["ProgramArguments"] == ["/bin/bash", str(runner)]
    assert d["WatchPaths"] == [str(root / ".pending")]
    assert d["ThrottleInterval"] == 10
    assert d["RunAtLoad"] is True
    assert d["StandardOutPath"] == str(root / "watch.log")
    assert d["StandardErrorPath"] == str(root / "watch.log")
    assert "PATH" in d.get("EnvironmentVariables", {})


# --- install / uninstall / dry-run (temp HOME + launchctl stub) -------------------

def test_install_uninstall_idempotent(tmp_path, monkeypatch):
    assert tw is not None, "bin/takeover_watch.py missing"
    watcher_env(tmp_path, monkeypatch)
    home = Path(tmp_path)
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    assert tw.install() == 0
    assert plist.is_file()
    first = plist.read_text()
    assert any("bootstrap" in c for c in stub_calls(tmp_path))
    assert tw.install() == 0          # idempotent: same content, no error
    assert plist.read_text() == first
    st = tw.status()
    assert st["installed"] is True and st["loaded"] is True and st["plist"] == str(plist)
    assert tw.uninstall() == 0
    assert not plist.exists()
    assert any("bootout" in c for c in stub_calls(tmp_path))
    assert tw.uninstall() == 0        # second uninstall: still exit 0
    assert tw.status()["installed"] is False


def test_dry_run_writes_nothing_and_calls_no_launchctl(tmp_path, monkeypatch):
    assert tw is not None, "bin/takeover_watch.py missing"
    watcher_env(tmp_path, monkeypatch)
    assert tw.install(dry_run=True) == 0
    assert not (tmp_path / "Library" / "LaunchAgents" / f"{LABEL}.plist").exists()
    assert stub_calls(tmp_path) == []
    assert tw.uninstall(dry_run=True) == 0
    assert stub_calls(tmp_path) == []


def test_takeover_watch_help():
    assert (REPO / "bin" / "takeover_watch.py").exists(), "bin/takeover_watch.py missing"
    r = subprocess.run([sys.executable, str(REPO / "bin" / "takeover_watch.py"), "--help"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    for word in ("install", "uninstall", "status", "FA_TAKEOVER_DIR", "dry-run"):
        assert word in r.stdout, word
    r2 = subprocess.run([sys.executable, str(REPO / "bin" / "takeover_watch.py"), "--bogus"],
                        capture_output=True, text=True, timeout=30)
    assert r2.returncode == 2


# --- the worker under --from-watcher ----------------------------------------------

BUDGET_TEXT = ("API Error: 422 Budget has been exceeded! Key=Test-Key (sk-...test) "
               "Current cost: 40.00000000000001, Max budget: 40.0")


def _payload(sid="sess-bw"):
    return {"hook_event_name": "StopFailure", "session_id": sid,
            "transcript_path": str(FIX / "budget_wall.jsonl"),
            "last_assistant_message": BUDGET_TEXT, "cwd": "/tmp"}


def _spool(tmp_path, name="1.json", pl=None, header="#fa-endpoint https://llmgw.example/v1"):
    pend = tmp_path / "takeovers" / ".pending"
    pend.mkdir(parents=True, exist_ok=True)
    (pend / name).write_text(header + "\n" + json.dumps(pl or _payload()) + "\n")
    return pend / name


def _run_watcher_subprocess(tmp_path, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LA_", "FA_"))}
    env.update({"HOME": str(tmp_path),
                "FA_TAKEOVER_DIR": str(tmp_path / "takeovers"),
                "FA_SECRET_SCANNER": str(REPO / "tests" / "fixtures" / "stub_redact_secret.py"),
                "FA_TAKEOVER_DETACHED_VERIFY": "0",
                "FA_AUTO_TAKEOVER": "1",
                "FA_NOTIFY_SINK": str(tmp_path / "sink.jsonl"),
                "FA_TAKEOVER_LAUNCHER": str(tmp_path / "stub-launcher"),
                "ANTHROPIC_BASE_URL": "https://llmgw.example/v1"})
    env.update(env_extra or {})
    stub = tmp_path / "stub-launcher"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(0o755)
    return subprocess.run([sys.executable, str(REPO / "bin" / "budget-wall-capture.py"),
                           "--pending", "--from-watcher"],
                          capture_output=True, text=True, env=env, timeout=60)


def _sink_records(tmp_path):
    p = tmp_path / "sink.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def test_fresh_partial_payload_is_left_in_place(tmp_path, monkeypatch):
    """Review Focus 1: WatchPaths fires on CREATE — a mid-write spool file must be retried,
    never unlinked as unparseable; only an ANCIENT unparseable file moves to .failed/."""
    watcher_env(tmp_path, monkeypatch, sink=tmp_path / "sink.jsonl",
                extra={"FA_AUTO_TAKEOVER": "0", "ANTHROPIC_BASE_URL": "https://llmgw.example/v1"})
    f = _spool(tmp_path)
    f.write_text("#fa-endpoint https://llmgw.example/v1\n" + '{"truncated": ')  # partial JSON
    import io
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert bwc.main(["--pending", "--from-watcher"]) == 0
    assert f.exists(), "fresh partial payload must be LEFT in place"
    assert not list((tmp_path / "takeovers" / ".failed").glob("*")) if (tmp_path / "takeovers" / ".failed").exists() else True
    # the same file, 11 minutes old -> parked in .failed/
    old = 11 * 60
    os.utime(f, (time.time() - old, time.time() - old))
    assert bwc.main(["--pending", "--from-watcher"]) == 0
    assert not f.exists()
    assert list((tmp_path / "takeovers" / ".failed").glob("1.json"))


def test_two_workers_race_one_launch(tmp_path):
    """Review Focus 3: SessionStart hook and watcher on the same payload at the same moment
    -> exactly one package, one .launched claim, one window record."""
    _spool(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LA_", "FA_"))}
    env.update({"HOME": str(tmp_path),
                "FA_TAKEOVER_DIR": str(tmp_path / "takeovers"),
                "FA_SECRET_SCANNER": str(REPO / "tests" / "fixtures" / "stub_redact_secret.py"),
                "FA_TAKEOVER_DETACHED_VERIFY": "0",
                "FA_AUTO_TAKEOVER": "1",
                "FA_NOTIFY_SINK": str(tmp_path / "sink.jsonl"),
                "FA_TAKEOVER_LAUNCHER": str(tmp_path / "stub-launcher"),
                "ANTHROPIC_BASE_URL": "https://llmgw.example/v1"})
    stub = tmp_path / "stub-launcher"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(0o755)
    procs = [subprocess.Popen([sys.executable, str(REPO / "bin" / "budget-wall-capture.py"),
                               "--pending", "--from-watcher"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
             for _ in range(2)]
    rcs = [p.wait(timeout=60) for p in procs]
    assert rcs == [0, 0], rcs
    root = tmp_path / "takeovers"
    assert len(list(root.glob("tk-*"))) == 1
    assert len(list((root / ".launched").glob("*"))) == 1
    wins = [r for r in _sink_records(tmp_path) if r.get("kind") == "window"]
    assert len(wins) == 1


def test_watcher_respects_opt_out_each_run(tmp_path, monkeypatch):
    """Review Focus 4: the watcher re-reads the setting on EVERY run — OFF means the payload
    is packaged (manual path) but NO window opens, and the notice says auto-launch is off."""
    import session_menu_state as sms
    cfg = tmp_path / "cfg"
    assert sms.save_auto_takeover(cfg, False) is None
    watcher_env(tmp_path, monkeypatch, sink=tmp_path / "sink.jsonl",
                extra={"ANTHROPIC_BASE_URL": "https://llmgw.example/v1",
                       "LA_SESSION_MENU_CONFIG_DIR": str(cfg)})
    monkeypatch.delenv("FA_AUTO_TAKEOVER", raising=False)
    _spool(tmp_path)
    # FA_AUTO_TAKEOVER="" = unset, so the STORED setting (OFF) is what the watcher reads
    r = _run_watcher_subprocess(tmp_path, env_extra={"LA_SESSION_MENU_CONFIG_DIR": str(cfg),
                                                      "FA_AUTO_TAKEOVER": ""})
    assert r.returncode == 0
    root = tmp_path / "takeovers"
    assert len(list(root.glob("tk-*"))) == 1                      # packaged (manual path)
    assert [x for x in _sink_records(tmp_path) if x.get("kind") == "window"] == []
    notifies = [x for x in _sink_records(tmp_path) if x.get("kind") != "window"]
    assert any("auto-launch off" in x.get("text", "") for x in notifies), notifies


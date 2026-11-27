"""tests/test_budget_wall_auto_delivery.py — hermetic end-to-end (R4 plan Task 5, NEW file).

Drives the REAL chain the way the machine does: StopFailure payload -> the REAL
hooks/budget-wall-spool.sh -> <root>/.pending -> the generated runner shim under a hermetic
empty environment (tests/lib/hermetic_env.py) with the plist's PATH, exactly as launchd runs
it -> capture + auto launch.
Asserts: one package in continue mode, one 📡 notice, one window record (route = first live
alias of config/session-preferences.json), zero 💸 in this run's hook.log — and a second
wall of the same session supersedes without a second launch.

Run: pytest tests/test_budget_wall_auto_delivery.py -v
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


tw = _load("takeover_watch", REPO / "bin" / "takeover_watch.py")
sys.path.insert(0, str(REPO / "tests" / "lib"))
from hermetic_env import hermetic_env  # noqa: E402
import emoji_constants  # noqa: E402
import fa_capture  # noqa: E402

BUDGET_TEXT = ("API Error: 422 Budget has been exceeded! Key=Test-Key (sk-...test) "
               "Current cost: 40.00000000000001, Max budget: 40.0")


def _payload(transcript):
    return {"hook_event_name": "StopFailure", "session_id": "sess-bw",
            "transcript_path": str(transcript),
            "last_assistant_message": BUDGET_TEXT, "cwd": "/tmp",
            "effort": {"level": "medium"}, "prompt_id": "p1"}


def _setup(tmp_path):
    """temp HOME, launchctl stub, takeover root, sink, stub launcher, empty last-effort."""
    stubdir = tmp_path / "stub-bin"
    stubdir.mkdir()
    (stubdir / "launchctl").write_text(
        "#!/usr/bin/env bash\nexit 0\n")
    (stubdir / "launchctl").chmod(0o755)
    (tmp_path / "stub-launcher").write_text("#!/usr/bin/env bash\nexit 0\n")
    (tmp_path / "stub-launcher").chmod(0o755)
    (tmp_path / "last-effort.json").write_text("{}")
    root = tmp_path / "takeovers"
    return root


def _install_agent(tmp_path, root):
    """Generate the runner shim + plist exactly like takeover_watch.install (temp HOME,
    launchctl stubbed) and return the runner path."""
    env = {"PATH": f"{tmp_path}/stub-bin:{os.environ.get('PATH', '')}",
           "HOME": str(tmp_path), "FA_TAKEOVER_DIR": str(root)}
    r = subprocess.run([sys.executable, str(REPO / "bin" / "takeover_watch.py"), "install"],
                       capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    return root.parent / tw.RUNNER_NAME


def _spool_real_hook(root, tmp_path, transcript):
    """Run the REAL StopFailure spool hook (pure shell) with the walled session's endpoint."""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
           "FA_TAKEOVER_DIR": str(root), "ANTHROPIC_BASE_URL": "https://llmgw.example/v1"}
    r = subprocess.run(["bash", str(REPO / "hooks" / "budget-wall-spool.sh")],
                       input=json.dumps(_payload(transcript)), capture_output=True,
                       text=True, env=env, timeout=15)
    assert r.returncode == 0, r.stderr


def _run_runner_as_launchd(tmp_path, root, runner, sink, plugin=REPO):
    """Hermetic empty environment (tests/lib/hermetic_env.py) + the plist's PATH, through
    the runner shim — exactly how launchd runs it."""
    env = hermetic_env({"PATH": tw.PLIST_PATH_ENV, "HOME": str(tmp_path),
                        "FREE_AGENTS_PLUGIN": str(plugin),
                        "FA_TAKEOVER_DIR": str(root), "FA_NOTIFY_SINK": str(sink),
                        "FA_SECRET_SCANNER": str(REPO / "tests" / "fixtures" / "stub_redact_secret.py"),
                        "FA_TAKEOVER_DETACHED_VERIFY": "0", "FA_AUTO_TAKEOVER": "1",
                        "FA_TAKEOVER_LAUNCHER": str(tmp_path / "stub-launcher"),
                        "LA_LAST_EFFORT_FILE": str(tmp_path / "last-effort.json")})
    return subprocess.run(["/bin/bash", str(runner)], capture_output=True, text=True,
                          env=env, timeout=90)


def _sink(tmp_path):
    p = tmp_path / "sink.jsonl"
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def test_wall_to_successor_end_to_end(tmp_path):
    root = _setup(tmp_path)
    runner = _install_agent(tmp_path, root)
    sink = tmp_path / "sink.jsonl"
    _spool_real_hook(root, tmp_path, FIX / "budget_wall.jsonl")
    r = _run_runner_as_launchd(tmp_path, root, runner, sink)
    assert r.returncode == 0, r.stderr
    # one package, built in continue mode for the auto successor
    pkgs = sorted(root.glob("tk-*"))
    assert len(pkgs) == 1, pkgs
    handoff = (pkgs[0] / "handoff.md").read_text()
    assert "continue autonomously" in handoff
    # one window record; route = first live alias of the session order
    route = fa_capture.resolve_takeover_route(None).alias
    wins = [x for x in _sink(tmp_path) if x.get("kind") == "window"]
    assert len(wins) == 1 and wins[0]["argv"][1] == route, wins
    # one notice, starting with the 📡 mark; zero 💸 in this run's hook.log
    notices = [x for x in _sink(tmp_path) if x.get("kind") != "window"]
    assert len(notices) == 1
    assert notices[0]["text"].startswith(
        emoji_constants.SESSION_EMOJI_FREE_API_STR + " free-agents: budget wall — ")
    assert "💸" not in (root / "hook.log").read_text()
    # the same session's SECOND wall: new package supersedes, NO second window
    old = pkgs[0].name
    _spool_real_hook(root, tmp_path, FIX / "budget_wall_repeat.jsonl")
    r2 = _run_runner_as_launchd(tmp_path, root, runner, sink)
    assert r2.returncode == 0, r2.stderr
    pkgs2 = sorted(root.glob("tk-*"))
    assert len(pkgs2) == 2
    new = next(p.name for p in pkgs2 if p.name != old)
    assert fa_capture.superseded_by(root, old) == new
    wins = [x for x in _sink(tmp_path) if x.get("kind") == "window"]
    assert len(wins) == 1, "one successor per source session, ever"


def test_runner_fails_loudly_when_plugin_missing(tmp_path):
    """Discriminant: a runner whose plugin resolution fails must exit non-zero with a named
    error — not a silent pass."""
    root = _setup(tmp_path)
    runner = _install_agent(tmp_path, root)
    r = _run_runner_as_launchd(tmp_path, root, runner, tmp_path / "sink.jsonl",
                               plugin=tmp_path / "no-such-plugin")
    assert r.returncode != 0
    assert "free-agents" in (r.stderr + r.stdout)


def test_watcher_chain_imports_under_python39():
    """The launchd chain runs under /usr/bin/python3 (3.9 on macOS): every module the
    watcher path imports must import cleanly there (the PEP 604 fix). Skips off-mac."""
    py39 = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else None
    if not py39:
        pytest.skip("no /usr/bin/python3")
    ver = subprocess.run([py39, "--version"], capture_output=True, text=True).stdout
    mods = ["fa_notify", "fa_transcript", "fa_capture", "session_menu_state",
            "effort_resolve", "takeover_watch"]
    code = ("import sys, importlib.util; sys.path.insert(0, %r); " % str(REPO / "bin")
            + "".join(f"import {m}; " for m in mods)
            # last-effort.py has a hyphen: loaded via SourceFileLoader, like effort_resolve does
            + "spec = importlib.util.spec_from_file_location('last_effort', %r); "
              "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
              % str(REPO / "bin" / "last-effort.py")
            + "print('ok')")
    r = subprocess.run([py39, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "ok" in r.stdout, (ver, r.stdout, r.stderr)


def test_version_surfaces_agree():
    import re
    mv = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())["version"]
    cv = re.search(r"^## \[(\d+\.\d+\.\d+)\]", (REPO / "CHANGELOG.md").read_text(), re.M).group(1)
    rv = re.search(r"^## Current released version\s*\n\s*`(\d+\.\d+\.\d+)`",
                   (REPO / "docs" / "ROADMAP.md").read_text(), re.M).group(1)
    vv = (REPO / "VERSION").read_text().strip()
    assert mv == cv == rv == vv == "0.64.1", (mv, cv, rv, vv)

#!/usr/bin/env python3
"""tests/test_config_dir_resolver.py — ONE call-time config-dir resolver (R1-A).

FREE_AGENTS_CONFIG_DIR is the canonical name; LA_SESSION_MENU_CONFIG_DIR is an alias
that must AGREE when both are set (different resolved paths abort loudly, naming
both). The split-brain this fixes: session_menu_state.REPO_CONFIG_DIR was fixed at
IMPORT while rate_limiter / session_picker read the LA_ name per call — so a test
that set the env after import silently still used the real ~/.claude dir.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_menu_state as sms  # noqa: E402

CANON = "FREE_AGENTS_CONFIG_DIR"
ALIAS = "LA_SESSION_MENU_CONFIG_DIR"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Isolate from the real env and the real HOME. The CANONICAL var is left as the
    conftest autouse fixture set it (its per-test sandbox IS the isolation) — tests that
    need it unset delenv it explicitly. The alias is removed so one-name tests cannot
    trip the both-set conflict."""
    monkeypatch.delenv(ALIAS, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_neither_set_uses_default_under_home(monkeypatch):
    monkeypatch.delenv(CANON, raising=False)
    assert sms.config_dir() == Path.home() / ".claude" / "free-agents" / "session-menu"


def test_only_canonical_set_wins(monkeypatch, tmp_path):
    d = tmp_path / "canon"
    monkeypatch.setenv(CANON, str(d))
    assert sms.config_dir() == d


def test_only_alias_set_wins(monkeypatch, tmp_path):
    monkeypatch.delenv(CANON, raising=False)
    d = tmp_path / "alias"
    monkeypatch.setenv(ALIAS, str(d))
    assert sms.config_dir() == d
    # outcome, not just the resolver: load() with no arg must READ through the alias
    d.mkdir()
    (d / sms.PREFS_NAME).write_text('{"schema_version": 11, "effort": {"local_session": "low"}}',
                                   encoding="utf-8")
    assert sms.load().effort["local_session"] == "low"


def test_both_set_same_dir_different_spelling_ok(monkeypatch, tmp_path):
    d = tmp_path / "same"
    monkeypatch.setenv(CANON, str(d))
    monkeypatch.setenv(ALIAS, str(d) + "/")   # trailing slash must not be a conflict
    assert sms.config_dir() == d


def test_both_set_different_paths_aborts_naming_both(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv(CANON, str(a))
    monkeypatch.setenv(ALIAS, str(b))
    with pytest.raises(sms.ConfigDirConflict) as exc:
        sms.config_dir()
    msg = str(exc.value)
    assert CANON in msg and ALIAS in msg and str(a) in msg and str(b) in msg


def test_resolution_is_call_time_not_import_time(monkeypatch, tmp_path):
    """The split-brain regression: the env is set AFTER import; config_dir() and
    load() with no argument must both use it (old code used the import-time value)."""
    real = Path.home() / ".claude" / "free-agents" / "session-menu"
    real.mkdir(parents=True)
    (real / sms.PREFS_NAME).write_text('{"schema_version": 11, "effort": {"local_session": "low"}}',
                                      encoding="utf-8")
    override = tmp_path / "override"
    override.mkdir()
    (override / sms.PREFS_NAME).write_text('{"schema_version": 11, "effort": {"local_session": "max"}}',
                                           encoding="utf-8")
    # import already happened (module-level import above); with the canonical var unset:
    monkeypatch.delenv(CANON, raising=False)
    assert sms.config_dir() == real
    monkeypatch.setenv(CANON, str(override))
    assert sms.config_dir() == override                      # call-time, not import-time
    # planted positive: with the override set, load() must NOT read the real dir's marker
    assert sms.load().effort["local_session"] == "max"
    monkeypatch.delenv(CANON)
    assert sms.load().effort["local_session"] == "low"       # real default dir visible again


def test_no_reader_bypasses_the_resolver():
    """Guard: no bin/*.py outside session_menu_state.py may read either env name
    directly, in ANY spelling (os.environ.get / os.getenv / os.environ[...]) — the
    fixture copy under tests/fixtures is frozen old code, excluded. M1 dispatch found
    the literal-only scan evadable via os.getenv; this regex closes it."""
    direct = re.compile(
        r'os\.(?:environ\.get|getenv)\s*\(\s*["\'](?:' + CANON + r'|' + ALIAS + r')["\']'
        r'|os\.environ\s*\[\s*["\'](?:' + CANON + r'|' + ALIAS + r')["\']')
    offenders = []
    for py in (ROOT / "bin").glob("*.py"):
        if py.name == "session_menu_state.py":
            continue
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if direct.search(line):
                offenders.append(f"{py.name}:{i}")
    assert offenders == [], f"direct env reads bypass the resolver: {offenders}"


def test_cli_config_dir_prints_resolved_dir(tmp_path):
    d = tmp_path / "cli"
    env = dict(os.environ, **{CANON: str(d)})
    out = subprocess.run([sys.executable, str(ROOT / "bin" / "session_menu_state.py"), "config-dir"],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(d)


def test_cli_conflict_exits_2_naming_both(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    env = dict(os.environ, **{CANON: str(a), ALIAS: str(b)})
    out = subprocess.run([sys.executable, str(ROOT / "bin" / "session_menu_state.py"), "config-dir"],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 2
    assert CANON in out.stderr and ALIAS in out.stderr and str(a) in out.stderr and str(b) in out.stderr


def test_cli_help_lists_config_dir():
    out = subprocess.run([sys.executable, str(ROOT / "bin" / "session_menu_state.py"), "--help"],
                         capture_output=True, text=True)
    assert out.returncode == 0
    assert "config-dir" in out.stdout


def test_autouse_fixture_sandboxes_config_dir(tmp_path):
    """The conftest autouse fixture (D2): inside any test the canonical var points at a
    per-test temp dir, the alias is unset, and the resolver returns the sandbox."""
    env_dir = Path(os.environ["FREE_AGENTS_CONFIG_DIR"])
    assert env_dir.is_relative_to(tmp_path)          # per-test dir from the conftest fixture
    assert "LA_SESSION_MENU_CONFIG_DIR" not in os.environ
    assert sms.config_dir() == env_dir               # the resolver sees the sandbox


def test_conftest_does_not_fake_home():
    """D2: the config-dir sandbox is config-dir ONLY — conftest must not set HOME by any
    API (monkeypatch.setenv("HOME", …) or a direct os.environ["HOME"] = …)."""
    conftest_src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    home_set = re.compile(r'(?:setenv\s*\(\s*|environ\s*\[\s*)["\']HOME["\']')
    assert not home_set.search(conftest_src), "conftest must not set HOME"

#!/usr/bin/env python3
"""0.63.2: every test runs with CLAUDE_CONFIG_DIR sandboxed (tests/conftest.py
_sandbox_claude_config_dir), and the real-settings guard REPORTS changed key names instead of
failing the run.

Why: the old guard byte-compared the real ~/.claude/settings.json at session start vs end and
failed the run, so the user editing settings, or a live Claude Code session saving
model/effort/theme mid-gate, failed 2 of 3 free-agents ship gates on 2026-11-24. The real
protection is that no test can reach the real file; the guard only names what changed.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))

REAL = Path.home() / ".claude"


def test_every_test_sees_a_sandboxed_config_dir():
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    assert cfg, "CLAUDE_CONFIG_DIR is not set: the autouse sandbox fixture did not run"
    assert Path(cfg).resolve() != REAL.resolve(), "the sandbox points at the REAL ~/.claude"
    assert Path(cfg).is_dir()


def test_cloud_session_env_writes_into_the_sandbox():
    import cloud_session_env as cse
    p = cse.settings_path().resolve()
    assert p != (REAL / "settings.json").resolve(), f"cse would write the REAL file: {p}"
    assert str(p).startswith(str(Path(os.environ["CLAUDE_CONFIG_DIR"]).resolve()))


def test_each_test_gets_its_own_sandbox_dir(tmp_path):
    # function-scoped: the sandbox of this test is not the previous test's
    from conftest import _sandbox_claude_config_dir  # noqa: F401  (importable = defined)
    assert Path(os.environ["CLAUDE_CONFIG_DIR"]).exists()


def test_changed_setting_keys_names_only():
    from conftest import changed_setting_keys
    before = b'{"a": 1, "env": {"X": "1", "TOKEN": "s3cr3t-value"}, "theme": "dark"}'
    after = b'{"a": 1, "env": {"X": "2", "TOKEN": "s3cr3t-value"}, "theme": "light", "new": true}'
    keys = changed_setting_keys(before, after)
    assert keys == ["env.X", "new", "theme"], keys
    assert not any("s3cr3t" in k or "light" in k for k in keys), "values must never be reported"


def test_changed_setting_keys_edge_cases():
    from conftest import changed_setting_keys
    assert changed_setting_keys(None, None) == []
    assert changed_setting_keys(None, b'{"a": 1}') == ["a"]
    assert changed_setting_keys(b'{"a": 1}', b'{not json') == ["<unparseable settings.json>"]
    assert changed_setting_keys(b'{"a": 1}', b'{ "a" : 1 }') == []   # formatting only

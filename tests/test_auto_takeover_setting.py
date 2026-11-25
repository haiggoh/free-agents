"""Tests for the auto_takeover opt-out setting (R4 plan Task 2 — NEW file, split playbook).

D4: default ON; FA_AUTO_TAKEOVER accepts exactly "0"/"1" (any other set value raises
loudly); a non-bool in the file is refused loudly (schema error, never silently True);
the csl settings row renders ON/OFF + agent state and toggling fires the save callback,
which persists via sms.save_auto_takeover and installs/uninstalls the watch agent.

Run: pytest tests/test_auto_takeover_setting.py -v
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))
import session_menu_state as sms  # noqa: E402


def _write_prefs(config_dir: Path, doc: dict) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    p = config_dir / sms.PREFS_NAME
    p.write_text(json.dumps(doc))
    return p


# --- state layer ---------------------------------------------------------------

def test_default_on_when_absent(tmp_path):
    assert sms.load(tmp_path).auto_takeover is True
    assert sms.auto_takeover_enabled(env={}, config_dir=tmp_path) is True


def test_save_roundtrip_false_then_true(tmp_path):
    assert sms.save_auto_takeover(tmp_path, False) is None
    doc = json.loads((tmp_path / sms.PREFS_NAME).read_text())
    assert doc["auto_takeover"] is False
    assert doc["schema_version"] == sms.SCHEMA_VERSION
    assert sms.load(tmp_path).auto_takeover is False
    assert sms.auto_takeover_enabled(env={}, config_dir=tmp_path) is False
    assert sms.save_auto_takeover(tmp_path, True) is None
    assert sms.load(tmp_path).auto_takeover is True


def test_non_bool_in_file_is_refused_loudly(tmp_path):
    p = _write_prefs(tmp_path, {"schema_version": sms.SCHEMA_VERSION, "auto_takeover": "no"})
    with pytest.raises(ValueError, match="auto_takeover"):
        sms._parse(p.read_bytes())
    state = sms.load(tmp_path)
    assert any("auto_takeover" in w for w in state.warnings), state.warnings
    assert state.auto_takeover is True  # built-in default, but REPORTED — never silently adopted
    assert json.loads(p.read_text())["auto_takeover"] == "no"  # the file is left untouched


def test_section_needs_schema_12(tmp_path):
    p = _write_prefs(tmp_path, {"schema_version": 11, "auto_takeover": False})
    with pytest.raises(ValueError, match="schema_version 12"):
        sms._parse(p.read_bytes())


def test_env_override_and_invalid_env_raises(tmp_path):
    assert sms.save_auto_takeover(tmp_path, True) is None
    assert sms.auto_takeover_enabled(env={"FA_AUTO_TAKEOVER": "0"}, config_dir=tmp_path) is False
    assert sms.auto_takeover_enabled(env={"FA_AUTO_TAKEOVER": "1"}, config_dir=tmp_path) is True
    # an EMPTY value is unset (env -i passthrough), like the FA_NOTIFY_SINK rule
    assert sms.auto_takeover_enabled(env={"FA_AUTO_TAKEOVER": ""}, config_dir=tmp_path) is True
    with pytest.raises(ValueError, match="FA_AUTO_TAKEOVER must be 0 or 1"):
        sms.auto_takeover_enabled(env={"FA_AUTO_TAKEOVER": "yes"}, config_dir=tmp_path)


def test_save_preserves_other_settings(tmp_path):
    assert sms.save_effort(tmp_path, "remote_api_session", "max") is None
    assert sms.save_auto_takeover(tmp_path, False) is None
    st = sms.load(tmp_path)
    assert st.effort["remote_api_session"] == "max"
    assert st.auto_takeover is False


# --- picker row (the ONE settings front end on main: session_picker_model) ------
# Actions-based pattern copied from test_session_picker_model.py's settings-row tests
# (the pilot/PTY layer needs the picker venv and would skip in the gate).

def _remote_agents():
    import session_picker_model as m
    return [m.RemoteAgent("nvidia-a", "nvidia", "NVIDIA A", "unknown"),
            m.RemoteAgent("nvidia-b", "nvidia", "NVIDIA B", "unknown")]


def test_settings_row_renders_and_toggles():
    import session_picker_model as m
    s = m.Settings()
    saved = []
    s.on_auto_takeover_saved = saved.append
    lane = m.RemoteScreen(s, _remote_agents(), owner=m.HOME_OWNED)
    row = {a.key: a for a in lane.actions()}["f"]
    assert row.step is not None, "the auto-launch row is a settings row"
    assert "Auto-launch a free successor" in row.label
    assert "ON" in row.label and "agent:" in row.label  # default ON + agent state shown
    row.step(+1)
    assert s.auto_takeover is False and saved == [False]
    row2 = {a.key: a for a in lane.actions()}["f"]
    assert "OFF" in row2.label
    row2.step(+1)
    assert s.auto_takeover is True and saved == [False, True]


def test_row_label_uses_emoji_constant_not_literal():
    import emoji_constants as ec
    import session_picker_model as m
    lane = m.RemoteScreen(m.Settings(), _remote_agents(), owner=m.HOME_OWNED)
    label = {a.key: a for a in lane.actions()}["f"].label
    assert ec.SESSION_EMOJI_FREE_API_STR in label
    # the label is built from the constant: the literal mark must not appear twice
    assert label.count(ec.SESSION_EMOJI_FREE_API_STR) == 1


def test_row_shows_watch_agent_state(monkeypatch):
    import session_picker_model as m

    class FakeWatch:
        def status(self):
            return {"installed": True, "loaded": True, "plist": "/tmp/x.plist"}
    monkeypatch.setattr(m, "takeover_watch_module", lambda: FakeWatch())
    lane = m.RemoteScreen(m.Settings(), _remote_agents(), owner=m.HOME_OWNED)
    label = {a.key: a for a in lane.actions()}["f"].label
    assert "agent: loaded" in label


def test_save_with_watch_installs_and_uninstalls(monkeypatch, tmp_path):
    """save_auto_takeover_with_watch persists via sms AND installs/uninstalls the agent.
    (Lives in session_picker_model so the gate env can test it — session_picker.py's
    import is venv-guarded and would SystemExit under the gate's pytest.)"""
    import session_picker_model as m

    calls = []

    class FakeWatch:
        def install(self, dry_run=False):
            calls.append(("install", dry_run))
            return 0

        def uninstall(self, dry_run=False):
            calls.append(("uninstall", dry_run))
            return 0

        def status(self):
            return {"installed": False, "loaded": False, "plist": ""}

    monkeypatch.setattr(m, "takeover_watch_module", lambda: FakeWatch())
    assert m.save_auto_takeover_with_watch(tmp_path, False) is None
    assert sms.load(tmp_path).auto_takeover is False
    assert ("uninstall", False) in calls
    calls.clear()
    assert m.save_auto_takeover_with_watch(tmp_path, True) is None
    assert sms.load(tmp_path).auto_takeover is True
    assert ("install", False) in calls


def test_app_wires_the_callback():
    """Wiring pin (same shape as test_hooks_json_registers_once): the Picker passes
    on_auto_takeover_saved=self._save_auto_takeover, which delegates to the tested
    m.save_auto_takeover_with_watch."""
    src = (REPO / "bin" / "session_picker.py").read_text()
    assert "on_auto_takeover_saved=self._save_auto_takeover" in src
    assert "save_auto_takeover_with_watch" in src

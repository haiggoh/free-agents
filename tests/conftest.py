"""Suite-wide pytest guards.

open_url() (bin/session_picker_model.py) falls back to the real macOS `open` when LA_URL_OPENER is
unset, so any test that activates an "open:<provider>" item outside the picker harness launches a
real browser tab. Point the opener at `true` for the whole run; tests that assert on the opener
(picker_harness, mock.patch) still override this.

The picker writes Claude Code's settings.json at startup (0.42.0: stale security-review cleanup),
and a test that starts it without a sandboxed CLAUDE_CONFIG_DIR edits the USER'S real file — it
happened once, from a subprocess the picker harness never saw. So the run fails if the real
~/.claude/settings.json changes while it runs, whichever test (or child process) did it.
"""
import os
from pathlib import Path

os.environ.setdefault("LA_URL_OPENER", "true")

_REAL_SETTINGS = Path.home() / ".claude" / "settings.json"


def _snapshot():
    try:
        return _REAL_SETTINGS.read_bytes()
    except OSError:
        return None


_BEFORE = _snapshot()


def pytest_sessionfinish(session, exitstatus):
    if _snapshot() != _BEFORE:
        print(f"\nFAILED: a test changed the real {_REAL_SETTINGS} — sandbox CLAUDE_CONFIG_DIR "
              "for any test that starts the picker or writes settings")
        session.exitstatus = 1

"""Suite-wide pytest guards.

open_url() (bin/session_picker_model.py) falls back to the real macOS `open` when LA_URL_OPENER is
unset, so any test that activates an "open:<provider>" item outside the picker harness launches a
real browser tab. Point the opener at `true` for the whole run; tests that assert on the opener
(picker_harness, mock.patch) still override this.

The picker writes Claude Code's settings.json at startup (0.42.0: stale security-review cleanup),
and a test that starts it without a sandboxed CLAUDE_CONFIG_DIR edits the USER'S real file — it
happened once, from a subprocess the picker harness never saw. So the run fails if the real
~/.claude/settings.json changes while it runs, whichever test (or child process) did it.

A session started by the launcher EXPORTS ~37 LA_* variables (LA_LIVE_CONFIG_DIR, LA_MOESPRESSO_BIN,
LA_CUR_*, LA_SESSION_*...), and every test inherits them, including each bash suite this run spawns.
config-lib.sh honours an inherited LA_LIVE_CONFIG_DIR ahead of a sandbox's own config, so a gate run
from such a session loaded the REAL machine roster into the sandboxes: 15-19 suites red there and
green from a plain shell, on identical code (test-shell-suites-red-live, 2026-10-26/27). Patching
suites one at a time with `env -u` covered three of them. The scrub lives here because it is the
one step every gate route shares; the run's tests must not depend on which shell started it.
LA_TEST_* (test-harness knobs) are kept.
"""
import os
from pathlib import Path

SCRUBBED_ENV = sorted(k for k in os.environ
                      if k.startswith(("LA_", "_LA_")) and not k.startswith("LA_TEST_"))
for _k in SCRUBBED_ENV:
    del os.environ[_k]

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


def pytest_report_header(config):
    if SCRUBBED_ENV:
        return f"conftest: scrubbed {len(SCRUBBED_ENV)} inherited LA_* var(s) from the environment"
    return None


def pytest_configure(config):
    # Catalog-cache tripwire (2026-11-01): a test that writes the REAL catalog cache
    # (~/.local/state/local-agents/remote-catalogs) pollutes every later picker start —
    # fixture rows show up as live models ("nvidia/cat-two"…). Tests must sandbox
    # LA_REMOTE_CATALOG_CACHE_DIR; an in-process write to the default dir fails the test.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
    import session_picker_model as _m

    _default_cache = Path.home() / ".local" / "state" / "local-agents" / "remote-catalogs"
    _orig_write = _m.write_catalog_cache

    def _guarded_write(provider, rows):
        if _m.catalog_cache_dir() == _default_cache and os.environ.get("LA_REMOTE_CATALOG_CACHE_DIR") is None:
            raise AssertionError(
                f"test wrote the REAL catalog cache for {provider!r}; set "
                "LA_REMOTE_CATALOG_CACHE_DIR to a sandbox dir (picker_harness does)")
        return _orig_write(provider, rows)

    _m.write_catalog_cache = _guarded_write

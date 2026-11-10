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

import pytest

SCRUBBED_ENV = sorted(k for k in os.environ
                      if k.startswith(("LA_", "_LA_")) and not k.startswith("LA_TEST_"))
for _k in SCRUBBED_ENV:
    del os.environ[_k]

os.environ.setdefault("LA_URL_OPENER", "true")

# Quota-ledger sandbox (0.50.3): remote-agent-dispatch appends to the quota ledger after every
# completed call, so any test that stubs a successful dispatch would write the user's REAL
# ~/.local/share/local-agent/remote-usage.jsonl (it did, during 0.50.2/0.50.3 development).
# FORCED, not setdefault: an override inherited from the shell may point at the real dir.
# Subprocesses inherit it. Tests that exercise the override itself use monkeypatch.setenv.
import tempfile as _tempfile  # noqa: E402
os.environ["FA_QUOTA_LEDGER_DIR"] = _tempfile.mkdtemp(prefix="fa-quota-test-")

# 0.54.0 Task 1 Step 0 — fence the machine-wide shared state BEFORE any new test exists.
# The scrub above deleted every inherited LA_*, which makes rate limiting ON at its DEFAULT
# (real) path: a test reaching remote_http with provider nvidia would write the REAL
# ~/.claude/free-agents/.nvidia_throttle_state — the one bucket every live session on this
# box queues on. The HF params cache (~/.cache/free-agents/hf-params.json) is user data for
# the same reason. FORCED, not setdefault (an inherited override may point at the real file);
# subprocesses inherit both. The tripwires in pytest_configure fail any in-process write that
# still resolves to the real files.
os.environ["LA_NVIDIA_THROTTLE_STATE"] = str(
    Path(_tempfile.mkdtemp(prefix="fa-throttle-test-")) / "throttle.json")
os.environ["LA_HF_PARAMS_CACHE"] = str(
    Path(_tempfile.mkdtemp(prefix="fa-hfparams-test-")) / "hf-params.json")

# 0.55.8 Task 0 (R1, 0541 plan Item H): no test may post a real notification or open a
# window. One shim dir with no-op osascript/terminal-notifier/afplay/say/open stubs + a
# session FA_NOTIFY_SINK; the CONTROLLER creates them once (pytest_configure, not a worker)
# and every xdist worker + subprocess inherits FA_NOTIFY_SHIM_DIR/FA_NOTIFY_SINK via
# os.environ. pytest_sessionfinish fails the whole session if the shim log records anything
# — a real-notification attempt is a test failure, not a warning.
_NOTIFY_STUBS = ("osascript", "terminal-notifier", "afplay", "say", "open")


def _ensure_notify_shim():
    """Create-or-reuse the shim dir + sink; export both. Idempotent across workers."""
    shim_dir = os.environ.get("FA_NOTIFY_SHIM_DIR") or str(
        Path(_tempfile.mkdtemp(prefix="fa-notify-shim-")))
    os.environ["FA_NOTIFY_SHIM_DIR"] = shim_dir
    d = Path(shim_dir)
    d.mkdir(parents=True, exist_ok=True)
    for name in _NOTIFY_STUBS:
        stub = d / name
        if not stub.exists():
            stub_body = ("#!/bin/sh\n"
                         "printf '%s\\n' \"$(date +%s) " + name +
                         " $* CALLER=$(ps -o command= -p $PPID | cut -c1-160)\" >> \""
                         + str(d / "calls.log") + "\"\n"
                         "exit 0\n")
            stub.write_text(stub_body)
            stub.chmod(0o755)
    if "FA_NOTIFY_SINK" not in os.environ:
        os.environ["FA_NOTIFY_SINK"] = str(d / "sink.jsonl")
    path = os.environ.get("PATH", "")
    if str(d) not in path.split(":"):
        os.environ["PATH"] = f"{d}:{path}"
    return d


@pytest.fixture(autouse=True)
def _limiter_knobs_for_load_sensitive(request, monkeypatch):
    # 0.50.4: tests marked `load_sensitive` may run while a concurrent session hammers the
    # shared nvidia rate limiter, and the limiter is the flake source (waypoint
    # pty-litellm-venv-tests-flake). Give marked tests the priority lane BY DEFAULT instead of
    # waiting for a quiet limiter — the knobs exist since 0.50.1 (bin/rate_limiter.py):
    #   LA_LIMITER_RESERVE=0.2  reserve 20% of the bucket for priority acquisitions; normal
    #                           traffic stops at 80% (limit*(1-reserve)). Default 0.0 = off.
    #   LA_LIMITER_PRIORITY=1   this process acquires with priority=True, drawing from the reserve.
    # The limiter reads both from the env AT CALL TIME, and subprocesses inherit os.environ,
    # so monkeypatch covers in-process limiter use and spawned proxy/dispatch children alike.
    # Manual / live-session use (marked tests don't need this — it is set here automatically):
    #   LA_LIMITER_RESERVE=0.2 in the priority process's env, or
    #   python3 bin/rate_limiter.py set-owner <session-id> --priority [--rpm-scale 0.2]
    #   python3 bin/rate_limiter.py owners   # who holds the window
    #   python3 bin/rate_limiter.py probe    # external occupancy of MY bucket
    # Also documented in rtc-ship.json "_comment_limiter_knobs" and tests/test_loadscale.py.
    if request.node.get_closest_marker("load_sensitive"):
        monkeypatch.setenv("LA_LIMITER_RESERVE", "0.2")
        monkeypatch.setenv("LA_LIMITER_PRIORITY", "1")

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
    shim_log = Path(os.environ.get("FA_NOTIFY_SHIM_DIR", "")) / "calls.log"
    calls = []
    try:
        if shim_log.exists():
            calls = [x for x in shim_log.read_text().splitlines() if x.strip()]
    except OSError:
        calls = []
    # Calls from the INSTALLED plugin copy are the pre-existing leak this release fixes
    # (the installed 0.56.0 predates the fence; fixed for every copy installed after
    # 0.56.1 — waypoint investigate-why-does-the). They are reported, not fatal: the
    # repo's tests cannot fence a copy outside this tree. IN-REPO callers still fail.
    installed_marker = os.sep + "plugins" + os.sep + "cache" + os.sep
    installed = [x for x in calls if installed_marker in x]
    repo_calls = [x for x in calls if installed_marker not in x]
    if installed:
        print(f"\nWARNING: {len(installed)} notification attempt(s) from the INSTALLED "
              "plugin copy (pre-existing leak, fixed by this release; waypoint "
              "investigate-why-does-the):")
        for line in installed[:10]:
            print(f"  {line}")
    if repo_calls:
        print(f"\nFAILED: {len(repo_calls)} real notification/window attempt(s) from "
              "REPO code reached the notify shim (0.55.8 Task 0):")
        for line in repo_calls[:10]:
            print(f"  {line}")
        session.exitstatus = 1


def pytest_report_header(config):
    if SCRUBBED_ENV:
        return f"conftest: scrubbed {len(SCRUBBED_ENV)} inherited LA_* var(s) from the environment"
    return None


def pytest_configure(config):
    # Load-sensitive marker (2026-11-01 pty-litellm-venv flake): tests that spawn real
    # processes or drive the picker PTY harness depend on shared-machine load and the
    # shared nvidia rate limiter, so they can flake one-per-run at load > 10 even with
    # load-scaled timeouts. Deselect them on a loaded box with: pytest -m "not load_sensitive"
    if not hasattr(config, "workerinput"):
        _ensure_notify_shim()   # R1: the CONTROLLER owns the one shim dir; workers inherit
    config.addinivalue_line(
        "markers",
        "load_sensitive: test spawns real processes / PTY harness and flakes under extreme "
        "shared load (incl. rate-limiter pressure); deselect with -m 'not load_sensitive'")
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

    # Quota-ledger tripwire (0.50.3): an in-process append under the REAL ledger dir fails
    # the test, whatever path the caller passed (write_ledger is the one writer).
    from quota import telemetry as _qt

    _real_ledger_dir = _qt.DEFAULT_LEDGER_DIR.resolve()
    _orig_ledger = _qt.write_ledger

    def _guarded_ledger(record, ledger_path=_qt.DEFAULT_LEDGER_PATH):
        if Path(ledger_path).resolve().parent == _real_ledger_dir:
            raise AssertionError(
                f"test wrote the REAL quota ledger {ledger_path}; FA_QUOTA_LEDGER_DIR must "
                "point at a sandbox (conftest sets one for the whole run)")
        return _orig_ledger(record, ledger_path)

    _qt.write_ledger = _guarded_ledger

    # Throttle-bucket tripwire (0.54.0 Task 1 Step 0): with LA_NVIDIA_THROTTLE_STATE forced
    # above, limiter instances point at the per-run temp file; any open/write that still
    # resolves to the REAL shared bucket (shared with live sessions and this very session's
    # proxy) fails the test. _locked is the choke point every acquire goes through; _save is
    # SmoothTokenBucket's separate atomic write.
    import rate_limiter as _rl
    _real_throttle = _rl.DEFAULT_STATE.resolve()

    def _guard_path(self):
        if Path(self.path).resolve() == _real_throttle:
            raise AssertionError(
                f"test opened the REAL throttle state {self.path} — LA_NVIDIA_THROTTLE_STATE "
                "is forced to a sandbox by conftest; pass an explicit temp path instead")

    for _cls in (_rl.SmoothTokenBucket, _rl.SlidingWindowLimiter):
        _orig_locked = _cls._locked

        def _guarded_locked(self, _orig=_orig_locked):
            _guard_path(self)
            return _orig(self)

        _cls._locked = _guarded_locked
    _orig_save = _rl.SmoothTokenBucket._save

    def _guarded_save(self):
        _guard_path(self)
        return _orig_save(self)

    _rl.SmoothTokenBucket._save = _guarded_save

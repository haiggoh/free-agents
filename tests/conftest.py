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
# free-agent-tool.py appends this machine's csl-registered local models by default (0.65.4);
# FORCED off so the suite never sees real aliases. Tests of the fallback set it themselves.
os.environ["FA_DISPATCH_LOCAL_FALLBACK"] = "0"

# 0.54.0 Task 1 Step 0 — fence the machine-wide shared state BEFORE any new test exists.
# The scrub above deleted every inherited LA_*, which makes rate limiting ON at its DEFAULT
# (real) path: a test reaching remote_http with provider nvidia would write the REAL
# ~/.claude/free-agents/.nvidia_throttle_state — the one bucket every live session on this
# box queues on. The HF params cache (~/.cache/free-agents/hf-params.json) is user data for
# the same reason. FORCED, not setdefault (an inherited override may point at the real file);
# subprocesses inherit both. The tripwires in pytest_configure fail any in-process write that
# still resolves to the real files.
# 0.56.15 (perf): per-xdist-worker fence files — one shared throttle state made every
# worker's limiter flock serialize the whole gate (measured: -n 12 on the picker PTY file
# took the same wall time as serial). The worker id (PYTEST_XDIST_WORKER, "gw0"..) makes
# each worker's state file distinct; the tripwires still catch any real-path write.
_worker = os.environ.get("PYTEST_XDIST_WORKER", "")
os.environ["LA_NVIDIA_THROTTLE_STATE"] = str(
    Path(_tempfile.mkdtemp(prefix="fa-throttle-test-")) / f"throttle{_worker}.json")
os.environ["LA_HF_PARAMS_CACHE"] = str(
    Path(_tempfile.mkdtemp(prefix="fa-hfparams-test-")) / f"hf-params{_worker}.json")
# fix/runtime-model-facts: the NETWORK fence for Hugging Face. Limits.model(online=True) and the
# warm-ups would otherwise reach the real huggingface.co from inside the suite (measured: a ladder
# test's "unknown context" model resolved to 1M over the network). An unreachable loopback origin
# makes every un-stubbed HF call a fast network error — which the cache rules never cache. Tests
# with their own fake server override it; stubs of _hf_get never reach it.
os.environ["LA_HF_API_BASE"] = "http://127.0.0.1:9"
os.environ["LA_HF_ROUTER_URL"] = "http://127.0.0.1:9/v1/models"   # the router catalog too
_HF_PARAMS_FENCE = os.environ["LA_HF_PARAMS_CACHE"]
_HF_API_BASE_FENCE = os.environ["LA_HF_API_BASE"]
_HF_ROUTER_FENCE = os.environ["LA_HF_ROUTER_URL"]
# 0.62.0 (provider buckets): per-PROVIDER buckets live under FA_LIMITER_DIR (default
# ~/.claude/free-agents/limiter) — the same live-state fence as the NVIDIA file applies:
# FORCED to a per-worker sandbox, never the real dir.
os.environ["FA_LIMITER_DIR"] = str(
    Path(_tempfile.mkdtemp(prefix="fa-limiterdir-test-")) / f"limiter{_worker}")

# F1 (2026-11-25 followups): fence the two remaining live-state writers. bin/last-effort.py
# defaults its state file to <repo>/config/remote-last-effort.local.json when
# LA_LAST_EFFORT_FILE is unset (measured 2026-11-25: a full suite run created that file in
# the checkout it ran in — in the CANONICAL checkout that IS the user's live effort store),
# and install/backend_upgrades.py defaults LA_BACKEND_UPGRADES_STATE to the REAL
# ~/.claude/free-agents/backend-upgrades.json. Both FORCED, not setdefault (an inherited
# override may point at the real file); per-xdist-worker like the throttle file above;
# subprocesses inherit both. The autouse fixture below re-asserts them per test, because a
# test may pop the override (test_cycle_ui_0554 sets LA_BACKEND_UPGRADES_STATE on
# os.environ directly and pops it on cleanup, which would otherwise leave the fence unset
# for every later test in the same worker).
os.environ["LA_LAST_EFFORT_FILE"] = str(
    Path(_tempfile.mkdtemp(prefix="fa-lasteffort-test-")) / f"last-effort{_worker}.json")
os.environ["LA_BACKEND_UPGRADES_STATE"] = str(
    Path(_tempfile.mkdtemp(prefix="fa-upgrades-test-")) / f"backend-upgrades{_worker}.json")
_LAST_EFFORT_FENCE = os.environ["LA_LAST_EFFORT_FILE"]
_BACKEND_UPGRADES_FENCE = os.environ["LA_BACKEND_UPGRADES_STATE"]

# 0.63.3 (R1-A/D2): run-level sandbox for the session-menu config dir. FORCED like the
# fences above (an inherited override may point at the real dir); the per-test autouse
# fixture below overrides it with a per-test dir. The LA_SESSION_MENU_CONFIG_DIR alias is
# scrubbed with the other LA_* vars at the top of this file, so only the canonical name
# is set here.
os.environ["FREE_AGENTS_CONFIG_DIR"] = str(
    Path(_tempfile.mkdtemp(prefix="fa-configdir-test-")) / f"session-menu{_worker}")

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
                         " $* CALLER=$(ps -o command= -p $PPID | cut -c1-400)\" >> \""
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
    #   LA_LIMITER_PRIORITY=1   this process registers its requests in the priority lane
    #                           (queue-jump over normal waiters under contention).
    #   LA_LIMITER_RESERVE=0.2  lane SHARE CAP since the fairness rework: while normal waiters
    #                           are queued, priority grants are capped at max(1, round(0.2*limit))
    #                           in the rolling window; with no normal waiters the cap does not
    #                           bind, so no capacity idles. Default 0.2.
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


@pytest.fixture(autouse=True)
def _sandbox_claude_config_dir(tmp_path_factory, monkeypatch):
    """0.63.2: EVERY test runs with CLAUDE_CONFIG_DIR pointing at a fresh temp dir, so no code
    path that resolves Claude Code's config through it (cloud_session_env.config_dir(), and the
    picker via cse) can reach the user's real ~/.claude/settings.json. A test that needs its own
    dir still sets it; one that needs the default path unsets it AND sandboxes HOME itself
    (test_fa_pre_trust's `sb`). Before this, sandboxing was per test, and the only backstop was
    the byte-compare below, which could not tell a leaking test from the user editing settings
    or a live Claude Code session saving model/effort/theme mid-gate (2026-11-24: 2 of 3 ship
    gates failed that way)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))


@pytest.fixture(autouse=True)
def _sandbox_free_agents_config_dir(tmp_path, monkeypatch):
    """0.63.3 (R1-A, user decision D2): EVERY test runs with the free-agents session-menu
    config dir sandboxed — FREE_AGENTS_CONFIG_DIR forced to a fresh per-test temp dir and
    the LA_SESSION_MENU_CONFIG_DIR alias removed, so a test that sets one name cannot trip
    the both-set conflict. Config-dir isolation ONLY: HOME is not faked (D2 — 42 of 64
    HOME-touching test files read the real HOME on purpose: venvs, ~/.local/bin/pytest)."""
    monkeypatch.delenv("LA_SESSION_MENU_CONFIG_DIR", raising=False)
    monkeypatch.setenv("FREE_AGENTS_CONFIG_DIR", str(tmp_path / "fa-config"))


@pytest.fixture(autouse=True)
def _reassert_live_state_fences(monkeypatch):
    """F1: re-assert the run-level fences for EVERY test — a test that sets an override on
    os.environ directly and pops it on cleanup (test_cycle_ui_0554.py BackendUpgradeToggles,
    and the HF tests popping LA_HF_PARAMS_CACHE / LA_HF_API_BASE / LA_HF_ROUTER_URL) would
    otherwise leave the fence unset for all later tests in the same xdist worker. The HF
    fences matter because the session ladder resolves a snapshot-unknown context through
    them (provider_limits._with_runtime_facts "hf" layer); without the fence it reads the
    REAL warm ~/.cache/free-agents/hf-params.json and a ladder test flakes (0.64.2 gate,
    gw11: test_unknown_context_skips_with_reason). Proven by
    tests/test_live_state_fences_reassert.py (red before this fixture covered them)."""
    monkeypatch.setenv("LA_LAST_EFFORT_FILE", _LAST_EFFORT_FENCE)
    monkeypatch.setenv("LA_BACKEND_UPGRADES_STATE", _BACKEND_UPGRADES_FENCE)
    monkeypatch.setenv("LA_HF_PARAMS_CACHE", _HF_PARAMS_FENCE)
    monkeypatch.setenv("LA_HF_API_BASE", _HF_API_BASE_FENCE)
    monkeypatch.setenv("LA_HF_ROUTER_URL", _HF_ROUTER_FENCE)


def _snapshot():
    try:
        return _REAL_SETTINGS.read_bytes()
    except OSError:
        return None


def _load_settings_guard():
    # Loaded BY FILE PATH and registered as `settings_guard`: when pytest imports this conftest,
    # tests/ is not on sys.path (ModuleNotFoundError), and `from conftest import ...` is ambiguous
    # (tests/live_proxy/conftest.py). Tests then `import settings_guard` and get this same module.
    import importlib.util
    import sys as _sys
    spec = importlib.util.spec_from_file_location(
        "settings_guard", Path(__file__).resolve().parent / "settings_guard.py")
    assert spec is not None and spec.loader is not None, "tests/settings_guard.py is missing"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _sys.modules.setdefault("settings_guard", mod)
    return _sys.modules["settings_guard"]


changed_setting_keys = _load_settings_guard().changed_setting_keys


_BEFORE = _snapshot()

# F1.2: byte-snapshot the checkout's own config/remote-last-effort.local.json at import;
# pytest_sessionfinish FAILS the run if it changed during the run (that file is
# bin/last-effort.py's default target when LA_LAST_EFFORT_FILE is unset).
_REPO_EFFORT_FILE = Path(__file__).resolve().parents[1] / "config" / "remote-last-effort.local.json"


def _snapshot_repo_effort():
    try:
        return _REPO_EFFORT_FILE.read_bytes()
    except OSError:
        return None


_REPO_EFFORT_BEFORE = _snapshot_repo_effort()
_REPO_EFFORT_EXISTED_AT_IMPORT = _REPO_EFFORT_FILE.exists()


def pytest_sessionfinish(session, exitstatus):
    after = _snapshot()
    if after != _BEFORE:
        # REPORT, never fail: every test is sandboxed (fixture above), so a change to the real
        # file during the run is almost always the user or a live Claude Code session. Naming
        # the keys lets a real leak still be spotted at a glance.
        keys = changed_setting_keys(_BEFORE, after)
        print(f"\nNOTE: the real {_REAL_SETTINGS} changed during this run "
              f"(keys: {', '.join(keys) or 'formatting only'}). Tests run with CLAUDE_CONFIG_DIR "
              "sandboxed, so this is most likely your own edit or a live Claude Code session; "
              "it does not fail the run.")
    if _snapshot_repo_effort() != _REPO_EFFORT_BEFORE:
        if _REPO_EFFORT_EXISTED_AT_IMPORT:
            # REPORT, never fail (same policy as the settings.json NOTE above): the file
            # existed at import, so in the CANONICAL checkout it is the user's live effort
            # store (config/*.local.* is gitignored) and a live csl may legitimately
            # rewrite it mid-run.
            print(f"\nNOTE: {_REPO_EFFORT_FILE} changed during this run, but it existed at "
                  "import — in the canonical checkout that is the live effort store and a "
                  "live csl is the benign writer; not failing the run.")
        else:
            # FAIL: the file did not exist at import, so a change means a test created it —
            # a leak into the checkout's config/ (bin/last-effort.py's default target).
            print(f"\nFAILED: {_REPO_EFFORT_FILE} changed during this run — a test wrote the "
                  "checkout's config/ effort store; conftest forces LA_LAST_EFFORT_FILE to a "
                  "sandbox (F1.2).")
            if session.exitstatus == 0:
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
    installed_marker = os.sep + "plugins" + os.sep   # the install cache; survives the 160-char CALLER cut
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
    # F1 ruling: install/ too — APPENDED so it never shadows bin/ or test modules.
    # tests/test_suite_fences.py loads install/backend_upgrades.py by file path and its
    # `import legacy_venv_migration` only resolves with install/ on sys.path; without
    # this the fence tests flake with ModuleNotFoundError on any xdist worker that did
    # not also import a test module inserting install/ itself.
    sys.path.append(str(Path(__file__).resolve().parents[1] / "install"))
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
    _real_limiter_dir = (Path.home() / ".claude" / "free-agents" / "limiter").resolve()

    def _guard_path(self):
        p = Path(self.path).resolve()
        if p == _real_throttle or p.parent == _real_limiter_dir:
            raise AssertionError(
                f"test opened the REAL limiter state {self.path} — LA_NVIDIA_THROTTLE_STATE / "
                "FA_LIMITER_DIR are forced to sandboxes by conftest; pass an explicit temp path")

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

    # F1.3 tripwire: any in-process write that still resolves to the REAL
    # ~/.claude/free-agents/backend-upgrades.json (or its .tmp) fails the test BEFORE the
    # file is touched. install/backend_upgrades.save_state() writes p.with_suffix(".tmp")
    # via Path.write_text then Path.replace; tests load fresh module copies, so the Path
    # methods are the one choke point every in-process writer shares. Both wrappers carry
    # the _fa_real_state_tripwire marker (tests/test_suite_fences.py asserts it).
    _real_upgrades = (Path.home() / ".claude" / "free-agents" / "backend-upgrades.json").resolve()
    _real_upgrades_tmp = _real_upgrades.with_suffix(".tmp")
    _orig_path_write_text = Path.write_text
    _orig_path_replace = Path.replace

    def _guarded_write_text(self, data, *args, **kwargs):
        p = Path(self).resolve()
        if p == _real_upgrades or p == _real_upgrades_tmp:
            raise AssertionError(
                f"test wrote the REAL backend-upgrades state {self} — conftest forces "
                "LA_BACKEND_UPGRADES_STATE to a sandbox; pass an explicit temp path")
        return _orig_path_write_text(self, data, *args, **kwargs)

    def _guarded_replace(self, target, *args, **kwargs):
        if Path(self).resolve() in (_real_upgrades, _real_upgrades_tmp) or \
                Path(target).resolve() in (_real_upgrades, _real_upgrades_tmp):
            raise AssertionError(
                f"test replaced the REAL backend-upgrades state {self} -> {target} — conftest "
                "forces LA_BACKEND_UPGRADES_STATE to a sandbox; pass an explicit temp path")
        return _orig_path_replace(self, target, *args, **kwargs)

    _guarded_write_text._fa_real_state_tripwire = True
    _guarded_replace._fa_real_state_tripwire = True
    Path.write_text = _guarded_write_text
    Path.replace = _guarded_replace

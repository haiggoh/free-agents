#!/usr/bin/env python3
"""Tests for bin/rate_limiter.py -- the machine-wide NVIDIA sliding-window limiter.

The property that matters is INTER-PROCESS: N separate processes sharing one state file must
never take more slots than the window allows. The race test freezes the clock (no refill) so
the expected total is exact, and widens the read->write window so a missing lock is caught
reliably rather than by luck. The mutation check proves the test can fail.
"""
import importlib.util
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("rate_limiter", REPO / "bin" / "rate_limiter.py")
rl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rl)

FROZEN = 1_000_000.0


def _worker(path, attempts, disable_lock, out):
    if disable_lock:
        rl.fcntl.flock = lambda fd, op: None          # the mutation under test
    bucket = rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: FROZEN)
    orig_read = bucket._read

    def slow_read(fd, now):                            # widen the race window
        result = orig_read(fd, now)
        time.sleep(0.002)
        return result

    bucket._read = slow_read
    got = sum(1 for _ in range(attempts) if bucket.try_acquire() == 0.0)
    out.put(got)


def _run(disable_lock):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "state")
        rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: FROZEN).status()  # create, empty
        q = mp.Queue()
        procs = [mp.Process(target=_worker, args=(path, 30, disable_lock, q)) for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        return sum(q.get() for _ in procs)


def _admitted_in_first_minute(limiter, now):
    """Simulate greedy callers against a fake clock; count admissions in the first 60 s."""
    sent = 0
    while now[0] < 60.0:
        if limiter.try_acquire() == 0.0:
            sent += 1
        else:
            now[0] += 0.05
    return sent


class SlidingWindowTests(unittest.TestCase):
    def test_single_process_limit_then_wait_for_oldest(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = rl.SlidingWindowLimiter(os.path.join(d, "s"), rpm=40, clock=lambda: now[0])
            self.assertEqual([b.try_acquire() for _ in range(40)], [0.0] * 40)
            self.assertAlmostEqual(b.try_acquire(), 60.0, places=6)   # oldest leaves at +60 s
            now[0] += 60.0
            self.assertEqual(b.try_acquire(), 0.0)

    def test_never_more_than_limit_in_any_rolling_minute(self):
        # The 0.19.10 token bucket admitted 79 in the first minute (full start + refill).
        now = [0.0]
        with tempfile.TemporaryDirectory() as d:
            b = rl.SlidingWindowLimiter(os.path.join(d, "s"), rpm=40, clock=lambda: now[0])
            self.assertEqual(_admitted_in_first_minute(b, now), 40)

    def test_limit_is_env_tunable(self):
        now = [0.0]
        with tempfile.TemporaryDirectory() as d:
            os.environ["LA_NVIDIA_RPM"] = "30"
            try:
                b = rl.SlidingWindowLimiter(os.path.join(d, "s"), clock=lambda: now[0])
            finally:
                del os.environ["LA_NVIDIA_RPM"]
            self.assertEqual(_admitted_in_first_minute(b, now), 30)

    def test_429_pauses_every_process_sharing_the_file(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s")
            a = rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: now[0], cooldown=10)
            other = rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: now[0], cooldown=10)
            for _ in range(5):
                a.try_acquire()
            info = a.note_429()
            self.assertEqual(info["in_window"], 5)          # locates the real limit
            self.assertAlmostEqual(other.try_acquire(), 10.0, places=6)
            now[0] += 10.0
            self.assertEqual(other.try_acquire(), 0.0)

    def test_429_books_hidden_sdk_attempts(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = rl.SlidingWindowLimiter(os.path.join(d, "s"), rpm=40, clock=lambda: now[0])
            b.try_acquire()
            self.assertEqual(b.note_429(hidden_attempts=2)["in_window"], 3)
            now[0] += 61.0                                  # all three age out together
            self.assertEqual(b.status()["in_window"], 0)

    def test_429_honours_retry_after(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = rl.SlidingWindowLimiter(os.path.join(d, "s"), rpm=40, clock=lambda: now[0])
            self.assertEqual(b.note_429(retry_after=25)["pause"], 25.0)
            self.assertAlmostEqual(b.try_acquire(), 25.0, places=6)

    def test_old_token_bucket_state_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s")
            Path(p).write_text('{"tokens": 38.0, "stamp": 1.0, "capacity": 40.0}')
            b = rl.SlidingWindowLimiter(p, rpm=40, clock=lambda: FROZEN)
            self.assertEqual(b.try_acquire(), 0.0)

    def test_corrupt_state_starts_empty_not_wedged(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s")
            Path(p).write_text("{not json")
            b = rl.SlidingWindowLimiter(p, rpm=40, clock=lambda: FROZEN)
            self.assertEqual(b.try_acquire(), 0.0)

    def test_timeout_raises(self):
        with tempfile.TemporaryDirectory() as d:
            b = rl.SlidingWindowLimiter(os.path.join(d, "s"), rpm=40, clock=lambda: FROZEN)
            for _ in range(40):
                b.try_acquire()
            with self.assertRaises(rl.RateLimitTimeout):
                b.acquire(max_wait=0.5)

    def test_state_file_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s")
            rl.SlidingWindowLimiter(p).status()
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)

    def test_inter_process_never_overspends(self):
        self.assertEqual(_run(disable_lock=False), 40)

    def test_mutation_without_lock_overspends(self):
        # If this ever passes with the lock removed, the race test above proves nothing.
        self.assertGreater(_run(disable_lock=True), 40)


class SavedSettingsTests(unittest.TestCase):
    """The picker's rate-limiter screen persists settings; proxies must honour them.

    Precedence: explicit constructor arg > LA_NVIDIA_* env > saved picker setting > default.
    """
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("LA_NVIDIA_")}
        self.env["LA_SESSION_MENU_CONFIG_DIR"] = str(self.cfg)
        self.state = os.path.join(self.tmp.name, "state")

    def save(self, doc):
        (self.cfg / "session-menu.local.json").write_text(
            '{"schema_version": 1, "rate_limiter": %s}' % doc)

    def limiter(self, **env):
        with unittest.mock.patch.dict(os.environ, {**self.env, **env}, clear=True):
            return rl.get_limiter(self.state, clock=lambda: FROZEN)

    def test_saved_mode_and_rpm_are_used(self):
        # Fails if get_limiter only reads the environment (the old menu's settings vanished).
        self.save('{"mode": "smooth_bucket", "rpm": 30, "bucket_capacity": 4, "cooldown": 5}')
        lim = self.limiter()
        self.assertIsInstance(lim, rl.SmoothTokenBucket)
        self.assertAlmostEqual(lim.rate, 0.5)
        # The saved key "cooldown" is the BASE cooldown of main's 429 backoff.
        self.assertEqual((lim.capacity, lim.base_cooldown), (4, 5.0))

    def test_saved_backoff_settings_are_used(self):
        # Fails if the backoff keys (0.20.11) bypass _setting() and read only the environment.
        self.save('{"max_cooldown": 600, "backoff_multiplier": 3, "max_retries": 7}')
        for lim in (self.limiter(), self.limiter(LA_NVIDIA_MODE="smooth_bucket")):
            self.assertEqual((lim.max_cooldown, lim.backoff_multiplier, lim.max_retries),
                             (600.0, 3.0, 7))
        lim = self.limiter(LA_NVIDIA_429_MAX_RETRIES="2")
        self.assertEqual(lim.max_retries, 2)

    def test_backoff_defaults_follow_main(self):
        lim = self.limiter()
        self.assertEqual((lim.base_cooldown, lim.max_cooldown, lim.backoff_multiplier,
                          lim.max_retries, rl._max_wait(None)), (30.0, 300.0, 2.0, 5, 300.0))

    def test_environment_still_wins(self):
        self.save('{"mode": "smooth_bucket", "rpm": 30}')
        lim = self.limiter(LA_NVIDIA_MODE="sliding_window", LA_NVIDIA_RPM="20")
        self.assertIsInstance(lim, rl.SlidingWindowLimiter)
        self.assertEqual(lim.limit, 20)

    def test_absent_or_broken_file_falls_back_to_defaults(self):
        self.assertEqual(self.limiter().limit, 40)
        (self.cfg / "session-menu.local.json").write_text("{broken")
        self.assertIsInstance(self.limiter(), rl.SlidingWindowLimiter)

    def test_max_wait_uses_saved_value(self):
        self.save('{"max_wait": 30}')
        with unittest.mock.patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(rl._max_wait(None), 30.0)


class PerModelCooldownTests(unittest.TestCase):
    """Phase 2 (0.40.0): a 429 with a NEAR-EMPTY window is NVIDIA's per-MODEL throttle (GLM
    answered 429 all night while DeepSeek on the same key served 46/46), not our RPM overrun
    -- it cools only that model and leaves every other proxy alone. A real overrun (window at
    least half full, or model unknown) keeps today's machine-wide pause. The backoff counter
    grows across EXPIRED pauses (reset on success, never on expiry) so a model throttled for
    hours backs off 30 -> 60 -> 120s instead of being retried twice a minute forever."""

    def setUp(self):
        # The limiter reads the PICKER'S SAVED SETTINGS (user-level: ~/.claude/free-agents/
        # session-menu) for every knob the constructor does not take. Without this
        # isolation the tests assert whatever the USER'S machine currently has saved --
        # a live cooldown=10 / multiplier=1.5 made the 30/60/120 growth expectations read
        # as failures (2026-10-22). Strip the LA_NVIDIA_* env and point the config dir at
        # an empty tempdir: defaults everywhere.
        import unittest.mock
        env = {k: v for k, v in os.environ.items() if not k.startswith("LA_NVIDIA_")}
        cfg_dir = tempfile.TemporaryDirectory()
        self.addCleanup(cfg_dir.cleanup)
        env["LA_SESSION_MENU_CONFIG_DIR"] = cfg_dir.name
        patcher = unittest.mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def limiter(self, path, now, **kw):
        return rl.SlidingWindowLimiter(path, rpm=40, clock=lambda: now[0], **kw)

    def test_near_empty_window_cools_only_that_model(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s")
            a = self.limiter(path, now)
            other = self.limiter(path, now)
            a.try_acquire()
            a.try_acquire()
            info = a.note_429(model="nvidia_nim/z-ai/glm-5.3-flash")
            self.assertEqual(info["kind"], "model_throttle")
            self.assertEqual(info["consecutive_429s"], 1)
            self.assertEqual(other.try_acquire(), 0.0)      # NO global pause
            self.assertGreater(a.model_cooling("nvidia_nim/z-ai/glm-5.3-flash"), 29.0)

    def test_half_full_window_is_still_machine_wide(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s")
            a = self.limiter(path, now, cooldown=10)
            other = self.limiter(path, now)
            for _ in range(20):
                a.try_acquire()
            info = a.note_429(model="nvidia_nim/z-ai/glm-5.3-flash")
            self.assertEqual(info["kind"], "rpm_overrun")
            self.assertGreater(other.try_acquire(), 0.0)     # every proxy pauses

    def test_model_backoff_grows_across_expired_pauses(self):
        # The old try_acquire reset consecutive_429s to 0 the moment a pause EXPIRED, before
        # the next request had even failed: four 429s in a row each read "pause 30s, n=1"
        # (measured 2026-10-21), so a model throttled for hours was retried ~2x/min forever.
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = self.limiter(os.path.join(d, "s"), now)
            m = "nvidia_nim/z-ai/glm-5.3-flash"
            self.assertAlmostEqual(b.note_429(model=m)["pause"], 30.0)
            now[0] += 31.0
            self.assertEqual(b.model_cooling(m), 0.0)        # expired: one probe request passes
            self.assertAlmostEqual(b.note_429(model=m)["pause"], 60.0)   # 2nd consecutive
            now[0] += 61.0
            self.assertAlmostEqual(b.note_429(model=m)["pause"], 120.0)  # 3rd consecutive

    def test_global_backoff_survives_an_expired_pause_while_the_window_stays_full(self):
        # The expiry-reset the 0.40.0 fix removed: a GLOBAL pause lapses but the window is
        # still full (a real overrun), so the acquire FAILS -- the old code zeroed the
        # backoff counter anyway and the next 429 restarted at 30 s forever.
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = self.limiter(os.path.join(d, "s"), now)
            for _ in range(40):
                b.try_acquire()                             # window full
            self.assertAlmostEqual(b.note_429()["pause"], 30.0)
            now[0] += 31.0                                  # the pause expires...
            self.assertGreater(b.try_acquire(), 0.0)         # ...window still full: no slot
            self.assertAlmostEqual(b.note_429()["pause"], 60.0)      # 2nd consecutive, not reset

    def test_model_success_clears_the_entry_and_restarts_the_backoff(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = self.limiter(os.path.join(d, "s"), now)
            m = "nvidia_nim/z-ai/glm-5.3-flash"
            b.note_429(model=m)
            b.note_success(m)
            self.assertEqual(b.model_cooling(m), 0.0)
            self.assertAlmostEqual(b.note_429(model=m)["pause"], 30.0)   # base again

    def test_request_success_resets_the_global_counter_too(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = self.limiter(os.path.join(d, "s"), now)
            b.note_429()                                     # model unknown: machine-wide
            self.assertEqual(b.status()["consecutive_429s"], 1)
            b.note_success("nvidia_nim/z-ai/glm-5.3-flash")  # a success resets the global count
            self.assertEqual(b.status()["consecutive_429s"], 0)

    def test_retry_after_wins_for_a_model_throttle_too(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = self.limiter(os.path.join(d, "s"), now)
            m = "nvidia_nim/z-ai/glm-5.3-flash"
            self.assertAlmostEqual(b.note_429(model=m, retry_after=25)["pause"], 25.0)
            self.assertAlmostEqual(b.model_cooling(m), 25.0, places=6)


class PerModelCooldownBucketTests(PerModelCooldownTests):
    """The same contract for the smooth_bucket mode. Before this, the bucket ignored the
    model and model_cooling was always 0 -- and the picker can SAVE smooth_bucket (the
    maintainer's machine had), so the whole per-model cooldown was inert for that user
    (found 2026-10-22). "Near-empty window" maps to "bucket more than half full"."""

    def limiter(self, path, now, **kw):
        return rl.SmoothTokenBucket(path, rpm=kw.pop("rpm", 40), clock=lambda: now[0], **kw)

    def test_global_backoff_survives_an_expired_pause_while_the_window_stays_full(self):
        # Bucket shape of the same defect: a 1 RPM bucket drained, the pause lapses while
        # the bucket is still short of a token, the acquire FAILS -- that must not reset the
        # counter (the old try_acquire zeroed it on expiry alone).
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = self.limiter(os.path.join(d, "s"), now, rpm=1, capacity=1)
            b.try_acquire()                                 # bucket empty
            self.assertAlmostEqual(b.note_429()["pause"], 30.0)
            now[0] += 31.0                                  # pause over, ~0.5 token back
            self.assertGreater(b.try_acquire(), 0.0)         # still no token
            self.assertAlmostEqual(b.note_429()["pause"], 60.0)      # 2nd consecutive, not reset

    def test_status_lists_cooling_models(self):
        now = [FROZEN]
        with tempfile.TemporaryDirectory() as d:
            b = self.limiter(os.path.join(d, "s"), now)
            b.note_429(model="nvidia_nim/z-ai/glm-5.3")
            self.assertEqual(list(b.status()["cooling_models"]), ["nvidia_nim/z-ai/glm-5.3"])


class OwnerAttributionPriorityTests(unittest.TestCase):
    """0.50.1: stamps carry their owner; a reserved priority fraction; per-owner live
    throttle/prioritize via set-owner; the probe that separates 'concurrent sessions ate
    my bucket' from a real defect."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state")
        self.env = unittest.mock.patch.dict(os.environ, {
            "LA_LIMITER_OWNER": "sess-A", "LA_LIMITER_RESERVE": "0.2"})
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop("LA_LIMITER_PRIORITY", None)

    def _state(self):
        with open(self.path) as f:
            return json.load(f)

    def test_stamps_carry_owner_and_legacy_floats_still_load(self):
        b = rl.SlidingWindowLimiter(self.path, rpm=40, clock=lambda: FROZEN)
        self.assertEqual(b.try_acquire(), 0.0)
        stamps = self._state()["stamps"]
        self.assertEqual(stamps[0]["o"], "sess-A", "new stamps are attributed")
        # a pre-0.50.1 file (bare float stamps, no owners key) still loads and counts
        with open(self.path, "w") as f:
            json.dump({"stamps": [FROZEN - 1], "limit": 40}, f)
        b2 = rl.SlidingWindowLimiter(self.path, rpm=40, clock=lambda: FROZEN)
        self.assertEqual(b2.status()["in_window"], 1)
        self.assertEqual(b2.try_acquire(), 0.0)
        self.assertEqual(self._state()["stamps"][-1]["o"], "sess-A",
                         "a fresh stamp is attributed even when legacy ones were floats")

    def test_bucket_reserve_is_a_cap_not_a_partition(self):
        # capacity 6 with LA_LIMITER_RESERVE=0.2 (setUp): the reserve no longer withholds
        # tokens — normal traffic fills the whole bucket when nobody is queued
        b = rl.SmoothTokenBucket(self.path, rpm=1, capacity=6, clock=lambda: FROZEN)
        ok = sum(1 for _ in range(10) if b.try_acquire() == 0.0)
        self.assertEqual(ok, 6, "no token partition: normal fills the bucket")

    def test_reserve_no_longer_partitions_normal_traffic(self):
        # LA_LIMITER_RESERVE=0.2 (setUp) is now the lane SHARE CAP, not a partition: with an
        # empty queue, normal traffic uses the full window — zero idle capacity.
        b = rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: FROZEN)
        ok = sum(1 for _ in range(6) if b.try_acquire() == 0.0)
        self.assertEqual(ok, 4, "no partition: normal uses the full limit when the queue is empty")

    def test_per_owner_scale_still_caps_own_window_count(self):
        rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: FROZEN).status()
        b = rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: FROZEN)
        rl.set_owner(b, "sess-A", priority=None, rpm_scale=0.5)
        self.assertEqual(b.try_acquire(), 0.0)
        self.assertEqual(b.try_acquire(), 0.0)
        self.assertGreater(b.try_acquire(), 0.0, "own scale 0.5 caps at 2 of 4")

    def _seed_queue(self, stamps, waiters, owners=None, limit=4):
        doc = {"stamps": stamps, "limit": limit, "waiters": waiters}
        if owners:
            doc["owners"] = owners
        with open(self.path, "w") as f:
            json.dump(doc, f)

    def _w(self, token, owner, arrived, cls="normal", kind="interactive"):
        return {token: {"owner": owner, "arrived": arrived, "last_seen": arrived,
                        "class": cls, "kind": kind, "model": ""}}

    def test_priority_waiter_jumps_normal_queue(self):
        now = [FROZEN]
        tN = rl._waiter_token("sess-B")
        tP = rl._waiter_token("sess-A")
        self._seed_queue([{"t": FROZEN, "o": "sess-B"}] * 3,
                         {**self._w(tN, "sess-B", FROZEN),
                          # registered BEFORE the lane was granted (class still "normal"):
                          # the live owners map must refresh the class at grant time
                          **self._w(tP, "sess-A", FROZEN + 1, cls="normal")},
                         owners={"sess-A": {"priority": True}})
        b = rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: now[0])
        with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": "sess-C"}):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            st = self._state()
            self.assertEqual(st["stamps"][-1]["o"], "sess-A", "priority lane jumps the queue")
            self.assertIn("granted", st["waiters"][tP])

    def test_lane_cap_binds_under_contention(self):
        # limit 40, cap 0.2 -> 8: with 8 priority stamps in the window AND a normal waiter
        # present, the next grant must go to the normal waiter
        now = [FROZEN]
        tP = rl._waiter_token("sess-A")
        tN = rl._waiter_token("sess-B")
        self._seed_queue([{"t": FROZEN, "o": "sess-A"}] * 8 + [{"t": FROZEN, "o": "sess-B"}],
                         {**self._w(tP, "sess-A", FROZEN, cls="priority"),
                          **self._w(tN, "sess-B", FROZEN + 1)},
                         owners={"sess-A": {"priority": True}}, limit=40)
        b = rl.SlidingWindowLimiter(self.path, rpm=40, clock=lambda: now[0])
        with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": "sess-C"}):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            st = self._state()
            self.assertEqual(st["stamps"][-1]["o"], "sess-B", "cap reached: grant goes to normal")
            self.assertNotIn("granted", st["waiters"][tP])

    def test_lane_cap_free_when_no_normal_waiters(self):
        # same 8 priority stamps, but the only waiter is priority: the cap must NOT bind
        # (no normal waiter) — idle capacity never occurs
        now = [FROZEN]
        tP = rl._waiter_token("sess-A")
        self._seed_queue([{"t": FROZEN, "o": "sess-A"}] * 8,
                         {**self._w(tP, "sess-A", FROZEN, cls="priority")},
                         owners={"sess-A": {"priority": True}}, limit=40)
        b = rl.SlidingWindowLimiter(self.path, rpm=40, clock=lambda: now[0])
        with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": "sess-C"}):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            st = self._state()
            self.assertEqual(st["stamps"][-1]["o"], "sess-A", "no normal waiters: priority uncapped")
            self.assertIn("granted", st["waiters"][tP])

    def test_probe_counts_only_other_owners(self):
        b = rl.SlidingWindowLimiter(self.path, rpm=40, clock=lambda: FROZEN)
        b.try_acquire()                                    # sess-A
        with unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": "sess-B"}):
            b2 = rl.SlidingWindowLimiter(self.path, rpm=40, clock=lambda: FROZEN)
            b2.try_acquire()                               # sess-B
            self.assertEqual(rl.external_occupancy(b2, "sess-B"), 1, "sess-A's slot is external")
            self.assertEqual(rl.external_occupancy(b2, "sess-A"), 1, "sess-B's slot is external to A")
            self.assertEqual(rl.external_occupancy(b2, "nobody"), 2)

    def test_cli_set_owner_and_owners(self):
        with unittest.mock.patch.dict(os.environ, {"LA_NVIDIA_THROTTLE_STATE": self.path}):
            rl.SlidingWindowLimiter(self.path, rpm=40, clock=lambda: FROZEN).status()
            rc = rl.main(["set-owner", "sess-B", "--priority", "--rpm-scale", "0.5",
                          "--user-approved"])
            self.assertEqual(rc, 0)
            owners = self._state().get("owners", {})
            self.assertEqual(owners["sess-B"]["priority"], True)
            self.assertEqual(owners["sess-B"]["rpm_scale"], 0.5)
            self.assertEqual(owners["sess-B"].get("approved_by_user"), True)

    def test_cli_set_owner_kind(self):
        with unittest.mock.patch.dict(os.environ, {"LA_NVIDIA_THROTTLE_STATE": self.path}):
            rl.SlidingWindowLimiter(self.path, rpm=40, clock=lambda: FROZEN).status()
            rc = rl.main(["set-owner", "sess-B", "--kind", "dispatch"])
            self.assertEqual(rc, 0)
            self.assertEqual(self._state()["owners"]["sess-B"]["kind"], "dispatch")

    def test_lane_approval_refuses_nontty_without_user_approved(self):
        # stdin is not a TTY under pytest: an agent-initiated lane must be refused (rc != 0)
        with unittest.mock.patch.dict(os.environ, {"LA_NVIDIA_THROTTLE_STATE": self.path}):
            with open(self.path, "w") as f:
                json.dump({"stamps": [], "limit": 40}, f)
            rl.SlidingWindowLimiter(self.path, rpm=40, clock=lambda: FROZEN).status()
            rc = rl.main(["set-owner", "sess-B", "--priority"])
            self.assertNotEqual(rc, 0, "non-TTY without --user-approved is refused")
            self.assertNotIn("sess-B", self._state().get("owners", {}))
            os.environ["LA_LIMITER_LANE_APPROVAL"] = "agent"
            try:
                rc = rl.main(["set-owner", "sess-B", "--priority"])
                self.assertEqual(rc, 0, "lane_approval=agent skips the gate")
                self.assertEqual(self._state()["owners"]["sess-B"]["priority"], True)
            finally:
                del os.environ["LA_LIMITER_LANE_APPROVAL"]


class WaiterQueueTests(unittest.TestCase):
    """Fairness queue (design 2026-11-19, sections 4.0/4.1): waiting becomes visible in the
    state file (`waiters`), grants are ranked under the lock, a fresh arrival does not barge
    an occupied queue, and an empty queue keeps today's exact behavior."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("LA_NVIDIA_") and not k.startswith("LA_LIMITER_")}
        cfg = tempfile.TemporaryDirectory()
        self.addCleanup(cfg.cleanup)
        env["LA_SESSION_MENU_CONFIG_DIR"] = cfg.name
        patcher = unittest.mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _state(self):
        with open(self.path) as f:
            return json.load(f)

    def _as(self, owner):
        return unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": owner})

    def _limiter(self, now, rpm=4):
        return rl.SlidingWindowLimiter(self.path, rpm=rpm, clock=lambda: now[0])

    def test_no_barging_fresh_arrival_registers_when_queue_non_empty(self):
        now = [FROZEN]
        os.environ["LA_NVIDIA_WAITER_TTL"] = "120"     # window is 60s; keep the waiter fresh past it
        b = self._limiter(now)
        with self._as("sess-A"):
            self.assertEqual([b.try_acquire() for _ in range(4)], [0.0] * 4)   # window full
            tA = rl._waiter_token("sess-A")
            self.assertGreater(b.try_acquire(token=tA), 0.0)                    # registers
        now[0] += 61.0                                                              # capacity free, waiter fresh
        with self._as("sess-B"):
            tB = rl._waiter_token("sess-B")
            self.assertGreater(b.try_acquire(token=tB), 0.0, "fresh arrival must not barge")
            st = self._state()
            self.assertEqual(st["stamps"][-1]["o"], "sess-A", "slot booked under the WINNER's owner")
            self.assertIn("granted", st["waiters"][tA])
            self.assertIn(tB, st["waiters"])
            self.assertEqual(b.try_acquire(token=tB), 0.0, "next poll: B is the only waiter")
        with self._as("sess-A"):
            self.assertEqual(b.try_acquire(token=tA), 0.0, "winner acks its granted entry")
            self.assertNotIn(tA, self._state()["waiters"])

    def test_own_owner_winner_takes_slot_for_caller(self):
        now = [FROZEN]
        os.environ["LA_NVIDIA_WAITER_TTL"] = "120"
        b = self._limiter(now)
        with self._as("sess-A"):
            for _ in range(4):
                b.try_acquire()
            tA = rl._waiter_token("sess-A")
            self.assertGreater(b.try_acquire(token=tA), 0.0)
        now[0] += 61.0
        with self._as("sess-A"):
            self.assertEqual(b.try_acquire(token=tA), 0.0)
            st = self._state()
            self.assertNotIn(tA, st["waiters"])
            self.assertEqual(st["stamps"][-1]["o"], "sess-A")

    def test_stale_waiter_ttl_prune(self):
        now = [FROZEN]
        b = self._limiter(now)
        with self._as("sess-A"):
            for _ in range(4):
                b.try_acquire()
            tA = rl._waiter_token("sess-A")
            self.assertGreater(b.try_acquire(token=tA), 0.0)
        now[0] += 16.0                      # > default ttl 15; window (60s) still full
        with self._as("sess-B"):
            tB = rl._waiter_token("sess-B")
            self.assertGreater(b.try_acquire(token=tB), 0.0)
            st = self._state()
            self.assertNotIn(tA, st["waiters"], "stale waiter pruned")
            self.assertIn(tB, st["waiters"])

    def test_heartbeat_clamp_survives_900s_cooldown(self):
        now = [FROZEN]
        b = self._limiter(now)
        with self._as("sess-A"):
            for _ in range(4):
                b.try_acquire()
            tA = rl._waiter_token("sess-A")
            self.assertGreater(b.try_acquire(token=tA), 0.0)
            b.note_429(retry_after=900.0)                       # machine-wide 15 min pause
            for _ in range(10):                                  # 20 s of polling, past ttl=15
                need = b.try_acquire(token=tA)
                self.assertGreater(need, 0.0)
                now[0] += min(need, 2.0)                         # clamped heartbeat
            st = self._state()
            self.assertIn(tA, st["waiters"], "heartbeats keep the waiter alive past the TTL")
            w = st["waiters"][tA]
            self.assertEqual(w["arrived"], FROZEN, "arrived never resets on re-register")
            self.assertEqual(w["last_seen"], now[0] - 2.0, "every poll refreshes last_seen")
        now[0] += 900.0
        with self._as("sess-A"):
            self.assertEqual(b.try_acquire(token=tA), 0.0, "cooldown over: the aged waiter wins")

    def test_acquire_clamps_sleeps_to_poll_and_serves_the_waiter(self):
        now = [FROZEN]
        b = self._limiter(now)
        with self._as("sess-A"):
            for _ in range(4):
                b.try_acquire()
        sleeps = []

        def fake_sleep(s):
            sleeps.append(s)
            now[0] += s

        with self._as("sess-A"), unittest.mock.patch.object(rl.time, "sleep", fake_sleep):
            waited = b.acquire(max_wait=300.0)
        self.assertGreater(waited, 0.0)
        self.assertLessEqual(max(sleeps), 2.0 + 1e-9, "every sleep clamped to waiter_poll_seconds")
        self.assertEqual(self._state().get("waiters", {}), {})

    def test_timeout_deregisters_waiter(self):
        now = [FROZEN]
        b = self._limiter(now)
        with self._as("sess-A"):
            for _ in range(4):
                b.try_acquire()
            with self.assertRaises(rl.RateLimitTimeout):
                b.acquire(max_wait=0.5)
            self.assertEqual(self._state().get("waiters", {}), {}, "deregistered on timeout")

    def test_legacy_policy_keeps_tokenless_semantics(self):
        now = [FROZEN]
        os.environ["LA_NVIDIA_POLICY"] = "legacy"
        os.environ["LA_NVIDIA_WAITER_TTL"] = "120"
        b = self._limiter(now)
        with self._as("sess-A"):
            for _ in range(4):
                b.try_acquire()
            tA = rl._waiter_token("sess-A")
            self.assertGreater(b.try_acquire(token=tA), 0.0)
        now[0] += 61.0
        with self._as("sess-B"):
            tB = rl._waiter_token("sess-B")
            self.assertEqual(b.try_acquire(token=tB), 0.0, "legacy: a capable caller barges (today)")
            st = self._state()
            self.assertEqual(st["stamps"][-1]["o"], "sess-B")
            self.assertNotIn("granted", st["waiters"].get(tA, {}))

    def test_tokenless_use_never_touches_the_registry(self):
        now = [FROZEN]
        b = self._limiter(now)
        with self._as("sess-A"):
            self.assertEqual([b.try_acquire() for _ in range(4)], [0.0] * 4)
            self.assertGreater(b.try_acquire(), 0.0)
        self.assertEqual(self._state().get("waiters", {}), {},
                         "tokenless callers leave no waiter entries")

    def test_state_without_waiters_key_loads(self):
        now = [FROZEN]
        with open(self.path, "w") as f:
            json.dump({"stamps": [{"t": FROZEN - 1, "o": "legacy"}], "limit": 4}, f)
        b = self._limiter(now)
        with self._as("sess-A"):
            self.assertEqual(b.try_acquire(), 0.0)          # tokenless parity on a pre-change file
            for _ in range(2):
                self.assertEqual(b.try_acquire(), 0.0)      # window now full (1 legacy + 1 + 2)
            tA = rl._waiter_token("sess-A")
            self.assertGreater(b.try_acquire(token=tA), 0.0)
            self.assertIn(tA, self._state()["waiters"])

    # --- Task 2: aging tiers, tier-0-only dispatch boost, rpm_scale eligibility ---

    def _seed(self, stamps, waiters, owners=None):
        doc = {"stamps": stamps, "limit": 4, "waiters": waiters}
        if owners:
            doc["owners"] = owners
        with open(self.path, "w") as f:
            json.dump(doc, f)

    def _waiter(self, token, owner, arrived, kind="interactive", cls="normal", last_seen=None):
        return {token: {"owner": owner, "arrived": arrived,
                        "last_seen": arrived if last_seen is None else last_seen,
                        "class": cls, "kind": kind, "model": ""}}

    def test_dispatch_boost_within_tier0(self):
        now = [FROZEN]
        os.environ["LA_NVIDIA_TIER1_SECONDS"] = "600"     # keep both waiters in tier 0
        os.environ["LA_NVIDIA_TIER2_SECONDS"] = "3600"
        os.environ["LA_NVIDIA_WAITER_TTL"] = "300"
        tA = rl._waiter_token("sess-A")
        tB = rl._waiter_token("sess-B")
        self._seed([{"t": FROZEN, "o": "sess-A"}] * 3,          # one slot free
                   {**self._waiter(tA, "sess-A", FROZEN),
                    **self._waiter(tB, "sess-B", FROZEN + 1, kind="dispatch")})
        b = self._limiter(now)
        with self._as("sess-C"):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            st = self._state()
            self.assertEqual(st["stamps"][-1]["o"], "sess-B",
                             "tier-0 dispatch outranks the earlier interactive")
            self.assertIn("granted", st["waiters"][tB])

    def test_dispatch_boost_off_falls_back_to_fifo(self):
        now = [FROZEN]
        os.environ["LA_NVIDIA_TIER1_SECONDS"] = "600"
        os.environ["LA_NVIDIA_TIER2_SECONDS"] = "3600"
        os.environ["LA_NVIDIA_WAITER_TTL"] = "300"
        os.environ["LA_NVIDIA_DISPATCH_BOOST"] = "0"
        tA = rl._waiter_token("sess-A")
        tB = rl._waiter_token("sess-B")
        self._seed([{"t": FROZEN, "o": "sess-A"}] * 3,
                   {**self._waiter(tA, "sess-A", FROZEN),
                    **self._waiter(tB, "sess-B", FROZEN + 1, kind="dispatch")})
        b = self._limiter(now)
        with self._as("sess-C"):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            self.assertEqual(self._state()["stamps"][-1]["o"], "sess-A",
                             "boost off: FIFO, the earlier interactive wins")

    def test_aged_tier1_beats_tier0_dispatch(self):
        now = [FROZEN + 61]                                   # default tier1 = 45s
        os.environ["LA_NVIDIA_WAITER_TTL"] = "300"
        tA = rl._waiter_token("sess-A")
        tB = rl._waiter_token("sess-B")
        self._seed([], {**self._waiter(tA, "sess-A", FROZEN),          # waited 61s -> tier 1
                        **self._waiter(tB, "sess-B", FROZEN + 60, kind="dispatch")})  # tier 0
        b = self._limiter(now)
        with self._as("sess-C"):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            self.assertEqual(self._state()["stamps"][-1]["o"], "sess-A",
                             "an aged tier-1 request outranks a tier-0 dispatch")

    def test_tier2_outranks_tier1(self):
        now = [FROZEN + 200]                                  # tier1=45, tier2=150
        tA = rl._waiter_token("sess-A")
        tB = rl._waiter_token("sess-B")
        # both heartbeated just now (ttl max is 120), but arrived long ago
        self._seed([], {**self._waiter(tA, "sess-A", FROZEN, last_seen=now[0]),          # tier 2
                        **self._waiter(tB, "sess-B", FROZEN + 150, last_seen=now[0])})    # tier 1
        b = self._limiter(now)
        with self._as("sess-C"):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            self.assertEqual(self._state()["stamps"][-1]["o"], "sess-A")

    def test_rpm_scale_owner_skipped_when_at_cap(self):
        now = [FROZEN]
        os.environ["LA_NVIDIA_WAITER_TTL"] = "300"
        tA = rl._waiter_token("sess-A")
        tB = rl._waiter_token("sess-B")
        self._seed([{"t": FROZEN, "o": "sess-A"}] * 2,         # window has room (2 < 4) ...
                   {**self._waiter(tA, "sess-A", FROZEN),
                    **self._waiter(tB, "sess-B", FROZEN)},
                   owners={"sess-A": {"rpm_scale": 0.5}})     # ... but A is at its cap of 2
        b = self._limiter(now)
        with self._as("sess-C"):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            st = self._state()
            self.assertEqual(st["stamps"][-1]["o"], "sess-B",
                             "the capped owner is skipped; the slot goes to the next eligible")
            self.assertNotIn("granted", st["waiters"][tA])


class DeficitPolicyTests(unittest.TestCase):
    """Deficit policy (design 2026-11-19 section 4.1, opt-in via policy=deficit): rank by
    (class, debt, -arrived). An owner's debt = stored residual + sum over its waiting
    requests of (now-arrived)*deficit_rate, capped at deficit_cap_seconds; a grant spends
    the inter-slot credit (WINDOW/limit) and persists the remainder as the owner's residual."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("LA_NVIDIA_") and not k.startswith("LA_LIMITER_")}
        cfg = tempfile.TemporaryDirectory()
        self.addCleanup(cfg.cleanup)
        env["LA_SESSION_MENU_CONFIG_DIR"] = cfg.name
        patcher = unittest.mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ["LA_NVIDIA_POLICY"] = "deficit"

    def _state(self):
        with open(self.path) as f:
            return json.load(f)

    def _as(self, owner):
        return unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": owner})

    def _seed(self, stamps, waiters, owners=None, deficit=None, limit=4):
        doc = {"stamps": stamps, "limit": limit, "waiters": waiters}
        if owners:
            doc["owners"] = owners
        if deficit:
            doc["deficit"] = deficit
        with open(self.path, "w") as f:
            json.dump(doc, f)

    def _w(self, token, owner, arrived, last_seen):
        return {token: {"owner": owner, "arrived": arrived, "last_seen": last_seen,
                        "class": "normal", "kind": "interactive", "model": ""}}

    def test_owner_with_two_waiters_wins_over_earlier_single_waiter(self):
        # debt is summed over the owner's waiting requests: sess-A has 2 waiters (debt 200)
        # vs sess-B's single waiter (debt 150) — deficit picks A, FIFO would pick B
        now = [FROZEN + 100]
        tA1 = rl._waiter_token("sess-A")
        tA2 = rl._waiter_token("sess-A")
        tB = rl._waiter_token("sess-B")
        self._seed([{"t": FROZEN, "o": "sess-X"}] * 3,
                   {**self._w(tA1, "sess-A", FROZEN, now[0]),
                    **self._w(tA2, "sess-A", FROZEN, now[0]),
                    **self._w(tB, "sess-B", FROZEN - 50, now[0])})
        b = rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: now[0])
        with self._as("sess-C"):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            self.assertEqual(self._state()["stamps"][-1]["o"], "sess-A",
                             "deficit: the owner with more accumulated wait wins over FIFO")

    def test_debt_capped_at_deficit_cap_seconds(self):
        # sess-A raw debt 5000 (residual), sess-B raw debt 700; cap 600 -> both 600 -> tie
        # -> FIFO: the EARLIER arrival (sess-B) wins. Uncapped, sess-A would win.
        now = [FROZEN + 700]
        os.environ["LA_NVIDIA_DEFICIT_CAP_SECONDS"] = "600"
        tA = rl._waiter_token("sess-A")
        tB = rl._waiter_token("sess-B")
        self._seed([{"t": FROZEN, "o": "sess-X"}] * 3,
                   {**self._w(tA, "sess-A", now[0], now[0]),
                    **self._w(tB, "sess-B", FROZEN, now[0])},
                   deficit={"sess-A": 5000.0})
        b = rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: now[0])
        with self._as("sess-C"):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            self.assertEqual(self._state()["stamps"][-1]["o"], "sess-B",
                             "capped debts tie -> FIFO decides, not the bigger raw debt")

    def test_grant_spends_credit_and_persists_residual(self):
        # limit 4 -> inter-slot credit = 60/4 = 15s; sess-A debt = 200 residual + 100 waited
        # = 300 -> after the grant the residual must be 300 - 15 = 285
        now = [FROZEN + 100]
        tA = rl._waiter_token("sess-A")
        tB = rl._waiter_token("sess-B")
        self._seed([{"t": FROZEN, "o": "sess-X"}] * 3,
                   {**self._w(tA, "sess-A", FROZEN, now[0]),
                    **self._w(tB, "sess-B", FROZEN + 90, now[0])},
                   deficit={"sess-A": 200.0})
        b = rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: now[0])
        with self._as("sess-C"):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            st = self._state()
            self.assertEqual(st["stamps"][-1]["o"], "sess-A")
            self.assertAlmostEqual(st["deficit"]["sess-A"], 285.0, places=6,
                                   msg="grant spends the inter-slot credit, residual persists")

    def test_policy_switch_between_aging_and_deficit(self):
        # identical seed: under aging the earliest arrival wins; under deficit the max-debt
        # owner wins — the policy is a hot env/saved switch
        def run(policy):
            now = [FROZEN + 100]
            os.environ["LA_NVIDIA_POLICY"] = policy
            tA = rl._waiter_token("sess-A")
            tB = rl._waiter_token("sess-B")
            self._seed([{"t": FROZEN, "o": "sess-X"}] * 3,
                       {**self._w(tA, "sess-A", FROZEN, now[0]),
                        **self._w(tB, "sess-B", FROZEN - 50, now[0])},
                       deficit={"sess-A": 500.0})
            b = rl.SlidingWindowLimiter(self.path, rpm=4, clock=lambda: now[0])
            with self._as("sess-C"):
                tC = rl._waiter_token("sess-C")
                b.try_acquire(token=tC)
                return self._state()["stamps"][-1]["o"]
        self.assertEqual(run("aging"), "sess-B", "aging: FIFO, earliest arrival")
        self.assertEqual(run("deficit"), "sess-A", "deficit: residual + wait beats earlier arrival")


class BucketFairnessTests(unittest.TestCase):
    """Smooth bucket (the user's live mode, capacity 10) gets the same queue: no barging,
    the grant books tokens for the winner, the lane cap comes from the additive `grants`
    rolling list, and the reserve token-partition is retired."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "state")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("LA_NVIDIA_") and not k.startswith("LA_LIMITER_")}
        cfg = tempfile.TemporaryDirectory()
        self.addCleanup(cfg.cleanup)
        env["LA_SESSION_MENU_CONFIG_DIR"] = cfg.name
        env["LA_LIMITER_RESERVE"] = "0.2"
        patcher = unittest.mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _state(self):
        with open(self.path) as f:
            return json.load(f)

    def _as(self, owner):
        return unittest.mock.patch.dict(os.environ, {"LA_LIMITER_OWNER": owner})

    def _bucket(self, now, rpm=40, capacity=10):
        return rl.SmoothTokenBucket(self.path, rpm=rpm, capacity=capacity, clock=lambda: now[0])

    def _seed(self, doc):
        with open(self.path, "w") as f:
            json.dump(doc, f)

    def _w(self, token, owner, arrived, last_seen, cls="normal", kind="interactive"):
        return {token: {"owner": owner, "arrived": arrived, "last_seen": last_seen,
                        "class": cls, "kind": kind, "model": ""}}

    def test_bucket_no_barging_grant_books_tokens_for_winner(self):
        now = [FROZEN]
        os.environ["LA_NVIDIA_WAITER_TTL"] = "120"     # the +60s refill step must not prune the waiter
        b = self._bucket(now, rpm=1, capacity=2)
        with self._as("sess-A"):
            self.assertEqual(b.try_acquire(), 0.0)
            self.assertEqual(b.try_acquire(), 0.0)     # bucket empty (frozen clock: no refill)
            tA = rl._waiter_token("sess-A")
            self.assertGreater(b.try_acquire(token=tA), 0.0)
        now[0] += 60.0                                   # one token refilled (rpm=1)
        with self._as("sess-B"):
            tB = rl._waiter_token("sess-B")
            self.assertGreater(b.try_acquire(token=tB), 0.0, "no barging")
            st = self._state()
            self.assertEqual(st["grants"][-1]["o"], "sess-A", "token booked for the winner")
            self.assertAlmostEqual(st["tokens"], 0.0, places=6)
            self.assertIn("granted", st["waiters"][tA])
        with self._as("sess-A"):
            self.assertEqual(b.try_acquire(token=tA), 0.0, "winner acks")

    def test_bucket_lane_cap_via_grants_list(self):
        # 8 priority grants in the rolling window (cap = round(0.2*40) = 8) + a normal
        # waiter present -> the grant goes to the normal waiter
        now = [FROZEN]
        tP = rl._waiter_token("sess-A")
        tN = rl._waiter_token("sess-B")
        self._seed({"tokens": 10.0, "last_refill": FROZEN, "capacity": 10,
                    "grants": [{"t": FROZEN, "o": "sess-A", "c": "priority"}] * 8,
                    "owners": {"sess-A": {"priority": True}},
                    "waiters": {**self._w(tP, "sess-A", FROZEN, FROZEN, cls="priority"),
                                **self._w(tN, "sess-B", FROZEN + 1, FROZEN + 1)}})
        b = self._bucket(now)
        with self._as("sess-C"):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            st = self._state()
            self.assertEqual(st["grants"][-1]["o"], "sess-B", "cap reached: normal wins")
            self.assertEqual(st["grants"][-1]["c"], "normal")

    def test_bucket_lane_cap_free_without_normal_waiters(self):
        now = [FROZEN]
        tP = rl._waiter_token("sess-A")
        self._seed({"tokens": 10.0, "last_refill": FROZEN, "capacity": 10,
                    "grants": [{"t": FROZEN, "o": "sess-A", "c": "priority"}] * 8,
                    "owners": {"sess-A": {"priority": True}},
                    "waiters": {**self._w(tP, "sess-A", FROZEN, FROZEN, cls="priority")}})
        b = self._bucket(now)
        with self._as("sess-C"):
            tC = rl._waiter_token("sess-C")
            self.assertGreater(b.try_acquire(token=tC), 0.0)
            self.assertEqual(self._state()["grants"][-1]["o"], "sess-A",
                             "no normal waiters: the cap does not bind (zero idle capacity)")

    def test_bucket_user_scenario_capacity10(self):
        # the user's live config shape: capacity 10, rpm 40; frozen clock, refill by advancing
        now = [FROZEN]
        b = self._bucket(now, rpm=40, capacity=10)
        with self._as("sess-A"):
            for _ in range(10):
                self.assertEqual(b.try_acquire(), 0.0)   # full burst, capacity 10
            tA = rl._waiter_token("sess-A")
            self.assertGreater(b.try_acquire(token=tA), 0.0)
        now[0] += 2.0                                     # refill 2 * 40/60 = 1.333 tokens
        with self._as("sess-B"):
            tB = rl._waiter_token("sess-B")
            self.assertGreater(b.try_acquire(token=tB), 0.0)
            st = self._state()
            self.assertAlmostEqual(st["tokens"], 2 * 40 / 60 - 1.0, places=6)
            self.assertEqual(st["grants"][-1]["o"], "sess-A")
        with self._as("sess-A"):
            self.assertEqual(b.try_acquire(token=tA), 0.0)


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    unittest.main(verbosity=2)

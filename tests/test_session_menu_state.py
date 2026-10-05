#!/usr/bin/env python3
"""tests/test_session_menu_state.py — the repo-local session-menu preference store.

What this protects: the picker remembers effort (and the NVIDIA rate-limiter settings)
in config/session-menu.local.json. That file is written by several entry points, may be
read while another picker writes it, and must never leak, clobber, or silently fall back
to a second store. Each test names the production change that would make it fail.
"""
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_menu_state as sms  # noqa: E402


def _writer(config_dir, lane, values, barrier):
    barrier.wait()
    for value in values:
        sms.save_effort(config_dir, lane, value)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Path(self.tmp.name) / "config"
        self.cfg.mkdir()
        self.file = self.cfg / sms.PREFS_NAME
        self.lock = self.cfg / sms.LOCK_NAME

    # --- defaults / round trip -------------------------------------------------
    def test_absent_file_gives_builtin_defaults_and_writes_nothing(self):
        # Fails if load() creates the file (state must never be written on read).
        state = sms.load(self.cfg)
        self.assertEqual(state.effort, {"local_session": "high",
                                        "remote_api_session": "max",
                                        "lowkey": "xhigh"})
        self.assertEqual(state.warnings, [])
        self.assertFalse(self.file.exists())
        self.assertFalse(self.lock.exists())

    def test_each_lane_saves_independently_and_survives_restart(self):
        # Fails if save_effort rewrites the whole effort map from defaults.
        sms.save_effort(self.cfg, "local_session", "low")
        sms.save_effort(self.cfg, "lowkey", "minimal")
        state = sms.load(self.cfg)
        self.assertEqual(state.effort["local_session"], "low")
        self.assertEqual(state.effort["remote_api_session"], "max")
        self.assertEqual(state.effort["lowkey"], "minimal")
        doc = json.loads(self.file.read_text())
        self.assertEqual(doc["schema_version"], sms.SCHEMA_VERSION)

    def test_provider_default_is_a_sentinel_that_means_omit(self):
        # Fails if provider_default is turned into a real effort string.
        sms.save_effort(self.cfg, "remote_api_session", "provider_default")
        state = sms.load(self.cfg)
        self.assertEqual(state.effort["remote_api_session"], "provider_default")
        self.assertIsNone(sms.effort_arg("remote_api_session", "provider_default"))
        self.assertEqual(sms.effort_arg("remote_api_session", "high"), "high")

    def test_lane_allowlists(self):
        # Fails if lowkey accepts `max` (Rapid-MLX rejects it with HTTP 400) or a local
        # session accepts provider_default (it has no provider).
        with self.assertRaises(ValueError):
            sms.save_effort(self.cfg, "lowkey", "max")
        with self.assertRaises(ValueError):
            sms.save_effort(self.cfg, "local_session", "provider_default")
        with self.assertRaises(ValueError):
            sms.save_effort(self.cfg, "no_such_lane", "high")
        self.assertFalse(self.file.exists())

    # --- corruption / versions / unsafe paths ----------------------------------
    def _assert_warns_and_keeps(self, raw):
        self.file.write_bytes(raw)
        before = self.file.read_bytes()
        state = sms.load(self.cfg)
        self.assertEqual(state.effort, dict(sms.DEFAULT_EFFORT))
        self.assertTrue(state.warnings, "expected a warning")
        # A save must refuse rather than overwrite a file it could not understand.
        warning = sms.save_effort(self.cfg, "local_session", "low")
        self.assertIsNotNone(warning)
        self.assertEqual(self.file.read_bytes(), before, "unreadable file was clobbered")

    def test_last_launched_tier_round_trips_and_clears(self):
        # Go-last on the remote lane needs the tier without an inventory call (0.22.0).
        self.assertIsNone(sms.save_last_launched(self.cfg, "remote_api_session", "cerebras-oss", "trial"))
        st = sms.load(self.cfg)
        self.assertEqual(st.last_launched["remote_api_session"], "cerebras-oss")
        self.assertEqual(st.last_launched_tier, {"remote_api_session": "trial"})
        self.assertNotIn("remote_api_session:tier", st.last_launched, "reserved key must not leak")
        doc = json.loads((self.file).read_text())
        self.assertEqual(doc["last_launched_tier"], {"remote_api_session": "trial"})
        sms.save_last_launched(self.cfg, "remote_api_session", "gemini-flash")   # no tier -> cleared
        self.assertEqual(sms.load(self.cfg).last_launched_tier, {})
        with self.assertRaises(ValueError):
            sms.save_last_launched(self.cfg, "remote_api_session", "x", "free-forever")

    def test_bad_tier_in_file_is_refused_not_trusted(self):
        (self.file).write_text(json.dumps(
            {"schema_version": 2, "last_launched_tier": {"remote_api_session": "bogus"}}))
        st = sms.load(self.cfg)
        self.assertEqual(st.last_launched_tier, {})
        self.assertTrue(st.warnings)

    def test_invalid_json_warns_without_overwrite(self):
        self._assert_warns_and_keeps(b"{not json")

    def test_unknown_future_schema_warns_without_overwrite(self):
        self._assert_warns_and_keeps(json.dumps({"schema_version": 5, "effort": {}}).encode())

    def test_unknown_section_warns_without_overwrite(self):
        # Fails if a section this version does not know is silently dropped by the next write.
        self._assert_warns_and_keeps(json.dumps({"schema_version": 2, "favourites": ["x"]}).encode())

    def test_value_outside_allowlist_warns_without_overwrite(self):
        self._assert_warns_and_keeps(json.dumps(
            {"schema_version": 1, "effort": {"local_session": "turbo"}}).encode())

    def test_oversized_file_is_rejected(self):
        self._assert_warns_and_keeps(b" " * (sms.MAX_BYTES + 1))

    def test_symlinked_preference_file_is_rejected(self):
        target = Path(self.tmp.name) / "elsewhere.json"
        target.write_text(json.dumps({"schema_version": 1, "effort": {"local_session": "low"}}))
        self.file.symlink_to(target)
        state = sms.load(self.cfg)
        self.assertEqual(state.effort["local_session"], "high", "followed a symlink")
        self.assertTrue(state.warnings)
        self.assertIsNotNone(sms.save_effort(self.cfg, "local_session", "max"))
        self.assertIn("low", target.read_text(), "wrote through a symlink")
        # The lstat check must hold on its own, not only via O_NOFOLLOW (which some
        # platforms lack): with O_NOFOLLOW stripped the symlink must still be refused.
        with mock.patch.object(sms.os, "O_NOFOLLOW", 0, create=True):
            self.assertIn("symlink", " ".join(sms.load(self.cfg).warnings))

    def test_non_regular_preference_file_is_rejected(self):
        self.file.mkdir()
        state = sms.load(self.cfg)
        self.assertTrue(state.warnings)
        self.assertIsNotNone(sms.save_effort(self.cfg, "local_session", "low"))

    def test_plugin_cache_is_never_written(self):
        # Fails if the store writes into an installed plugin cache copy.
        cache = Path(self.tmp.name) / ".claude/plugins/cache/haiggoh/free-agents/9.9.9/config"
        cache.mkdir(parents=True)
        warning = sms.save_effort(cache, "local_session", "low")
        self.assertIsNotNone(warning)
        self.assertEqual(list(cache.iterdir()), [])

    # --- permissions / atomicity / concurrency ---------------------------------
    def test_file_and_lock_are_user_only(self):
        sms.save_effort(self.cfg, "local_session", "low")
        for path in (self.file, self.lock):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    def test_mode_is_forced_even_if_mkstemp_is_permissive(self):
        # mkstemp happens to create 0600 today; the explicit fchmod is what guarantees it.
        real = sms.tempfile.mkstemp

        def loose(*a, **kw):
            fd, name = real(*a, **kw)
            os.fchmod(fd, 0o644)
            return fd, name
        with mock.patch.object(sms.tempfile, "mkstemp", side_effect=loose):
            sms.save_effort(self.cfg, "local_session", "low")
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)

    def test_unwritable_directory_keeps_value_in_memory_and_warns(self):
        # Deterministic: simulate EACCES at the temp-file step rather than trusting chmod
        # (a privileged user ignores directory modes).
        with mock.patch.object(sms.tempfile, "mkstemp", side_effect=PermissionError(13, "denied")):
            warning = sms.save_effort(self.cfg, "local_session", "low")
        self.assertIsNotNone(warning)
        self.assertFalse(self.file.exists())
        self.assertNotIn(str(Path.home()), warning, "diagnostic leaks a private path")

    def test_concurrent_writers_lose_no_update(self):
        # Fails without the read-merge-write lock: the last writer would drop the
        # other process's lane.
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(3)
        seqs = {"local_session": ["low", "medium", "xhigh"] * 5,
                "remote_api_session": ["high", "provider_default", "low"] * 5,
                "lowkey": ["none", "medium", "high"] * 5}
        procs = [ctx.Process(target=_writer, args=(self.cfg, lane, vals, barrier))
                 for lane, vals in seqs.items()]
        for p in procs:
            p.start()
        for p in procs:
            p.join(30)
            self.assertEqual(p.exitcode, 0)
        state = sms.load(self.cfg)
        self.assertEqual(state.warnings, [])
        self.assertEqual(state.effort, {lane: vals[-1] for lane, vals in seqs.items()})

    # --- rate limiter section ---------------------------------------------------
    def test_rate_limiter_settings_round_trip_and_validate(self):
        # Fails if a nonsense value is persisted (the proxy would read it machine-wide).
        sms.save_rate_limiter(self.cfg, {"mode": "smooth_bucket", "rpm": 30})
        sms.save_effort(self.cfg, "local_session", "low")      # must not drop the section
        state = sms.load(self.cfg)
        self.assertEqual(state.rate_limiter, {"mode": "smooth_bucket", "rpm": 30})
        for bad in ({"mode": "yolo"}, {"rpm": 0}, {"rpm": "40"}, {"bucket_capacity": -1},
                    {"unknown_key": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sms.save_rate_limiter(self.cfg, bad)
        sms.save_rate_limiter(self.cfg, {"mode": None})       # None clears a key
        self.assertEqual(sms.load(self.cfg).rate_limiter, {"rpm": 30})

    def test_backoff_keys_validate(self):
        # 0.20.11's backoff keys: fails if they are rejected as unknown or accept nonsense.
        sms.save_rate_limiter(self.cfg, {"max_cooldown": 600, "backoff_multiplier": 1.5,
                                         "max_retries": 0})
        self.assertEqual(sms.load(self.cfg).rate_limiter,
                         {"max_cooldown": 600, "backoff_multiplier": 1.5, "max_retries": 0})
        for bad in ({"max_cooldown": 3601}, {"backoff_multiplier": 0.5}, {"backoff_multiplier": 11},
                    {"max_retries": 2.5}, {"max_retries": 51}, {"max_retries": True}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sms.save_rate_limiter(self.cfg, bad)

    # --- last launched (schema 2) ------------------------------------------------
    def test_v1_file_migrates_only_on_confirmed_write_keeping_everything(self):
        v1 = {"schema_version": 1, "effort": {"local_session": "low", "lowkey": "minimal"},
              "rate_limiter": {"mode": "smooth_bucket", "rpm": 30, "cooldown": 10}}
        raw = json.dumps(v1).encode()
        self.file.write_bytes(raw)
        state = sms.load(self.cfg)                       # load alone: no rewrite
        self.assertEqual((state.warnings, state.last_launched), ([], {}))
        self.assertEqual(self.file.read_bytes(), raw)
        self.assertIsNone(sms.save_last_launched(self.cfg, "local_session", "qwen-3.8-operator"))
        doc = json.loads(self.file.read_text())
        self.assertEqual(doc["schema_version"], 4)
        self.assertEqual(doc["effort"], v1["effort"])
        self.assertEqual(doc["rate_limiter"], v1["rate_limiter"])
        self.assertEqual(doc["last_launched"], {"local_session": "qwen-3.8-operator"})

    def test_last_launched_is_per_lane_and_validated(self):
        sms.save_last_launched(self.cfg, "local_session", "qwen-3.8-operator")
        sms.save_last_launched(self.cfg, "remote_api_session", "nvidia-a")
        sms.save_effort(self.cfg, "lowkey", "low")          # other writers keep the map
        self.assertEqual(sms.load(self.cfg).last_launched,
                         {"local_session": "qwen-3.8-operator", "remote_api_session": "nvidia-a"})
        for lane, bad in (("nope", "x"), ("lowkey", ""), ("lowkey", "a b"), ("lowkey", "-rf"),
                          ("lowkey", 7)):
            with self.subTest(lane=lane, bad=bad), self.assertRaises(ValueError):
                sms.save_last_launched(self.cfg, lane, bad)

    def test_v1_file_carrying_last_launched_is_refused(self):
        self._assert_warns_and_keeps(json.dumps(
            {"schema_version": 1, "last_launched": {"lowkey": "x"}}).encode())

    def test_pre_backoff_file_loads_unchanged(self):
        # A file written before the backoff keys existed must load as-is: no warning, no rewrite.
        path = self.cfg / sms.PREFS_NAME
        raw = (b'{"schema_version": 1, "effort": {"local_session": "low"}, '
               b'"rate_limiter": {"mode": "sliding_window", "rpm": 40, "max_wait": 120, "cooldown": 10}}')
        path.write_bytes(raw)
        state = sms.load(self.cfg)
        self.assertEqual(state.warnings, [])
        self.assertEqual(state.rate_limiter,
                         {"mode": "sliding_window", "rpm": 40, "max_wait": 120, "cooldown": 10})
        self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()

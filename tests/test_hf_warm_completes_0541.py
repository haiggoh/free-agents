#!/usr/bin/env python3
"""tests/test_hf_warm_completes_0541.py — 0.55.8 Task 2 (0541 plan Item A): the HF warm-up
finishes — unsized ids first, bounded parallel workers, and a bounded detached finisher that
outlives the picker on every exit route (quit, atexit, SIGHUP, SIGTERM).

Run: ~/.local/share/free-agents/picker-venv/bin/python -m pytest tests/test_hf_warm_completes_0541.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from picker_harness import PilotCase, ROOT  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, str(ROOT / "bin"))
try:
    import textual  # noqa: F401
except ImportError:
    textual = None
sp = None
if textual is not None:
    import session_picker as sp  # noqa: E402


def _load_emf():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "estimate_model_footprint",
        ROOT / "bin" / "estimate-model-footprint.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


emf = _load_emf()
pytestmark = pytest.mark.load_sensitive


class FakeHF(BaseHTTPRequestHandler):
    """Loopback HF API fake: /api/models/<id> answers a safetensors total."""
    hits = 0

    def do_GET(self):
        FakeHF.hits += 1
        if "?search=" in self.path:
            body = json.dumps([]).encode()
        else:
            mid = self.path.split("/api/models/")[1].split("?")[0]
            body = json.dumps({"id": mid, "createdAt": "2026-01-01",
                               "safetensors": {"total": 604339504832}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _start_fake_hf():
    srv = HTTPServer(("127.0.0.1", 0), FakeHF)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class WarmOrderAndCLI(PilotCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="fa-warm-test-"))
        self.cache = self.tmp / "hf-params.json"
        os.environ["LA_HF_PARAMS_CACHE"] = str(self.cache)
        self.addCleanup(os.environ.pop, "LA_HF_PARAMS_CACHE", None)
        self._saved_lookup = os.environ.get("LA_PICKER_HF_LOOKUP")
        os.environ["LA_PICKER_HF_LOOKUP"] = "1"
        self.addCleanup(self._restore_lookup)
        self._saved_get = emf._hf_get
        self.addCleanup(setattr, emf, "_hf_get", self._saved_get)

    def _restore_lookup(self):
        if self._saved_lookup is None:
            os.environ.pop("LA_PICKER_HF_LOOKUP", None)
        else:
            os.environ["LA_PICKER_HF_LOOKUP"] = self._saved_lookup

    def test_unsized_ids_are_warmed_first(self):
        # one id already has a cache entry; the rest do not -> unsized first, catalog order kept
        self.cache.write_text(json.dumps({"a/sized-7b": {"v": 2, "total": 7_000_000_000,
                                                          "at": time.time()}}))
        emf._hf_get = lambda url, timeout: emf.HFError("network")
        order = sp._warm_order("nvidia", ["a/sized-7b", "z/step-5-preview", "m/mid-1b"])
        self.assertEqual(order, ["z/step-5-preview", "a/sized-7b", "m/mid-1b"], order)

    def test_hf_warm_cli_help_and_unknown_flag(self):
        script = ROOT / "bin" / "estimate-model-footprint.py"
        h = subprocess.run([sys.executable, str(script), "--help"],
                           capture_output=True, text=True)
        self.assertEqual(h.returncode, 0, h.stderr)
        self.assertIn("--hf-warm", h.stdout, "--help must document the finisher mode")
        bad = subprocess.run([sys.executable, str(script), "--hf-warm", "x.json", "--bogus"],
                             capture_output=True, text=True,
                             env={**os.environ, "LA_HF_PARAMS_CACHE": str(self.cache)})
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("--bogus", bad.stderr)

    def test_finisher_completes_on_real_subprocess(self):
        srv, base = _start_fake_hf()
        self.addCleanup(srv.shutdown)
        ids = [f"org/fin-{i}" for i in range(6)]
        ids_file = self.tmp / "ids.json"
        ids_file.write_text(json.dumps(ids))
        env = {**os.environ, "LA_HF_PARAMS_CACHE": str(self.cache),
               "LA_HF_API_BASE": base}
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "estimate-model-footprint.py"),
                            "--hf-warm", str(ids_file), "--max-seconds", "30"],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(self.cache.read_text())
        self.assertEqual(sorted(k for k in data if k.startswith("org/fin-")), sorted(ids),
                         "every finisher id must land in the cache")
        outcome = json.loads((self.tmp / "hf-warm-last.json").read_text())
        self.assertEqual(outcome.get("sized"), 6, outcome)

    def test_second_finisher_refused_by_lock(self):
        srv, base = _start_fake_hf()
        self.addCleanup(srv.shutdown)
        ids_file = self.tmp / "ids2.json"
        ids_file.write_text(json.dumps(["org/lock-1"]))
        env = {**os.environ, "LA_HF_PARAMS_CACHE": str(self.cache), "LA_HF_API_BASE": base}
        lock = self.cache.with_suffix(".warm.lock")
        import fcntl
        with open(lock, "a+b") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)     # hold the lock ourselves
            r = subprocess.run([sys.executable, str(ROOT / "bin" / "estimate-model-footprint.py"),
                                "--hf-warm", str(ids_file), "--max-seconds", "5"],
                               capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(r.returncode, 0)
            self.assertIn("already warming", r.stdout)
            self.assertFalse(self.cache.exists() and "org/lock-1" in self.cache.read_text())

    def test_finisher_network_failure_writes_nothing_and_exits(self):
        srv, base = _start_fake_hf()
        srv.shutdown()                                   # fake closes: connections fail
        ids_file = self.tmp / "ids3.json"
        ids_file.write_text(json.dumps(["org/dead-1", "org/dead-2"]))
        env = {**os.environ, "LA_HF_PARAMS_CACHE": str(self.cache), "LA_HF_API_BASE": base}
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "estimate-model-footprint.py"),
                            "--hf-warm", str(ids_file), "--max-seconds", "10"],
                           capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(self.cache.read_text()) if self.cache.exists() else {}
        self.assertEqual([k for k in data if k.startswith("org/dead-")], [],
                         "a network failure is not an answer — nothing may be written")
        outcome = json.loads((self.tmp / "hf-warm-last.json").read_text())
        self.assertIn("error", str(outcome).lower(), outcome)


class WarmHandOff(PilotCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="fa-warm-handoff-"))
        self.cache = self.tmp / "hf-params.json"
        os.environ["LA_HF_PARAMS_CACHE"] = str(self.cache)
        self.addCleanup(os.environ.pop, "LA_HF_PARAMS_CACHE", None)
        self._saved_lookup = os.environ.get("LA_PICKER_HF_LOOKUP")
        os.environ["LA_PICKER_HF_LOOKUP"] = "1"
        self.addCleanup(self._restore_lookup)
        self._saved_get = emf._hf_get
        self.addCleanup(setattr, emf, "_hf_get", self._saved_get)
        self.spawned = []
        self._orig_spawn = sp._spawn_warm_finisher
        def spy(remaining):
            self.spawned.append(list(remaining))
            return True
        sp._spawn_warm_finisher = spy
        self.addCleanup(setattr, sp, "_spawn_warm_finisher", self._orig_spawn)

    def _restore_lookup(self):
        if self._saved_lookup is None:
            os.environ.pop("LA_PICKER_HF_LOOKUP", None)
        else:
            os.environ["LA_PICKER_HF_LOOKUP"] = self._saved_lookup

    def test_exit_mid_warm_hands_remaining_to_finisher(self):
        # R5: after the pool stops, remaining = every catalog id with NO cache entry
        # (in-flight writes included — they landed in the cache).
        catalog = [f"org/mid-{i}" for i in range(30)]
        def slow_get(url, timeout):
            time.sleep(0.3)
            return emf.HFError("network")          # nothing ever lands in the cache
        emf._hf_get = slow_get
        async def s(app, pilot, h):
            app._warm_pending.update(catalog)
            app._warm_run_start = time.time() - 10
            t0 = time.time()
            app._handoff_warm_finisher()            # the exit hand-off
            self.assertLess(time.time() - t0, 1.0, "the hand-off must not join network calls (R4)")
        self.run_picker(s, start="home")
        self.assertEqual(len(self.spawned), 1)
        self.assertEqual(sorted(self.spawned[0]), sorted(catalog),
                         "every unattempted id is handed to the finisher")

    def test_exit_mid_warm_skips_ids_written_in_flight(self):
        # R5: ids that DID land in the cache during the run are not re-handed-off
        catalog = [f"org/in-{i}" for i in range(5)]
        self.cache.write_text(json.dumps({
            "org/in-0": {"v": 2, "total": 1, "at": time.time()},
            "org/in-1": {"v": 2, "total": 1, "at": time.time()},
        }))
        emf._hf_get = lambda url, timeout: emf.HFError("network")
        async def s(app, pilot, h):
            app._warm_pending.update(catalog)
            app._warm_run_start = time.time()
            app._handoff_warm_finisher()
        self.run_picker(s, start="home")
        self.assertEqual(sorted(self.spawned[0]), sorted(c for c in catalog if c not in ("org/in-0", "org/in-1")))

    def test_warm_disabled_spawns_nothing(self):
        os.environ["LA_PICKER_HF_LOOKUP"] = "0"
        async def s(app, pilot, h):
            app._warm_pending.update(["org/x-1"])
            app._handoff_warm_finisher()
        self.run_picker(s, start="home")
        self.assertEqual(self.spawned, [], "LA_PICKER_HF_LOOKUP=0 must not spawn a finisher")

    def test_finisher_survives_parent_exit(self):
        # R2: spawn via _spawn_warm_finisher from a short-lived child that exits at once;
        # the finisher (own session) still completes.
        srv, base = _start_fake_hf()
        self.addCleanup(srv.shutdown)
        ids_file = self.tmp / "ids4.json"
        ids_file.write_text(json.dumps(["org/surv-1", "org/surv-2"]))
        child = subprocess.run(
            [sys.executable, "-c",
             "import sys, json; sys.path.insert(0, %r); import session_picker as sp; "
             "sp._spawn_warm_finisher(%r)" % (str(ROOT / "bin"), ["org/surv-1", "org/surv-2"])],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "LA_HF_PARAMS_CACHE": str(self.cache),
                 "LA_HF_API_BASE": base})
        self.assertEqual(child.returncode, 0, child.stderr)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.cache.exists():
                data = json.loads(self.cache.read_text())
                if all(k in data for k in ("org/surv-1", "org/surv-2")):
                    return
            time.sleep(0.5)
        self.fail("the finisher did not complete after its parent exited")

    def test_finisher_writes_to_the_pickers_resolved_cache(self):
        # R6: the finisher argv carries the picker's RESOLVED cache path as an argument AND env.
        # Dead loopback port: the finisher's requests fail fast, nothing reaches real HF.
        os.environ["LA_HF_API_BASE"] = "http://127.0.0.1:1"
        self.addCleanup(os.environ.pop, "LA_HF_API_BASE", None)
        setattr(sp, "_spawn_warm_finisher", self._orig_spawn)   # call the REAL spawner here
        captured = {}
        real_popen = subprocess.Popen
        class SpyPopen(real_popen):
            def __init__(self, argv, **kw):
                captured["argv"] = argv
                captured["env"] = kw.get("env", os.environ)
                super().__init__(argv, **kw)
        import subprocess as sp_mod
        sp_mod.Popen = SpyPopen
        self.addCleanup(setattr, sp_mod, "Popen", real_popen)
        sp._spawn_warm_finisher(["org/resolved-1"])
        argv = captured["argv"]
        hf_cache_arg = argv[argv.index("--hf-cache") + 1]
        self.assertEqual(Path(hf_cache_arg), self.cache.resolve(),
                         "the resolved cache path must be the --hf-cache argument")
        self.assertEqual(Path(captured["env"]["LA_HF_PARAMS_CACHE"]), self.cache.resolve())

    def test_failed_finisher_surfaces_status_line(self):
        # R7: a failure outcome file makes the next picker open show ONE status line
        (self.tmp / "hf-warm-last.json").write_text(json.dumps(
            {"error": "network", "remaining": ["org/left-1"], "sized": 0}))
        async def s(app, pilot, h):
            app._maybe_warm_status()               # what on_mount shows (R7)
            await h.pilot.pause(0.3)
            text = h.text()
            self.assertIn("warm", text.lower(), text)
        self.run_picker(s, start="home")

    def test_rendered_rows_show_size_after_slow_warm(self):
        # a disk-catalog of one fake id, warmed through the fake HF API; the Remote row
        # shows the size suffix once the warm lands (the label text the user sees)
        srv, base = _start_fake_hf()
        self.addCleanup(srv.shutdown)
        os.environ["LA_HF_API_BASE"] = base
        self.addCleanup(os.environ.pop, "LA_HF_API_BASE", None)
        os.environ["LA_EAGER_CATALOGS"] = "0"
        self.addCleanup(os.environ.pop, "LA_EAGER_CATALOGS", None)
        import session_picker_model as mm
        mm.write_catalog_cache("nvidia", [mm.CatalogModel("org/fake-size")])
        async def s(app, pilot, h):
            sm = app.screen_model
            sm._toggle_choose_model()
            app.render_model()
            await h.wait_for(lambda: isinstance(sm.catalogs.get("nvidia"), list)
                             and sm._models_loaded, "catalog", secs=15)
            await h.wait_for(lambda: self.cache.exists() and "org/fake-size" in
                             json.loads(self.cache.read_text()), "warm landed", secs=20)
            app.render_model()
            await h.pilot.pause(0.5)
            text = h.text()
            self.assertIn("604.3B", text, "the Remote row must show the size after the warm")
        self.run_picker(s, start="remote")

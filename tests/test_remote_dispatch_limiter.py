#!/usr/bin/env python3
"""Every free-API dispatch path to a rate-limited provider takes a slot from the SAME
machine-wide bucket the LiteLLM proxies use (bin/rate_limiter.py), and books a 429 there.

Paths covered: remote_http.stream_chat (used by remote-agent-dispatch.py, agent-fallback.py and
free-agent-tool.py's API route) and librarian-dispatch.py --provider. All traffic goes to a
loopback fake; the limiter state file and the key directory are temp paths, so no real quota or
real key is touched.
"""
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BIN = REPO / "bin"
sys.path.insert(0, str(BIN))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BIN / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load("remote_provider_core", "remote_provider_core.py")
rhttp = _load("remote_http", "remote_http.py")
rl = _load("rate_limiter", "rate_limiter.py")


class _Fake:
    def __init__(self, status=200, body=b"data: {\"choices\":[{\"delta\":{\"content\":\"hi\"}}]}\n\ndata: [DONE]\n\n",
                 headers=None, responses=None):
        # `responses` (list of (status, body)) makes the fake DETERMINISTIC: hit N gets
        # responses[N-1], the last entry repeating. Without it every hit gets status/body.
        self.status, self.body, self.headers = status, body, headers or {}
        self.responses = list(responses) if responses else None
        self.hits = 0
        self.auth = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                fake.hits += 1
                fake.auth.append(self.headers.get("Authorization"))
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                st, bd = fake.status, fake.body
                if fake.responses:
                    st, bd = fake.responses[min(fake.hits - 1, len(fake.responses) - 1)]
                self.send_response(st)
                for k, v in fake.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(bd)))
                self.end_headers()
                self.wfile.write(bd)

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.state = t / "nvidia_state"
        self.keys = t / "keys"
        self.keys.mkdir()
        (self.keys / "nvidia").write_text("nvapi-TESTKEY\n")
        self.env = {"LA_NVIDIA_THROTTLE_STATE": str(self.state), "LA_API_KEYS_DIR": str(self.keys),
                    "LA_NVIDIA_RPM": "2", "LA_NVIDIA_MAX_WAIT": "0",
                    "LA_SESSION_MENU_CONFIG_DIR": str(t / "menu")}
        self._old = {k: os.environ.get(k) for k in list(self.env) + ["NVIDIA_API_KEY", "LA_REMOTE_RATE_LIMIT"]}
        os.environ.update(self.env)
        os.environ.pop("NVIDIA_API_KEY", None)
        os.environ.pop("LA_REMOTE_RATE_LIMIT", None)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def provider(self, fake, pid="nvidia"):
        real = core.get(pid)
        return core.Provider(id=real.id, display=real.display, tier=real.tier,
                             base_url="http://127.0.0.1:%d" % fake.port,
                             chat_path="/v1/chat/completions", models_path="/models",
                             key_env=real.key_env)

    def window(self):
        try:
            return len(json.loads(self.state.read_text()).get("stamps", []))
        except (OSError, ValueError):
            return 0


class TestKeyStore(Base):
    def test_key_file_fallback(self):
        p = core.get("nvidia")
        self.assertEqual(p.key(), "nvapi-TESTKEY")
        self.assertTrue(p.config_present())

    def test_env_wins_over_file(self):
        os.environ["NVIDIA_API_KEY"] = "from-env"
        self.assertEqual(core.get("nvidia").key(), "from-env")

    def test_no_key_anywhere(self):
        (self.keys / "nvidia").unlink()
        self.assertFalse(core.get("nvidia").config_present())


class TestTransportLimiter(Base):
    def test_each_call_takes_a_slot(self):
        fake = _Fake()
        try:
            p = self.provider(fake)
            for _ in range(2):
                r = rhttp.stream_chat(p, {"model": "m", "messages": []}, None, io.StringIO(), timeout=5)
                self.assertTrue(r.ok, r.error_reason)
            self.assertEqual(self.window(), 2, "both calls booked in the SHARED state file")
            # Window full (rpm=2, max_wait=0): the third call is refused BEFORE any socket.
            r = rhttp.stream_chat(p, {"model": "m", "messages": []}, None, io.StringIO(), timeout=5)
            self.assertFalse(r.ok)
            self.assertEqual(r.error_class, "quota")
            self.assertIn("limiter", r.error_reason)
            self.assertEqual(fake.hits, 2, "a refused slot must not reach the provider")
            self.assertEqual(fake.auth[0], "Bearer nvapi-TESTKEY")
        finally:
            fake.close()

    def test_429_pauses_every_caller(self):
        fake = _Fake(status=429, body=b'{"error":"rate limit exceeded"}', headers={"Retry-After": "40"})
        try:
            r = rhttp.stream_chat(self.provider(fake), {"model": "m"}, None, io.StringIO(), timeout=5)
            self.assertEqual(r.error_class, "quota")
            st = json.loads(self.state.read_text())
            self.assertGreater(float(st.get("cooldown_until", 0)), 0, "429 recorded machine-wide")
            self.assertGreater(rl.get_limiter().try_acquire(), 0.0, "next caller must wait")
        finally:
            fake.close()

    def test_unlimited_provider_untouched(self):
        fake = _Fake()
        try:
            r = rhttp.stream_chat(self.provider(fake, "gemini"), {"model": "m"}, None, io.StringIO(), timeout=5)
            self.assertTrue(r.ok)
            self.assertEqual(self.window(), 0, "gemini does not spend NVIDIA slots")
        finally:
            fake.close()

    def test_proxy_and_dispatch_share_one_bucket(self):
        """The proxy hooks' limiter and the transport's limiter are the same file."""
        self.assertEqual(Path(rl.get_limiter().path), self.state)
        self.assertEqual(Path(rhttp._limiter_for(core.get("nvidia")).path), self.state)


class TestLibrarianProvider(Base):
    def _run(self, fake, *extra):
        # Point the nvidia provider at the fake by overriding its base URL through a shim
        # module on PYTHONPATH (librarian imports remote_provider_core by name).
        shim = Path(self.tmp.name) / "shim"
        shim.mkdir(exist_ok=True)
        (shim / "sitecustomize.py").write_text(
            "import sys; sys.path.insert(0, %r)\n"
            "import remote_provider_core as c, dataclasses\n"
            "c.PROVIDERS['nvidia'] = dataclasses.replace(c.PROVIDERS['nvidia'], "
            "base_url='http://127.0.0.1:%d', chat_path='/v1/chat/completions')\n" % (str(BIN), fake.port))
        env = dict(os.environ, PYTHONPATH=str(shim))
        out = Path(self.tmp.name) / "out"
        return subprocess.run([sys.executable, str(BIN / "librarian-dispatch.py"), "--provider", "nvidia",
                               "--prompt", "hi", "--model", "m", "--outdir", str(out), "--no-ledger", *extra],
                              capture_output=True, text=True, env=env, timeout=30), out

    def test_librarian_remote_takes_slot_and_hides_key(self):
        fake = _Fake()
        try:
            res, out = self._run(fake)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual((out / "output.txt").read_text(), "hi")
            self.assertEqual(self.window(), 1, "librarian --provider nvidia booked one slot")
            self.assertEqual(fake.auth, ["Bearer nvapi-TESTKEY"])
            self.assertNotIn("TESTKEY", res.stdout + res.stderr, "key never printed")
            self.assertFalse((out / "_headers").exists(), "header file removed after the call")
        finally:
            fake.close()

    def test_librarian_retries_after_a_429(self):
        """First call 429s; the dispatch books the pause and retries instead of failing."""
        # CONTENTION NOTE (fa-gate-test-flakes): the old version raced a polling thread
        # (flip to 200 once hits>=1) against the dispatcher's own retry — with cooldown=0
        # the retry can reach the fake BEFORE the poller wakes up, take a second 429, and
        # push hits to 3, failing `hits == 2` on a loaded runner while passing solo. The
        # fake now serves a deterministic per-hit sequence (hit 1 = 429, hit 2 = 200), so
        # the retry always finds the recovery response; `hits == 2` stays the real check.
        # The env writes are restored too: they used to leak into later tests in the
        # same pytest worker (a latent order-dependent flake of its own).
        ok_body = _Fake().body
        env_keys = ("LA_NVIDIA_429_COOLDOWN", "LA_NVIDIA_MAX_WAIT", "LA_NVIDIA_RPM")
        old_env = {k: os.environ.get(k) for k in env_keys}
        os.environ.update(LA_NVIDIA_429_COOLDOWN="0", LA_NVIDIA_MAX_WAIT="5", LA_NVIDIA_RPM="40")
        fake = _Fake(responses=[(429, b'{"status":429,"title":"Too Many Requests"}'),
                                (200, ok_body)])
        try:
            res, out = self._run(fake)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(fake.hits, 2, "one 429, then one successful retry")
            self.assertIn("retry 1", res.stderr)
            self.assertEqual((out / "output.txt").read_text(), "hi")
        finally:
            fake.close()
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_librarian_refuses_when_window_full(self):
        fake = _Fake()
        try:
            lim = rl.get_limiter()
            lim.try_acquire(); lim.try_acquire()          # fill rpm=2
            res, _ = self._run(fake)
            self.assertEqual(res.returncode, 2)
            self.assertEqual(fake.hits, 0, "no request leaves while the shared window is full")
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()

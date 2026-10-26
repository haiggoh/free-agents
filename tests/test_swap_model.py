#!/usr/bin/env python3
"""--swap-model (0.40.0): rescue a STALLED session by relaunching its proxy on another
model, same port. Drives the REAL remote-session.sh functions against a stub proxy command
(LA_LITELLM_CMD) whose /v1/messages answers can be made to fail or slow per port via control
files, so the three contract paths are deterministic:

  healthy session  -> REFUSED (the log answered inside the healthy window)
  stalled session  -> swapped: new config on the SAME path, pidfile updated, .owner
                      byte-identical, pre-flight proven on a scratch port, probe 200
  probe fails      -> reverted: the backup restored, the OLD model answering again

Usage: python3 tests/test_swap_model.py [-v]
"""
import json
import fcntl
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A stub "litellm": health endpoint always; /v1/messages 200 unless the port's control file
# exists (.fail -> 500, .slow -> wait before binding, so the revert's readiness wait has
# something real to wait for).
STUB = """#!/usr/bin/env python3
import http.server, json, os, signal, sys, time

port = None
cfg = None
it = iter(sys.argv[1:])
for a in it:
    if a == "--port":
        port = int(next(it))
    elif a == "--config":
        cfg = next(it)
ctrl = os.environ["LA_SWAP_TEST_DIR"]

if os.path.exists(os.path.join(ctrl, f"{port}.slow")):
    time.sleep(3)

def _on_term(signum, frame):
    # Checked at SIGNAL time, not startup: the swap test writes this file only after
    # the proxy is up (the port isn't known before), so stubbornness must be lazy.
    if os.path.exists(os.path.join(ctrl, f"ignore-term-{port}")):
        return
    sys.exit(0)

signal.signal(signal.SIGTERM, _on_term)

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health/liveliness":
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        if os.path.exists(os.path.join(ctrl, f"{port}.fail")):
            out = json.dumps({"error": {"type": "server_error",
                                        "message": "fixture: upstream throttled"}}).encode()
            self.send_response(500)
        else:
            out = json.dumps({"id": "msg_1", "type": "message", "role": "assistant",
                              "model": "x", "content": [{"type": "text", "text": "PONG"}],
                              "stop_reason": "end_turn", "stop_sequence": None,
                              "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
            self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)
    def log_message(self, *a):
        pass

srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
print(f"stub litellm on :{port} cfg={cfg}", flush=True)
srv.serve_forever()
"""


def sh(root, script, env):
    return subprocess.run(['bash', '-c', 'source "$1"; ' + script,
                           'x', str(root / 'bin/library.sh')],
                          env=env, text=True, capture_output=True)


def probe(port, key):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/messages",
        data=json.dumps({"model": "claude-opus-5-5", "max_tokens": 8,
                         "messages": [{"role": "user", "content": "hi"}]}).encode(),
        method="POST", headers={"x-api-key": key, "content-type": "application/json",
                                "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def master_key(cfg_text):
    return next(line.split()[1] for line in cfg_text.splitlines()
                if line.strip().startswith('master_key:'))


class SwapModelTests(unittest.TestCase):
    ALIAS_TO = "nvidia-glm53"                  # the rescue model (z-ai/glm-5.3)

    @classmethod
    def setUpClass(cls):
        # These tests bind REAL ports through the launcher's own free_port, whose pick is
        # only momentarily true: two parallel workers can both see a port free and one's
        # start_proxy then lands on it. Hold a machine-wide lock for the whole class so the
        # swap tests serialize against each other (other files still run in parallel).
        cls._lock_fh = open(os.path.join(tempfile.gettempdir(), "free-agents-swap-tests.lock"), "w")
        fcntl.flock(cls._lock_fh, fcntl.LOCK_EX)

    @classmethod
    def tearDownClass(cls):
        fcntl.flock(cls._lock_fh, fcntl.LOCK_UN)
        cls._lock_fh.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for d in ('bin', 'config', 'keys', 'stubs', 'home', 'ctrl'):
            (self.root / d).mkdir()
        for rel in ('bin/remote-session.sh', 'bin/remote-provider-registry.sh',
                    'bin/remote-keys.sh', 'bin/blind-trust-settings.py',
                    'config/remote-agents.sh', 'config/remote-agent-system-prompt.txt',
                    'config/shared-agent-shipping-rules.txt', 'config/emoji.sh',
                    'config/dispatch-preferences.json', 'bin/session-fallbacks.py'):
            shutil.copy2(ROOT / rel, self.root / rel)
        source = (self.root / 'bin/remote-session.sh').read_text()
        (self.root / 'bin/library.sh').write_text(source.split('# ---- argument parsing')[0])
        (self.root / 'keys/nvidia').write_text('fixture-not-a-real-key')
        stub = self.root / 'stubs/litellm'
        stub.write_text(STUB)
        stub.chmod(0o755)
        os.environ["LA_SWAP_TEST_DIR"] = str(self.root / 'ctrl')
        self.addCleanup(os.environ.pop, "LA_SWAP_TEST_DIR", None)
        self.env = dict(os.environ, HOME=str(self.root / 'home'),
                        TMPDIR=str(self.root), LA_API_KEYS_DIR=str(self.root / 'keys'),
                        PATH=str(self.root / 'stubs') + ':' + os.environ['PATH'],
                        CLOUD_BYPASS_PERMISSIONS='0',
                        LA_LITELLM_CMD=str(stub),
                        LA_SWAP_PROBE_TIMEOUT='15',
                        # A private port band, far from the real 4141-4151 session range:
                        # the product band holds ELEVEN ports and several belong to LIVE
                        # sessions, so a parallel suite exhausts it; these tests claim up
                        # to 3 ports each and must never collide with a real session.
                        LA_REMOTE_PROXY_PORT_MIN='47100',
                        LA_REMOTE_PROXY_PORT_MAX='47200')
        self.rundir = self.root / 'local-agents-remote'

    def _stub_proxies_for_root(self):
        """PIDs of stub proxies whose command line names THIS test's own temp dir — never any
        other process on the machine (the match includes this run's unique tmp path)."""
        out = subprocess.run(["pgrep", "-f", f"{self.root}/stubs/litellm"],
                             capture_output=True, text=True)
        return [int(p) for p in out.stdout.split()]

    def tearDown(self):
        # every proxy this test launched, through its own pidfile
        for pf in sorted(self.rundir.glob('proxy-*.pid')) + sorted(self.rundir.glob('swap-scratch-*.pid')):
            try:
                os.kill(int(pf.read_text().strip() or 0), 9)
            except (ProcessLookupError, ValueError):
                pass
        # Guard (0.41.1): a pidfile can lie — test_a_foreign_pid_is_refused overwrites one with
        # a foreign pid, so the pidfile pass can no longer reach the REAL stub and it survived
        # every full-suite run (the "71 orphaned processes" that motivated this handover).
        # Kill by matching this test's own temp dir, then assert nothing of ours survived.
        for pid in self._stub_proxies_for_root():
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        time.sleep(0.3)
        self.assertEqual(self._stub_proxies_for_root(), [],
                         "stub proxy(es) survived tearDown — Leak A is back")

    def start_old_session(self):
        """A running session: start_proxy's artifacts (cfg/pid/log/owner) on a real port."""
        r = sh(self.root, 'port="$(start_proxy nvidia nvidia/nemotron-3-ultra-550b-a55b false)" && '
                          'echo "PORT=$port" && echo "PID=$(cat "$RUNDIR/proxy-$port.pid")"', self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        port = int(next(line for line in r.stdout.splitlines() if line.startswith("PORT=")).split("=")[1])
        pid = int(next(line for line in r.stdout.splitlines() if line.startswith("PID=")).split("=")[1])
        return port, pid

    def swap(self, port, alias, force=False):
        return sh(self.root, f'swap_model {port} {alias} {"force" if force else ""}', self.env)

    def age_log(self, port, seconds=7200):
        log = self.rundir / f'proxy-{port}.log'
        old = time.time() - seconds
        os.utime(log, (old, old))

    # ---- guards --------------------------------------------------------------------------

    def test_a_recently_active_session_is_refused(self):
        port, _ = self.start_old_session()
        (self.rundir / f'proxy-{port}.log').touch()           # answered just now
        r = self.swap(port, self.ALIAS_TO)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("ALIVE", r.stderr)
        cfg = (self.rundir / f'proxy-{port}.yaml').read_text()
        self.assertIn('nemotron-3-ultra', cfg)                 # the session's model is unchanged
        self.assertIn('refusing', r.stderr)

    def test_a_foreign_pid_is_refused(self):
        port, _ = self.start_old_session()
        (self.rundir / f'proxy-{port}.pid').write_text('999999')   # not one of ours
        self.age_log(port)
        r = self.swap(port, self.ALIAS_TO)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("not one of our proxies", r.stderr)

    def test_force_swaps_a_recently_active_session(self):
        port, _ = self.start_old_session()
        (self.rundir / f'proxy-{port}.log').touch()
        r = self.swap(port, self.ALIAS_TO, force=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"swapped :{port} to z-ai/glm-5.3", r.stdout)

    # ---- the swap ------------------------------------------------------------------------

    def test_a_stalled_session_is_swapped_in_place(self):
        port, old_pid = self.start_old_session()
        self.age_log(port)
        owner = (self.rundir / f'proxy-{port}.owner').read_bytes()
        r = self.swap(port, self.ALIAS_TO)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"swapped :{port} to z-ai/glm-5.3", r.stdout)
        self.assertIn("press Enter", r.stdout)
        cfg = (self.rundir / f'proxy-{port}.yaml').read_text()
        primary = cfg.split('la-fallback-1', 1)[0]            # the primary deployments
        self.assertIn('z-ai/glm-5.3', primary)                # the rescue model is the primary
        new_pid = int((self.rundir / f'proxy-{port}.pid').read_text())
        self.assertNotEqual(new_pid, old_pid)
        with self.assertRaises(ProcessLookupError):
            os.kill(old_pid, 0)                                # the old proxy is gone
        os.kill(new_pid, 0)                                    # the new one is live
        self.assertEqual((self.rundir / f'proxy-{port}.owner').read_bytes(), owner,
                         "the .owner file is the session's identity — never touched")
        self.assertTrue(list(self.rundir.glob(f'proxy-{port}.yaml.bak-*')),
                        "the backup must stay on disk as the paper trail")
        self.assertIn("pre-flight probe 200", r.stdout)
        self.assertIn("live probe 200", r.stdout)

    def test_a_sigterm_stubborn_proxy_is_escalated_to_kill_before_rebind(self):
        """2026-10-25 live dogfood, twice: LiteLLM exits GRACEFULLY on SIGTERM (uvicorn
        finishes in-flight work first), so a proxy mid-shutdown keeps its port past the
        bare kill+wait; the new proxy then binds a HELD port and the live probe reads
        000 (auto-revert) while the old model still owns the session. The swap must
        escalate TERM->KILL and only rebind once the port is actually free."""
        port, old_pid = self.start_old_session()
        self.age_log(port)
        (self.root / 'ctrl' / f'ignore-term-{port}').write_text('1')
        r = self.swap(port, self.ALIAS_TO)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with self.assertRaises(ProcessLookupError):
            os.kill(old_pid, 0)          # the stubborn proxy did NOT survive the swap
        new_pid = int((self.rundir / f'proxy-{port}.pid').read_text())
        os.kill(new_pid, 0)              # the new proxy owns the port now
        self.assertEqual(probe(port, master_key(
            (self.rundir / f'proxy-{port}.yaml').read_text())), 200)

    def test_a_failing_new_model_is_reverted(self):
        port, _ = self.start_old_session()
        old_cfg = (self.rundir / f'proxy-{port}.yaml').read_bytes()
        self.age_log(port)
        (self.root / 'ctrl' / f'{port}.fail').write_text('500')   # the live probe will fail
        r = self.swap(port, self.ALIAS_TO)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("reverting", r.stderr)
        self.assertEqual((self.rundir / f'proxy-{port}.yaml').read_bytes(), old_cfg,
                         "the original config is restored byte-for-byte")
        new_pid = int((self.rundir / f'proxy-{port}.pid').read_text())
        os.kill(new_pid, 0)                                    # the reverted proxy is live
        (self.root / 'ctrl' / f'{port}.fail').unlink()          # the throttle was transient
        self.assertEqual(probe(port, master_key(old_cfg.decode())), 200,
                         "the OLD model must answer again after the revert")

    def test_the_revert_waits_for_readiness(self):
        # MUTATION TARGET: if _swap_revert drops its readiness wait, this fails — the
        # relaunch is slow (3 s) and without the wait swap_model would return while the
        # port is still dead.
        port, _ = self.start_old_session()
        self.age_log(port)
        (self.root / 'ctrl' / f'{port}.fail').write_text('500')
        (self.root / 'ctrl' / f'{port}.slow').write_text('3')
        t0 = time.time()
        r = self.swap(port, self.ALIAS_TO)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertGreaterEqual(time.time() - t0, 3.0,
                                 "the revert must not return before the relaunched proxy is ready")
        (self.root / 'ctrl' / f'{port}.fail').unlink()
        (self.root / 'ctrl' / f'{port}.slow').unlink()
        new_pid = int((self.rundir / f'proxy-{port}.pid').read_text())
        os.kill(new_pid, 0)
        self.assertEqual(probe(port, master_key(
            (self.rundir / f'proxy-{port}.yaml').read_text())), 200,
            "the revert returns only once the old proxy ANSWERS — readiness, not just a pid")

    def test_the_swap_scratch_port_stays_outside_the_live_band(self):
        """2026-10-24 live dogfood: the pre-flight scratch landed on 4144 — inside the live
        band. The scratch port must come from a band PAST the configured live range, so a
        rescue can never occupy a port a live session may be about to launch on."""
        scratch = sh(self.root, 'scratch="$(swap_scratch_port)"; echo "SCRATCH=$scratch"', self.env)
        line = next(l for l in scratch.stdout.splitlines() if l.startswith("SCRATCH="))
        port = int(line.split("=")[1])
        self.assertGreater(port, 47200, "past the configured live band")
        self.assertLessEqual(port, 47200 + 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)

class SwapModelCLIDispatch(unittest.TestCase):
    """0.43.x dogfood finding (2026-10-24, Phase-4 live run): the no-alias picker block exec'd
    session-picker BEFORE the swap-model dispatch, so `remote-session.sh --swap-model ...`
    never reached swap_model — dead in the CLI (headless AND interactive). The stub tests
    called swap_model() directly, so only the live dogfood could catch it."""

    def test_the_cli_reaches_swap_model_not_the_picker(self):
        # Port 49999 has no session: swap_model's OWN refusal must be the output — not the
        # picker's TTY error (which is how the bug manifested headless).
        r = subprocess.run(
            ["bash", str(ROOT / "bin" / "remote-session.sh"),
             "--swap-model", "49999", "nvidia-deepseek-v4"],
            capture_output=True, text=True, timeout=30)
        combined = r.stdout + r.stderr
        self.assertNotIn("session-picker", combined,
                         "the no-alias picker block must not intercept --swap-model")
        self.assertIn("missing", combined,
                      "swap_model's own refusal for a port with no session")
        self.assertEqual(r.returncode, 2)


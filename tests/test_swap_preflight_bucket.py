#!/usr/bin/env python3
"""Task 7 Step 1 — swap pre-flight through the bucket, target-only (C5). RED first.

Drives the REAL remote-session.sh swap_model against an extended stub proxy
(LA_LITELLM_CMD) that RECORDS how it was started: its env's LA_PROXY_HOOKS_RATE_LIMIT
and a copy of its config. Verdicts come from _swap_probe's PREFLIGHT lines.

  - the pre-flight runs WITH the limiter (no LA_PROXY_HOOKS_RATE_LIMIT=0)
  - the scratch config has no fallback chain and num_retries: 0
  - 200 from the target proceeds; 410 aborts with nothing changed; 429 aborts
    "throttled" unless --force
  - a slow-but-progressing start is not aborted (progress-based readiness;
    LA_SWAP_TEST_FAST=1 scales the waits — and is ignored outside pytest)
  - a dead current route (402) allows a paid_only target, labelled
  - LA_SWAP_SKIP_PREFLIGHT=1 (the superseded branch's knob) has NO effect
"""
import atexit
import fcntl
import glob
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 0.66.2 isolation (waypoint swap-model-preflight-rate): no fixed sandbox ports. The band is
# derived at setUp from a free contiguous range (probed by bind), and stub cleanup survives a
# timeout-killed worker: every stub records "<pid> <lstart>" in <tmp>/ctrl/<port>.stub.pid,
# and the next run's setUpClass (plus atexit) reaps roots whose markers are dead, reused, or
# orphaned (ppid 1) — never by port, which belongs to whichever run derived it.


def _proc_start(pid):
    """macOS lstart of pid ("" when gone) — the pid-reuse guard."""
    r = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def _proc_ppid(pid):
    r = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return -1


def _free_band(count, floor=20000, start=None):
    """First run of `count` contiguous ports that all bind right now (held, then released).

    Probed by bind, never a fixed range: concurrent runs from other worktrees must not
    collide. `floor` keeps the band clear of the live proxy/local bands; `start` forces the
    search to begin at a chosen port (tests)."""
    if start is not None:
        lo = max(floor, start)
    else:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        lo = max(floor, s.getsockname()[1])
        s.close()
    while lo + count <= 65535:
        held = []
        blocked = None
        for p in range(lo, lo + count):
            sk = socket.socket()
            try:
                sk.bind(("127.0.0.1", p))
                held.append(sk)
            except OSError:
                sk.close()
                blocked = p
                break
        for sk in held:
            sk.close()
        if blocked is None:
            return lo
        lo = blocked + 1
    raise RuntimeError(f"no contiguous free band of {count} ports below 65535")


def _reap_stale_stub_roots():
    """Reap stub proxies leaked by a timeout-killed worker, by marker — never by port."""
    base = tempfile.gettempdir()
    by_root = {}
    for marker in glob.glob(os.path.join(base, "fa-swap-test-*", "ctrl", "*.stub.pid")):
        by_root.setdefault(Path(marker).parents[1], []).append(marker)
    for root, markers in by_root.items():
        live = False
        for marker in markers:
            try:
                # split(None, 1): lstart is multi-word ("Mon Nov 28 11:20:00 2026")
                parts = Path(marker).read_text().split(None, 1)
                if len(parts) != 2:
                    continue
                pid = int(parts[0])
                start = parts[1].strip()
            except (OSError, ValueError):
                continue
            cur = _proc_start(pid)
            if cur and cur == start:
                if _proc_ppid(pid) == 1:
                    try:
                        os.kill(pid, 9)   # orphaned stub of a dead worker
                    except OSError:
                        pass
                else:
                    live = True   # another live run owns this root — leave it alone
                    break
        if not live:
            shutil.rmtree(root, ignore_errors=True)


atexit.register(_reap_stale_stub_roots)

# Extended stub: health always unless ctrl/<port>.nohealth exists; /v1/messages answers
# 200 unless ctrl/<port>.failcode (one-shot, its content is the HTTP code) or
# ctrl/<port>.failalways (persistent) exists; ctrl/<port>.slow holds seconds to sleep
# BEFORE binding while logging every second (progress-based readiness must not abort it).
# At startup it records its env knob and copies its config into ctrl/.
STUB = """#!/usr/bin/env python3
import http.server, json, os, shutil, signal, subprocess, sys, time

port = None
cfg = None
it = iter(sys.argv[1:])
for a in it:
    if a == "--port":
        port = int(next(it))
    elif a == "--config":
        cfg = next(it)
ctrl = os.environ["LA_SWAP_TEST_DIR"]
fast = os.environ.get("LA_SWAP_TEST_FAST") == "1"

os.makedirs(ctrl, exist_ok=True)
if port is not None:
    # 0.66.2: record "<pid> <lstart>" so a LATER run can reap this stub if our worker is
    # timeout-killed (tearDown never runs) — reaped by marker, never by port.
    _start = subprocess.check_output(["ps", "-o", "lstart=", "-p", str(os.getpid())], text=True).strip()
    with open(os.path.join(ctrl, f"{port}.stub.pid"), "w") as f:
        f.write(f"{os.getpid()} {_start}\n")
with open(os.path.join(ctrl, f"{port}.env"), "w") as f:
    f.write("LA_PROXY_HOOKS_RATE_LIMIT=" + os.environ.get("LA_PROXY_HOOKS_RATE_LIMIT", "<unset>") + "\\n")
if cfg:
    shutil.copyfile(cfg, os.path.join(ctrl, f"{port}.cfg"))

slow = 0
p = os.path.join(ctrl, f"{port}.slow")
if not os.path.exists(p):
    p = os.path.join(ctrl, "slow-all")     # band-independent: every stub start slows...
if os.path.exists(p) and not os.path.exists(os.path.join(ctrl, f"{port}.noslow")):
    slow = float(open(p).read().strip() or 0)   # ...except ports explicitly marked live
    if fast:
        slow = min(slow, 15)
end = time.time() + slow
while time.time() < end:
    print(f"stub :{port} still starting ({slow:.0f}s budget)", flush=True)
    # 0.62.0 final gate: 0.3 s cadence under the fast knob — a 1 s cadence starved under a
    # loaded box and the (correct) progress-based readiness aborted a healthy slow start
    time.sleep(0.3 if fast else 10)

def _on_term(signum, frame):
    sys.exit(0)

signal.signal(signal.SIGTERM, _on_term)

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health/liveliness":
            if os.path.exists(os.path.join(ctrl, f"{port}.nohealth")):
                # R1-C root-cause knob: drop the connection (curl rc 52) so health counts
                # as SILENT — a 500 would still be an answered probe under `curl -s`.
                self.close_connection = True
                return
            self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        fp = os.path.join(ctrl, f"{port}.failcode")
        fa = os.path.join(ctrl, f"{port}.failalways")
        # one-shot: failcode applies to the FIRST request only (models "the route died
        # once, then answered"), so the dead-route probe can fail and the swap still lands;
        # failalways persists (live-probe-failure / revert tests)
        if os.path.exists(fa):
            code = int(open(fa).read().strip())
        elif os.path.exists(fp):
            code = int(open(fp).read().strip())
            os.unlink(fp)
        else:
            code = 200
        out = json.dumps({"id": "msg_1", "type": "message", "role": "assistant",
                          "model": "x", "content": [{"type": "text", "text": "PONG"}],
                          "stop_reason": "end_turn", "stop_sequence": None,
                          "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
        self.send_response(code)
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


class SwapPreflightBucketTests(unittest.TestCase):
    ALIAS_TO = "nvidia-glm53"
    ALIAS_PAID = "mistral-large-4"          # tier: paid_only in the fixture registry

    @classmethod
    def setUpClass(cls):
        _reap_stale_stub_roots()   # 0.66.2: reap stubs leaked by a timeout-killed prior run
        # per-checkout lock: two worktrees may run this file concurrently (derived bands);
        # runs within one checkout still serialize.
        digest = hashlib.md5(str(ROOT).encode()).hexdigest()[:8]
        cls._lock_fh = open(os.path.join(tempfile.gettempdir(),
                                         f"free-agents-swap-tests-{digest}.lock"), "w")
        fcntl.flock(cls._lock_fh, fcntl.LOCK_EX)

    @classmethod
    def tearDownClass(cls):
        fcntl.flock(cls._lock_fh, fcntl.LOCK_UN)
        cls._lock_fh.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fa-swap-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # 0.66.2: derive the sandbox band from a free contiguous range — MIN..MAX plus the
        # MAX+1..MAX+20 swap scratch band = 121 ports — instead of a fixed range, so
        # concurrent runs from other worktrees cannot collide.
        band_lo = _free_band(121)
        self.port_min, self.port_max = band_lo, band_lo + 100
        self.scratch_ports = range(band_lo + 101, band_lo + 121)
        for d in ('bin', 'config', 'keys', 'stubs', 'home', 'ctrl'):
            (self.root / d).mkdir()
        for rel in ('bin/remote-session.sh', 'bin/remote-provider-registry.sh',
                    'bin/remote-keys.sh', 'bin/blind-trust-settings.py',
                    'config/remote-agents.sh', 'config/remote-agent-system-prompt.txt',
                    'config/shared-agent-shipping-rules.txt', 'config/emoji.sh',
                    'config/dispatch-preferences.json', 'bin/session-fallbacks.py',
                    'config/broken-nvidia-models.json'):
            shutil.copy2(ROOT / rel, self.root / rel)
        source = (self.root / 'bin/remote-session.sh').read_text()
        (self.root / 'bin/library.sh').write_text(source.split('# ---- argument parsing')[0])
        (self.root / 'keys/nvidia').write_text('fixture-not-a-real-key')
        (self.root / 'keys/mistral').write_text('fixture-not-a-real-key')
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
                        PYTEST_CURRENT_TEST='1',
                        LA_REMOTE_PROXY_PORT_MIN=str(self.port_min),
                        LA_REMOTE_PROXY_PORT_MAX=str(self.port_max))
        self.rundir = self.root / 'local-agents-remote'

    def _stub_proxies_for_root(self):
        out = subprocess.run(["pgrep", "-f", f"{self.root}/stubs/litellm"],
                             capture_output=True, text=True)
        return [int(p) for p in out.stdout.split()]

    def tearDown(self):
        for pf in sorted(self.rundir.glob('proxy-*.pid')) + sorted(self.rundir.glob('swap-scratch-*.pid')):
            try:
                os.kill(int(pf.read_text().strip() or 0), 9)
            except (ProcessLookupError, ValueError):
                pass
        for pid in self._stub_proxies_for_root():
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
        time.sleep(0.3)
        self.assertEqual(self._stub_proxies_for_root(), [], "stub proxy(es) survived tearDown")

    def start_old_session(self):
        r = sh(self.root, 'port="$(start_proxy nvidia nvidia/nemotron-3-ultra-550b-a55b false)" && '
                          'echo "PORT=$port" && echo "PID=$(awk \'{print $1}\' "$RUNDIR/proxy-$port.pid")"', self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        port = int(next(l for l in r.stdout.splitlines() if l.startswith("PORT=")).split("=")[1])
        pid = int(next(l for l in r.stdout.splitlines() if l.startswith("PID=")).split("=")[1])
        return port, pid

    def swap(self, port, alias, force=False, extra_env=None):
        env = dict(self.env, **(extra_env or {}))
        return sh(self.root, f'swap_model {port} {alias} {"force" if force else ""}', env)

    def age_log(self, port, seconds=7200):
        log = self.rundir / f'proxy-{port}.log'
        old = time.time() - seconds
        os.utime(log, (old, old))

    def _scratch_recordings(self):
        """ctrl/<port>.env + <port>.cfg from the stub proxies this test started."""
        ctrl = self.root / 'ctrl'
        envs = {p.stem: p.read_text() for p in ctrl.glob('*.env')}
        cfgs = {p.stem: p.read_text() for p in ctrl.glob('*.cfg')}
        return envs, cfgs

    # ---- the seven behaviors -------------------------------------------------------------

    def test_preflight_runs_with_the_limiter(self):
        port, pid = self.start_old_session()
        self.age_log(port)
        r = self.swap(port, self.ALIAS_TO)
        self.assertEqual(r.returncode, 0, r.stderr)
        envs, _cfgs = self._scratch_recordings()
        scratch_envs = [v for k, v in envs.items() if k not in (str(port),)]
        self.assertTrue(scratch_envs, "no scratch proxy recorded")
        for v in scratch_envs:
            self.assertNotIn("LA_PROXY_HOOKS_RATE_LIMIT=0", v, v)

    def test_preflight_config_has_no_fallbacks(self):
        port, pid = self.start_old_session()
        self.age_log(port)
        r = self.swap(port, self.ALIAS_TO)
        self.assertEqual(r.returncode, 0, r.stderr)
        _envs, cfgs = self._scratch_recordings()
        scratch_cfgs = [t for k, t in cfgs.items() if k != str(port)]
        self.assertTrue(scratch_cfgs, "no scratch config recorded")
        for t in scratch_cfgs:
            self.assertNotIn("la-fallback", t, "target-only config must have no fallback chain")
            self.assertIn("num_retries: 0", t, "target-only config must not retry")

    def test_preflight_200_from_target_proceeds(self):
        port, pid = self.start_old_session()
        self.age_log(port)
        r = self.swap(port, self.ALIAS_TO)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("PREFLIGHT ok", r.stdout)

    def test_preflight_410_aborts_nothing_changed(self):
        port, pid = self.start_old_session()
        self.age_log(port)
        ctrl = self.root / 'ctrl'
        # every scratch port in the band answers 410 once; the live port stays healthy
        for p in self.scratch_ports:   # the SWAP SCRATCH band is MAX+1..+20 (derived at setUp)
            if p != port:
                (ctrl / f"{p}.failcode").write_text("410")
        r = self.swap(port, self.ALIAS_TO)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PREFLIGHT refused 410", r.stdout + r.stderr)
        # nothing changed: the live pid is still the same
        self.assertEqual(int((self.rundir / f'proxy-{port}.pid').read_text().split()[0]), pid)

    def test_preflight_429_aborts_throttled_unless_force(self):
        port, pid = self.start_old_session()
        self.age_log(port)
        ctrl = self.root / 'ctrl'
        # every scratch port in the band answers 429 once; the live port stays healthy
        for p in self.scratch_ports:   # the SWAP SCRATCH band is MAX+1..+20 (derived at setUp)
            if p != port:
                (ctrl / f"{p}.failcode").write_text("429")
        r = self.swap(port, self.ALIAS_TO)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PREFLIGHT throttled", r.stdout + r.stderr)
        self.assertIn("throttled", r.stderr.lower())
        self.assertEqual(int((self.rundir / f'proxy-{port}.pid').read_text().split()[0]), pid)
        # with --force the throttled pre-flight is overridden and the swap proceeds
        for p in self.scratch_ports:   # the SWAP SCRATCH band is MAX+1..+20 (derived at setUp)
            (ctrl / f"{p}.failcode").unlink(missing_ok=True)
        for p in self.scratch_ports:   # the SWAP SCRATCH band is MAX+1..+20 (derived at setUp)
            if p != port:
                (ctrl / f"{p}.failcode").write_text("429")
        r2 = self.swap(port, self.ALIAS_TO, force=True)
        self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)
        self.assertIn("--force", r2.stdout + r2.stderr)

    def test_slow_but_progressing_start_is_not_aborted(self):
        port, pid = self.start_old_session()
        self.age_log(port)
        ctrl = self.root / 'ctrl'
        # the NEXT stub to start sleeps 150 s while logging every second (15 s under the
        # test knob) — progress-based readiness must wait it out, not abort at 60 s/90 s.
        # (band-independent: any stub port slows, but not the already-running live one)
        (ctrl / f"{port}.noslow").write_text("1")     # marker: live port must NOT slow
        (ctrl / "slow-all").write_text("150")
        env = dict(self.env, LA_SWAP_TEST_FAST="1", LA_PROXY_READY_CAP="120")
        r = sh(self.root, f'swap_model {port} {self.ALIAS_TO}', env)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("PREFLIGHT ok", r.stdout)

    def test_dead_route_may_target_paid_only(self):
        port, pid = self.start_old_session()
        self.age_log(port)
        ctrl = self.root / 'ctrl'
        (ctrl / f"{port}.failcode").write_text("402")   # the CURRENT route is dead (402)
        r = self.swap(port, self.ALIAS_PAID)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertIn("paid_only", r.stderr + r.stdout, "the paid-only target must be labelled")

    def test_reachable_model_still_aborts_when_scratch_health_is_silent(self):
        # R1-C root cause, pinned (2026-11-21 symptom): the scratch proxy's /health never
        # answers but /v1/messages answers 200 — the no-progress ready window aborts although
        # the target is reachable. waypoint swap-model-preflight-rate carries the repro.
        port, pid = self.start_old_session()
        self.age_log(port)
        ctrl = self.root / 'ctrl'
        for p in self.scratch_ports:   # the SWAP SCRATCH band is MAX+1..+20 (derived at setUp)
            if p != port:
                (ctrl / f"{p}.nohealth").write_text("1")
        r = self.swap(port, self.ALIAS_TO, extra_env={"LA_SWAP_TEST_FAST": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not become ready", r.stdout + r.stderr)
        # nothing changed: the live pid is untouched
        self.assertEqual(int((self.rundir / f'proxy-{port}.pid').read_text().split()[0]), pid)

    def test_skip_override_skips_scratch_preflight_only(self):
        # R1-C/D3: LA_SWAP_SKIP_PREFLIGHT=1 is a documented permanent opt-in hatch: NO scratch
        # proxy starts, but the live probe + auto-revert still run and the swap lands.
        port, pid = self.start_old_session()
        self.age_log(port)
        r = self.swap(port, self.ALIAS_TO, extra_env={"LA_SWAP_SKIP_PREFLIGHT": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        envs, _cfgs = self._scratch_recordings()
        scratch_envs = [v for k, v in envs.items() if k != str(port)]
        self.assertEqual(scratch_envs, [], "with the override set NO scratch proxy may start")
        self.assertIn("skipped", (r.stdout + r.stderr).lower())
        self.assertIn("live probe", r.stdout)   # the live probe still ran
        self.assertNotEqual(int((self.rundir / f'proxy-{port}.pid').read_text().split()[0]), pid)

    def test_skip_override_still_reverts_when_live_probe_fails(self):
        # the hatch skips only the SCRATCH pre-flight; a failing LIVE probe still auto-reverts.
        port, pid = self.start_old_session()
        self.age_log(port)
        ctrl = self.root / 'ctrl'
        (ctrl / f"{port}.failalways").write_text("410")   # the LIVE port fails every probe
        r = self.swap(port, self.ALIAS_TO, extra_env={"LA_SWAP_SKIP_PREFLIGHT": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("live probe", r.stdout)
        # reverted: the live config is back to the old model
        yaml = (self.rundir / f'proxy-{port}.yaml').read_text()
        self.assertIn("nemotron", yaml, "auto-revert must restore the old model's config")


class SwapTestIsolationTests(unittest.TestCase):
    """0.66.2 isolation helpers (waypoint swap-model-preflight-rate) — fast, no proxy started."""

    def test_free_band_returns_a_contiguous_bindable_run(self):
        lo = _free_band(8)
        held = []
        try:
            for p in range(lo, lo + 8):
                sk = socket.socket()
                sk.bind(("127.0.0.1", p))   # raises unless the whole run was free
                held.append(sk)
        finally:
            for sk in held:
                sk.close()
        self.assertGreaterEqual(lo, 20000)   # never the live proxy/local bands

    def test_free_band_slides_past_a_taken_port(self):
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        x = blocker.getsockname()[1]
        try:
            lo = _free_band(8, start=x - 3)   # force the candidate run to straddle x
            self.assertNotIn(x, range(lo, lo + 8), "a taken port must never be in the band")
            held = []
            try:
                for p in range(lo, lo + 8):
                    sk = socket.socket()
                    sk.bind(("127.0.0.1", p))
                    held.append(sk)
            finally:
                for sk in held:
                    sk.close()
        finally:
            blocker.close()

    def test_reaper_reaps_dead_and_reused_markers_but_keeps_a_live_run(self):
        live = subprocess.Popen(["sleep", "30"])
        dead = subprocess.Popen(["sleep", "0"])
        dead.wait()
        self.addCleanup(lambda: live.kill() if live.poll() is None else None)
        base = Path(tempfile.gettempdir())
        cases = (("dead", dead.pid, "whatever"),
                 ("reused", os.getpid(), "bogus-lstart"),
                 ("live", live.pid, _proc_start(live.pid)))
        roots = {}
        for name, pid, start in cases:
            root = base / f"fa-swap-test-reap-{name}-{os.getpid()}"
            (root / "ctrl").mkdir(parents=True)
            (root / "ctrl" / "1.stub.pid").write_text(f"{pid} {start}\n")
            roots[name] = root
        try:
            _reap_stale_stub_roots()
            self.assertFalse(roots["dead"].exists(), "dead-pid root must be reaped")
            self.assertFalse(roots["reused"].exists(), "pid-reused root must be reaped")
            self.assertTrue(roots["live"].exists(), "a live non-orphan run's root must survive")
            self.assertIsNone(live.poll(), "the reaper must never kill the live marker's process")
            os.kill(os.getpid(), 0)
        finally:
            for r in roots.values():
                shutil.rmtree(r, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)

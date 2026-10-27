#!/usr/bin/env python3
"""tests/test_prefill_measure.py — the prefill measurement tool (cloud-screen plan Task 10).

What this protects: the tool measures a session's true prefill on a FREE lane only (a headless
`claude -p` through the lane's own launcher, never a bare claude that could inherit a paid
gateway URL), sums the usage fields honestly, and caches measurements keyed by an inputs hash
so a stale number can never masquerade as current. Each test names the change that breaks it.
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "bin" / "fa-prefill-measure.py"
spec = importlib.util.spec_from_file_location("fa_prefill_measure", TOOL)
fpm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fpm)

CANNED_RESULT = {
    "type": "result",
    "subtype": "success",
    "result": "ok",
    "usage": {"input_tokens": 31000, "cache_creation_input_tokens": 1200,
              "cache_read_input_tokens": 255800},
}

# A failed launch still emits its result JSON on stdout, but a failed run carries no usage
# block — only the error verdict (measured: a Vertex 402 quota rejection surfaced here while
# stderr held a German shell job-done notice).
FAILED_VERDICT = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "result": "API Error: 402 quota exceeded (vertex)",
    "api_error_status": 402,
}


def write_launcher(tmp_path, behavior="ok", extra=None):
    """A stub remote-session.sh: records its argv and env, prints the canned claude JSON."""
    stub = tmp_path / "remote-session.sh"
    body = ["#!/usr/bin/env bash",
            'printf "%s\\n" "$@" > "$(dirname "$0")/launcher-argv.txt"',
            'env | grep -E "^(LA_REMOTE_ENABLE_MCP|LA_THINKING_OVERRIDE|ANTHROPIC_BASE_URL)=" '
            '> "$(dirname "$0")/launcher-env.txt" || true']
    if behavior == "ok":
        body.append(f'cat <<\'JSON\'\n{json.dumps(CANNED_RESULT)}\nJSON')
        body.append("exit 0")
    elif behavior == "fail":
        body.append('echo "boom" >&2; exit 1')
    elif behavior == "verdict":
        body.append(f'cat <<\'JSON\'\n{json.dumps(FAILED_VERDICT)}\nJSON')
        body.append('echo "Fertig" >&2; exit 1')
    body += extra or []
    stub.write_text("\n".join(body) + "\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


class MeasureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name) / "state"
        self.launcher = write_launcher(Path(self.tmp.name))
        self.env = {"FA_PREFILL_STATE_DIR": str(self.state_dir),
                    "FA_PREFILL_LAUNCHER": str(self.launcher),
                    "FA_PREFILL_ALIAS": "fixture-alias"}
        for k in ("ANTHROPIC_BASE_URL", "CLAUDE_CODE_SESSION_ID"):
            self.env[k] = self.env.get(k, "")

    def test_total_prefill_is_the_sum_of_usage_fields(self):
        # Fails if the tool reports a made-up total or drops a cache field: the prefill the
        # request carried is uncached + written + read, all three, straight from usage.
        res = fpm.measure("remote", {}, env=dict(self.env, ANTHROPIC_BASE_URL=""))
        self.assertEqual(res["input_tokens"], 31000)
        self.assertEqual(res["cache_creation_input_tokens"], 1200)
        self.assertEqual(res["cache_read_input_tokens"], 255800)
        self.assertEqual(res["total_prefill"], 31000 + 1200 + 255800)
        self.assertIn("at", res)
        self.assertIn("inputs_hash", res)

    def test_levers_reach_the_launcher_as_env(self):
        # Fails if a lever only changes the cache key: the measurement must actually RUN
        # differently — mcp=none and mcp=all must set LA_REMOTE_ENABLE_MCP for the launch.
        fpm.measure("remote", {"mcp": "all"}, env=dict(self.env, ANTHROPIC_BASE_URL=""))
        env_on = (Path(self.tmp.name) / "launcher-env.txt").read_text()
        self.assertIn("LA_REMOTE_ENABLE_MCP=1", env_on)
        fpm.measure("remote", {"mcp": "none"}, env=dict(self.env, ANTHROPIC_BASE_URL=""))
        env_off = (Path(self.tmp.name) / "launcher-env.txt").read_text()
        self.assertIn("LA_REMOTE_ENABLE_MCP=0", env_off)

    def test_cache_round_trip_and_staleness(self):
        # Fails if a measurement is recomputed when nothing changed, or REUSED when the
        # machine's inputs changed: the inputs hash (CLAUDE.md among them) is the cache key.
        first = fpm.measure("remote", {}, env=dict(self.env, ANTHROPIC_BASE_URL=""))
        hit = fpm.cached("remote", {}, env=dict(self.env, ANTHROPIC_BASE_URL=""))
        self.assertIsNotNone(hit)
        self.assertEqual(hit["total_prefill"], first["total_prefill"])
        # The inputs change: a different CLAUDE.md hash must stale the entry.
        env2 = dict(self.env, ANTHROPIC_BASE_URL="",
                    FA_PREFILL_CLAUDE_MD=str(Path(self.tmp.name) / "CLAUDE.md"))
        Path(env2["FA_PREFILL_CLAUDE_MD"]).write_text("# changed inputs\n")
        stale = fpm.cached("remote", {}, env=env2)
        self.assertIsNone(stale, "a changed CLAUDE.md must stale the cached measurement")
        fresh = fpm.measure("remote", {}, env=env2)
        self.assertNotEqual(fresh["inputs_hash"], first["inputs_hash"])

    def test_never_runs_against_a_cloud_base_url(self):
        # Fails if the tool would run inside a paid session's env: a nested bare claude
        # inherits the ambient base URL and BILLS (measured $0.19 surprise, handoff rule 8).
        # A cloud-looking ambient URL must refuse before any launcher call.
        cloud_env = dict(self.env, ANTHROPIC_BASE_URL="https://gateway.joyia.example/api")
        launcher_before = (Path(self.tmp.name) / "launcher-argv.txt").exists()
        with self.assertRaises(fpm.CloudBaseURLError):
            fpm.measure("remote", {}, env=cloud_env)
        self.assertEqual((Path(self.tmp.name) / "launcher-argv.txt").exists(), launcher_before,
                         "nothing may launch when the guard fires")

    def test_unsupported_lane_names_the_gap_instead_of_running(self):
        # Fails if an unimplemented lane silently runs the remote vehicle: the receipt must
        # never mislabel a lane.
        with self.assertRaises(ValueError) as cm:
            fpm.measure("local", {}, env=dict(self.env, ANTHROPIC_BASE_URL=""))
        self.assertIn("local", str(cm.exception))

    def test_launcher_failure_is_an_error_not_a_zero(self):
        # Fails if a broken launch is cached as a measurement: a failed run produces no
        # number at all.
        write_launcher(Path(self.tmp.name), behavior="fail")
        with self.assertRaises(RuntimeError):
            fpm.measure("remote", {}, env=dict(self.env, ANTHROPIC_BASE_URL=""))
        self.assertIsNone(fpm.cached("remote", {}, env=dict(self.env, ANTHROPIC_BASE_URL="")))

    def test_failure_reports_the_structured_verdict_first(self):
        # Fails if a failed launch reports the stderr tail instead of the verdict the
        # launcher already printed on stdout: stderr carries teardown noise (measured: a
        # shell job-done notice masked a Vertex 402 quota verdict sitting in the result
        # JSON), and the structured diagnosis is the actionable one.
        write_launcher(Path(self.tmp.name), behavior="verdict")
        with self.assertRaises(RuntimeError) as cm:
            fpm.measure("remote", {}, env=dict(self.env, ANTHROPIC_BASE_URL=""))
        msg = str(cm.exception)
        self.assertIn("402", msg)
        self.assertIn("quota", msg)
        self.assertNotIn("Fertig", msg, "teardown noise must not be the diagnosis")

    def test_failure_without_a_verdict_still_names_exit_and_output(self):
        # Fails if the structured-verdict path breaks the plain failure report: a launch
        # that printed nothing parseable must still raise with the exit code and the
        # output tail — that tail is the only diagnosis those runs have.
        write_launcher(Path(self.tmp.name), behavior="fail")
        with self.assertRaises(RuntimeError) as cm:
            fpm.measure("remote", {}, env=dict(self.env, ANTHROPIC_BASE_URL=""))
        msg = str(cm.exception)
        self.assertIn("exit 1", msg)
        self.assertIn("boom", msg)

    def test_cli_json_and_guard(self):
        # Fails if the CLI cannot print the measured JSON or skips the cloud guard.
        clean = dict(os.environ, **self.env)
        clean["ANTHROPIC_BASE_URL"] = ""
        r = subprocess.run([sys.executable, str(TOOL), "--lane", "remote", "--json"],
                           env=clean, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["total_prefill"], 31000 + 1200 + 255800)
        cloud = dict(clean, ANTHROPIC_BASE_URL="https://api.anthropic.example")
        r2 = subprocess.run([sys.executable, str(TOOL), "--lane", "remote", "--json"],
                            env=cloud, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn("ANTHROPIC_BASE_URL", r2.stderr)
        r3 = subprocess.run([sys.executable, str(TOOL), "--help"],
                            env=dict(os.environ), capture_output=True, text=True, timeout=60)
        self.assertEqual(r3.returncode, 0)
        self.assertIn("--lever", r3.stdout)


if __name__ == "__main__":
    unittest.main()


class TimeoutCleanupTests(unittest.TestCase):
    """A timed-out launch must kill the launcher's whole process GROUP, not just the
    launcher: remote-session.sh spawns the proxy as its own child, and a wrapper-only kill
    orphaned a live LiteLLM proxy on a scratch port (measured 2026-10-25: the 08:46
    measurement run's proxy outlived its 900 s timeout and had to be killed by hand)."""

    def test_timeout_kills_the_whole_process_group(self):
        # Fails if only the launcher process is killed: the stub launcher backgrounds a
        # sleeper and traps SIGTERM itself, so a bare process kill leaves the sleeper alive.
        tmp = Path(tempfile.mkdtemp())
        stub = tmp / "slow-launcher.sh"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "trap '' TERM INT\n"
            "# The background child must NOT hold the captured stdout/stderr pipes, or the\n"
            "# caller blocks on EOF for the full sleep even after the launcher is killed.\n"
            "sleep 300 >/dev/null 2>&1 </dev/null &\n"
            "echo $! > \"$STUB_CHILD_PID\"\n"
            "# The launcher's own foreground wait is what survives to the timeout; it must\n"
            "# also be pipe-detached, or ITS orphan holds the pipes for the same reason.\n"
            "sleep 300 >/dev/null 2>&1 </dev/null\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        env = {"FA_PREFILL_STATE_DIR": str(tmp / "state"),
               "FA_PREFILL_LAUNCHER": str(stub),
               "FA_PREFILL_ALIAS": "fixture",
               "FA_PREFILL_TIMEOUT": "2",
               "ANTHROPIC_BASE_URL": "",
               "STUB_CHILD_PID": str(tmp / "child-pid")}
        child_pid_file = Path(env["STUB_CHILD_PID"])
        with self.assertRaises(RuntimeError) as cm:
            fpm.measure("remote", {}, env=env)
        self.assertIn("timed out", str(cm.exception))
        deadline = time.time() + 5
        while not child_pid_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        child = int(child_pid_file.read_text().strip())
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break                       # the sleeper is gone: the group kill worked
            time.sleep(0.1)
        else:
            self.fail(f"the launcher's child (pid {child}) survived the timeout — orphaned")

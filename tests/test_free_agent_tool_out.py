#!/usr/bin/env python3
"""free-agent-tool.py --out FILE persists the full dispatch envelope, atomically.

0.55.6: callers kept saving just the `content` field of the result, losing model/route/attempts.
`--out FILE` makes the tool itself write EXACTLY the JSON object it prints (the full envelope on
success, the error object on total failure) to FILE — tmp file in the same directory, then rename.

These tests drive the REAL CLI as a subprocess against a fake provider on a scratch loopback port
(LA_REMOTE_REGISTRY_JSON points the real provider id "nvidia" — the only id the dispatcher's MVP
list accepts — at the fake; LA_API_KEYS_DIR / FA_DISPATCH_PREFS / FA_QUOTA_LEDGER_DIR /
LA_NVIDIA_THROTTLE_STATE are temp paths), so no network, no real key and no real quota is touched.
The fake counts its hits, which proves the argument guards exit BEFORE any dispatch is attempted.

Usage: python3 tests/test_free_agent_tool_out.py [-v]   (also runs under pytest)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "bin" / "free-agent-tool.py"

spec = importlib.util.spec_from_file_location("free_agent_tool_out", TOOL)
assert spec is not None and spec.loader is not None
fat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fat)

STUB_ANSWER = "STUB ANSWER"
PREFS = {"order": [{"id": "stub1", "kind": "remote", "provider": "nvidia", "model": "stub-model",
                    "display": "Stub One", "smoke_tested": True}]}


class FakeProvider:
    """A fake chat-completions server on a scratch loopback port plus a registry JSON that points
    provider `stub` at it (via LA_REMOTE_REGISTRY_JSON). Counts hits so tests can prove no dispatch
    was attempted."""

    def __init__(self, tmp: Path, status: int = 200, err_text: str = "boom"):
        self.hits = 0
        self.status = status
        self.err_text = err_text
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                outer.hits += 1
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                if outer.status != 200:
                    body = json.dumps({"error": {"message": outer.err_text}}).encode()
                    self.send_response(outer.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"model":"stub-model","choices":[{"delta":{"content":"'
                                 + STUB_ANSWER.encode() + b'"}}]}\n\n')
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.registry = tmp / "registry.json"
        self.registry.write_text(json.dumps({
            "providers": {"nvidia": {
                "id": "nvidia", "display": "NVIDIA NIM (fake)", "tier": "unknown",
                "base_url": f"http://127.0.0.1:{self.port}/v1",
                "chat_path": "/chat/completions", "models_path": "/models", "catalog_url": "",
                "key_env": "NVIDIA_API_KEY", "extra_env": [], "default_models": ["stub-model"],
            }},
            "entries": [],
        }))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.fake = FakeProvider(t)
        self.addCleanup(self.fake.close)
        self.keys = t / "keys"
        self.keys.mkdir()
        (self.keys / "nvidia").write_text("fixture-key\n")   # free-agent-tool's availability check
        self.prefs = t / "prefs.json"
        self.prefs.write_text(json.dumps(PREFS))
        # Scrub inherited LA_* (a live-session launcher exports ~37 of them); the conftest sandbox
        # knobs are LA_* too, so re-point the shared-state fences at this test's tmp dir, FORCED.
        e = {k: v for k, v in os.environ.items() if not k.startswith("LA_")}
        e.pop("CLAUDE_CODE_SESSION_ID", None)
        e.pop("NVIDIA_API_KEY", None)
        e.update({
            "LA_REMOTE_REGISTRY_JSON": str(self.fake.registry),
            "LA_API_KEYS_DIR": str(self.keys),
            "FA_DISPATCH_PREFS": str(self.prefs),
            "FA_FALLBACK_MARK_DIR": str(t / "marks"),
            "FA_QUOTA_LEDGER_DIR": str(t / "quota"),
            "LA_NVIDIA_THROTTLE_STATE": str(t / "throttle.json"),   # fresh bucket: never the real one
            "LA_NVIDIA_RPM": "1000", "LA_NVIDIA_MAX_WAIT": "0",      # never block on the fake
            "NVIDIA_API_KEY": "fixture-key",                  # the dispatcher's own key lookup
        })
        self.env = e

    def run_tool(self, *args, stdin=""):
        return subprocess.run([sys.executable, str(TOOL), *args], env=self.env, cwd=ROOT,
                              input=stdin, capture_output=True, text=True, timeout=120)

    def test_success_writes_the_full_envelope_equal_to_stdout(self):
        out = Path(self.tmp.name) / "envelope.json"
        r = self.run_tool("--out", str(out), stdin=json.dumps({"prompt": "hi"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        envelope = json.loads(r.stdout)
        self.assertEqual(envelope["content"], STUB_ANSWER)
        self.assertEqual(envelope["model_used"], "stub1")
        self.assertIn("attempts", envelope)                    # the FULL envelope, not bare content
        self.assertTrue(out.is_file(), "--out must write the result file")
        file_text = out.read_text(encoding="utf-8")
        self.assertEqual(json.loads(file_text), envelope, "file must hold the same object as stdout")
        self.assertEqual(file_text.strip(), r.stdout.strip(), "file must be the exact stdout JSON line")
        self.assertEqual(list(Path(self.tmp.name).glob("*.tmp.*")), [], "no .tmp. file left behind")

    def test_total_failure_writes_the_error_object(self):
        self.fake.status = 500
        out = Path(self.tmp.name) / "err.json"
        r = self.run_tool("--out", str(out), stdin=json.dumps({"prompt": "hi"}))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "", "stdout behaviour unchanged: nothing printed on failure")
        obj = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(obj["error"], "every dispatch preference failed")
        self.assertEqual(len(obj["attempts"]), 1)
        self.assertIs(obj["attempts"][0]["ok"], False)
        self.assertIn("paid_fallback_allowed", obj)
        stderr_obj = json.loads([ln for ln in r.stderr.splitlines() if ln.startswith("{")][-1])
        self.assertEqual(obj, stderr_obj, "the file holds the same error object stderr carries")

    def test_out_without_a_value_exits_2_with_usage_and_dispatches_nothing(self):
        r = self.run_tool("--out", stdin=json.dumps({"prompt": "hi"}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", r.stderr.lower())
        self.assertIn("--out", r.stderr)
        self.assertEqual(self.fake.hits, 0, "arguments are parsed before any dispatch")

    def test_out_into_a_missing_directory_exits_2_before_any_dispatch(self):
        bad = Path(self.tmp.name) / "no-such-dir" / "envelope.json"
        r = self.run_tool("--out", str(bad), stdin=json.dumps({"prompt": "hi"}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("no-such-dir", r.stderr, "the stderr line names the bad path")
        self.assertEqual(self.fake.hits, 0, "a set-but-invalid --out aborts before dispatching")
        self.assertFalse(bad.exists())

    def test_unknown_flag_still_exits_2(self):
        r = self.run_tool("--bogus", stdin=json.dumps({"prompt": "hi"}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown argument", r.stderr)
        self.assertEqual(self.fake.hits, 0)

    def test_out_cannot_be_combined_with_list_or_help(self):
        for extra in (["--list"], ["--help"]):
            r = self.run_tool("--out", str(Path(self.tmp.name) / "e.json"), *extra,
                              stdin=json.dumps({"prompt": "hi"}))
            self.assertEqual(r.returncode, 2, extra)
        self.assertEqual(self.fake.hits, 0)

    def test_repeated_out_is_allowed_and_last_wins(self):
        p1 = Path(self.tmp.name) / "one.json"
        p2 = Path(self.tmp.name) / "two.json"
        r = self.run_tool("--out", str(p1), "--out", str(p2), stdin=json.dumps({"prompt": "hi"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(p1.exists())
        self.assertEqual(json.loads(p2.read_text())["model_used"], "stub1")

    def test_failed_write_exits_1_names_the_path_and_leaves_no_tmp(self):
        adir = Path(self.tmp.name) / "adir"                   # a directory: os.replace onto it fails
        adir.mkdir()
        r = self.run_tool("--out", str(adir), stdin=json.dumps({"prompt": "hi"}))
        self.assertEqual(r.returncode, 1, "a failed write never silently succeeds")
        self.assertIn(str(adir), r.stderr, "stderr names PATH")
        self.assertEqual(json.loads(r.stdout)["content"], STUB_ANSWER,
                         "stdout behaviour unchanged even when the write fails")
        self.assertEqual(list(Path(self.tmp.name).glob("*.tmp.*")), [], "the tmp file is cleaned up")

    def test_help_documents_out_and_the_envelope(self):
        r = self.run_tool("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--out", r.stdout)
        self.assertIn("envelope", r.stdout.lower())
        self.assertEqual(self.fake.hits, 0)


class ParseArgsTests(unittest.TestCase):
    """The CLI parser is pure: (out_path, mode, err) — no stdin read, no dispatch."""

    def test_no_args_is_a_plain_run(self):
        self.assertEqual(fat.parse_args([]), (None, "run", None))

    def test_out_takes_a_value(self):
        self.assertEqual(fat.parse_args(["--out", "f.json"]), ("f.json", "run", None))

    def test_out_without_value_is_an_error_with_usage(self):
        res = fat.parse_args(["--out"])
        self.assertIsNone(res[0])
        self.assertIn("usage", res[2].lower())

    def test_unknown_argument_is_an_error(self):
        self.assertIn("unknown argument", fat.parse_args(["--bogus"])[2])

    def test_help_and_list_alone(self):
        self.assertEqual(fat.parse_args(["--help"]), (None, "help", None))
        self.assertEqual(fat.parse_args(["-h"]), (None, "help", None))
        self.assertEqual(fat.parse_args(["--list"]), (None, "list", None))

    def test_out_with_help_or_list_is_refused(self):
        for args in (["--out", "f", "--help"], ["--out", "f", "--list"], ["--list", "--out", "f"]):
            self.assertIsNotNone(fat.parse_args(args)[2], args)

    def test_repeated_out_last_wins(self):
        self.assertEqual(fat.parse_args(["--out", "a", "--out", "b"]), ("b", "run", None))


class WriteEnvelopeTests(unittest.TestCase):
    """The write is tmp-file-in-the-same-directory + os.replace (atomic), never a direct write —
    spying on os.replace makes that mechanism itself testable."""

    def test_write_goes_through_tmp_then_replace_in_the_same_dir(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "e.json"
            calls = []
            real_replace = os.replace

            def spy(src, dst):
                calls.append((Path(src), Path(dst)))
                return real_replace(src, dst)

            fat.os.replace = spy
            try:
                fat.write_envelope(str(p), '{"a": 1}')
            finally:
                fat.os.replace = real_replace
            self.assertEqual(calls, [(p.with_name(f"{p.name}.tmp.{os.getpid()}"), p)],
                             "the envelope lands via tmp + os.replace, in the same directory")
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": 1})
            self.assertEqual(list(Path(d).glob("*.tmp.*")), [])

    def test_failed_write_raises_and_removes_the_tmp_file(self):
        with tempfile.TemporaryDirectory() as d:
            adir = Path(d) / "adir"                       # a directory: os.replace onto it fails
            adir.mkdir()
            with self.assertRaises(OSError):
                fat.write_envelope(str(adir), "x")
            self.assertEqual(list(Path(d).glob("*.tmp.*")), [], "the tmp file is cleaned up")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Quota ledger wiring (0.50.2): dispatch feeds allowlisted response headers to bin/quota.

bin/quota/ (parsers + ledger, addendum §5-§7) shipped with tests but no caller, and the HTTP
layer kept no headers beyond Retry-After, so the ledger could never fill. Every test here pins
FA_QUOTA_LEDGER_DIR to a temp dir (never the real ~/.local/share) and uses a 127.0.0.1 fake.
"""
import importlib.util
import io
import json
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))

import remote_http  # noqa: E402
import remote_provider_core as core  # noqa: E402
from quota import telemetry  # noqa: E402

GROQ_HEADERS = {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "998",
                "x-ratelimit-limit-tokens": "6000", "x-ratelimit-remaining-tokens": "5900",
                "x-ratelimit-reset-requests": "2m", "x-ratelimit-reset-tokens": "1s"}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    d = tmp_path / "ledger"
    monkeypatch.setenv("FA_QUOTA_LEDGER_DIR", str(d))
    return d / "remote-usage.jsonl"


@pytest.fixture
def dispatch():
    spec = importlib.util.spec_from_file_location("rad", ROOT / "bin" / "remote-agent-dispatch.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ok_result(**kw):
    base: dict[str, Any] = dict(ok=True, provider="groq", tier="renewing_free", model_requested="m",
                model_effective="m", status=200, usage={"prompt_tokens": 5, "completion_tokens": 2},
                output_chars=2, stream_complete=True, quota_headers=dict(GROQ_HEADERS))
    base.update(kw)
    return core.Result(**base)


def test_suite_sandboxes_the_ledger_by_default():
    # conftest pins FA_QUOTA_LEDGER_DIR for the whole run (subprocesses inherit it): a test
    # that stubs a successful dispatch without thinking about the ledger must not reach $HOME.
    assert telemetry.ledger_dir() != telemetry.DEFAULT_LEDGER_DIR


def test_tripwire_refuses_the_real_ledger(monkeypatch):
    # SAFE EVEN WITHOUT THE GUARD: os.open is stubbed, so a missing tripwire fails on the
    # sentinel instead of writing the user's real ledger (a first version of this test did).
    def _no_disk(*a, **k):
        raise RuntimeError("WOULD WRITE THE REAL LEDGER")
    monkeypatch.setattr(telemetry.os, "open", _no_disk)
    monkeypatch.setattr(telemetry, "_ensure_ledger_dir", lambda p: p)
    rec = telemetry.QuotaRecord(provider_id="groq", credential_id="", model_id="m",
                                provenance="unknown", source="local-ledger", observed_at="x")
    with pytest.raises(AssertionError, match="REAL quota ledger"):
        telemetry.write_ledger(rec, telemetry.DEFAULT_LEDGER_PATH)


def test_subset_drops_credentials():
    got = remote_http.quota_header_subset([
        ("Authorization", "Bearer sk-secret"), ("x-api-key", "k"), ("Set-Cookie", "c=1"),
        ("x-goog-api-key", "g"), ("X-RateLimit-Remaining-Requests", "59"), ("Retry-After", "3")])
    assert got == {"x-ratelimit-remaining-requests": "59", "retry-after": "3"}


def test_record_writes_one_private_line(dispatch, ledger):
    dispatch._record_quota("groq", _ok_result())
    lines = ledger.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["provider_id"] == "groq"
    assert "sk-" not in lines[0]
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


def test_record_fail_open(dispatch, ledger, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(telemetry, "write_parsed_record", boom)
    assert dispatch._record_quota("groq", _ok_result()) is None
    assert "quota: not recorded (RuntimeError" in capsys.readouterr().err


def test_invalid_override_is_loud_not_silent(monkeypatch):
    monkeypatch.setenv("FA_QUOTA_LEDGER_DIR", "/nonexistent-fa-quota/x/y")
    with pytest.raises(ValueError, match="FA_QUOTA_LEDGER_DIR"):
        telemetry.ledger_dir()


def test_cli_invalid_override_exits_2(monkeypatch):
    monkeypatch.setenv("FA_QUOTA_LEDGER_DIR", "/nonexistent-fa-quota/x/y")
    assert telemetry.main(["--show-ledger"]) == 2


class _Fake:
    def __init__(self, headers):
        body = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


def test_stream_chat_captures_only_quota_headers(monkeypatch):
    fake = _Fake({**GROQ_HEADERS, "Set-Cookie": "session=abc", "Authorization": "Bearer echo"})
    try:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-TEST")
        real = core.get("groq")
        assert real is not None
        prov = core.Provider(id=real.id, display=real.display, tier=real.tier,
                             base_url="http://127.0.0.1:%d" % fake.port,
                             chat_path="/v1/chat/completions", models_path="/models",
                             key_env=real.key_env)
        res = remote_http.stream_chat(prov, {"model": "m", "messages": []}, "/tmp", io.StringIO())
    finally:
        fake.srv.shutdown()
    assert res.ok
    assert res.quota_headers["x-ratelimit-remaining-requests"] == "998"
    assert not any(k in res.quota_headers for k in ("set-cookie", "authorization"))
    assert "quota_headers" not in res.to_dict()


def test_dispatch_main_records_after_success(dispatch, ledger, monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-TEST")

    def fake_stream(provider, body, outdir, out_fd, rfd=None, timeout=120.0, dry_run=False):
        out_fd.write("hi")
        return _ok_result()
    monkeypatch.setattr(dispatch.remote_http, "stream_chat", fake_stream)
    code = dispatch.main(["--provider", "groq", "--prompt", "x", "--outdir", str(tmp_path / "o")])
    assert code == 0
    assert len(ledger.read_text().splitlines()) == 1


def test_dispatch_dry_run_records_nothing(dispatch, ledger, monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-TEST")
    code = dispatch.main(["--provider", "groq", "--prompt", "x", "--dry-run",
                          "--outdir", str(tmp_path / "o")])
    assert code == 0
    assert not ledger.exists()


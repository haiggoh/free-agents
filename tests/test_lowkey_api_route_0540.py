"""0.54.0 Task 1 — lowkey honours an API selection end to end.

The 0.53.1 claim was paper-only: tests/test_picker_hide_toggle_state_0510.py asserted the picker
argv, but nothing ran `lowkey-cli.py --provider …`, and main called hotswap_get_port
UNCONDITIONALLY — so selecting an API model still started a local Qwen. These tests run the
USER-VISIBLE process (lowkey-cli.py as a subprocess against a fake provider on a scratch
loopback port) and assert the process outcome: no hotswap marker, the API model in the label
and the saved session, the machine-wide NVIDIA limiter acquired in that process, and API
errors shown with their reason.

Shared state is fenced: every subprocess gets LA_NVIDIA_THROTTLE_STATE / LOCAL_AGENT_SESSION_DIR
/ LA_API_KEYS_DIR pointed at the test's tmp dir, and the hotswap is a marker stub via
LA_LOWKEY_HOTSWAP_SCRIPT. No real server, no real key, no real throttle file is ever touched.
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import session_picker_model as m  # noqa: E402

LOWKEY = ROOT / "bin" / "lowkey-cli.py"


def _load_lowkey():
    spec = importlib.util.spec_from_file_location("lowkey_cli", LOWKEY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["lowkey_cli"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class FakeProvider:
    """A fake chat-completions server on a scratch loopback port + a registry JSON that points
    provider `nvidia` at it (via LA_REMOTE_REGISTRY_JSON). Records every request body."""

    def __init__(self, tmp: Path, status: int = 200, err_text: str = ""):
        self.requests: list = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                outer.requests.append(json.loads(self.rfile.read(n).decode("utf-8")))
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": outer.err_text}}).encode())
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"model":"test/model","choices":[{"delta":{"content":"ok"}}]}\n\n')
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)   # port 0 = scratch port
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.registry = tmp / "registry.json"
        self.registry.write_text(json.dumps({
            "providers": {"nvidia": {
                "id": "nvidia", "display": "NVIDIA NIM (fake)", "tier": "unknown",
                "base_url": f"http://127.0.0.1:{self.port}/v1",
                "chat_path": "/chat/completions", "models_path": "/models", "catalog_url": "",
                "key_env": "NVIDIA_API_KEY", "extra_env": [],
                "default_models": ["test/default-model"],
            }},
            "entries": [],
        }))
        self.status = status
        self.err_text = err_text

    def env(self, tmp: Path) -> dict:
        e = {k: v for k, v in os.environ.items() if not k.startswith("LA_")}
        e.update({
            "LA_REMOTE_REGISTRY_JSON": str(self.registry),
            "NVIDIA_API_KEY": "test-key",
            "LA_API_KEYS_DIR": str(tmp / "keys"),
            "LA_NVIDIA_THROTTLE_STATE": str(tmp / "throttle.json"),
            "LOCAL_AGENT_SESSION_DIR": str(tmp / "sessions"),
            "LA_LOWKEY_HOTSWAP_SCRIPT": str(tmp / "hotswap-marker.sh"),
        })
        return e

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _marker_stub(tmp: Path) -> Path:
    p = tmp / "hotswap-marker.sh"
    p.write_text(f"#!/bin/sh\necho ran >> {tmp}/hotswap-marker.txt\nexit 1\n")
    p.chmod(0o755)
    return p


def _run_lowkey(env, *args, stdin=None):
    return subprocess.run([sys.executable, str(LOWKEY), *args], env=env, cwd=ROOT,
                          input=stdin, capture_output=True, text=True, timeout=90)


@pytest.fixture
def fake(tmp_path):
    _marker_stub(tmp_path)
    f = FakeProvider(tmp_path)
    yield f
    f.close()


# --- the user-visible process: lowkey-cli.py as a subprocess ------------------------------

def test_api_launch_never_hotswaps(fake, tmp_path):
    """--provider nvidia --api-model X must NOT run the hotswap (marker file stays absent), must
    print the API reply, exit 0 — and the machine-wide limiter must have been acquired in THIS
    process (temp throttle file lost a token)."""
    r = _run_lowkey(fake.env(tmp_path), "--provider", "nvidia", "--api-model", "test/model",
                    "--prompt", "hi")
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr[-400:]}"
    assert "ok" in r.stdout, f"API reply missing: stdout={r.stdout!r} stderr={r.stderr[-300:]}"
    assert not (tmp_path / "hotswap-marker.txt").exists(), "hotswap ran despite --provider"
    assert "Hotswapping" not in r.stderr
    state = json.loads((tmp_path / "throttle.json").read_text())
    assert state["tokens"] <= state["capacity"] - 0.5, (
        f"limiter did not acquire in the lowkey process: {state}")


def test_api_error_is_shown(fake, tmp_path):
    """A 429 from the provider must surface its reason: --prompt mode exits non-zero with the
    reason on stderr; convo mode prints the reason and keeps the session open (exit 0)."""
    fake.status, fake.err_text = 429, "429 slow down"
    r = _run_lowkey(fake.env(tmp_path), "--provider", "nvidia", "--api-model", "test/model",
                    "--prompt", "hi")
    assert r.returncode != 0, "a failed API call must exit non-zero in --prompt mode"
    assert "429 slow down" in r.stderr, f"error reason missing: stderr={r.stderr[-400:]}"
    r2 = _run_lowkey(fake.env(tmp_path), "--provider", "nvidia", "--api-model", "test/model",
                     "--convo", stdin="hi\nexit\n")
    assert "429 slow down" in r2.stderr, "convo must print the provider's reason"
    assert r2.returncode == 0, f"convo must stay open after an API error: {r2.returncode}"


def test_api_dry_run_reports_provider(fake, tmp_path):
    r = _run_lowkey(fake.env(tmp_path), "--provider", "nvidia", "--api-model", "test/model",
                    "--convo", "--dry-run")
    assert r.returncode == 0
    low = r.stdout.lower()
    assert "provider" in low and "nvidia" in low, f"no Provider line: {r.stdout}"
    assert "api model" in low and "test/model" in low, f"no API model line: {r.stdout}"
    assert "qwen-3.8-operator" not in r.stdout, "dry-run must not claim the local default model"


def test_api_session_label_is_api_model(fake, tmp_path):
    r = _run_lowkey(fake.env(tmp_path), "--provider", "nvidia", "--api-model", "test/model",
                    "--convo", "--session", "s1", stdin="hi\nexit\n")
    assert r.returncode == 0, r.stderr[-400:]
    saved = json.loads((tmp_path / "sessions" / "s1.json").read_text())
    assert saved["model_alias"] == "nvidia:test/model", (
        f"session keyed on the wrong model: {saved['model_alias']!r}")


def test_api_convo_never_needs_the_local_port(fake, tmp_path):
    """A full convo (compaction really firing, :context/:summary/:file/:save) over the API:
    exit 0, no hotswap marker, no connection errors, the compaction request went to the API,
    and the saved session names the API model."""
    f = tmp_path / "attach.txt"
    f.write_text("attached file content\n")
    turns = "\n".join(["x" * 150, ":context", "y" * 150, ":summary", f":file {f}",
                       "z" * 150, ":save", "exit"]) + "\n"
    r = _run_lowkey(fake.env(tmp_path), "--provider", "nvidia", "--api-model", "test/model",
                    "--convo", "--session", "s2", "--max-history-chars", "200", stdin=turns)
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"rc={r.returncode} out={out[-500:]}"
    assert not (tmp_path / "hotswap-marker.txt").exists()
    assert "Connection refused" not in out
    assert "Compacted" in r.stderr, f"compaction did not run: {r.stderr[-400:]}"
    compactions = [b for b in fake.requests
                   if "Maintain a compact, factual session memory" in json.dumps(b)]
    assert len(compactions) >= 1, "no compaction request reached the API"
    saved = json.loads((tmp_path / "sessions" / "s2.json").read_text())
    assert saved["model_alias"] == "nvidia:test/model"


# --- the limiter runs inside dispatch_messages_api (in-process, HTTP layer stubbed) --------

def test_nvidia_api_path_acquires_limiter(tmp_path, monkeypatch):
    """Pins existing behaviour: dispatch_messages_api for nvidia acquires the shared limiter
    exactly once per call, with LA_REMOTE_RATE_LIMIT at its default."""
    import rate_limiter
    import remote_http
    lowkey = _load_lowkey()

    class FakeLimiter:
        def __init__(self):
            self.acquires = 0

        def acquire(self, **kw):
            self.acquires += 1
            return 0.0

        def note_429(self, *a, **k):
            pass

    fake_limiter = FakeLimiter()
    monkeypatch.setattr(rate_limiter, "get_limiter", lambda *a, **k: fake_limiter)
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.delenv("LA_REMOTE_RATE_LIMIT", raising=False)

    sse = [b'data: {"model":"test/model","choices":[{"delta":{"content":"ok"}}]}\n',
           b"data: [DONE]\n", b""]

    class FakeResp:
        status = 200

        def getheaders(self):
            return []

        def getheader(self, name):
            return None

        def readline(self):
            return sse.pop(0) if sse else b""

        def __iter__(self):
            return iter(sse)

        def read(self):
            return b""

    class FakeConn:
        def request(self, *a, **k):
            pass

        def getresponse(self):
            return FakeResp()

        def close(self):
            pass

    monkeypatch.setattr(remote_http, "new_connection", lambda *a, **k: FakeConn())
    out = lowkey.dispatch_messages_api("nvidia", "test/model",
                                       [{"role": "user", "content": "hi"}], 16)
    assert out == "ok", f"stubbed API reply lost: {out!r}"
    assert fake_limiter.acquires == 1, (
        f"nvidia API path must acquire the machine-wide limiter once, got {fake_limiter.acquires}")


# --- the picker side: LowkeyScreen --------------------------------------------------------


def _api_item_id(lk, suffix):
    """Find the API row whose id ends with `suffix` (rows nest one level under provider groups)."""
    api = next(g for g in lk.groups() if g.id == "api")
    for it in api.items:
        rows = it.items if isinstance(it, m.Group) else [it]
        for row in rows:
            if row.id.endswith(suffix):
                return row.id
    raise AssertionError(f"no api row ending {suffix!r} in {[i.id for i in api.items]}")


def _agent(model_id="test/model", alias="nvidia-test", provider="nvidia"):
    return m.RemoteAgent(alias, provider, f"Test {model_id}", "unknown", model_id=model_id)


def _local_model(alias="qwen-3.8-operator"):
    return m.LocalModel(alias=alias, family="qwen")


def test_empty_model_id_uses_provider_default():
    """An API agent with an EMPTY model_id (alias-only) launches the provider DEFAULT model —
    the alias must never be passed as --api-model (that was the 0.53.1 bug)."""
    lk = m.LowkeyScreen(m.Settings(), [], api_agents=[_agent(model_id="", alias="foo-alias")])
    lk.accordion.select(_api_item_id(lk, "foo-alias"))
    lk.activate_selected()
    base = lk._base()
    assert base is not None and "--provider" in base and "nvidia" in base
    assert "--api-model" not in base, f"alias leaked as --api-model: {base}"


def test_local_pick_after_api_pick_launches_local():
    """Selecting an API row then a LOCAL row must launch the local model — api_selection is
    cleared on a local pick (0.53.1 leaked it, so the local pick still launched the API)."""
    lk = m.LowkeyScreen(m.Settings(), [_local_model()], api_agents=[_agent()])
    lk.accordion.select(_api_item_id(lk, "test/model"))
    lk.activate_selected()
    assert lk.api_selection is not None, "API row did not register a selection"
    lk.accordion.select("qwen-3.8-operator")
    lk.activate_selected()
    assert lk.api_selection is None, "local pick must clear the API selection"
    req = lk._convo()
    assert req is not None and "--model" in req.argv and "qwen-3.8-operator" in req.argv
    assert "--provider" not in req.argv, f"local launch must not carry --provider: {req.argv}"


def test_go_last_after_api_launch_relaunches_api():
    """After an API launch, `g` (Go launch last) rebuilds --provider/--api-model for that API
    model — never a bare --model <alias>."""
    s = m.Settings()
    lk = m.LowkeyScreen(s, [], api_agents=[_agent()])
    s.last_launched_model["lowkey"] = "nvidia:test/model"
    req = lk._launch_last()
    assert req is not None
    assert "--provider" in req.argv and "nvidia" in req.argv, req.argv
    assert "--api-model" in req.argv and "test/model" in req.argv, req.argv
    assert "--model" not in req.argv, f"g relaunched a local model after an API launch: {req.argv}"
    assert "test/model" in req.selected_id, f"label must name the API model: {req.selected_id}"

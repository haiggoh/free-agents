"""tests/live_proxy/conftest.py — the shared rig: REAL LiteLLM proxy vs a fake OpenAI upstream.

Provider-buckets plan Task 1. The delivery-proof harness for Tasks 2, 5 and 6: every
behavioural claim (429-not-500 mapping, bridge shim, per-provider bucket rpm, cooling
models) is proven end to end through real HTTP into real LiteLLM — never through mocks.

COLLECTION GATE: test_*.py in this directory are collected ONLY when FA_LIVE_PROXY_GATE=1
(set by the dedicated .rtc/pre-flight.sh live gate). The main suite runs `-n 12` and must
not start real proxies — cold starts measured ≈3 min (plan rule 11), so this gate is
serial with generous timeouts. (The var is FA_*, not LA_*: tests/conftest.py scrubs every
inherited LA_* before collection, and a gate knob must survive that scrub.)

SANDBOX: every child gets its own tmp dir and explicit state env
(LA_NVIDIA_THROTTLE_STATE / FA_LIMITER_DIR / FREE_AGENTS_CONFIG_DIR under it). A test
or child that can resolve the REAL ~/.claude/free-agents state is a bug (plan rule 8);
the self-check test_rig_never_uses_live_state asserts on the dumped child env.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BIN = REPO / "bin"

# The dedicated live gate sets FA_LIVE_PROXY_GATE=1; every other run (main suite, editor
# collects) must not even collect these files — a collected-but-deselected test would
# still be a "skip" some gate could misread as coverage.
if os.environ.get("FA_LIVE_PROXY_GATE") != "1":
    collect_ignore_glob = ["test_*.py"]

LITELLM_PY = os.environ.get("FA_LITELLM_PY") or \
    str(Path.home() / ".local" / "pipx" / "venvs" / "litellm" / "bin" / "python")
READY_CAP = 420          # hard cap (cold starts measured ≈180–200 s)
READY_WINDOW = 60        # no-progress window inside the cap


def free_port():
    """An ephemeral loopback port, asserted OUTSIDE the live band (4141–4199)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert port > 4199, f"ephemeral port {port} landed inside the live band"
    return port


class FakeUpstream:
    """One fake OpenAI upstream process; mode switchable per request via <log>.mode."""

    def __init__(self, tmp: Path, mode: str):
        self.log = tmp / "fake-hits.jsonl"
        self.mode_file = Path(str(self.log) + ".mode")
        self.port_file = tmp / "fake-port"
        self.proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve().parent / "fake_openai_upstream.py"),
             "--mode", mode, "--log", str(self.log), "--port-file", str(self.port_file)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 60   # loaded box: process spawn + import can exceed 15 s
        while not self.port_file.exists():
            if self.proc.poll() is not None:
                raise RuntimeError(f"fake upstream exited rc={self.proc.returncode}")
            if time.monotonic() > deadline:
                self.stop()
                raise RuntimeError("fake upstream never wrote its port file")
            time.sleep(0.05)
        self.port = int(self.port_file.read_text().strip())

    def hits(self) -> list:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in
                self.log.read_text().splitlines() if line.strip()]

    def set_mode(self, mode: str) -> None:
        self.mode_file.write_text(mode)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        port = getattr(self, "port", None)   # __init__ may have failed before setting it
        if port is None:
            return
        # the port must actually be free again
        s = socket.socket()
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
        except OSError:
            pass
        else:
            s.close()
            raise AssertionError(f"fake upstream port {port} still accepting after stop")
        finally:
            s.close()


class LiveProxy:
    """One real LiteLLM proxy child (bin/litellm-with-trust.py), progress-based readiness."""

    def __init__(self, tmp: Path, config_yaml: str, extra_env: dict | None = None,
                 ready_cap: int = READY_CAP, ready_window: int = READY_WINDOW):
        self.tmp = tmp
        self.cfg = tmp / "cfg.yaml"
        self.cfg.write_text(config_yaml)
        # LiteLLM resolves `callbacks: [la_proxy_hooks.proxy_hooks]` RELATIVE TO THE CONFIG
        # FILE (la_proxy_hooks.py docstring), so the hook module is symlinked next to it —
        # the same thing remote-session.sh does for live proxies.
        hook_target = tmp / "la_proxy_hooks.py"
        if not hook_target.exists():
            hook_target.symlink_to(BIN / "la_proxy_hooks.py")
        self.port = free_port()
        env = dict(os.environ)
        env.update({
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "LA_NVIDIA_THROTTLE_STATE": str(tmp / "state"),
            "FA_LIMITER_DIR": str(tmp / "limiter"),
            "FREE_AGENTS_CONFIG_DIR": str(tmp / "menu"),
            "PYTHONPATH": str(BIN) + os.pathsep + env.get("PYTHONPATH", ""),
        })
        if extra_env:
            env.update({k: str(v) for k, v in extra_env.items()})
        self.env_json = tmp / "env.json"
        self.env_json.write_text(json.dumps(env))   # before exec: the sandbox is auditable
        self.log_path = tmp / "proxy.log"
        self.ready_seconds: float | None = None
        self._logf = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [LITELLM_PY, str(BIN / "litellm-with-trust.py"),
             "--config", str(self.cfg), "--port", str(self.port)],
            cwd=tmp, env=env, stdout=self._logf, stderr=subprocess.STDOUT)
        self._wait_ready(ready_cap, ready_window)

    # -- readiness -------------------------------------------------------------
    def _health_ok(self) -> bool:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/health/liveliness", timeout=3) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def _cpu_seconds(self) -> float:
        """The child's accumulated CPU time (macOS+Linux), or -1.0 when unavailable."""
        try:
            out = subprocess.run(["ps", "-o", "time=", "-p", str(self.proc.pid)],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return -1.0
        try:
            parts = [float(x) for x in out.replace(",", ".").split(":")]
        except ValueError:
            return -1.0
        return sum(v * m for v, m in zip(reversed(parts), (1.0, 60.0, 3600.0)))

    def _wait_ready(self, ready_cap: int, ready_window: int) -> None:
        started = time.monotonic()
        deadline = started + ready_cap
        window_end = started + ready_window
        last_size = -1
        last_cpu = self._cpu_seconds()
        while True:
            if self._health_ok():
                self.ready_seconds = time.monotonic() - started
                print(f"live_proxy rig: proxy ready on port {self.port} "
                      f"in {self.ready_seconds:.1f}s", flush=True)
                return
            if self.proc.poll() is not None:
                self.stop()
                raise RuntimeError(
                    f"proxy exited rc={self.proc.returncode} while starting; log tail:\n"
                    + self.log_text()[-2000:])
            try:
                size = self.log_path.stat().st_size
            except OSError:
                size = last_size
            cpu = self._cpu_seconds()
            # progress = the log grew OR the child burned CPU (a silent, loaded import
            # phase can outlast the window while working hard — measured 2026-11-22)
            if size != last_size or cpu > last_cpu:
                last_size, last_cpu = size, cpu
                window_end = time.monotonic() + ready_window
            if time.monotonic() > window_end:
                self.stop()
                raise TimeoutError(f"proxy made no progress for {ready_window}s; log tail:\n"
                                   + self.log_text()[-2000:])
            if time.monotonic() > deadline:
                self.stop()
                raise TimeoutError(f"proxy not ready after {ready_cap}s hard cap; log tail:\n"
                                   + self.log_text()[-2000:])
            time.sleep(1)

    # -- surfaces --------------------------------------------------------------
    def log_text(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")
        except OSError:
            return ""

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        try:
            self._logf.close()
        except OSError:
            pass
        # only OUR pid was ever started; the port must be free again
        s = socket.socket()
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", self.port))
        except OSError:
            pass
        else:
            s.close()
            raise AssertionError(f"live proxy port {self.port} still accepting after stop")
        finally:
            s.close()


@pytest.fixture
def fake_upstream(tmp_path_factory):
    """fake_upstream(mode) -> FakeUpstream(port, hits(), set_mode())."""
    made: list[FakeUpstream] = []

    def make(mode: str) -> FakeUpstream:
        fu = FakeUpstream(tmp_path_factory.mktemp("fake"), mode)
        made.append(fu)
        return fu

    yield make
    for fu in made:
        fu.stop()


@pytest.fixture
def live_proxy(tmp_path_factory):
    """live_proxy(config_yaml, env=None) -> LiveProxy(port, log_text(), stop())."""
    made: list[LiveProxy] = []

    def make(config_yaml: str, env: dict | None = None, **kw) -> LiveProxy:
        lp = LiveProxy(tmp_path_factory.mktemp("proxy"), config_yaml, env, **kw)
        made.append(lp)
        return lp

    yield make
    for lp in made:
        lp.stop()


def _anthropic_stream(port: int, model: str = "claude-opus-5-5", max_tokens: int = 64,
                      timeout: float = 180.0):
    """POST /v1/messages (streaming) -> (http_status, events, headers).

    events: one parsed dict per `data:` SSE line ([DONE] excluded); a non-SSE payload
    (e.g. a JSON error body) yields NO events — tests assert on the status they got.
    """
    body = {"model": model, "max_tokens": max_tokens, "stream": True,
            "messages": [{"role": "user", "content": "ping"}]}
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/messages",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "x-api-key": "sk-test",
                 "anthropic-version": "2023-06-01"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, headers, payload = r.status, dict(r.headers.items()), r.read()
    except urllib.error.HTTPError as e:
        status, headers, payload = e.code, dict(e.headers.items()), e.read()
    text = payload.decode("utf-8", "replace")
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                try:
                    events.append(json.loads(data))
                except json.JSONDecodeError:
                    events.append({"raw": data})
    if not events and text.lstrip().startswith("{"):
        # a pre-first-chunk failure reaches the client as a JSON error BODY, not an SSE
        # stream (resp-e.txt shape); surface it as one event so tests can assert on it
        try:
            events.append(json.loads(text))
        except json.JSONDecodeError:
            pass
    return status, events, {k.lower(): v for k, v in headers.items()}


def _two_tier_config(primary_port: int, fallback_port: int | None = None,
                     hooks: bool = True, stream_timeout: float | None = None) -> str:
    """A rig proxy config in the PRODUCTION shape (spec §1.1b route fact): nvidia_nim/
    prefix — the openai/ prefix would bridge /v1/messages to the Responses API instead
    of chat/completions. hooks=True registers la_proxy_hooks exactly like live proxies."""
    lines = [
        "model_list:",
        "  - model_name: claude-opus-5-5",
        "    litellm_params:",
        "      model: nvidia_nim/fake-primary",
        f"      api_base: \"http://127.0.0.1:{primary_port}/v1\"",
        "      api_key: x",
        "      num_retries: 0",
    ]
    # 0.63.2: the same two settings remote-session.sh now writes — a per-deployment
    # stream_timeout and TimeoutErrorRetries: 0 — so a hung model fails over at once
    st = [f"      stream_timeout: {stream_timeout}"] if stream_timeout is not None else []
    lines += st
    if fallback_port is not None:
        lines += [
            "  - model_name: la-fallback-1",
            "    litellm_params:",
            "      model: nvidia_nim/fake-fallback",
            f"      api_base: \"http://127.0.0.1:{fallback_port}/v1\"",
            "      api_key: x",
            "      num_retries: 0",
        ] + st + [
            "router_settings:",
        ] + (["  retry_policy: {TimeoutErrorRetries: 0}"] if stream_timeout is not None else []) + [
            "  fallbacks: [{\"claude-opus-5-5\": [\"la-fallback-1\"]}]",
        ]
    settings = "drop_params: true"
    if hooks:
        settings += ", callbacks: [la_proxy_hooks.proxy_hooks]"
    lines += [
        f"litellm_settings: {{{settings}}}",
        "general_settings: {master_key: sk-test}",
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture
def anthropic_stream():
    return _anthropic_stream


@pytest.fixture
def two_tier_config():
    return _two_tier_config

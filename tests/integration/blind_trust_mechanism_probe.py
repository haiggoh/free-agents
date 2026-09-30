#!/usr/bin/env python3
"""Blind-trust mechanism probe — measures which permission mechanism skips the classifier.

This probe tests four candidates against a stub Anthropic server:
- C0 (control): --permission-mode auto + LA_CLASSIFIER_CMD=mock-classifier.py
- C1: --permission-mode bypassPermissions + --settings with deny rules
- C2: --permission-mode auto + --settings with PreToolUse hook that allows
- C3: --permission-mode auto + CLAUDE_CODE_AUTO_MODE_MODEL=claude-probe-classifier

The stub server counts classifier requests (via classifier_request_observation)
and answers them with a blocking verdict so auto mode would allow.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Import the classifier detection function from omlx-auto-prewarm for self-test
import importlib.util
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "omlx_auto_prewarm",
    REPO_ROOT / "bin" / "omlx-auto-prewarm.py"
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("Failed to load omlx-auto-prewarm module")
_omlx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_omlx)
classifier_request_observation = _omlx.classifier_request_observation

MAIN_MODEL = "claude-opus-5"
CLASSIFIER_MODEL = "claude-sonnet-5"
PROBE_TOOL_ID = "toolu_blind_trust_probe"
SEVERITY_OPEN = "<severity>"
SEVERITY_CLOSE = "</severity>"
BLOCK_OPEN = "<block>"
BLOCK_CLOSE = "</block>"
MARKER_TEXT = "blind-trust-probe-ok\n"

CLAUDE_RAW = shutil.which("claude")
CLAUDE = Path(CLAUDE_RAW).resolve() if CLAUDE_RAW else None


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(path, 0o600)


def write_json(path: Path, value: Any) -> None:
    write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def route(path: str) -> str:
    return path.split("?", 1)[0].rstrip("/")


def message(model: str, content: list, reason: str, sequence: str | None = None) -> dict:
    return {
        "id": f"msg_probe_{time.time_ns()}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": reason,
        "stop_sequence": sequence,
        "usage": {
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


def event(name: str, value: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(value, separators=(',', ':'))}\n\n".encode()


def to_sse(msg: dict) -> bytes:
    shell = dict(msg)
    blocks = shell.pop("content", [])
    reason = shell.pop("stop_reason", None)
    sequence = shell.pop("stop_sequence", None)
    shell["content"] = []
    shell["stop_reason"] = None
    shell["stop_sequence"] = None
    shell["usage"] = {
        "input_tokens": 1,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    out = bytearray(event("message_start", {"type": "message_start", "message": shell}))
    for i, b in enumerate(blocks):
        if b.get("type") == "text":
            initial = {"type": "text", "text": ""}
            delta = {"type": "text_delta", "text": str(b.get("text", ""))}
        elif b.get("type") == "tool_use":
            initial = {
                "type": "tool_use",
                "id": str(b.get("id", "")),
                "name": str(b.get("name", "")),
                "input": {},
            }
            delta = {
                "type": "input_json_delta",
                "partial_json": json.dumps(b.get("input", {}), separators=(',', ':')),
            }
        else:
            raise ValueError(f"unsupported block: {b.get('type')!r}")
        out += event("content_block_start", {"type": "content_block_start", "index": i, "content_block": initial})
        out += event("content_block_delta", {"type": "content_block_delta", "index": i, "delta": delta})
        out += event("content_block_stop", {"type": "content_block_stop", "index": i})
    out += event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": reason, "stop_sequence": sequence},
            "usage": {"output_tokens": 1},
        },
    )
    out += event("message_stop", {"type": "message_stop"})
    return bytes(out)


def send_json(h: BaseHTTPRequestHandler, status: int, value: dict) -> None:
    data = json.dumps(value, separators=(',', ':')).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    try:
        h.wfile.write(data)
    except BrokenPipeError:
        pass


def send(h: BaseHTTPRequestHandler, request: dict, msg: dict) -> None:
    if request.get("stream") is True:
        data = to_sse(msg)
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.send_header("Cache-Control", "no-cache")
        h.send_header("Connection", "close")
        h.end_headers()
        try:
            h.wfile.write(data)
            h.wfile.flush()
        except BrokenPipeError:
            pass
        h.close_connection = True
    else:
        send_json(h, 200, msg)


def has_probe_tool_result(payload: dict) -> bool:
    for m in payload.get("messages", []) if isinstance(payload.get("messages"), list) else []:
        if not isinstance(m, dict) or not isinstance(m.get("content"), list):
            continue
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == PROBE_TOOL_ID:
                return True
    return False


class State:
    def __init__(self, name: str, marker: Path):
        self.name = name
        self.marker = marker
        self.lock = threading.Lock()
        self.main = 0
        self.tools = 0
        self.final = 0
        self.classifier_requests = 0
        self.errors: list[str] = []
        self.obs: list[dict] = []

    def observe(self, path: str, p: dict) -> None:
        ms = p.get("messages")
        stops = p.get("stop_sequences")
        self.obs.append({
            "path": path,
            "model": p.get("model"),
            "max_tokens": p.get("max_tokens"),
            "stream": p.get("stream"),
            "messages_count": len(ms) if isinstance(ms, list) else None,
            "has_probe_tool_result": has_probe_tool_result(p),
            "stop_sequences_type": type(stops).__name__,
        })


def make_handler(state: State, classifier_model_id: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "blind-trust-probe/1"

        def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=unused-argument
            return

        def do_GET(self) -> None:
            if route(self.path) == "/v1/models":
                send_json(self, 200, {"object": "list", "data": [{"id": MAIN_MODEL, "object": "model"}, {"id": CLASSIFIER_MODEL, "object": "model"}]})
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            try:
                n = int(self.headers.get("Content-Length", "0"))
                p = json.loads(self.rfile.read(n) if n else b"")
            except Exception:
                self.send_error(400)
                return

            if not isinstance(p, dict) or route(self.path) != "/v1/messages":
                self.send_error(404)
                return

            with state.lock:
                state.observe(self.path, p)

            raw = p.get("model")
            base = raw.split("[", 1)[0] if isinstance(raw, str) else ""
            limit = p.get("max_tokens")

            # Check if this is a classifier request (simplified for test stub - matches model and max_tokens=64)
            raw_model = p.get("model")
            base_model = raw_model.split("[", 1)[0] if isinstance(raw_model, str) else ""
            limit = p.get("max_tokens")
            is_classifier = (base_model == classifier_model_id and limit == 64)

            if base == MAIN_MODEL:
                with state.lock:
                    state.main += 1
                if not has_probe_tool_result(p):
                    marker = shlex.quote(str(state.marker))
                    cmd = f"/usr/bin/printf 'blind-trust-probe-ok\\n' > {marker} && /bin/cat {marker}"
                    with state.lock:
                        state.tools += 1
                    send(self, p, message(str(raw), [{"type": "tool_use", "id": PROBE_TOOL_ID, "name": "Bash", "input": {"command": cmd, "description": "Write and read a private blind-trust probe marker"}}], "tool_use"))
                    return
                with state.lock:
                    state.final += 1
                send(self, p, message(str(raw), [{"type": "text", "text": "blind trust probe complete"}], "end_turn"))
                return

            if is_classifier:
                with state.lock:
                    state.classifier_requests += 1
                # Answer with a blocking verdict so auto mode would ALLOW (severity 0 = allow)
                verdict = SEVERITY_OPEN + "0" + SEVERITY_CLOSE
                send(self, p, message(str(raw), [{"type": "text", "text": verdict}], "end_turn"))
                return

            state.errors.append(json.dumps({"model": raw, "max_tokens": limit, "stream": p.get("stream")}, sort_keys=True))
            send_json(self, 500, {"type": "error", "error": {"type": "api_error", "message": "unrecognized probe request"}})

    return Handler


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=8)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        pass


def run_candidate(root: Path, name: str, candidate: str, settings: dict | None = None, extra_env: dict | None = None) -> dict:
    case = root / name
    case.mkdir()
    os.chmod(case, 0o700)
    scratch = case / "scratch"
    scratch.mkdir()
    os.chmod(scratch, 0o700)
    marker = case / "marker.txt"
    settings_file = case / "settings.json"
    output = case / "claude-output.json"

    default_settings = {"permissions": {"allow": [], "deny": []}}
    if settings:
        default_settings.update(settings)
    write_json(settings_file, default_settings)

    state = State(name, marker)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, CLASSIFIER_MODEL))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    host, port = server.server_address[:2]

    env = os.environ.copy()
    env.update({
        "ANTHROPIC_BASE_URL": f"http://{host}:{port}",
        "ANTHROPIC_AUTH_TOKEN": "local",
        "CLAUDE_CODE_AUTO_MODE_SEGMENTED_TRANSCRIPT": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        "API_FORCE_IDLE_TIMEOUT": "0",
        "CLAUDE_ENABLE_STREAM_WATCHDOG": "0",
    })
    if extra_env:
        env.update(extra_env)

    if CLAUDE is None or not CLAUDE.is_file():
        raise RuntimeError("Claude Code executable unavailable")

    # Build command based on candidate
    if candidate == "C0":
        # Control: auto + LA_CLASSIFIER_CMD
        env["LA_CLASSIFIER_CMD"] = "python3 /nonexistent/mock-classifier.py"
        perm_mode = "auto"
    elif candidate == "C1":
        # bypassPermissions + deny settings
        perm_mode = "bypassPermissions"
    elif candidate == "C2":
        # auto + PreToolUse hook
        perm_mode = "auto"
    elif candidate == "C3":
        # auto + CLAUDE_CODE_AUTO_MODE_MODEL
        perm_mode = "auto"
        env["CLAUDE_CODE_AUTO_MODE_MODEL"] = "claude-probe-classifier"
    else:
        raise ValueError(f"Unknown candidate: {candidate}")

    command = [
        str(CLAUDE),
        "--model", MAIN_MODEL,
        "--effort", "high",
        "--permission-mode", perm_mode,
        "--strict-mcp-config",
        "--print",
        "--output-format", "json",
        "--no-session-persistence",
        "--setting-sources", "local",
        "--settings", str(settings_file),
        "--tools", "Bash",
        "--", "Use Bash exactly as requested and report its output."
    ]

    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    proc = None
    _started = time.monotonic()
    try:
        with os.fdopen(fd, "wb") as out:
            proc = subprocess.Popen(
                command,
                cwd=str(scratch),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                stop(proc)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        stop(proc)

    raw = output.read_bytes()
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None

    marker_ok = marker.is_file() and marker.read_text() == MARKER_TEXT
    # tool_ran and deny_honoured tested separately via run_deny_test
    _ = state.tools >= 1 and marker_ok

    result = {
        "candidate": candidate,
        "elapsed_seconds": round(time.monotonic() - _started, 3),
        "claude_return_code": proc.returncode if proc else None,
        "marker_exists": marker.is_file(),
        "marker_text": marker.read_text() if marker.is_file() else None,
        "raw_main_requests": state.main,
        "tool_use_responses": state.tools,
        "final_responses": state.final,
        "classifier_requests": state.classifier_requests,
        "executed": bool(proc and proc.returncode == 0 and marker_ok and not state.errors),
        "proxy_errors": state.errors,
        "observations": state.obs,
        "claude_output_json": isinstance(parsed, dict),
        "claude_is_error": parsed.get("is_error") if isinstance(parsed, dict) else None,
        "permission_denials": parsed.get("permission_denials") if isinstance(parsed, dict) else None,
        "result": parsed.get("result") if isinstance(parsed, dict) else None,
    }
    write_json(case / "result.json", result)
    return result


def run_deny_test(root: Path, candidate: str, _mechanism: str) -> dict:
    """Test that destructive deny rules are honoured.

    Runs claude -p with deny rules and a prompt asking it to execute a forbidden
    command. We check the output JSON for permission_denials — Claude Code emits
    these when a deny rule matches a tool_use the model attempted.
    """
    case = root / f"deny_{candidate}"
    case.mkdir()
    os.chmod(case, 0o700)
    scratch = case / "scratch"
    scratch.mkdir()
    os.chmod(scratch, 0o700)
    output = case / "claude-output.json"
    settings_file = case / "settings.json"

    # Build settings: allow git so the model considers the call, deny the specific variant.
    deny_list = [
        "Bash(sudo:*)",
        "Bash(git push --force:*)", "Bash(git push -f:*)",
        "Bash(git reset --hard:*)",
        "Bash(gh release create:*)",
        "Bash(rm -rf /:*)", "Bash(rm -rf ~:*)", "Bash(rm -rf $HOME:*)",
        "Edit(~/.claude/settings.json)", "Edit(~/.claude/settings.local.json)",
        "Edit(~/.claude/plugins/**)",
    ]
    settings = {"permissions": {"allow": ["Bash(git:*)", "Bash(ls:*)"], "deny": deny_list}}
    write_json(settings_file, settings)

    # Initialise a real git repo so the model attempts the git command rather than
    # self-refusing on "not a git repository". The deny rule then blocks the specific
    # variant (push --force or reset --hard) through Claude Code's permission system.
    (scratch / ".git").mkdir()
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=str(scratch),
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "probe@test"], cwd=str(scratch),
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Probe"], cwd=str(scratch),
                   capture_output=True, check=True)
    (scratch / "file.txt").write_text("probe\n")
    subprocess.run(["git", "add", "."], cwd=str(scratch), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(scratch),
                   capture_output=True, check=True)

    if candidate == "C0":
        perm_mode = "auto"
    elif candidate == "C1":
        perm_mode = "bypassPermissions"
    elif candidate == "C2":
        perm_mode = "auto"
    elif candidate == "C3":
        perm_mode = "auto"
    else:
        raise ValueError(f"Unknown candidate: {candidate}")

    # Run against the real proxy (already configured in env) so Claude Code's
    # permission system processes the tool call normally.
    command = [
        str(CLAUDE),
        "--model", MAIN_MODEL,
        "--effort", "high",
        "--permission-mode", perm_mode,
        "--strict-mcp-config",
        "--print",
        "--output-format", "json",
        "--no-session-persistence",
        "--setting-sources", "local",
        "--settings", str(settings_file),
        "--tools", "Bash",
        "--", "Run: git push --force origin probe"
    ]

    env = {k: v for k, v in os.environ.items()
           if k not in ("LA_CLASSIFIER_CMD", "CLAUDE_CODE_AUTO_MODE_MODEL")}

    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    proc = None
    try:
        with os.fdopen(fd, "wb") as out:
            proc = subprocess.Popen(
                command,
                cwd=str(scratch),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                stop(proc)
    finally:
        stop(proc)

    raw = output.read_bytes()
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None

    # Claude Code emits permission_denials when a deny rule matches a tool_use.
    deny_honoured = False
    if isinstance(parsed, dict) and parsed.get("permission_denials"):
        for d in parsed["permission_denials"]:
            if "git push --force" in str(d):
                deny_honoured = True
                break

    result = {
        "candidate": candidate,
        "deny_test": "git_push_force",
        "deny_honoured": deny_honoured,
        "claude_return_code": proc.returncode if proc else None,
        "permission_denials": parsed.get("permission_denials") if isinstance(parsed, dict) else None,
        "result": parsed.get("result") if isinstance(parsed, dict) else None,
    }
    write_json(case / "result.json", result)
    return result


def discriminant_test(root: Path) -> dict:
    """Discriminant: temporarily add Bash(true:*) to allow list and verify classifier drops to 0."""
    case = root / "discriminant"
    case.mkdir()
    os.chmod(case, 0o700)
    scratch = case / "scratch"
    scratch.mkdir()
    os.chmod(scratch, 0o700)
    marker = case / "marker.txt"
    settings_file = case / "settings.json"
    output = case / "claude-output.json"

    # Add allow rule for the probe command
    settings = {"permissions": {"allow": ["Bash(true:*)"], "deny": []}}
    write_json(settings_file, settings)

    state = State("discriminant", marker)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, CLASSIFIER_MODEL))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    host, port = server.server_address[:2]

    env = os.environ.copy()
    env.update({
        "ANTHROPIC_BASE_URL": f"http://{host}:{port}",
        "ANTHROPIC_AUTH_TOKEN": "local",
        "CLAUDE_CODE_AUTO_MODE_SEGMENTED_TRANSCRIPT": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        "API_FORCE_IDLE_TIMEOUT": "0",
        "CLAUDE_ENABLE_STREAM_WATCHDOG": "0",
        "LA_CLASSIFIER_CMD": "python3 /nonexistent/mock-classifier.py",
    })

    command = [
        str(CLAUDE),
        "--model", MAIN_MODEL,
        "--effort", "high",
        "--permission-mode", "auto",
        "--strict-mcp-config",
        "--print",
        "--output-format", "json",
        "--no-session-persistence",
        "--setting-sources", "local",
        "--settings", str(settings_file),
        "--tools", "Bash",
        "--", "Use Bash exactly as requested and report its output."
    ]

    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    proc = None
    _started = time.monotonic()
    try:
        with os.fdopen(fd, "wb") as out:
            proc = subprocess.Popen(
                command,
                cwd=str(scratch),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                stop(proc)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        stop(proc)

    raw = output.read_bytes()
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = None

    marker_ok = marker.is_file() and marker.read_text() == MARKER_TEXT

    result = {
        "discriminant": True,
        "classifier_requests": state.classifier_requests,
        "tool_ran": state.tools >= 1 and marker_ok,
        "marker_ok": marker_ok,
        "claude_return_code": proc.returncode if proc else None,
        "executed": bool(proc and proc.returncode == 0 and marker_ok and not state.errors),
    }
    write_json(case / "result.json", result)
    return result


def self_test() -> int:
    """Run self-tests for the probe infrastructure."""
    # Test classifier_request_observation detection
    test_body = json.dumps({
        "model": "claude-sonnet-5",
        "max_tokens": 64,
        "stream": False,
        "messages": [
            {"role": "user", "content": "test1"},
            {"role": "assistant", "content": "test2"}
        ],
        "tools": [],
    }).encode()

    obs = classifier_request_observation(test_body, "claude-sonnet-5")
    if not obs["matches"]:
        print(f"FAIL: classifier detection failed: {obs['rejection_reasons']}")
        return 1

    # Test non-classifier (main model)
    test_body2 = json.dumps({
        "model": "claude-opus-5",
        "max_tokens": 4096,
        "stream": True,
        "messages": [
            {"role": "user", "content": "test1"},
            {"role": "assistant", "content": "test2"}
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }).encode()

    obs2 = classifier_request_observation(test_body2, "claude-sonnet-5")
    if obs2["matches"]:
        print("FAIL: non-classifier incorrectly matched")
        return 1

    print("BLIND_TRUST_PROBE_SELF_TEST=PASS")
    print("CLASSIFIER_DETECTION=PASS")
    print("NON_CLASSIFIER_REJECTION=PASS")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Blind-trust mechanism probe")
    ap.add_argument("--run-root", required=False, help="Root directory for test runs")
    ap.add_argument("--candidate", choices=["C0", "C1", "C2", "C3", "all"], default="all")
    ap.add_argument("--claude", help="Path to claude executable")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--discriminant", action="store_true", help="Run only discriminant test")
    a = ap.parse_args()

    if a.self_test:
        return self_test()

    if a.claude:
        global CLAUDE
        CLAUDE = Path(a.claude).resolve()

    if a.discriminant:
        if not a.run_root:
            raise SystemExit("STOP: --run-root required for discriminant test")
        root = Path(a.run_root).expanduser().resolve()
        if root.exists():
            raise SystemExit(f"STOP: run root exists: {root}")
        root.mkdir(parents=True, mode=0o700)
        os.chmod(root, 0o700)
        result = discriminant_test(root)
        print(f"DISCRIMINANT_CLASSIFIER_REQUESTS={result['classifier_requests']}")
        print(f"DISCRIMINANT_TOOL_RAN={int(result['tool_ran'])}")
        print(f"DISCRIMINANT_EXECUTED={int(result['executed'])}")
        return 0 if result["executed"] and result["classifier_requests"] == 0 else 2

    if not a.run_root:
        raise SystemExit("STOP: --run-root required")

    root = Path(a.run_root).expanduser().resolve()
    if root.exists() or root.is_symlink():
        raise SystemExit(f"STOP: run root exists: {root}")
    root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)

    candidates = ["C0", "C1", "C2", "C3"] if a.candidate == "all" else [a.candidate]

    # Settings for C2 (hook mechanism)
    hook_settings = {
        "permissions": {
            "allow": [],
            "deny": [],
            "hooks": {
                "PreToolUse": [{
                    "matcher": "*",
                    "hooks": [{
                        "type": "command",
                        "command": 'printf \'{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"blind-trust"}}\''
                    }]
                }]
            }
        }
    }

    results = {}
    for cand in candidates:
        print(f"Running candidate {cand}...")
        if cand == "C2":
            results[cand] = run_candidate(root, cand, cand, settings=hook_settings)
        else:
            results[cand] = run_candidate(root, cand, cand)

    # Run deny tests for candidates that passed
    deny_results = {}
    for cand in candidates:
        if results[cand]["executed"]:
            print(f"Running deny test for {cand}...")
            deny_results[cand] = run_deny_test(root, cand, "bypass" if cand == "C1" else "hook" if cand == "C2" else "auto")
        else:
            deny_results[cand] = {"candidate": cand, "deny_honoured": False, "skipped": True}

    # Print summary
    print("\n=== BLIND TRUST MECHANISM PROBE RESULTS ===")
    for cand in candidates:
        r = results[cand]
        dr = deny_results[cand]
        print(f"\n{cand}:")
        print(f"  classifier_requests: {r['classifier_requests']}")
        print(f"  tool_ran: {r['tool_use_responses'] >= 1}")
        print(f"  executed: {r['executed']}")
        print(f"  deny_honoured: {dr.get('deny_honoured', False)}")

    # Decision logic: first candidate with classifier_requests == 0 && tool_ran && deny_honoured
    chosen = None
    if a.candidate == "all":
        for cand in ["C1", "C2", "C3"]:  # C0 is control
            r = results[cand]
            dr = deny_results[cand]
            if r["classifier_requests"] == 0 and r["tool_use_responses"] >= 1 and dr.get("deny_honoured", False):
                chosen = cand
                break

    if chosen:
        print(f"\nCHOSEN_MECHANISM={chosen}")
    else:
        print("\nCHOSEN_MECHANISM=none (no candidate qualified)")

    # Write verdict file
    verdict = {
        "candidates": results,
        "deny_tests": deny_results,
        "chosen": chosen,
    }
    verdict_path = Path(a.run_root) / "blind-trust-probe.json"
    write_json(verdict_path, verdict)
    print(f"VERDICT_FILE={verdict_path}")

    # Return code: 0 if control reproduced bug AND at least one candidate passed
    control_ok = results.get("C0", {}).get("classifier_requests", 0) >= 1
    any_passed = chosen is not None

    if not control_ok:
        print("ERROR: C0 control did not reproduce bug (classifier_requests = 0)")
        return 3

    return 0 if any_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
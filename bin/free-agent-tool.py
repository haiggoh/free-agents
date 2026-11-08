#!/usr/bin/env python3
"""
free-agent-tool.py — run a subagent task on a FREE model instead of a paid Sonnet subagent.

Usage:
  free-agent-tool.py < task.json     # {"prompt", "description"?, "files"?: [paths],
                                     #  "role"?: operator|reasoner|validator|utility,
                                     #  "avoid_family"?: e.g. "google" for a cross-family review}
  free-agent-tool.py --list          # show the dispatch order and what is available now; sends nothing
  free-agent-tool.py --help
  free-agent-tool.py --out FILE < task.json
                                     # same dispatch, and the result JSON is ALSO written
                                     # atomically to FILE: the FULL envelope (content,
                                     # model_used, display, route, family, role, attempts, …;
                                     # on total failure the error object) — never bare content
  free-agent-tool.py --background RUN_DIR < task.json
                                     # same ladder, resumable: writes RUN_DIR/plan.json +
                                     # events.jsonl (one JSON per attempt) + result.json and
                                     # prints ONE outcome line; exit 0 success · 5 every route
                                     # failed · 6 DECISION_NEEDED (wall cap hit with attempts
                                     # untried — the outcome line carries the resume command)
  free-agent-tool.py --resume RUN_DIR [--extend DUR]
                                     # continue a --background run from its first untried
                                     # attempt, in the same deterministic order

The model comes from the maintainer's PREFERENCE ORDER (config/dispatch-preferences.json): the
first available entry runs, and a failed dispatch falls over to the next. An entry may list
"alternates" — the same model on another keyed provider — tried before the next entry when the
primary 429s or times out (untested alternates are skipped unless FA_DISPATCH_INCLUDE_UNTESTED=1).
Remote entries need
~/.api_keys/<provider>; local entries need their weights on disk. Every attempt is ANNOUNCED on
stderr (model + route), and the JSON result names the model, its route and every attempt.

Council routing: each entry lists the roles it is strong at and its model family. A "role" (or a
subagent_type such as code-reviewer -> validator) keeps only entries with that role, still in
preference order. "avoid_family" skips the family that did the work: an adversarial review from a
different family often finds what a stronger same-family model misses. Both are preferences: when
nothing on this machine satisfies them, the dispatch runs on what is available, prints a
recommendation to add another model, and the result says so ("family_avoided"/"role_matched": false).

Thinking (waypoint fa-thinking-toggle): a dispatch has NO manual thinking toggle. An entry's
optional "thinking": "on"|"off"|"auto" field (absent = auto) resolves by ROLE first —
reasoner/validator dispatches think, operator/utility do not — then by MODEL CONSTRAINT last,
and the result JSON reports it ("thinking", "thinking_overridden", "thinking_note").

This is a STATELESS dispatch: one request, text in, text out. The model cannot read files or run
commands, so pass what it needs in the prompt or as "files" (their contents are attached). Work
that must drive tools belongs in a free SESSION (remote-session.sh / csl), not here.

--out FILE persists the result: the file holds the full envelope (content, model_used, route,
attempts, …), not bare `content` — always persist results with `--out` so the model and attempt
history are never lost. The write is atomic (tmp file + rename in the same directory, fsynced);
a FILE in a missing directory exits 2 BEFORE anything is dispatched; a failed write exits 1 and
names FILE on stderr. --out cannot be combined with --list/--help, and a FILE value that
starts with "-" is refused (exit 2) rather than taken as a flag.

Environment:
  FA_DISPATCH_PREFS            preference file (default config/dispatch-preferences.local.json if
                               present, else config/dispatch-preferences.json)
  FA_DISPATCH_INCLUDE_UNTESTED 1 = also try entries/alternates marked smoke_tested=false
  FA_DISPATCH_WALL_S           wall-clock cap for the whole ladder, seconds (default 3600)
  FA_DISPATCH_ATTEMPT_TIMEOUT_S  per-attempt budget, seconds (default 900; an entry's own
                               "timeout" still wins when shorter)
  FA_DISPATCH_STALL_S          socket-silence budget per attempt, seconds (default 300);
                               any of the three set-but-invalid aborts naming the variable (exit 2)
  FA_DISPATCH_ROUTING=role     legacy: pick by role through council-router.sh instead
  LA_API_KEYS_DIR              credential directory (default ~/.api_keys)
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Add the repo root to path for config-lib
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "bin"))
import fa_fallback_mark  # noqa: E402  (the session-scoped "free exhausted" mark)

# Role mapping from subagent_type to our internal roles
ROLE_MAP = {
    "general-purpose": "operator",
    "operator": "operator",
    "reasoner": "reasoner",
    "validator": "validator",
    "utility": "utility",
    "code-reviewer": "validator",
    "architect": "reasoner",
    "planner": "reasoner",
    "researcher": "reasoner",
    "analyst": "reasoner",
    "coder": "operator",
    "tester": "operator",
    "debugger": "reasoner",
}


def get_council_router_path():
    """Get the path to council-router.sh"""
    return REPO_ROOT / "bin" / "council-router.sh"


def resolve_role(subagent_type: str) -> str:
    """Map subagent_type to our internal role."""
    return ROLE_MAP.get(subagent_type, "operator")


# THINKING POLICY (waypoint fa-thinking-toggle). NO manual toggle for a stateless dispatch —
# fire-and-forget work pays in wall-clock, not money. Instead: an optional "thinking":
# "on"|"off"|"auto" field per dispatch-preferences entry (absent = "auto"), resolved by ROLE
# first, then constrained by the MODEL last. The council's cross-family validator is a
# validator, so the council rule (think ON where the model supports it) rides the role
# default. The resolution reaches the caller as "thinking" / "thinking_overridden" /
# "thinking_note" in the result JSON.
ROLE_THINKING_DEFAULT = {"reasoner": "on", "validator": "on", "operator": "off", "utility": "off"}
_UNRESOLVED = object()   # sentinel: dispatch_entry called without a resolution (legacy path)


def resolve_thinking(entry, role):
    """-> (resolved "on"|"off", reasoning_effort to send or None, overridden, note).

    Order of precedence: the entry's own thinking field, then the role default, then the
    model-constraint table LAST (the same measured facts remote-session.sh resolves — GLM on
    NIM always runs its thinking template, so OFF travels as reasoning_effort low; Nemotron
    gates reasoning with enable_thinking, which a stateless dispatch cannot send, so OFF is
    reported overridden rather than claimed)."""
    choice = entry.get("thinking", "auto")
    if choice not in ("on", "off", "auto"):
        choice = "auto"
    resolved = choice if choice in ("on", "off") else ROLE_THINKING_DEFAULT.get(role or "", "off")
    prov = entry.get("provider", "")
    model = (entry.get("model") or "").lower()
    explicit = entry.get("reasoning_effort")
    if prov == "nvidia" and "glm" in model:
        if resolved == "off":
            if explicit:
                # The maintainer's per-model dial is the more specific fact: it stands, and
                # the result says the OFF choice could not be honored.
                return resolved, explicit, True, (
                    f"GLM on NIM always thinks; the entry's reasoning_effort={explicit} "
                    "stands — the OFF choice cannot disable the thinking template")
            return resolved, "low", False, (
                "GLM on NIM always thinks; reasoning_effort low is the OFF choice "
                "(≈0 reasoning tokens, measured 2026-10-13)")
        return resolved, explicit or "high", False, None
    if prov == "nvidia" and "nemotron" in model:
        # Nemotron does NOT take reasoning_effort at all (it gates reasoning with
        # enable_thinking inside chat_template_kwargs): nothing to send in either direction.
        if resolved == "off":
            return resolved, None, True, (
                "Nemotron gates reasoning with enable_thinking, which a stateless dispatch "
                "cannot send — the OFF choice is not honored; the model still reasons")
        return resolved, None, False, None
    if resolved == "on":
        return resolved, explicit or "high", False, None
    return resolved, None, False, None      # off elsewhere: do not ask for reasoning


def select_model(role: str) -> str:
    """Use council-router.sh to select the best model for a role."""
    router = get_council_router_path()
    result = subprocess.run(
        [str(router), "--role", role],
        capture_output=True,
        text=True,
        timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"council-router.sh failed: {result.stderr}")
    alias = result.stdout.strip()
    if not alias:
        raise RuntimeError(f"No model available for role: {role}")
    return alias


def get_model_endpoint(alias: str) -> tuple[str, str, str, str]:
    """Get the endpoint URL and model name for an alias by sourcing config-lib.sh."""
    # Source config-lib.sh and get the serve backend and spoof_id for this alias
    config_lib = REPO_ROOT / "config" / "config-lib.sh"
    script = f"""
source "{config_lib}"
la_load_config >/dev/null 2>&1
echo "SERVE=${{LA_SERVE[{alias}]:-}}"
echo "SPOOF=${{LA_SPOOF[{alias}]:-}}"
echo "SUBDIR=${{LA_SUBDIR[{alias}]:-}}"
echo "REPO=${{LA_REPO[{alias}]:-}}"
"""
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get model info: {result.stderr}")

    info = {}
    for line in result.stdout.strip().split("\n"):
        if "=" in line:
            key, val = line.split("=", 1)
            info[key] = val

    serve = info.get("SERVE", "")
    spoof = info.get("SPOOF", "")
    subdir = info.get("SUBDIR", "")
    repo = info.get("REPO", "")

    return serve, spoof, subdir, repo


# 0.54.0 Task 0: the hotswap failure message must carry the REASON, never the empty
# `Hotswap failed: ` seen on 0.53.1. Env overrides exist so tests can stub the hotswap; a SET
# but invalid override aborts naming it (never a silent fallthrough to the default).
DEFAULT_HOTSWAP_TIMEOUT_S = 120.0


def _hotswap_script() -> Path:
    """The hotswap script to run: FA_HOTSWAP_SCRIPT when set (must exist), else the repo's."""
    env = os.environ.get("FA_HOTSWAP_SCRIPT")
    if env:
        p = Path(env)
        if not p.is_file():
            raise RuntimeError(f"FA_HOTSWAP_SCRIPT is set but not a file: {env}")
        return p
    return REPO_ROOT / "bin" / "local-llm-hotswap.sh"


def _hotswap_timeout_s() -> float:
    raw = os.environ.get("FA_HOTSWAP_TIMEOUT_S")
    if not raw:
        return DEFAULT_HOTSWAP_TIMEOUT_S
    try:
        v = float(raw)
    except ValueError:
        raise RuntimeError(f"FA_HOTSWAP_TIMEOUT_S is set but not a number: {raw!r}") from None
    if v <= 0:
        raise RuntimeError(f"FA_HOTSWAP_TIMEOUT_S must be > 0, got {raw!r}")
    return v


def _hotswap_meta_dir() -> Path:
    """Where local-llm-hotswap.sh writes server_<port>.meta (pid=...); FA_HOTSWAP_META_DIR
    overrides for tests and must be an existing directory when set."""
    env = os.environ.get("FA_HOTSWAP_META_DIR")
    if env:
        p = Path(env)
        if not p.is_dir():
            raise RuntimeError(f"FA_HOTSWAP_META_DIR is set but not a directory: {env}")
        return p
    return Path.home() / ".claude" / "logs" / "local-agents-configs"


def _tail(text, limit: int = 400) -> str:
    if not text:
        return ""
    if isinstance(text, bytes):                 # TimeoutExpired output can be bytes
        text = text.decode("utf-8", "replace")
    text = text.strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def _orphan_pids(meta_dir: Path, started: float) -> list:
    """Servers this attempt started that are STILL RUNNING: server_*.meta files touched since
    `started` whose pid answers os.kill(pid, 0). Reported, never killed by the dispatcher."""
    orphans = []
    try:
        metas = list(meta_dir.glob("server_*.meta"))
    except OSError:
        return orphans
    for m in metas:
        try:
            if m.stat().st_mtime < started - 1:
                continue
            pid = None
            port = m.stem.split("_", 1)[1] if "_" in m.stem else "?"
            for line in m.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("pid="):
                    pid = int(line.split("=", 1)[1].strip())
                    break
            if pid is None:
                continue
            os.kill(pid, 0)                      # raises when the pid is dead
            orphans.append((pid, port, str(m)))
        except (OSError, ValueError):
            continue
    return orphans


def _hotswap_failure_message(result: subprocess.CompletedProcess) -> str:
    """exit status + the hotswap's own last words (stderr first, then stdout)."""
    out = _tail(result.stderr) or _tail(result.stdout)
    return f"Hotswap failed (exit {result.returncode}): {out or '<no output>'}"


def _hotswap_timeout_message(exc: subprocess.TimeoutExpired, started: float,
                             timeout_s: float, meta_dir: Path) -> str:
    partial = _tail(getattr(exc, "stderr", None) or getattr(exc, "output", None)
                    or getattr(exc, "stdout", None))
    orphans = _orphan_pids(meta_dir, started)
    msg = (f"Hotswap timed out after {timeout_s:g}s"
           f"{': ' + partial if partial else ' (no output captured)'}")
    if orphans:
        listing = "; ".join(f"pid {pid} (port {port}, {path})" for pid, port, path in orphans)
        msg += (f" — server(s) started by this attempt still running: {listing}. "
                "Left running; kill them manually if unwanted.")
    return msg


def execute_local_dispatch(alias: str, prompt: str, spoof_id: str, max_tokens: int = 8192) -> str:
    """Execute a dispatch against a local model via hotswap + curl."""
    # First hotswap the model to get a port
    hotswap = _hotswap_script()
    timeout_s = _hotswap_timeout_s()
    started = time.time()
    try:
        result = subprocess.run(
            [str(hotswap), alias],
            capture_output=True,
            text=True,
            timeout=timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(_hotswap_timeout_message(exc, started, timeout_s,
                                                    _hotswap_meta_dir())) from exc
    if result.returncode != 0:
        raise RuntimeError(_hotswap_failure_message(result))

    # Extract port from output
    import re
    port_match = re.search(r'SUCCESS_PORT=(\d+)', result.stdout)
    if not port_match:
        raise RuntimeError(f"Could not find port in hotswap output: {result.stdout}")
    port = port_match.group(1)

    # Now dispatch via curl - use first spoof_id if comma-separated
    model_name = (spoof_id.split(",")[0] if spoof_id else alias).strip()
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }

    curl_result = subprocess.run(
        [
            "curl", "-s",
            f"http://localhost:{port}/v1/chat/completions",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(payload)
        ],
        capture_output=True,
        text=True,
        timeout=1800
    )

    if curl_result.returncode != 0:
        raise RuntimeError(f"Curl dispatch failed: {curl_result.stderr}")

    try:
        response = json.loads(curl_result.stdout)
        return response["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError) as err:
        raise RuntimeError(f"Failed to parse response: {curl_result.stdout[:500]}") from err


def execute_api_dispatch(_alias: str, prompt: str, subdir: str, repo: str) -> str:
    """Execute a dispatch against a free API provider."""
    # Determine provider from subdir
    provider = subdir

    # Map provider to remote-agent-dispatch.py arguments
    provider_args = {
        "nvidia": ["--provider", "nvidia"],
        "gemini": ["--provider", "gemini"],
        "groq": ["--provider", "groq"],
        "openrouter": ["--provider", "openrouter"],
        "cerebras": ["--provider", "cerebras"],
        "cloudflare": ["--provider", "cloudflare"],
        "github": ["--provider", "github"],
        "kimi": ["--provider", "openrouter"],  # Kimi via OpenRouter
        "deepseek": ["--provider", "openrouter"],  # DeepSeek via OpenRouter
    }

    # The subdir names the model ("nvidia-nemotron-550b"); the provider is its prefix. Matching
    # only the full subdir sent every model-specific subdir to the OpenRouter fallback.
    args = provider_args.get(provider) or provider_args.get(provider.split("-", 1)[0],
                                                            ["--provider", "openrouter"])

    # Use remote-agent-dispatch.py
    dispatch = REPO_ROOT / "bin" / "remote-agent-dispatch.py"
    cmd = [
        sys.executable, str(dispatch),
        *args,
        "--model", repo,
        "--prompt", prompt,
        "--max-tokens", "8192",
        "--outdir", "/tmp/fa-free-agent"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode != 0:
        # Check if output file was created despite non-zero exit
        outdir = Path("/tmp/fa-free-agent")
        output_file = outdir / "output.txt"
        if output_file.exists():
            return output_file.read_text()
        raise RuntimeError(f"API dispatch failed: {result.stderr}")

    # Read output from file
    outdir = Path("/tmp/fa-free-agent")
    output_file = outdir / "output.txt"
    if output_file.exists():
        return output_file.read_text()

    return result.stdout


def execute_litellm_dispatch(_alias: str, prompt: str, repo: str) -> str:
    """Execute a dispatch via LiteLLM proxy."""
    # Get LiteLLM config from environment
    litellm_config = os.environ.get("LA_LITELLM_CONFIG")
    if not litellm_config or not os.path.exists(litellm_config):
        raise RuntimeError("LA_LITELLM_CONFIG not set or file not found")

    # Use the proxy URL (default localhost:4141)
    proxy_url = "http://localhost:4141"

    payload = {
        "model": repo,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 8192,
        "temperature": 0.7,
    }

    curl_result = subprocess.run(
        [
            "curl", "-s",
            f"{proxy_url}/v1/chat/completions",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(payload)
        ],
        capture_output=True,
        text=True,
        timeout=300
    )

    if curl_result.returncode != 0:
        raise RuntimeError(f"LiteLLM dispatch failed: {curl_result.stderr}")

    try:
        response = json.loads(curl_result.stdout)
        return response["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError) as err:
        raise RuntimeError(f"Failed to parse LiteLLM response: {curl_result.stdout[:500]}") from err


PREFS_SHIPPED = REPO_ROOT / "config" / "dispatch-preferences.json"
PREFS_LOCAL = REPO_ROOT / "config" / "dispatch-preferences.local.json"
MAX_FILE_CHARS = 100_000


class DispatchFailed(RuntimeError):
    """One preference entry failed in a way the next entry may not (quota, 5xx, timeout, empty)."""


def load_preferences(env) -> dict:
    """The preference order: FA_DISPATCH_PREFS, else the local overlay, else the shipped file.
    An unreadable override falls back to the shipped order rather than to nothing."""
    for p in (env.get("FA_DISPATCH_PREFS"), PREFS_LOCAL):
        if p and Path(p).is_file():
            try:
                return json.loads(Path(p).read_text())
            except (OSError, ValueError):
                break
    return json.loads(PREFS_SHIPPED.read_text())


def local_on_disk(alias: str) -> bool:
    """Weights for a local alias are on disk (config-lib's la_on_disk)."""
    script = (f'source "{REPO_ROOT / "config" / "config-lib.sh"}"; la_load_config >/dev/null 2>&1; '
              f'la_on_disk "{alias}"')
    try:
        return subprocess.run(["bash", "-c", script], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _has_key(provider: str, env) -> bool:
    p = Path(env.get("LA_API_KEYS_DIR") or Path.home() / ".api_keys") / provider
    return p.is_file() and p.stat().st_size > 0


def availability(entry, env, local_ok=None) -> str:
    """'' when the entry can run now, else the reason it is skipped."""
    if not entry.get("smoke_tested", True) and env.get("FA_DISPATCH_INCLUDE_UNTESTED") != "1":
        return "not smoke-tested yet"
    if entry.get("kind") == "remote":
        return "" if _has_key(entry["provider"], env) else f"no key (~/.api_keys/{entry['provider']})"
    if entry.get("kind") == "local":
        return "" if (local_ok or local_on_disk)(entry["alias"]) else "weights not on disk"
    return f"unknown kind {entry.get('kind')!r}"


def request_role(input_data):
    """The role a request asks for: an explicit "role", else one implied by subagent_type.
    None (no filter) for general-purpose work, so the plain preference order applies."""
    if input_data.get("role"):
        return input_data["role"]
    st = input_data.get("subagent_type", "general-purpose")
    return None if st == "general-purpose" else ROLE_MAP.get(st)


def candidates(prefs, env, local_ok=None, role=None, avoid_family=None) -> list:
    """Available entries in preference order. A role keeps only entries that list it (an entry
    with no roles serves every role); avoid_family skips one model family, so a review comes from
    a different family than the work it checks."""
    out = []
    for e in prefs.get("order", []):
        if role and e.get("roles") and role not in e["roles"]:
            continue
        if avoid_family and e.get("family") == avoid_family:
            continue
        if not availability(e, env, local_ok):
            out.append(e)
    return out


def degraded_candidates(prefs, env, role=None, avoid_family=None, local_ok=None):
    """candidates(), relaxing the filters when they would leave nothing: role and avoid_family
    are PREFERENCES, so a machine with one model family still gets an answer (user 2026-10-23).
    avoid_family gives way first (a same-family review is weaker, not wrong), then role.
    Returns (order, role_matched, family_avoided); an empty order means nothing is set up."""
    for r, a in ((role, avoid_family), (role, None), (None, avoid_family), (None, None)):
        order = candidates(prefs, env, local_ok=local_ok, role=r, avoid_family=a)
        if order:
            return order, (r == role), (a == avoid_family)
    return [], False, False


def route_of(entry) -> str:
    if entry.get("kind") == "remote":
        return f"remote {entry['provider']} · stateless dispatch"
    return f"local {entry['alias']} · stateless dispatch"


def dispatch_entry(entry, prompt: str, env, thinking_effort=_UNRESOLVED,
                   timeout_s=None, stall_s=None) -> str:
    """Run one preference entry; raise DispatchFailed so the caller tries the next.

    thinking_effort: the resolved reasoning_effort from resolve_thinking(). Left at the
    sentinel (direct callers, older tests) the entry's own reasoning_effort stands.
    timeout_s: the most this ONE attempt may take (run() passes what is left of the wall-clock
    cap); the entry's own "timeout" still wins when it is shorter. stall_s: seconds of silence
    on the socket before a remote attempt gives up (the dispatcher's --timeout is a per-read
    timer, not a total, so a streaming answer is never cut by it)."""
    if entry.get("kind") == "remote":
        import tempfile
        outdir = tempfile.mkdtemp(prefix="fa-free-agent-")   # fresh: never a previous run's output
        cmd = [sys.executable, str(REPO_ROOT / "bin" / "remote-agent-dispatch.py"),
               "--provider", entry["provider"], "--model", entry["model"],
               "--prompt", prompt, "--max-tokens", str(entry.get("max_tokens", 16384)),
               "--outdir", outdir]
        effort = entry.get("reasoning_effort") if thinking_effort is _UNRESOLVED else thinking_effort
        if effort:
            cmd += ["--reasoning-effort", str(effort)]
        if stall_s:
            cmd += ["--timeout", f"{stall_s:g}"]
        limit = entry.get("timeout", 1800)
        if timeout_s is not None:
            limit = min(limit, timeout_s)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=limit)
        except subprocess.TimeoutExpired as err:
            raise DispatchFailed(f"timed out after {err.timeout:.0f}s (this attempt's budget)") from err
        out = Path(outdir) / "output.txt"
        text = out.read_text() if out.exists() else ""
        if r.returncode != 0 or not text.strip():
            msg = (r.stderr or r.stdout or "").strip().splitlines()
            # The dispatcher's verdict line ("✖ REMOTE ... failed: [...] HTTP 503 — ...") names
            # the cause; the last line is often only the tail of a multi-line JSON body.
            verdict = next((ln for ln in msg if "✖ REMOTE" in ln), msg[-1] if msg else "")
            raise DispatchFailed(verdict.strip()[:240] or f"exit {r.returncode}, no answer")
        return text
    _serve, spoof, _subdir, _repo = get_model_endpoint(entry["alias"])
    try:
        text = execute_local_dispatch(entry["alias"], prompt, spoof, entry.get("max_tokens", 8192))
    except (RuntimeError, subprocess.SubprocessError) as err:
        raise DispatchFailed(str(err)) from err
    if not (text or "").strip():
        raise DispatchFailed("empty answer")
    return text


def attach_files(prompt: str, files) -> str:
    parts = [prompt]
    for f in files or []:
        try:
            body = Path(f).expanduser().read_text(errors="replace")[:MAX_FILE_CHARS]
        except OSError as err:
            body = f"(could not read: {err})"
        parts.append(f"\n--- FILE: {f} ---\n{body}")
    return "\n".join(parts)


# Honest-exhaustion policy (cloud-screen plan Task 4): only sent-and-failed requests count;
# the values are tunable in config/dispatch-preferences.json under "paid_fallback" and by the
# FA_DISPATCH_WALL_S / FA_DISPATCH_ATTEMPT_TIMEOUT_S / FA_DISPATCH_STALL_S env overrides.
# Plan 2026-11-08 route-watcher Task 3: generous defaults — on 2026-11-08 four remote
# preferences all failed under the shared NVIDIA 40 RPM while the old 600s wall cap still had
# untried local entries left.
DEFAULT_PAID_FALLBACK: dict[str, float] = {"min_real_attempts": 3, "min_providers": 2,
                         "retry_after_max_s": 30, "wall_clock_cap_s": 3600,
                         # Per-attempt budget: an attempt gets what is left of the wall cap, at
                         # most attempt_max_s and at least min_attempt_s (a late attempt still
                         # gets a fair window), and gives up after attempt_stall_s of silence
                         # (a streaming answer is never cut). An entry's own "timeout" still
                         # wins when shorter (dispatch_entry).
                         "attempt_stall_s": 300, "attempt_max_s": 900, "min_attempt_s": 180}
# remote-agent-dispatch.py does not surface Retry-After to its caller (verified 2026-10-24:
# its stderr carries only error_class + HTTP status + reason), so a 429 is a real failure
# with no retry; retry_after_max_s stays in the policy for the day the dispatch surfaces it.


def exhausted(attempts: list, available: list, policy: dict) -> bool:
    """True when the free attempts are honestly exhausted: at least `min_real_attempts`
    sent-and-failed requests across at least `min_providers` distinct providers — or every
    available entry was tried and failed (a one-key machine gets the paid escape too).
    Skipped entries (no key, no weights) never enter `attempts` and never count."""
    fails = [a for a in attempts if not a.get("ok")]
    if not fails:
        return False
    if available and len(fails) >= len(available):
        return True
    providers = {a.get("provider") for a in fails}
    return (len(fails) >= policy.get("min_real_attempts", 3)
            and len(providers) >= policy.get("min_providers", 2))


def _env_seconds(env, name):
    """A FA_DISPATCH_* env override in seconds. Unset -> None; set-but-invalid raises
    ValueError NAMING the variable (a silent fallthrough would run the wrong timeouts)."""
    raw = env.get(name)
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name}={raw!r} is not a number of seconds") from None
    if not v > 0:
        raise ValueError(f"{name}={raw!r} must be a positive number of seconds")
    return v


def resolve_policy(env, prefs):
    """DEFAULT_PAID_FALLBACK <- prefs["paid_fallback"] <- FA_DISPATCH_* env overrides (env wins)."""
    policy = dict(DEFAULT_PAID_FALLBACK, **(prefs.get("paid_fallback") or {}))
    for name, key in (("FA_DISPATCH_WALL_S", "wall_clock_cap_s"),
                      ("FA_DISPATCH_ATTEMPT_TIMEOUT_S", "attempt_max_s"),
                      ("FA_DISPATCH_STALL_S", "attempt_stall_s")):
        v = _env_seconds(env, name)
        if v is not None:
            policy[key] = v
    return policy


def build_plan(order, env):
    """Flatten entries + usable alternates into the deterministic attempt plan: each entry,
    then its smoke-tested alternates (same model on another KEYED provider — tried before the
    next entry; a 429/timeout on the primary moves straight to its alternate), then the next
    entry. Untested alternates are skipped unless FA_DISPATCH_INCLUDE_UNTESTED=1; an alternate
    on a provider without a key is skipped (it could never run)."""
    include_untested = env.get("FA_DISPATCH_INCLUDE_UNTESTED") == "1"
    plan = []
    for e in order:
        plan.append(e)
        if e.get("kind") != "remote":
            continue
        for alt in e.get("alternates") or []:
            if not alt.get("smoke_tested") and not include_untested:
                continue
            if not _has_key(alt["provider"], env):
                continue
            plan.append({"id": f"{e['id']}@{alt['provider']}", "kind": "remote",
                         "provider": alt["provider"], "model": alt["model"],
                         "display": alt.get("display") or f"{e.get('display', e['id'])} @ {alt['provider']}",
                         "max_tokens": e.get("max_tokens", 16384),
                         "reasoning_effort": e.get("reasoning_effort"),
                         "timeout": e.get("timeout"), "family": e.get("family"),
                         "alternate_of": e["id"]})
    return plan


def execute_plan(plan, full_prompt, env, policy, *, role=None, start_index=0,
                 elapsed_base=0.0, wall_cap_s=None, prior_attempts=None, result_extra=None,
                 shown_role=None, shown_avoid=None, note=None, on_event=None):
    """Run the flattened plan in order until one attempt succeeds, the wall-clock cap passes,
    or the plan is exhausted. Returns (status, result, attempts, remaining):
    "ok" (result set) | "failed" (every attempt failed) | "decision_needed" (cap hit with
    `remaining` attempts untried). note(msg) gets the human stderr lines; on_event(rec) gets
    one dict per attempt (background mode appends them to events.jsonl).

    The cap is measured on time.monotonic() (tests fake fat.time wholesale); a resumed
    --background run passes elapsed_base = wall seconds since its creation, and wall_cap_s
    carries any --extend addition."""
    note = note or (lambda msg: None)
    result_extra = result_extra or {}
    attempts = list(prior_attempts or [])
    cap = policy["wall_clock_cap_s"] if wall_cap_s is None else wall_cap_s
    started = time.monotonic()
    i = start_index
    while i < len(plan):
        entry = plan[i]
        elapsed = elapsed_base + (time.monotonic() - started)   # ONE clock read per attempt: gate and budget agree
        if elapsed > cap:
            note(f"free-agents: wall-clock cap ({cap:g}s) reached — "
                 f"{entry['id']} and the remaining preferences are not attempted")
            return "decision_needed", None, attempts, len(plan) - i
        budget = max(min(cap - elapsed, policy["attempt_max_s"]),
                     policy["min_attempt_s"])
        resolved_thinking, think_effort, think_overridden, think_note = resolve_thinking(entry, role)
        note(f"free-agents: offloading to {entry.get('display', entry['id'])} "
             f"({route_of(entry)}{', role ' + shown_role if shown_role else ''}"
             f"{', not ' + shown_avoid if shown_avoid else ''}"
             f"{'; thinking ' + resolved_thinking if resolved_thinking == 'on' or think_overridden or think_note else ''})")
        if think_note:
            # Any constraint note discloses on stderr, not only overrides: a GLM OFF with no
            # explicit effort sends reasoning_effort low, and the human watching the dispatch
            # gets the same fact the session banner shows (review finding 6, 2026-10-25).
            note(f"free-agents: thinking note: {think_note}")
        rec = {"i": i, "id": entry["id"], "provider": entry.get("provider") or route_of(entry),
               "model": entry.get("model")}
        try:
            text = dispatch_entry(entry, full_prompt, env, thinking_effort=think_effort,
                                  timeout_s=budget, stall_s=policy["attempt_stall_s"])
        except DispatchFailed as err:
            rec.update(ok=False, error=str(err))
            attempts.append({k: rec[k] for k in ("id", "provider", "model", "ok", "error")})
            note(f"free-agents: {entry['id']} failed ({err}) — trying the next preference")
            if on_event:
                on_event(rec)
            i += 1
            continue
        rec.update(ok=True)
        attempts.append({k: rec[k] for k in ("id", "provider", "model", "ok")})
        if on_event:
            on_event(rec)
        result = {"content": text, "model_used": entry["id"],
                  "display": entry.get("display", entry["id"]), "route": route_of(entry),
                  "family": entry.get("family"), "alternate_of": entry.get("alternate_of"),
                  "thinking": resolved_thinking, "thinking_overridden": think_overridden,
                  "thinking_note": think_note, "attempts": attempts}
        result.update(result_extra)
        return "ok", result, attempts, 0
    return "failed", None, attempts, 0


def run(input_data, env, out_path=None) -> int:
    prompt = input_data.get("prompt", "")
    if not prompt:
        print(json.dumps({"error": "prompt is required"}), file=sys.stderr)
        return 1
    description = input_data.get("description", "")
    full_prompt = attach_files(f"{description}\n\n{prompt}" if description else prompt,
                               input_data.get("files"))
    prefs = load_preferences(env)
    role, avoid = request_role(input_data), input_data.get("avoid_family")
    families = {e["family"] for e in prefs.get("order", []) if e.get("family")}
    if avoid and prefs.get("order") and avoid not in families:
        # An unknown name is a caller typo (measured 2026-10-29: "zai" for "zhipu"): it matches
        # no entry, so the filter would silently skip nothing and the family being avoided is
        # exactly what runs. Refuse instead of dispatching wrong; a KNOWN family that leaves
        # nothing available still degrades, in degraded_candidates() below.
        print(json.dumps({"error": f"avoid_family {avoid!r} matches no family in the dispatch roster "
                                   f"(known families: {', '.join(sorted(families)) or 'none'}) — run "
                                   "free-agent-tool.py --list; a name that matches nothing skips "
                                   "nothing, so the family you meant to avoid is what runs"}),
              file=sys.stderr)
        return 1
    order, role_matched, family_avoided = degraded_candidates(prefs, env, role=role, avoid_family=avoid)
    if not order:
        print(json.dumps({"error": "no dispatch model is available (no keys, no local weights) — "
                                   "run free-agent-tool.py --list"}), file=sys.stderr)
        return 1
    if avoid and not family_avoided:
        print(f"free-agents: only the {avoid} model family is available, so this review comes from the "
              "same family as the work it checks. A second family catches more: add one, e.g. an API "
              "key in csl -> API keys.", file=sys.stderr)
    if role and not role_matched:
        print(f"free-agents: no available model is listed for the {role} role, so the first available "
              "model runs instead. Add one that fills it, e.g. an API key in csl -> API keys.",
              file=sys.stderr)
    shown_avoid = avoid if family_avoided else None
    shown_role = role if role_matched else None
    try:
        policy = resolve_policy(env, prefs)
    except ValueError as err:
        msg = f"invalid dispatch env: {err}"
        print(f"free-agents: {msg}", file=sys.stderr)
        print(json.dumps({"error": msg}), file=sys.stderr)
        return 2
    plan = build_plan(order, env)
    status, result, attempts, _remaining = execute_plan(
        plan, full_prompt, env, policy, role=role,
        result_extra={"role": role,
                      "role_matched": role_matched if role else None,
                      "family_avoided": family_avoided if avoid else None},
        shown_role=shown_role, shown_avoid=shown_avoid,
        note=lambda msg: print(msg, file=sys.stderr))
    if status == "ok":
        line = json.dumps(result)
        print(line)
        return 0 if _persist(out_path, line) else 1
    allowed = exhausted(attempts, plan, policy)
    err_obj = {"error": "every dispatch preference failed", "attempts": attempts,
               "paid_fallback_allowed": allowed}
    if allowed:
        err_obj["paid_fallback_note"] = ("allowed only when the task's OWN free route is exhausted "
                                         "AND the agent states a reason — exhaustion alone never "
                                         "permits paid (2026-11-08 route-watcher, Task 2 wording)")
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if sid:
            try:
                fa_fallback_mark.write_mark(sid, attempts)
            except OSError as err:
                print(f"free-agents: could not write the paid-fallback mark ({err})", file=sys.stderr)
    line = json.dumps(err_obj)
    print(line, file=sys.stderr)
    _persist(out_path, line)      # exit stays 1 either way; a failed write is named, never silent
    return 1


def list_order(env) -> int:
    prefs = load_preferences(env)
    print("free-agents dispatch order (first available runs; failures fall over to the next):")
    for n, e in enumerate(prefs.get("order", []), 1):
        why = availability(e, env)
        print(f"  {n}. {e.get('display', e['id']):<42} {'✓ available' if not why else '○ ' + why}"
              f"   [{route_of(e)}; {e.get('family', '?')}; {'/'.join(e.get('roles', [])) or 'all roles'}]")
    return 0


def run_by_role(input_data, out_path=None) -> int:
    """Legacy routing (FA_DISPATCH_ROUTING=role): council-router.sh picks by role."""
    prompt = input_data.get("prompt", "")
    description = input_data.get("description", "")
    full_prompt = f"{description}\n\n{prompt}" if description else prompt
    try:
        role = resolve_role(input_data.get("subagent_type", "general-purpose"))
        alias = select_model(role)
        serve, spoof, subdir, repo = get_model_endpoint(alias)
        print(f"free-agents: offloading to {alias} (role {role}, backend {serve})", file=sys.stderr)
        if serve in ("rapid", "vllm", "mlx_lm", "llama_cpp", "omlx"):
            result = execute_local_dispatch(alias, full_prompt, spoof)
        elif serve == "api":
            result = execute_api_dispatch(alias, full_prompt, subdir, repo)
        elif serve == "litellm":
            result = execute_litellm_dispatch(alias, full_prompt, repo)
        else:
            raise RuntimeError(f"Unknown serve backend: {serve}")
        line = json.dumps({"content": result, "model_used": alias, "role": role, "backend": serve})
        print(line)
        return 0 if _persist(out_path, line) else 1
    except Exception as e:
        line = json.dumps({"error": str(e)})
        print(line, file=sys.stderr)
        _persist(out_path, line)
        return 1


USAGE = ("usage: free-agent-tool.py [--out FILE] [--background RUN_DIR | --resume RUN_DIR "
         "[--extend DUR]] < task.json   |   free-agent-tool.py --list   |   "
         "free-agent-tool.py --help")


class ParseResult(tuple):
    """parse_args result: a plain 3-tuple (out_path, mode, err) — existing callers compare
    against (None, "run", None) etc. — carrying the background/resume extras as attributes."""
    run_dir = None
    extend = None

    def __new__(cls, out_path, mode, err, run_dir=None, extend=None):
        self = super().__new__(cls, (out_path, mode, err))
        self.run_dir = run_dir
        self.extend = extend
        return self


def parse_args(args):
    """Parse argv (after the program name) BEFORE anything reads stdin or dispatches.

    Returns a ParseResult: a 3-tuple (out_path, mode, err) plus .run_dir/.extend attributes.
    mode is "run" | "list" | "help" | "background" | "resume"; err is set when the arguments
    are invalid (the caller prints it with the usage line and exits 2). --out takes a FILE
    value; --background/--resume take a RUN_DIR and stand alone (no --out/--list/--help);
    --extend takes a DURATION and only with --resume."""
    out_path = None
    mode = None
    run_dir = None
    extend = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            if mode is not None or out_path is not None:
                return ParseResult(None, None, f"{a} cannot be combined with other arguments\n{USAGE}")
            mode = "help"
        elif a == "--list":
            if mode is not None or out_path is not None:
                return ParseResult(None, None, f"--list cannot be combined with other arguments\n{USAGE}")
            mode = "list"
        elif a == "--out":
            if mode is not None:
                return ParseResult(None, None, f"--out cannot be combined with {mode!r}\n{USAGE}")
            # A value that looks like a flag is a missing value, not a filename: accepting it
            # would dispatch and then write an envelope to a file literally named "--list".
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return ParseResult(None, None, f"--out requires a FILE value\n{USAGE}")
            out_path = args[i + 1]
            i += 1
        elif a in ("--background", "--resume"):
            if mode is not None or out_path is not None:
                return ParseResult(None, None, f"{a} cannot be combined with other arguments\n{USAGE}")
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return ParseResult(None, None, f"{a} requires a RUN_DIR value\n{USAGE}")
            mode = a[2:]
            run_dir = args[i + 1]
            i += 1
        elif a == "--extend":
            if mode != "resume":
                return ParseResult(None, None, f"--extend only makes sense with --resume\n{USAGE}")
            if i + 1 >= len(args) or args[i + 1].startswith("-"):
                return ParseResult(None, None, f"--extend requires a DURATION value\n{USAGE}")
            extend = args[i + 1]
            i += 1
        else:
            return ParseResult(None, None, f"unknown argument {a!r} (see --help)\n{USAGE}")
        i += 1
    return ParseResult(out_path, (mode or "run"), None, run_dir=run_dir, extend=extend)


def write_envelope(out_path: str, line: str) -> None:
    """Write `line` (the exact result JSON the tool prints) to out_path ATOMICALLY: a tmp file
    in the SAME directory, fsync, then os.replace, so a reader never sees a half-written file.
    Raises OSError on failure; the caller reports it naming out_path and exits 1 (a failed
    write never silently succeeds). The tmp file is removed on failure."""
    p = Path(out_path).expanduser()
    tmp = p.with_name(f"{p.name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _persist(out_path, line) -> bool:
    """Write the result line to out_path when --out was given. True on success (or no --out);
    on failure a stderr line names PATH and False is returned."""
    if out_path is None:
        return True
    try:
        write_envelope(out_path, line)
        return True
    except OSError as err:
        print(f"free-agent-tool.py: --out write failed for {out_path}: {err}", file=sys.stderr)
        return False


def _parse_dur(text):
    """'90s' / '30m' / '4h' / plain seconds -> seconds. Raises ValueError on garbage."""
    m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([smh]?)\s*", str(text).lower())
    if not m:
        raise ValueError(f"{text!r} is not a duration like 90s / 30m / 4h")
    return float(m.group(1)) * {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}[m.group(2)]


def run_background(input_data, env, run_dir, resume=False, extend=None):
    """--background RUN_DIR / --resume RUN_DIR [--extend DUR] (plan 2026-11-08 route-watcher
    Task 3): run the same deterministic ladder, write RUN_DIR/plan.json + events.jsonl (one
    JSON per attempt, provider+model named) + result.json, and print exactly ONE outcome line.

    exit 0 success · 5 every route failed · 6 DECISION_NEEDED — the wall-clock deadline passed
    with attempts still untried; the outcome line then carries the exact
    `free-agent-tool.py --resume RUN_DIR --extend 30m` command, and --resume continues from the
    first untried attempt in the same order."""
    rd = Path(run_dir)
    plan_path, events_path, result_path = rd / "plan.json", rd / "events.jsonl", rd / "result.json"
    try:
        if resume:
            doc = json.loads(plan_path.read_text(encoding="utf-8"))
            plan, full_prompt = doc["plan"], doc["prompt"]
            policy, input_data, role = doc["policy"], doc.get("input") or {}, None
            done = []
            if events_path.exists():
                done = [json.loads(ln) for ln in
                        events_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            start_index = len(done)
            elapsed_base = max(0.0, time.time() - float(doc.get("created") or time.time()))
            wall_cap_s = policy["wall_clock_cap_s"] + (_parse_dur(extend) if extend else 0.0)
            prior = []
            for e in done:
                rec = {k: e[k] for k in ("id", "provider", "model", "ok") if k in e}
                if e.get("ok") is False:
                    rec["error"] = e.get("error", "")
                prior.append(rec)
        else:
            prompt = (input_data or {}).get("prompt", "")
            if not prompt:
                print(json.dumps({"error": "prompt is required"}), file=sys.stderr)
                return 2
            prefs = load_preferences(env)
            policy = resolve_policy(env, prefs)
            description = (input_data or {}).get("description", "")
            full_prompt = attach_files(f"{description}\n\n{prompt}" if description else prompt,
                                       (input_data or {}).get("files"))
            role, avoid = request_role(input_data), (input_data or {}).get("avoid_family")
            order, _role_matched, _family_avoided = degraded_candidates(
                prefs, env, role=role, avoid_family=avoid)
            if not order:
                print(json.dumps({"error": "no dispatch model is available (no keys, no local "
                                           "weights) — run free-agent-tool.py --list"}),
                      file=sys.stderr)
                return 2
            plan = build_plan(order, env)
            rd.mkdir(parents=True, exist_ok=True)
            write_envelope(str(plan_path),
                           json.dumps({"plan": plan, "prompt": full_prompt, "policy": policy,
                                       "input": input_data, "created": time.time()}))
            start_index, prior = 0, []
            elapsed_base, wall_cap_s = 0.0, None
    except (OSError, ValueError, KeyError) as err:
        print(json.dumps({"error": f"background run cannot start: {err}"}), file=sys.stderr)
        return 2
    events_fh = open(events_path, "a", encoding="utf-8")

    def on_event(rec):
        events_fh.write(json.dumps(rec) + "\n")
        events_fh.flush()

    status, result, attempts, remaining = execute_plan(
        plan, full_prompt, env, policy, role=role, start_index=start_index,
        elapsed_base=elapsed_base, wall_cap_s=wall_cap_s, prior_attempts=prior,
        note=lambda msg: print(msg, file=sys.stderr), on_event=on_event)
    events_fh.close()
    if status == "ok" and result is not None:
        result["status"] = "ok"
    elif status == "decision_needed":
        result = {"status": "decision_needed", "remaining": remaining, "attempts": attempts,
                  "resume": f"free-agent-tool.py --resume {rd} --extend 30m"}
    else:
        result = {"error": "every dispatch preference failed", "status": "failed",
                  "attempts": attempts}
    allowed = exhausted(attempts, plan, policy)
    result["paid_fallback_allowed"] = allowed
    if allowed:
        result["paid_fallback_note"] = ("allowed only when the task's OWN free route is exhausted "
                                        "AND the agent states a reason — exhaustion alone never "
                                        "permits paid")
    write_envelope(str(result_path), json.dumps(result))
    print(json.dumps(result))
    return {"ok": 0, "failed": 5, "decision_needed": 6}[status]


def main():
    res = parse_args(sys.argv[1:])
    out_path, mode, err = res
    if err:
        print(f"free-agent-tool.py: {err}", file=sys.stderr)
        return 2
    if mode == "help":
        print((__doc__ or "").strip())
        return 0
    if mode == "list":
        return list_order(os.environ)
    if mode == "resume":
        return run_background(None, os.environ, res.run_dir, resume=True, extend=res.extend)
    if out_path is not None:
        parent = Path(out_path).expanduser().parent
        if not parent.is_dir():
            # Explicit overrides fail loudly: a FILE in a missing directory aborts with exit 2
            # BEFORE stdin is read or any dispatch is attempted.
            print(f"free-agent-tool.py: --out {out_path}: directory does not exist: {parent}\n{USAGE}",
                  file=sys.stderr)
            return 2
    try:
        input_data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON input: {e}"}), file=sys.stderr)
        return 1
    if os.environ.get("FA_DISPATCH_ROUTING") == "role":
        return run_by_role(input_data, out_path=out_path)
    if mode == "background":
        return run_background(input_data, os.environ, res.run_dir)
    return run(input_data, os.environ, out_path=out_path)


if __name__ == "__main__":
    sys.exit(main())

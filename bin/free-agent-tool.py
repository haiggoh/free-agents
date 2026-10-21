#!/usr/bin/env python3
"""
free-agent-tool.py — run a subagent task on a FREE model instead of a paid Sonnet subagent.

Usage:
  free-agent-tool.py < task.json     # {"prompt", "description"?, "files"?: [paths],
                                     #  "role"?: operator|reasoner|validator|utility,
                                     #  "avoid_family"?: e.g. "google" for a cross-family review}
  free-agent-tool.py --list          # show the dispatch order and what is available now; sends nothing
  free-agent-tool.py --help

The model comes from the maintainer's PREFERENCE ORDER (config/dispatch-preferences.json): the
first available entry runs, and a failed dispatch falls over to the next. Remote entries need
~/.api_keys/<provider>; local entries need their weights on disk. Every attempt is ANNOUNCED on
stderr (model + route), and the JSON result names the model, its route and every attempt.

Council routing: each entry lists the roles it is strong at and its model family. A "role" (or a
subagent_type such as code-reviewer -> validator) keeps only entries with that role, still in
preference order. "avoid_family" skips the family that did the work: an adversarial review from a
different family often finds what a stronger same-family model misses.

This is a STATELESS dispatch: one request, text in, text out. The model cannot read files or run
commands, so pass what it needs in the prompt or as "files" (their contents are attached). Work
that must drive tools belongs in a free SESSION (remote-session.sh / csl), not here.

Environment:
  FA_DISPATCH_PREFS            preference file (default config/dispatch-preferences.local.json if
                               present, else config/dispatch-preferences.json)
  FA_DISPATCH_INCLUDE_UNTESTED 1 = also try entries marked smoke_tested=false
  FA_DISPATCH_ROUTING=role     legacy: pick by role through council-router.sh instead
  LA_API_KEYS_DIR              credential directory (default ~/.api_keys)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Add the repo root to path for config-lib
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "config"))

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


def execute_local_dispatch(alias: str, prompt: str, spoof_id: str, max_tokens: int = 8192) -> str:
    """Execute a dispatch against a local model via hotswap + curl."""
    # First hotswap the model to get a port
    hotswap = REPO_ROOT / "bin" / "local-llm-hotswap.sh"
    result = subprocess.run(
        [str(hotswap), alias],
        capture_output=True,
        text=True,
        timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"Hotswap failed: {result.stderr}")

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


def route_of(entry) -> str:
    if entry.get("kind") == "remote":
        return f"remote {entry['provider']} · stateless dispatch"
    return f"local {entry['alias']} · stateless dispatch"


def dispatch_entry(entry, prompt: str, env) -> str:
    """Run one preference entry; raise DispatchFailed so the caller tries the next."""
    if entry.get("kind") == "remote":
        import tempfile
        outdir = tempfile.mkdtemp(prefix="fa-free-agent-")   # fresh: never a previous run's output
        cmd = [sys.executable, str(REPO_ROOT / "bin" / "remote-agent-dispatch.py"),
               "--provider", entry["provider"], "--model", entry["model"],
               "--prompt", prompt, "--max-tokens", str(entry.get("max_tokens", 16384)),
               "--outdir", outdir]
        if entry.get("reasoning_effort"):
            cmd += ["--reasoning-effort", entry["reasoning_effort"]]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=entry.get("timeout", 1800))
        except subprocess.TimeoutExpired as err:
            raise DispatchFailed(f"timed out after {err.timeout}s") from err
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


def run(input_data, env) -> int:
    prompt = input_data.get("prompt", "")
    if not prompt:
        print(json.dumps({"error": "prompt is required"}), file=sys.stderr)
        return 1
    description = input_data.get("description", "")
    full_prompt = attach_files(f"{description}\n\n{prompt}" if description else prompt,
                               input_data.get("files"))
    prefs = load_preferences(env)
    role, avoid = request_role(input_data), input_data.get("avoid_family")
    order = candidates(prefs, env, role=role, avoid_family=avoid)
    if not order:
        print(json.dumps({"error": "no dispatch model is available (no keys, no local weights) — "
                                   "run free-agent-tool.py --list"}), file=sys.stderr)
        return 1
    attempts = []
    for entry in order:
        print(f"free-agents: offloading to {entry.get('display', entry['id'])} "
              f"({route_of(entry)}{', role ' + role if role else ''}"
              f"{', not ' + avoid if avoid else ''})", file=sys.stderr)
        try:
            text = dispatch_entry(entry, full_prompt, env)
        except DispatchFailed as err:
            attempts.append({"id": entry["id"], "ok": False, "error": str(err)})
            print(f"free-agents: {entry['id']} failed ({err}) — trying the next preference", file=sys.stderr)
            continue
        attempts.append({"id": entry["id"], "ok": True})
        print(json.dumps({"content": text, "model_used": entry["id"],
                          "display": entry.get("display", entry["id"]), "route": route_of(entry),
                          "family": entry.get("family"), "role": role,
                          "attempts": attempts}))
        return 0
    print(json.dumps({"error": "every dispatch preference failed", "attempts": attempts}), file=sys.stderr)
    return 1


def list_order(env) -> int:
    prefs = load_preferences(env)
    print("free-agents dispatch order (first available runs; failures fall over to the next):")
    for n, e in enumerate(prefs.get("order", []), 1):
        why = availability(e, env)
        print(f"  {n}. {e.get('display', e['id']):<42} {'✓ available' if not why else '○ ' + why}"
              f"   [{route_of(e)}; {e.get('family', '?')}; {'/'.join(e.get('roles', [])) or 'all roles'}]")
    return 0


def run_by_role(input_data) -> int:
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
        print(json.dumps({"content": result, "model_used": alias, "role": role, "backend": serve}))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if args and args[0] == "--list":
        return list_order(os.environ)
    if args:
        print(f"free-agent-tool.py: unknown argument {args[0]!r} (see --help)", file=sys.stderr)
        return 2
    try:
        input_data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON input: {e}"}), file=sys.stderr)
        return 1
    if os.environ.get("FA_DISPATCH_ROUTING") == "role":
        return run_by_role(input_data)
    return run(input_data, os.environ)


if __name__ == "__main__":
    sys.exit(main())

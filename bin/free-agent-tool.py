#!/usr/bin/env python3
"""
FreeAgent — native plugin tool replacement for Claude Code's built-in Agent tool.

This tool accepts the same arguments as the native Agent tool:
- prompt: The task/prompt to send to the agent
- subagent_type: The type/role of agent to use (maps to our roles: operator, reasoner, validator, utility)
- description: Optional description of the task
- model: Optional model override (not used - we route by role)
- files: Optional files to include in context

It routes the task through council-router.sh to select the best available model
for the requested role, then executes the prompt against that model's endpoint.
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


def execute_local_dispatch(alias: str, prompt: str, spoof_id: str) -> str:
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
        "max_tokens": 8192,
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
        timeout=300
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


def main():
    # Read input from stdin (JSON)
    try:
        input_data = json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON input: {e}"}), file=sys.stderr)
        sys.exit(1)

    # Extract arguments (matching native Agent tool schema)
    prompt = input_data.get("prompt", "")
    subagent_type = input_data.get("subagent_type", "general-purpose")
    description = input_data.get("description", "")
    _model = input_data.get("model", "")  # Ignored - we route by role
    _files = input_data.get("files", [])  # Not implemented yet

    if not prompt:
        print(json.dumps({"error": "prompt is required"}), file=sys.stderr)
        sys.exit(1)

    # Build full prompt with description if provided
    full_prompt = prompt
    if description:
        full_prompt = f"{description}\n\n{prompt}"

    try:
        # Resolve role
        role = resolve_role(subagent_type)

        # Select best model for role
        alias = select_model(role)

        # Get model info
        serve, spoof, subdir, repo = get_model_endpoint(alias)

        # Execute based on backend
        if serve in ("rapid", "vllm", "mlx_lm", "llama_cpp"):
            result = execute_local_dispatch(alias, full_prompt, spoof)
        elif serve == "api":
            result = execute_api_dispatch(alias, full_prompt, subdir, repo)
        elif serve == "litellm":
            result = execute_litellm_dispatch(alias, full_prompt, repo)
        else:
            raise RuntimeError(f"Unknown serve backend: {serve}")

        # Return result in format expected by native Agent tool
        print(json.dumps({
            "content": result,
            "model_used": alias,
            "role": role,
            "backend": serve
        }))

    except Exception as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
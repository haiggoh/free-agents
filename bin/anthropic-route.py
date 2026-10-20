#!/usr/bin/env python3
"""anthropic-route.py - choose, per (provider, model), between a provider's NATIVE Anthropic
/v1/messages endpoint and LiteLLM's chat/completions translation.

Why: some providers (OpenRouter, Kilo, Z.ai, LLM7) also speak Anthropic Messages. There the
request -- thinking, output_config.effort, cache_control -- arrives as Claude Code wrote it, with
nothing lost in translation. But support is MODEL-specific (LLM7 answers "does not support the
requested schema" for some ids), so it is decided per model by one tiny real request, and the
verdict is cached so a launch spends that probe at most once a month per model.

Verdicts (printed on stdout, one word):
  anthropic  the probe got a 200 with output_config.effort accepted -> route natively
  chat       the endpoint refused this model (400/404/405/422) -> cached, use the translation
             OR the probe was undecided (401/402/403/429/5xx/network) -> NOT cached, use the
             translation this time and probe again next launch. An empty balance says nothing
             about the route, so a top-up must be able to change the verdict.

Usage:
  anthropic-route.py decide <provider> <model> <anthropic_base>
  anthropic-route.py show                 print the cached verdicts
  anthropic-route.py forget [<provider> [<model>]]
  anthropic-route.py --help

The API key is read from the environment variable LA_ANTHROPIC_KEY (never from argv, never
written to the cache).

Environment:
  LA_ANTHROPIC_KEY           provider key for the probe (required for decide)
  LA_REMOTE_ANTHROPIC        auto (default) | on (always native, no probe) | off (never native)
  LA_ANTHROPIC_ROUTE_CACHE   cache file (default ~/.local/state/local-agents/anthropic-route.json)
  LA_ANTHROPIC_ROUTE_TTL_DAYS  cached-verdict lifetime in days (default 30)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFINITE_NO = {400, 404, 405, 422}


def cache_path() -> Path:
    default = Path.home() / ".local/state/local-agents/anthropic-route.json"
    return Path(os.environ.get("LA_ANTHROPIC_ROUTE_CACHE") or default)


def load_cache() -> dict:
    try:
        data = json.loads(cache_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(data: dict) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def probe(base: str, model: str, key: str) -> tuple[int | None, str]:
    """One tiny real /v1/messages request. Returns (status or None on network failure, text)."""
    body = json.dumps({"model": model, "max_tokens": 16,
                       "output_config": {"effort": "low"},
                       "messages": [{"role": "user", "content": "Reply with: ok"}]}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/v1/messages", data=body, method="POST",
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01",
                 "anthropic-beta": "effort-2025-11-24",
                 "x-api-key": key, "authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, resp.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return None, str(exc)


def decide(provider: str, model: str, base: str) -> str:
    mode = (os.environ.get("LA_REMOTE_ANTHROPIC") or "auto").lower()
    if mode == "off":
        return "chat"
    if mode == "on":
        return "anthropic"
    key = f"{provider}|{model}"
    ttl = float(os.environ.get("LA_ANTHROPIC_ROUTE_TTL_DAYS") or 30) * 86400
    cache = load_cache()
    hit = cache.get(key)
    if isinstance(hit, dict) and hit.get("route") in ("anthropic", "chat") \
            and time.time() - float(hit.get("at", 0)) < ttl:
        return hit["route"]
    status, text = probe(base, model, os.environ.get("LA_ANTHROPIC_KEY", ""))
    if status == 200 and '"type"' in text:
        route = "anthropic"
    elif status in DEFINITE_NO:
        route = "chat"
    else:
        print(f"anthropic-route: {provider} {model}: undecided ({status or 'no answer'}: "
              f"{text.strip()[:160]}); using chat/completions, will probe again next launch",
              file=sys.stderr)
        return "chat"
    cache[key] = {"route": route, "at": time.time(), "status": status}
    try:
        save_cache(cache)
    except OSError as exc:
        print(f"anthropic-route: could not save verdict ({exc})", file=sys.stderr)
    return route


USAGE = "usage: anthropic-route.py decide <provider> <model> <anthropic_base> | show | forget [p [m]] | --help"


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "decide" and len(rest) == 3:
        print(decide(*rest))
        return 0
    if cmd == "show" and not rest:
        for k, v in sorted(load_cache().items()):
            print(f"{k:60s} {v.get('route')}  ({time.strftime('%Y-%m-%d', time.localtime(v.get('at', 0)))})")
        return 0
    if cmd == "forget" and len(rest) <= 2:
        prefix = "|".join(rest)
        cache = {k: v for k, v in load_cache().items() if not (k == prefix or k.startswith(prefix + "|"))} \
            if rest else {}
        save_cache(cache)
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

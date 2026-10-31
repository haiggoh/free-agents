#!/usr/bin/env python3
"""session-fallbacks.py — the fallback chain for a free session's proxy, one "provider:model" per line.

Usage:
  session-fallbacks.py <provider> <session-model>
  session-fallbacks.py --help

Prints the models a session's proxy should fall back to when its own model is throttled
upstream: config/dispatch-preferences.json -> session_fallbacks.order, SAME provider as the
session only (a session would drain another provider's free quota), the session's own model
skipped, in the maintainer's order. Prints nothing when the provider has no chain.

0.49.3 (H5): ids listed in config/broken-nvidia-models.json (e.g. z-ai/glm-5.3, retired by NVIDIA
with 410 Gone, EOL 2026-10-28T08:00:00Z) are DROPPED from the chain even when they come from an
explicit LA_REMOTE_FALLBACKS override — an end-of-life id can only 410, so keeping it would turn
every upstream failure into the misleading "primary 410 + fallbacks 429" LiteLLM blob.

Environment:
  LA_REMOTE_FALLBACKS   off = no chain; "provider:model,..." = use exactly this list (same-provider,
                        own-model and retired-id filtering still apply); unset = the roster file
  FA_DISPATCH_PREFS     roster file (default config/dispatch-preferences.local.json if present,
                        else config/dispatch-preferences.json)
  LA_BROKEN_MODELS_FILE retired/broken id list (default config/broken-nvidia-models.json; missing or
                        corrupt = filter nothing, fail-open)
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def roster_path() -> Path:
    for p in (os.environ.get("FA_DISPATCH_PREFS"), ROOT / "config" / "dispatch-preferences.local.json"):
        if p and Path(p).is_file():
            return Path(p)
    return ROOT / "config" / "dispatch-preferences.json"


def broken_model_ids() -> set:
    """0.49.3 (H5): ids that can only answer 410/404 — dropped from every fallback chain.

    Reads the same broken-nvidia-models.json the picker hides. Missing/corrupt file = empty set
    (fail-open: a broken list must never empty the whole chain)."""
    f = Path(os.environ.get("LA_BROKEN_MODELS_FILE") or ROOT / "config" / "broken-nvidia-models.json")
    try:
        return {m.get("model_id", "") for m in json.loads(f.read_text()).get("models", [])}
    except (OSError, ValueError, AttributeError):
        return set()


def chain(provider: str, own_model: str) -> list:
    override = (os.environ.get("LA_REMOTE_FALLBACKS") or "").strip()
    if override.lower() == "off":
        return []
    if override:
        members = []
        for item in override.split(","):
            item = item.strip()
            if ":" in item:
                prov, model = item.split(":", 1)
                members.append({"provider": prov, "model": model})
    else:
        try:
            members = json.loads(roster_path().read_text()).get("session_fallbacks", {}).get("order", [])
        except (OSError, ValueError):
            return []
    retired = broken_model_ids()
    out = []
    for m in members:
        prov, model = m.get("provider", ""), m.get("model", "")
        if prov == provider and model and model != own_model and model not in retired \
                and f"{prov}:{model}" not in out:
            out.append(f"{prov}:{model}")
    return out


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        print((__doc__ or "").strip())
        return 0
    if len(args) != 2:
        print("usage: session-fallbacks.py <provider> <session-model> (see --help)", file=sys.stderr)
        return 2
    for line in chain(args[0], args[1]):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())

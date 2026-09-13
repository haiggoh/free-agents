#!/usr/bin/env python3
"""remote-provider-doctor — check whether the remote-fallback lane is usable.

Read-only. It NEVER sends a prompt or burns quota: at most it calls each
configured provider's GET /models endpoint (a cheap, free, no-completion
endpoint) to confirm the key works and to list what models are actually
available. Local providers are checked by probe only (key present / loopback
up) — never contacted beyond that.

This is the tool you run when you're about to rely on the fallback: "is my
Gemini key alive, and which models does it actually serve right now?" The
answer drives `agent-fallback.py`'s model selection, because a free-tier model
list changes over time and a hardcoded model id may already be gone.

Usage:
    remote-provider-doctor.py [provider ...]   # check named providers
    remote-provider-doctor.py                  # check every configured provider
    remote-provider-doctor.py --json           # machine-readable output
    remote-provider-doctor.py --all            # include unconfigured providers (status: no-key)

Exit codes: 0 all-checked-usable-or-explicitly-absent, 1 at least one checked
provider is broken (bad key / unreachable), 2 bad arguments.
"""
import http.client
import json
import os
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from remote_provider_core import (PROVIDERS, IMPLEMENTED, get, TIER_CHOICES)  # noqa: E402


def check_models(provider, timeout=15.0):
    """GET the provider's models endpoint. Returns (usable: bool, detail, models: list)."""
    if not provider.config_present():
        return False, "key not configured (env %s unset)" % ", ".join(
            provider.required_env()), []
    if provider.id not in IMPLEMENTED:
        return False, "declared but not implemented in the MVP", []

    # Resolve the models URL (GitHub uses a full catalog URL, not base+path).
    url = provider.catalog_url or (provider.base() + provider.models_path)
    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path + ("?" + parsed.query if parsed.query else "")

    try:
        conn = http.client.HTTPSConnection(host, port, timeout=timeout) \
            if parsed.scheme == "https" else http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path, headers=provider.auth_headers())
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
        conn.close()
    except (http.client.HTTPException, OSError) as exc:
        return False, "unreachable: %s" % exc, []

    if resp.status != 200:
        return False, "HTTP %s: %s" % (resp.status, body[:200]), []

    try:
        data = json.loads(body)
        ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
    except Exception:
        return False, "unparseable models response: %s" % body[:200], []
    return True, "ok — %d models" % len(ids), sorted(ids)


def doctor(provider_ids, all_providers=False, json_out=False, timeout=15.0):
    if not provider_ids:
        provider_ids = list(PROVIDERS) if all_providers else [
            pid for pid, p in PROVIDERS.items() if p.config_present()]
    if not provider_ids:
        return [], 0

    results = []
    broken = 0
    for pid in provider_ids:
        prov = get(pid)
        if prov is None:
            results.append({"provider": pid, "state": "unknown",
                            "detail": "not a known provider id"})
            broken += 1
            continue
        usable, detail, models = check_models(prov, timeout=timeout)
        state = "usable" if usable else ("no-key" if "key not configured" in detail
                                         else ("unimplemented" if "not implemented" in detail
                                              else "broken"))
        if usable or state in ("no-key", "unimplemented"):
            pass
        else:
            broken += 1
        # Never echo the key. Never list a key, only model ids.
        entry = {"provider": pid, "display": prov.display, "tier": prov.tier,
                 "state": state, "detail": detail,
                 "models": models[:50]}
        if not all_providers and state == "no-key":
            continue  # in default mode, hide unconfigured providers
        results.append(entry)
    return results, broken


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Check whether the remote-fallback providers are usable. "
                    "Read-only: never sends a prompt, never burns quota.")
    ap.add_argument("providers", nargs="*",
                    help="provider ids to check (default: every configured one)")
    ap.add_argument("--all", action="store_true",
                    help="include providers whose key is not configured")
    ap.add_argument("--json", action="store_true", dest="json_out",
                    help="machine-readable output")
    ap.add_argument("--timeout", type=float, default=15.0,
                    help="per-request timeout in seconds (default 15)")
    a = ap.parse_args(argv)

    known = set(PROVIDERS)
    for pid in a.providers:
        if pid not in known:
            print("unknown provider %r (known: %s)" % (pid, ", ".join(sorted(known))),
                  file=sys.stderr)
            return 2

    results, broken = doctor(a.providers, all_providers=a.all,
                             json_out=a.json_out, timeout=a.timeout)
    if a.json_out:
        print(json.dumps({"results": results, "broken": broken}, indent=2))
    else:
        if not results:
            print("No providers configured. Set the key env vars (GEMINI_API_KEY, ...) "
                  "to enable the remote lane — it is disabled by default.")
            print("Run with --all to see every declared provider and its state.")
        for r in results:
            impl = "  [not implemented in MVP]" if r["state"] == "unimplemented" else ""
            print("%-12s %-10s %-9s %s%s" % (r["display"], r["state"], r["tier"],
                                             r["detail"], impl))
            if r["models"]:
                print("               models: %s" % ", ".join(r["models"][:8]))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
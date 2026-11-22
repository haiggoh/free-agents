#!/usr/bin/env python3
"""model-facts.py — catalog ingestion for config/provider-limits.json (spec C10, Task 4).

  model-facts.py fetch [--provider P ...] [--out FILE]
      ONE GET <base>/models per keyed provider (User-Agent: free-agents-model-facts/1), keys
      read via the bin/ provider table inside Python and NEVER printed. Raw JSON saved to
      --out (default docs/measurements/<date>-catalog-facts.json).
  model-facts.py merge --into config/provider-limits.json FILE
      Merge catalog facts into the limits file. NEVER overwrites an entry whose existing
      "source" does not start with "catalog ". Models of keyed providers that appear in the
      roster but have no catalog fact get an explicit null-with-reason entry (the C10
      completeness gate), upgrading to the HF context value (estimate-model-footprint's
      cache, warmed read-only) when available: source "hf config.json <repo>".

Usage: model-facts.py --help | fetch ... | merge ...
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bin"))

UA = "free-agents-model-facts/1"
DEFAULT_PROVIDERS = ("nvidia", "mistral", "groq", "gemini", "openrouter")
# catalog field names per provider, tried in order (top level, then one level nested)
CONTEXT_FIELDS = ("context_window", "max_context_length", "context_length",
                  "inputTokenLimit", "max_position_embeddings")
MAXOUT_FIELDS = ("max_output_tokens", "outputTokenLimit", "max_completion_tokens",
                 "max_tokens")
CREATED_FIELDS = ("created", "created_at", "released")


def _providers(want):
    import remote_provider_core as rpc
    ids = list(want) if want else list(DEFAULT_PROVIDERS)
    out = {}
    for pid in ids:
        p = rpc.get(pid)
        if p is None:
            raise SystemExit(f"model-facts: unknown provider {pid!r}")
        if not p.has_catalog:
            raise SystemExit(f"model-facts: provider {pid!r} has no catalog endpoint")
        out[pid] = p
    return out


def _dig(d, names):
    for n in names:
        if isinstance(d, dict) and n in d:
            return d[n]
        if isinstance(d, dict):
            for v in d.values():
                if isinstance(v, dict) and n in v:
                    return v[n]
    return None


def _to_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None


def fetch(provider_ids, out_path):
    """ONE GET per provider; returns {"providers": {pid: {"url", "status", "models": {...}}}}."""
    result = {"fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "ua": UA, "providers": {}}
    for pid, p in _providers(provider_ids).items():
        url = (p.catalog_url or (p.base() + (p.models_path or "/models")))
        req = urllib.request.Request(url, headers={"User-Agent": UA, **p.auth_headers()})
        entry = {"url": url, "status": None, "models": {}, "error": None}
        try:
            import fa_tls
            with urllib.request.urlopen(req, timeout=30,
                                       context=fa_tls.client_context()) as r:   # repo TLS path
                entry["status"] = r.status
                doc = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001 — record, never raise, never print the key
            entry["error"] = f"{type(exc).__name__}: {exc}".split("sk-")[0][:200]
            result["providers"][pid] = entry
            continue
        for m in doc.get("data") or ([doc] if doc.get("id") else []):
            if not isinstance(m, dict) or not m.get("id"):
                continue
            mid = str(m["id"])
            created = _to_int(_dig(m, CREATED_FIELDS))
            entry["models"][mid] = {
                "context": _to_int(_dig(m, CONTEXT_FIELDS)),
                "max_output": _to_int(_dig(m, MAXOUT_FIELDS)),
                "released": (datetime.datetime.fromtimestamp(created, datetime.timezone.utc)
                             .date().isoformat() if created else None),
            }
        result["providers"][pid] = entry
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    return result


def _roster():
    """(alias, provider, model_id) from remote-session.sh --inventory."""
    out = subprocess.run(["bash", str(REPO / "bin" / "remote-session.sh"), "--inventory"],
                         capture_output=True, text=True, timeout=300, check=True).stdout
    rows = []
    import remote_provider_core as rpc
    keyed = {pid for pid, p in rpc.PROVIDERS.items() if p.key_name}
    for line in out.splitlines():
        cols = line.split("\t")
        if len(cols) >= 8 and cols[1] in keyed:
            rows.append((cols[0], cols[1], cols[-1]))
    return rows


def _hf_contexts(model_ids):
    """HF context values via the hotfix's cache (estimate-model-footprint.py --hf-warm),
    read-only. Returns {model_id: (ctx, repo_or_None)}."""
    ids = [i for i in model_ids if i]
    if not ids:
        return {}
    cache = Path(os.environ.get("LA_HF_CACHE")
                 or (Path.home() / ".cache" / "free-agents" / "hf-context.json"))
    try:
        subprocess.run([sys.executable, str(REPO / "bin" / "estimate-model-footprint.py"),
                        "--hf-warm", json.dumps(ids), "--hf-cache", str(cache)],
                       capture_output=True, timeout=600, check=False)
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "emf", REPO / "bin" / "estimate-model-footprint.py")
        emf = importlib.util.module_from_spec(spec)
        sys.modules["emf"] = emf               # dataclasses need the module registered
        spec.loader.exec_module(emf)
        out = {}
        for mid in ids:
            out[mid] = emf.hf_context(mid)
        return out
    except Exception:
        return {}


def merge(into_path, fetch_file):
    import provider_limits as pl
    into = Path(into_path)
    doc = json.loads(into.read_text())
    raw = json.loads(Path(fetch_file).read_text())
    models = doc.setdefault("models", {})
    fetched = raw.get("providers", {})
    # 1) catalog facts (never overwrite a non-catalog source)
    for pid, pentry in fetched.items():
        for mid, fields in (pentry.get("models") or {}).items():
            key = f"{pid}:{mid}"
            existing = models.get(key)
            if existing and not str(existing.get("source", "")).startswith("catalog "):
                continue  # curated/hand-pinned wins over catalog
            nulls = [f for f in ("context", "max_output", "released", "broken")
                     if fields.get(f) is None]
            models[key] = {
                "context": fields.get("context"), "max_output": fields.get("max_output"),
                "released": fields.get("released"), "broken": None,
                "source": f"catalog {pentry.get('url')} {raw.get('fetched_at', '')[:10]}",
                "reason": ("no catalog field/broken-list entry for: " + ", ".join(nulls)) if nulls else None,
            }
    # 2) HF upgrade + roster completeness: models with null context try the HF value
    #    (the hotfix's warmed cache), then every keyed-provider alias gets an entry.
    roster = _roster()
    keys_missing = [f"{p}:{m}" for _a, p, m in roster if f"{p}:{m}" not in models]
    keys_nullctx = [k for k, v in models.items() if v.get("context") is None]
    hf = _hf_contexts([k.split(":", 1)[1] for k in keys_missing + keys_nullctx if "/" in k])
    for key in keys_nullctx:
        ctx = hf.get(key.split(":", 1)[1])
        if isinstance(ctx, int):
            models[key] = {
                "context": ctx,
                "max_output": models[key].get("max_output"),
                "released": models[key].get("released"),
                "broken": None,
                "source": f"hf config.json (via estimate-model-footprint) {key.split(':', 1)[1]}",
                "reason": "max_output/broken: no catalog field or broken-list entry; context from HF config.json"}
    for alias, pid, mid in roster:
        key = f"{pid}:{mid}"
        if key in models:
            continue
        ctx = hf.get(mid)
        if isinstance(ctx, int):
            models[key] = {"context": ctx, "max_output": None, "released": None,
                           "broken": None, "source": f"hf config.json (via estimate-model-footprint) {mid}",
                           "reason": "max_output/released/broken: no catalog field or broken-list entry; context from HF config.json"}
        else:
            models[key] = {"context": None, "max_output": None, "released": None,
                           "broken": None, "source": "roster alias",
                           "reason": "no catalog entry fetched for this provider and no HF value; the filter treats unknown context as shown-tagged (broken unknown too)"}
    into.write_text(json.dumps(doc, indent=2) + "\n")
    pl.load(into)  # the result must validate
    return len(models)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="model-facts.py",
        description="Catalog ingestion for config/provider-limits.json: fetch does ONE "
                    "GET <base>/models per keyed provider (UA free-agents-model-facts/1; keys "
                    "never printed); merge folds catalog facts in without overwriting "
                    "non-catalog sources and gives every keyed roster model a complete entry.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="one catalog GET per provider; saves raw JSON to --out")
    f.add_argument("--provider", action="append", default=None,
                   help="provider id (repeatable; default: %s)" % ", ".join(DEFAULT_PROVIDERS))
    f.add_argument("--out", default=None,
                   help="raw output file (default docs/measurements/<today>-catalog-facts.json)")
    m = sub.add_parser("merge", help="merge a fetch file into the limits file")
    m.add_argument("--into", default=str(REPO / "config" / "provider-limits.json"))
    m.add_argument("file")
    args = ap.parse_args(argv)
    if args.cmd == "fetch":
        out = args.out or str(REPO / "docs" / "measurements"
                              / f"{datetime.date.today().isoformat()}-catalog-facts.json")
        result = fetch(args.provider, out)
        for pid, entry in result["providers"].items():
            print(f"{pid}: status={entry['status']} models={len(entry['models'])}"
                  + (f" error={entry['error']}" if entry.get("error") else ""))
        print(f"saved {out}")
        return 0
    if args.cmd == "merge":
        n = merge(args.into, args.file)
        print(f"merged; {n} model fact(s) now in {args.into}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

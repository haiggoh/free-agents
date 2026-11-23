#!/usr/bin/env python3
"""provider_limits.py — loader for config/provider-limits.json, the SINGLE structured source
of per-provider rate limits and per-model facts (spec C1/C10, provider-buckets plan).

Semantics: `null` = no known limit (distinct from `0` = forbidden); a MISSING key is a schema
error. Strict validation on purpose: an invalid limits file is a LOUD error (picker infobox,
proxy start via the override rule), never a silent fallback to "no limits". The override
env FA_PROVIDER_LIMITS must point at an existing, valid file or abort naming the var.

Schema (schema_version 1):
  {"schema_version": 1, "session_min_context": int, "large_context_threshold": int,
   "providers": {<id>: {"rpm": int|null, "tpm": int|null, "rpd": int|null, "rpw": int|null,
                        "tpd": int|null, "dispatch_only": bool, "reserves_on_request": bool,
                        "measured_at": str, "source": str}},
   "models": {"<provider>:<model_id>": {"context": int|null, "max_output": int|null,
                                        "released": str|null, "broken": bool|null,
                                        "source": str, "reason": str|null}}}
A model fact with a null field MUST carry "reason".

Usage:
  provider_limits.py --help
  provider_limits.py validate [PATH]     exit 0 if valid; 2 with the error on stderr if not
  provider_limits.py show PROVIDER       print that provider's limits as JSON ("unknown" if none)

Environment:
  FA_PROVIDER_LIMITS   override path; if SET it must exist and validate, else LimitsError
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_PATH = REPO / "config" / "provider-limits.json"
SCHEMA_VERSION = 1

_TOP_KEYS = {"schema_version", "session_min_context", "large_context_threshold",
             "providers", "models"}
_PROVIDER_KEYS = {"rpm", "tpm", "rpd", "rpw", "tpd", "dispatch_only", "reserves_on_request",
                  "measured_at", "source"}
_LIMIT_KEYS = ("rpm", "tpm", "rpd", "rpw", "tpd")
_MODEL_KEYS = {"context", "max_output", "released", "broken", "source", "reason"}


class LimitsError(ValueError):
    """The limits file (or the FA_PROVIDER_LIMITS override) is missing or invalid."""


@dataclass(frozen=True)
class ProviderLimits:
    rpm: int | None
    tpm: int | None
    rpd: int | None
    rpw: int | None
    tpd: int | None
    dispatch_only: bool
    reserves_on_request: bool
    measured_at: str
    source: str


@dataclass(frozen=True)
class ModelFacts:
    context: int | None
    max_output: int | None
    released: str | None
    broken: bool | None
    source: str
    reason: str | None


@dataclass(frozen=True)
class Limits:
    session_min_context: int
    large_context_threshold: int
    providers: dict
    models: dict

    def provider(self, pid: str) -> ProviderLimits | None:
        return self.providers.get(pid)

    def model(self, pid: str, model_id: str, *, online: bool = False) -> ModelFacts | None:
        """The model's facts with RUNTIME facts layered in (fix/runtime-model-facts, user rule
        2026-11-22: facts are fetched by the code, never by a manual snapshot). Context follows
        CONTEXT_PRECEDENCE. online=False (default) reads caches only — render paths never wait;
        online=True allows ONE bounded HF lookup (launch-time ladders) and caches its answer."""
        return _with_runtime_facts(pid, model_id, self.models.get(f"{pid}:{model_id}"), online)


# --- runtime model facts (fix/runtime-model-facts) ------------------------------------------------
# Context precedence, highest first. "pinned" = a snapshot value whose source is a human/doc
# measurement; "catalog" = the PROVIDER's own published number (what it actually serves); "hf" =
# the model's config.json via the HF cache the code warms; "roster" = the alias's
# autocompact_default window proxy (config/remote-registry.json).
CONTEXT_PRECEDENCE = ("pinned", "catalog", "hf", "roster")
ONLINE_TIMEOUT_S = 4.0
_RUNTIME_SOURCES = ("catalog ", "hf ", "roster")
_FOOTPRINT = None
_ROSTER = None


def _reset_runtime_caches() -> None:
    """Tests: forget the memoized footprint module and roster windows."""
    global _FOOTPRINT, _ROSTER
    _FOOTPRINT = None
    _ROSTER = None


def _set_footprint_module_for_tests(module) -> None:
    global _FOOTPRINT
    _FOOTPRINT = module


def _footprint():
    """bin/estimate-model-footprint.py (hyphenated, so loaded by path); None if unavailable."""
    global _FOOTPRINT
    if _FOOTPRINT is None:
        try:
            import importlib.util
            here = Path(__file__).resolve().parent
            if str(here) not in sys.path:
                sys.path.insert(0, str(here))           # its fa_tls import lives here
            spec = importlib.util.spec_from_file_location(
                "fa_footprint_rt", here / "estimate-model-footprint.py")
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _FOOTPRINT = mod
        except Exception:
            return None
    return _FOOTPRINT


def _roster_window(pid: str, model_id: str) -> int | None:
    """The registry's autocompact_default for (provider, model id), as tokens (fa_capture's
    WINDOW_TOKENS — one constant per shared label); None for '-', unknown or unreadable."""
    global _ROSTER
    if _ROSTER is None:
        _ROSTER = {}
        try:
            here = str(Path(__file__).resolve().parent)
            if here not in sys.path:
                sys.path.insert(0, here)
            import remote_provider_registry as rpr
            from fa_capture import WINDOW_TOKENS
            for e in rpr._load_registry()[1]:
                tok = WINDOW_TOKENS.get(str(e.get("autocompact_default", "")).strip().lower())
                if tok and e.get("model_id"):
                    _ROSTER[(e.get("provider", ""), e["model_id"])] = tok
        except Exception:
            _ROSTER = {}
    return _ROSTER.get((pid, model_id))


def _hf_runtime_context(model_id: str, online: bool) -> int | None:
    fp = _footprint()
    if fp is None or "/" not in (model_id or ""):
        return None
    try:
        ctx = fp.hf_context(model_id)
        if ctx is None and online:
            fp.hf_total_params(model_id, timeout=ONLINE_TIMEOUT_S, lane="remote")
            ctx = fp.hf_context(model_id)
        return ctx
    except Exception:
        return None


def _with_runtime_facts(pid: str, model_id: str, base: ModelFacts | None,
                        online: bool) -> ModelFacts | None:
    snap_ctx = base.context if base is not None else None
    snap_src = base.source if base is not None else ""
    layers = {
        "pinned": snap_ctx if snap_ctx is not None and not snap_src.startswith(_RUNTIME_SOURCES) else None,
        "catalog": snap_ctx if snap_ctx is not None and snap_src.startswith(_RUNTIME_SOURCES) else None,
    }
    for name in CONTEXT_PRECEDENCE:
        if name in ("pinned", "catalog") and layers[name] is not None:
            return base                                  # the snapshot already answers
        if name == "hf":
            ctx = _hf_runtime_context(model_id, online)
            if ctx is not None:
                return _overlay(base, ctx, f"runtime hf config.json (cached) {model_id}")
        if name == "roster":
            ctx = _roster_window(pid, model_id)
            if ctx is not None:
                return _overlay(base, ctx, f"roster autocompact_default ({pid}:{model_id})")
    return base


def _overlay(base: ModelFacts | None, ctx: int, source: str) -> ModelFacts:
    return ModelFacts(context=ctx,
                      max_output=base.max_output if base is not None else None,
                      released=base.released if base is not None else None,
                      broken=base.broken if base is not None else None,
                      source=source,
                      reason=(base.reason if base is not None else None))


def _err(msg: str) -> LimitsError:
    return LimitsError(f"provider-limits: {msg}")


def _check_int_or_null(value, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _err(f"{where} must be a non-negative int or null, got {value!r}")
    return value


def _parse(doc, origin: str) -> Limits:
    if not isinstance(doc, dict):
        raise _err(f"{origin}: top level must be an object")
    unknown = set(doc) - _TOP_KEYS
    if unknown:
        raise _err(f"{origin}: unknown top-level key(s): {sorted(unknown)}")
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise _err(f"{origin}: schema_version must be {SCHEMA_VERSION}, got {doc.get('schema_version')!r}")
    for key in ("session_min_context", "large_context_threshold"):
        v = doc.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise _err(f"{origin}: {key} must be a positive int, got {v!r}")
    providers_raw = doc.get("providers")
    if not isinstance(providers_raw, dict):
        raise _err(f"{origin}: 'providers' must be an object")
    providers = {}
    for pid, entry in providers_raw.items():
        where = f"{origin}: providers.{pid}"
        if not isinstance(entry, dict):
            raise _err(f"{where} must be an object")
        unknown = set(entry) - _PROVIDER_KEYS
        if unknown:
            raise _err(f"{where}: unknown key(s): {sorted(unknown)}")
        missing = _PROVIDER_KEYS - set(entry)
        if missing:
            raise _err(f"{where}: missing key(s): {sorted(missing)} (missing = schema error; use null for no known limit)")
        for lk in _LIMIT_KEYS:
            _check_int_or_null(entry[lk], f"{where}.{lk}")
        for bk in ("dispatch_only", "reserves_on_request"):
            if not isinstance(entry[bk], bool):
                raise _err(f"{where}.{bk} must be a bool, got {entry[bk]!r}")
        for sk in ("measured_at", "source"):
            if not isinstance(entry[sk], str) or not entry[sk]:
                raise _err(f"{where}.{sk} must be a non-empty string")
        providers[pid] = ProviderLimits(
            rpm=entry["rpm"], tpm=entry["tpm"], rpd=entry["rpd"], rpw=entry["rpw"],
            tpd=entry["tpd"], dispatch_only=entry["dispatch_only"],
            reserves_on_request=entry["reserves_on_request"],
            measured_at=entry["measured_at"], source=entry["source"])
    models_raw = doc.get("models")
    if not isinstance(models_raw, dict):
        raise _err(f"{origin}: 'models' must be an object")
    models = {}
    for key, entry in models_raw.items():
        where = f"{origin}: models.{key!r}"
        if ":" not in key:
            raise _err(f"{where}: key must be '<provider>:<model_id>'")
        if not isinstance(entry, dict):
            raise _err(f"{where} must be an object")
        unknown = set(entry) - _MODEL_KEYS
        if unknown:
            raise _err(f"{where}: unknown key(s): {sorted(unknown)}")
        missing = {"context", "max_output", "released", "broken", "source"} - set(entry)
        if missing:
            raise _err(f"{where}: missing key(s): {sorted(missing)}")
        for fk in ("context", "max_output"):
            v = entry[fk]
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
                raise _err(f"{where}.{fk} must be a positive int or null, got {v!r}")
        if entry["released"] is not None and not isinstance(entry["released"], str):
            raise _err(f"{where}.released must be a string or null")
        if entry["broken"] is not None and not isinstance(entry["broken"], bool):
            raise _err(f"{where}.broken must be a bool or null")
        if not isinstance(entry["source"], str) or not entry["source"]:
            raise _err(f"{where}.source must be a non-empty string")
        reason = entry.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise _err(f"{where}.reason must be a string or null")
        nulls = [f for f in ("context", "max_output", "released", "broken") if entry[f] is None]
        if nulls and not reason:
            raise _err(f"{where}: null field(s) {nulls} require a 'reason'")
        models[key] = ModelFacts(context=entry["context"], max_output=entry["max_output"],
                                 released=entry["released"], broken=entry["broken"],
                                 source=entry["source"], reason=reason)
    return Limits(session_min_context=doc["session_min_context"],
                  large_context_threshold=doc["large_context_threshold"],
                  providers=providers, models=models)


def load(path: Path | str | None = None) -> Limits:
    """Load and strictly validate the limits file. Resolution: FA_PROVIDER_LIMITS if set
    (must exist and validate, else LimitsError naming it), else <repo>/config/provider-limits.json."""
    override = os.environ.get("FA_PROVIDER_LIMITS")
    if override is not None:
        p = Path(override)
        if not p.is_file():
            raise LimitsError(
                f"FA_PROVIDER_LIMITS={override} does not exist — set-but-invalid overrides abort, "
                f"never fall through")
        origin = f"FA_PROVIDER_LIMITS={override}"   # every error from this file names the var
    else:
        p = Path(path) if path is not None else DEFAULT_PATH
        origin = str(p)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise LimitsError(f"{origin}: cannot read ({exc})") from exc
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise LimitsError(f"{origin}: invalid JSON ({exc})") from exc
    return _parse(doc, origin)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="provider_limits.py",
        description="Load/validate config/provider-limits.json (single source of provider "
                    "limits + model facts). Env: FA_PROVIDER_LIMITS overrides the path and "
                    "must exist+validate or aborts naming it.")
    sub = ap.add_subparsers(dest="cmd")
    v = sub.add_parser("validate", help="validate a limits file (default: the repo's); "
                                        "exit 0 valid, 2 with the error on stderr if not")
    v.add_argument("path", nargs="?", default=None)
    s = sub.add_parser("show", help="print one provider's limits as JSON")
    s.add_argument("provider")
    args = ap.parse_args(argv)
    if args.cmd == "validate":
        try:
            limits = load(args.path)
        except LimitsError as exc:
            print(f"INVALID: {exc}", file=sys.stderr)
            return 2
        print(f"OK: {len(limits.providers)} provider(s), {len(limits.models)} model fact(s), "
              f"threshold {limits.large_context_threshold}")
        return 0
    if args.cmd == "show":
        limits = load()
        p = limits.provider(args.provider)
        if p is None:
            print(json.dumps({"provider": args.provider, "limits": "unknown"}))
            return 0
        print(json.dumps({"provider": args.provider,
                          "limits": {"rpm": p.rpm, "tpm": p.tpm, "rpd": p.rpd,
                                     "rpw": p.rpw, "tpd": p.tpd,
                                     "dispatch_only": p.dispatch_only,
                                     "reserves_on_request": p.reserves_on_request,
                                     "measured_at": p.measured_at, "source": p.source}},
                         indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

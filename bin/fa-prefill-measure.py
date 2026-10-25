#!/usr/bin/env python3
"""fa-prefill-measure.py — measure a session's true prefill on a FREE lane (cloud-screen plan Task 10).

Runs one headless `claude -p "ok" --output-format json` through the lane's OWN launcher
(default: bin/remote-session.sh, which starts its own proxy on its own port), reads the usage
block, and caches the measurement keyed by a hash of the machine's prompt-weight inputs. The
numbers feed the token-profile receipt (spec §5 B3) and the Task-11 level proposal.

NEVER runs a bare claude: a nested claude inherits the ambient ANTHROPIC_BASE_URL and bills the
PAID gateway (measured: a $0.19 surprise). When the ambient environment points at a remote
(non-localhost) base URL — i.e. this tool was invoked from inside a paid session — it refuses
before launching anything.

Usage:
  fa-prefill-measure.py --lane remote [--alias <alias>] [--lever name=level ...] [--json]
  fa-prefill-measure.py --cached --lane remote [--lever name=level ...]

Options:
  --lane local|remote|cloud   the lane to measure (remote = the verified free-lane headless
                              vehicle; cloud = the same vehicle with the cloud profile's
                              settings applied; local = no headless vehicle yet)
  --alias <alias>             remote alias to launch (default: $FA_PREFILL_ALIAS; required
                              for a real run otherwise — `remote-session.sh --list` shows them)
  --lever name=level          repeatable; known levers: mcp=all|none. A lever changes BOTH the
                              launch env and the cache key.
  --cached                    print the cached measurement for lane+levers (or nothing)
  --json                      print the measurement dict as JSON (default: a human line)

Environment:
  FA_PREFILL_STATE_DIR        cache directory (default ~/.local/state/free-agents)
  FA_PREFILL_LAUNCHER         launcher override (tests; default the repo's remote-session.sh)
  FA_PREFILL_ALIAS            default --alias
  FA_PREFILL_CLAUDE_MD        extra CLAUDE.md path folded into the inputs hash (tests)
  FA_PREFILL_CLOUD_PROFILE    settings file applied on the cloud lane (LA_REMOTE_CLAUDE_SETTINGS)

Exit codes: 0 measured/printed · 2 usage error · 3 cloud guard refused · 1 launch/parse failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "free-agents"
CACHE_NAME = "prefill-measurements.json"
LAUNCH_PROMPT = "ok"
KNOWN_LEVERS = {"mcp": ("all", "none")}


class CloudBaseURLError(RuntimeError):
    """The ambient environment points at a remote base URL: refuse to launch anything."""


def _inputs_hash(lane: str, levers: dict, env: dict) -> str:
    """A stable hash of everything that weights the prompt, so a stale number can never
    masquerade as current. Local to this machine by construction."""
    h = hashlib.sha256()
    h.update(lane.encode())
    h.update(json.dumps(levers, sort_keys=True).encode())
    home = Path(env.get("HOME") or Path.home())
    candidates = [
        home / ".claude" / "CLAUDE.md",
        home / ".claude" / "settings.json",
        home / ".claude" / "settings.local.json",
        home / ".claude.json",                      # user-scope mcpServers live here
    ]
    extra_md = env.get("FA_PREFILL_CLAUDE_MD")
    if extra_md:
        candidates.append(Path(extra_md))
    for p in candidates:
        try:
            h.update(b"\0" + p.read_bytes())
        except OSError:
            h.update(b"\0<absent>")
    # Installed plugins: one line per cache dir (name + version dirs), order-independent.
    plugins = home / ".claude" / "plugins" / "cache"
    try:
        names = sorted(f"{d.parent.name}/{d.name}" for d in plugins.iterdir() if d.is_dir())
    except OSError:
        names = []
    h.update(json.dumps(names).encode())
    cloud_profile = env.get("FA_PREFILL_CLOUD_PROFILE")
    if cloud_profile:
        try:
            h.update(b"\0profile:" + Path(cloud_profile).read_bytes())
        except OSError:
            h.update(b"\0profile:<absent>")
    return h.hexdigest()[:16]


def _guard(env: dict) -> None:
    """Refuse to launch anything from inside a paid session's environment."""
    base = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    if base and not base.startswith(("http://localhost", "http://127.", "https://localhost",
                                     "https://127.")):
        raise CloudBaseURLError(
            f"ANTHROPIC_BASE_URL={base} looks like a remote/paid endpoint. A nested claude "
            "would bill it (measured: a $0.19 surprise). Run this tool from a neutral shell "
            "or with ANTHROPIC_BASE_URL unset.")


def _validate_levers(levers: dict) -> None:
    for name, level in levers.items():
        if name not in KNOWN_LEVERS or level not in KNOWN_LEVERS[name]:
            raise ValueError(f"unknown lever {name}={level}; known: "
                             + ", ".join(f"{k}={'|'.join(v)}" for k, v in sorted(KNOWN_LEVERS.items())))


def _lever_env(lane: str, levers: dict) -> dict:
    out = {}
    mcp = levers.get("mcp")
    if mcp == "all":
        out["LA_REMOTE_ENABLE_MCP"] = "1"
    elif mcp == "none":
        out["LA_REMOTE_ENABLE_MCP"] = "0"
    if lane == "cloud":
        profile = os.environ.get("FA_PREFILL_CLOUD_PROFILE")
        if not profile:
            raise ValueError("the cloud lane needs FA_PREFILL_CLOUD_PROFILE (a --settings file)")
        out["LA_REMOTE_CLAUDE_SETTINGS"] = profile
    return out


def _extract_result_json(text: str) -> dict:
    """claude's JSON lands on stdout after the launcher's banner, so find the last JSON
    object that carries a usage block."""
    try:
        doc = json.loads(text)
        if isinstance(doc, dict) and "usage" in doc:
            return doc
    except ValueError:
        pass
    best = None
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch == "{":
            try:
                doc, _end = decoder.raw_decode(text[idx:])
            except ValueError:
                continue
            if isinstance(doc, dict) and "usage" in doc:
                best = doc
    if best is None:
        raise RuntimeError("no claude result JSON with a usage block found on stdout")
    return best


def _cache_path(env: dict) -> Path:
    return Path(env.get("FA_PREFILL_STATE_DIR") or DEFAULT_STATE_DIR) / CACHE_NAME


def _load_cache(env: dict) -> dict:
    try:
        return json.loads(_cache_path(env).read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(doc: dict, env: dict) -> None:
    path = _cache_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".prefill-", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def measure(lane: str, levers: dict, env: dict | None = None) -> dict:
    """One headless free-lane run -> the usage dict, cached under lane + levers + inputs hash."""
    env = dict(os.environ if env is None else env)
    _guard(env)
    if lane not in ("remote", "cloud"):
        raise ValueError(f"no headless vehicle wired for the {lane!r} lane yet (Task 11); "
                         "measured lanes so far: remote")
    _validate_levers(levers)
    launcher = env.get("FA_PREFILL_LAUNCHER") or str(REPO_ROOT / "bin" / "remote-session.sh")
    alias = env.get("FA_PREFILL_ALIAS") or ""
    if not alias:
        raise ValueError("no alias given: pass --alias or set FA_PREFILL_ALIAS "
                         "(remote-session.sh --list shows the roster)")
    argv = [launcher, alias, "-p", LAUNCH_PROMPT, "--output-format", "json"]
    child_env = dict(env)
    child_env.update(_lever_env(lane, levers))
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        r = subprocess.run(argv, env=child_env, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired as err:
        raise RuntimeError(f"the launch timed out after {err.timeout}s") from err
    if r.returncode != 0:
        raise RuntimeError(f"launch failed (exit {r.returncode}): "
                           f"{(r.stderr or r.stdout or '').strip()[-400:]}")
    usage = _extract_result_json(r.stdout)["usage"]
    result = {
        "lane": lane,
        "levers": dict(levers),
        "input_tokens": int(usage.get("input_tokens") or 0),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "inputs_hash": _inputs_hash(lane, levers, env),
        "at": started,
    }
    result["total_prefill"] = (result["input_tokens"] + result["cache_creation_input_tokens"]
                               + result["cache_read_input_tokens"])
    cache = _load_cache(env)
    cache.setdefault("measurements", {})[f"{lane}:{json.dumps(levers, sort_keys=True)}"] = result
    _save_cache(cache, env)
    return result


def cached(lane: str, levers: dict, env: dict | None = None) -> dict | None:
    """The cached measurement for lane+levers, or None when absent or stale (the machine's
    inputs changed since it was taken)."""
    env = dict(os.environ if env is None else env)
    _validate_levers(levers)
    doc = _load_cache(env).get("measurements", {}).get(f"{lane}:{json.dumps(levers, sort_keys=True)}")
    if not doc or doc.get("inputs_hash") != _inputs_hash(lane, levers, env):
        return None
    return doc


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="fa-prefill-measure.py",
        description="Measure a session's true prefill on a FREE lane (headless claude -p "
                    "through the lane's launcher; never a bare claude, never a paid endpoint).")
    ap.add_argument("--lane", default="remote", choices=("remote", "cloud", "local"))
    ap.add_argument("--alias", help="remote alias to launch (default $FA_PREFILL_ALIAS)")
    ap.add_argument("--lever", action="append", default=[], metavar="NAME=LEVEL",
                    help="repeatable; known: mcp=all|none")
    ap.add_argument("--cached", action="store_true", help="print the cached measurement only")
    ap.add_argument("--json", action="store_true", help="print the measurement dict as JSON")
    args = ap.parse_args(argv)

    levers = {}
    for spec in args.lever:
        name, _, level = spec.partition("=")
        levers[name] = level
    try:
        if args.alias:
            os.environ["FA_PREFILL_ALIAS"] = args.alias
        if args.cached:
            doc = cached(args.lane, levers)
            if doc is None:
                print("no cached measurement for this lane+levers (or the inputs changed)")
                return 0
        else:
            doc = measure(args.lane, levers)
    except CloudBaseURLError as exc:
        print(f"fa-prefill-measure: refusing: {exc}", file=sys.stderr)
        return 3
    except (ValueError, RuntimeError) as exc:
        print(f"fa-prefill-measure: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(doc, indent=2))
    else:
        print(f"{doc['lane']} {json.dumps(doc['levers'], sort_keys=True)}: "
              f"total_prefill={doc['total_prefill']:,} (input {doc['input_tokens']:,} + "
              f"cache_write {doc['cache_creation_input_tokens']:,} + "
              f"cache_read {doc['cache_read_input_tokens']:,}) at {doc['at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

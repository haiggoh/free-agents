#!/usr/bin/env python3
"""fa-eligibility-probe.py — is auto mode's safety checking FREE on this machine's cloud setup?

One headless auto-mode request whose action is provably outside every allow rule, and the
verdict is read from EVIDENCE, never from absence:
  not-eligible  the safety notice arrives as a `system` warning in the stream
  unknown       no positive signal (absence fails open — it never proves "eligible")
The verdict is cached (with its date and evidence) at
~/.local/state/free-agents/eligibility.json; the screen shows the cost box only for
not-eligible. Refuses on a free-lane shell (localhost base URL): never sends anything.

Usage:
  fa-eligibility-probe.py [--json] [--credential-command CMD] [--force]

Env:
  ANTHROPIC_BASE_URL      localhost -> refuse (this is a free lane; nothing to measure)
  FA_STATE_DIR            override the cache dir (default ~/.local/state/free-agents)

The credential command's output (e.g. `joyia agent claude --setup-only --json`) travels only
in the child environment — never in argv, logs, the cache or this process's output.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_CREDENTIAL_COMMAND = ["joyia", "agent", "claude", "--setup-only", "--json"]
PROBE_PREFIX = "fa-eligibility-probe-"


def state_dir() -> Path:
    return Path(os.environ.get("FA_STATE_DIR") or Path.home() / ".local" / "state" / "free-agents")


def cache_path() -> Path:
    return state_dir() / "eligibility.json"


def cached() -> dict | None:
    """The cached verdict (with date + evidence) or None."""
    try:
        doc = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and doc.get("verdict") else None


def _localhost(url: str | None) -> bool:
    from urllib.parse import urlparse
    try:
        return (urlparse(url or "").hostname or "") in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    except ValueError:
        return False


def _merged_allow(cfg_dir: Path) -> list:
    """The user settings' permissions.allow entries (the layers csl does not own are not
    readable here; the run itself is the arbiter — this check only avoids the pointless case
    where the probe command is allowlisted and would never reach the classifier)."""
    try:
        doc = json.loads((cfg_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    perms = doc.get("permissions") if isinstance(doc, dict) else None
    allow = perms.get("allow") if isinstance(perms, dict) else None
    return allow if isinstance(allow, list) else []


def bash_blanket_allowed(cfg_dir: Path | None = None) -> bool:
    """True when a settings layer allows Bash or Bash(*) outright: then the probe's action
    would not reach the classifier either, so the honest verdict is unknown without a run."""
    cfg = cfg_dir or Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    for rule in _merged_allow(cfg):
        if rule in ("Bash", "Bash(*)"):
            return True
    return False


def resolve_credential_command(env: dict) -> str | None:
    """The credential command to run, or None when credentials are already present (a native
    login needs no setup; a base URL with a token in the env is already resolvable)."""
    if env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY"):
        return None
    if not env.get("ANTHROPIC_BASE_URL") and not shutil.which("claude"):
        return None
    cmd = env.get("FA_CREDENTIAL_COMMAND")
    if cmd:
        return cmd
    return " ".join(DEFAULT_CREDENTIAL_COMMAND) if shutil.which("joyia") else None


def _extract_json_object(text: str) -> dict:
    """The first balanced JSON object in `text` — the default credential command wraps its
    JSON in an ANSI banner (measured 2026-10-24 against joyia's setup output), so a plain
    json.loads on the whole stdout fails on the banner line."""
    import re
    clean = re.sub(r"\x1b\[[0-9;]*m", "", text)
    start = clean.find("{")
    if start < 0:
        raise ValueError("no JSON object found")
    depth = 0
    for i in range(start, len(clean)):
        if clean[i] == "{":
            depth += 1
        elif clean[i] == "}":
            depth -= 1
            if depth == 0:
                doc = json.loads(clean[start:i + 1])
                if not isinstance(doc, dict):
                    raise ValueError("not a JSON object")
                return doc
    raise ValueError("unbalanced JSON object")


def probe(run_child, env: dict | None = None, cfg_dir: Path | None = None,
          now: float | None = None, force: bool = False) -> dict:
    """Run the probe and return the verdict dict {verdict, evidence, at}. A cached verdict is
    returned verbatim unless `force`. `run_child(argv, env, stdin_text) -> (returncode, stdout)`
    is injected for tests; production runs plain claude."""
    env = dict(os.environ if env is None else env)
    now = time.time() if now is None else now
    if not force:
        hit = cached()
        if hit:
            return hit
    base = env.get("ANTHROPIC_BASE_URL")
    if _localhost(base):
        return {"verdict": "refused", "evidence": f"this shell is a free lane ({base}) — "
                "the probe only measures a cloud setup", "at": now}
    if bash_blanket_allowed(cfg_dir):
        return {"verdict": "unknown", "evidence": "a settings layer allows Bash outright, so the "
                "probe's action would not reach the safety check", "at": now}

    cred_cmd = resolve_credential_command(env)
    child_env = dict(env)
    if cred_cmd:
        r = subprocess.run(cred_cmd, shell=True, capture_output=True, text=True, timeout=60)
        try:
            injected = _extract_json_object(r.stdout)
        except ValueError as exc:
            return {"verdict": "refused", "evidence": f"the credential command printed no JSON "
                    f"({exc}: {(r.stderr or r.stdout or '').strip()[:120]})", "at": now}
        for k, v in injected.items():
            if k.startswith("ANTHROPIC_"):
                child_env[k] = v
    if _localhost(child_env.get("ANTHROPIC_BASE_URL")):
        return {"verdict": "refused", "evidence": "the resolved base URL is a free lane — "
                "the probe only measures a cloud setup", "at": now}

    action = f"{PROBE_PREFIX}{secrets.token_hex(4)}"
    argv = ["claude", "-p", action, "--permission-mode", "auto", "--output-format", "stream-json"]
    rc, out = run_child(argv, child_env, "")
    notice = ""
    for line in out.splitlines():
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        if evt.get("type") == "system":
            notice = json.dumps(evt)[:300]
            break
    verdict = {"verdict": "not-eligible" if notice else "unknown",
               "evidence": notice or "no system notice in the stream — absence is never positive",
               "at": now, "probe_action": action}
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        cache_path().write_text(json.dumps(verdict, indent=1) + "\n", encoding="utf-8")
    except OSError:
        pass
    return verdict


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="fa-eligibility-probe.py",
        description="Measure (with one small request) whether auto mode's safety checks are free "
                    "on this machine's cloud setup; caches the verdict with its date.")
    p.add_argument("--json", action="store_true", help="print the verdict as JSON")
    p.add_argument("--credential-command", default=None, metavar="CMD",
                   help="command printing the cloud env as JSON (default: joyia agent claude "
                        "--setup-only --json, when joyia is installed and no token is present)")
    p.add_argument("--force", action="store_true", help="run even when a cached verdict exists")
    a = p.parse_args(argv)

    env = dict(os.environ)
    if a.credential_command:
        env["FA_CREDENTIAL_COMMAND"] = a.credential_command
    def _run(argv_, env_, stdin_):
        r = subprocess.run(argv_, capture_output=True, text=True, env=env_, input=stdin_,
                           timeout=300)
        return r.returncode, r.stdout

    verdict = probe(_run, env, force=a.force)
    cached_hit = verdict.get("at") is not None and verdict.get("probe_action") is None
    if verdict["verdict"] == "refused":
        print(f"fa-eligibility-probe: {verdict['evidence']}", file=sys.stderr)
        return 2
    if cached_hit and not a.json:
        print(f"{verdict['verdict']} (cached {time.strftime('%Y-%m-%d', time.localtime(verdict['at']))})")
        print(f"  evidence: {verdict['evidence'][:200]}")
        return 0
    print(json.dumps(verdict) if a.json else
          f"{verdict['verdict']}\n  evidence: {verdict['evidence'][:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

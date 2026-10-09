#!/usr/bin/env python3
"""cloud_session_env — cloud-session settings that live in Claude Code's OWN settings.json `env`.

Why there: Claude Code applies `settings.json` -> `env` to every session it starts and to the
plugin hooks inside it, whatever the login (native Anthropic account, API key, an LLM gateway) and
whatever the launcher (plain `claude`, an IDE, a wrapper script). Probed 2026-10-08: a Stop hook saw
`env` values from user settings AND from a `--settings` file. So a value written here reaches every
cloud session with no launcher cooperation, and the user can see and edit it in the file Claude
Code documents for exactly this. (Before 0.26.0 the Cloud Session Configuration screen reached no
cloud session at all; an env file only one private launcher read was the first, discarded fix.)

Only whitelisted keys are ever written or removed; every other byte of settings.json is preserved
(same JSON, same mode, symlinks followed rather than replaced).

Settings:
  security_review   0 = plugin default (security-guidance reviews on its default model, opus-4-7)
                    1 = cheaper: SECURITY_REVIEW_MODEL=claude-sonnet-4-6 (first-party ids only;
                        not offered under Bedrock/Vertex/Foundry, which need provider ids)
                    2 = off:     ENABLE_CODE_SECURITY_REVIEW=0 (pattern warnings keep working)
                   -1 = custom:  SECURITY_REVIEW_MODEL set by hand to something else (left alone
                        until the user cycles the option)

The option is only OFFERED when the security-guidance plugin is installed and enabled — or when an
override from us is still present, so it can always be cleared (a hidden leftover would be stale state).

  replace_agents    FA_REPLACE_AGENTS in env, read by hooks/cloud-agent-intercept.py (PreToolUse on Agent):
                    unset = undecided (the hook asks ONCE, the first time a cloud session calls Agent)
                    1 = replace paid Sonnet subagents with free agents (bin/free-agent-tool.py)
                    0 = keep native Sonnet subagents
                    Free sessions (local, free-API) are never affected: their subagents already run free.

Usage:
  cloud_session_env.py show
  cloud_session_env.py set security_review {0,1,2}
  cloud_session_env.py set replace_agents {0,1,unset}
  cloud_session_env.py --help

Environment:
  CLAUDE_CONFIG_DIR   Claude Code config dir (default ~/.claude) — settings.json and plugins/ live here
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

PLUGIN_NAME = "security-guidance"
SECURITY_REVIEW_LABELS = {0: "default (opus-4-7)", 1: "cheaper (sonnet-4-6)", 2: "off", -1: "custom"}
CHEAP_MODEL = "claude-sonnet-4-6"
_OWNED_KEYS = ("SECURITY_REVIEW_MODEL", "ENABLE_CODE_SECURITY_REVIEW")
_ENV_FOR = {0: {}, 1: {"SECURITY_REVIEW_MODEL": CHEAP_MODEL}, 2: {"ENABLE_CODE_SECURITY_REVIEW": "0"}}
REPLACE_AGENTS_KEY = "FA_REPLACE_AGENTS"
REPLACE_AGENTS_LABELS = {None: "ask once (undecided)", "1": "ON — free agents", "0": "OFF — native Sonnet"}
_THIRD_PARTY_FLAGS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")


def config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def settings_path() -> Path:
    return config_dir() / "settings.json"


def _read_json(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _env(settings: dict) -> dict:
    e = settings.get("env")
    return e if isinstance(e, dict) else {}


def third_party_provider(settings: dict | None = None) -> bool:
    """Bedrock/Vertex/Foundry need provider-specific model ids, so a bare 'cheaper' id would break."""
    env = {**os.environ, **_env(settings if settings is not None else _read_json(settings_path()))}
    return any(str(env.get(k, "")).strip().lower() in ("1", "true", "yes", "on") for k in _THIRD_PARTY_FLAGS)


def plugin_status(settings: dict | None = None) -> tuple[bool, str]:
    """(active, reason). Active = installed (any marketplace) AND enabled in user settings AND not
    killed by the plugin's own SECURITY_GUIDANCE_DISABLE switch."""
    settings = settings if settings is not None else _read_json(settings_path())
    reg = _read_json(config_dir() / "plugins" / "installed_plugins.json")
    plugins = reg.get("plugins", reg) if isinstance(reg, dict) else {}
    keys = [k for k in plugins if isinstance(k, str) and k.split("@", 1)[0] == PLUGIN_NAME]
    if not keys:
        return False, "not installed"
    enabled = settings.get("enabledPlugins") or {}
    if not any(enabled.get(k) is True for k in keys):
        return False, "installed but disabled"
    if str(_env(settings).get("SECURITY_GUIDANCE_DISABLE", os.environ.get("SECURITY_GUIDANCE_DISABLE", ""))) == "1":
        return False, "disabled by SECURITY_GUIDANCE_DISABLE=1"
    return True, "active"


def load() -> dict:
    """Current state, read from settings.json env. Never writes."""
    s = _read_json(settings_path())
    env = _env(s)
    if str(env.get("ENABLE_CODE_SECURITY_REVIEW", "")) == "0":
        state, custom = 2, None
    elif "SECURITY_REVIEW_MODEL" in env:
        model = str(env["SECURITY_REVIEW_MODEL"])
        state, custom = (1, None) if model == CHEAP_MODEL else (-1, model)
    else:
        state, custom = 0, None
    active, reason = plugin_status(s)
    return {"security_review": state, "custom_model": custom, "plugin_active": active,
            "plugin_reason": reason, "override_present": state != 0,
            "cheaper_offered": not third_party_provider(s)}


def offered() -> bool:
    st = load()
    return st["plugin_active"] or st["override_present"]


def next_state(current: int) -> int:
    """Cycle default -> cheaper -> off -> default, skipping 'cheaper' where its id would be wrong."""
    order = [0, 1, 2] if not third_party_provider() else [0, 2]
    if current not in order:
        return 0
    return order[(order.index(current) + 1) % len(order)]


def save(security_review: int) -> Path:
    """Set the mode by editing ONLY the owned env keys; everything else in settings.json is kept.
    Raises ValueError (writing nothing) on a value outside 0/1/2."""
    if security_review not in _ENV_FOR:
        raise ValueError(f"invalid security_review: {security_review!r} (allowed: 0, 1, 2)")
    return _write_env(_OWNED_KEYS, _ENV_FOR[security_review])


def replace_agents() -> str | None:
    """FA_REPLACE_AGENTS from settings.json env: "1", "0", or None (undecided). Never writes."""
    v = _env(_read_json(settings_path())).get(REPLACE_AGENTS_KEY)
    return str(v) if v is not None and str(v) in ("0", "1") else None


def save_replace_agents(value: str | None) -> Path:
    """Set FA_REPLACE_AGENTS to "1"/"0", or remove it (None = undecided, the hook asks once)."""
    if value not in (None, "0", "1"):
        raise ValueError(f"invalid replace_agents: {value!r} (allowed: 0, 1, unset)")
    return _write_env((REPLACE_AGENTS_KEY,), {} if value is None else {REPLACE_AGENTS_KEY: value})


def _write_env(owned: tuple, updates: dict) -> Path:
    """Drop `owned` env keys, apply `updates`; preserve every other byte, the mode and symlinks."""
    path = settings_path()
    real = Path(os.path.realpath(path))          # a dotfiles symlink stays a symlink
    try:
        raw = real.read_text(encoding="utf-8")
        doc = json.loads(raw) if raw.strip() else {}
        mode = os.stat(real).st_mode & 0o777
    except FileNotFoundError:
        raw, doc, mode = None, {}, 0o600
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a JSON object; refusing to rewrite it")
    had_env = "env" in doc
    env = dict(_env(doc))
    for k in owned:
        env.pop(k, None)
    env.update(updates)
    if env or had_env:
        doc["env"] = env
    body = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if body == raw:
        return path
    real.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".tmp", dir=real.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def describe(st: dict | None = None) -> str:
    st = st or load()
    label = SECURITY_REVIEW_LABELS[st["security_review"]]
    if st["security_review"] == -1:
        label = f"custom ({st['custom_model']})"
    return label


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cloud_session_env.py",
        description="security-guidance review mode for Claude Code sessions, kept in settings.json env.",
        epilog="Env: CLAUDE_CONFIG_DIR selects the Claude Code config dir (default ~/.claude).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show", help="print plugin status, the current mode and the env keys behind it")
    s = sub.add_parser("set", help="set security_review {0,1,2} or replace_agents {0,1,unset}")
    s.add_argument("key", choices=["security_review", "replace_agents"])
    s.add_argument("value", choices=["0", "1", "2", "unset"])
    a = p.parse_args(argv)
    if a.cmd == "set" and a.key == "replace_agents":
        if a.value not in ("0", "1", "unset"):
            p.error("replace_agents takes 0, 1 or unset")
        print(f"updated {save_replace_agents(None if a.value == 'unset' else a.value)}")
    elif a.cmd == "set":
        if a.value == "unset":
            p.error("security_review takes 0, 1 or 2")
        a.value = int(a.value)
        if a.value == 1 and third_party_provider():
            print("cloud_session_env.py: 'cheaper' needs a provider-specific model id under "
                  "Bedrock/Vertex/Foundry; set SECURITY_REVIEW_MODEL yourself", file=sys.stderr)
            return 2
        print(f"updated {save(a.value)}")
    st = load()
    print(f"{PLUGIN_NAME}: {st['plugin_reason']}")
    print(f"security_review = {describe(st)}")
    env = _env(_read_json(settings_path()))
    for k in _OWNED_KEYS:
        if k in env:
            print(f"  settings.json env: {k}={env[k]}")
    print(f"replace_agents = {REPLACE_AGENTS_LABELS[replace_agents()]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

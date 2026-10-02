#!/usr/bin/env python3
"""session_menu_state.py — repo-local preferences for the session picker.

Stores ONLY confirmed menu choices (effort per lane, NVIDIA rate-limiter settings, the alias each
lane last STARTED) in config/session-menu.local.json next to the source checkout. No secrets,
paths or history.

Schema 2 (0.22.0) adds `last_launched`. A schema-1 file loads unchanged and is migrated only by
the next confirmed write, which keeps every validated effort and rate-limiter value. A file with
a section this version does not know is refused (warned, left untouched) rather than rewritten
without it.

Safety rules, each covered by tests/test_session_menu_state.py:
  * reading never writes (no file or lock appears on startup, --help or a direct launch);
  * an unreadable / invalid / future-schema / symlinked / non-regular file is WARNED about
    and left byte-for-byte untouched — defaults are used in memory instead;
  * writes take an advisory lock, re-read, change one field, then mkstemp + fchmod 0600 +
    fsync + os.replace in the same directory;
  * an installed plugin-cache copy is never written, and there is no second fallback store.

CLI (used by the Bash entry points; read-only unless you call `set`):
  session_menu_state.py get <lane>            print the saved-or-default effort
  session_menu_state.py set <lane> <value>    persist one lane's effort
  session_menu_state.py show                  print the effective state as JSON
Lanes: local_session, remote_api_session, lowkey.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 2
READABLE_SCHEMAS = (1, 2)
SECTIONS = ("schema_version", "effort", "rate_limiter", "last_launched")
PREFS_NAME = "session-menu.local.json"
LOCK_NAME = "session-menu.local.lock"
MAX_BYTES = 16 * 1024

CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Rapid-MLX validates reasoning_effort against this closed set (api/models.py
# _VALID_REASONING_EFFORTS) and answers HTTP 400 to anything else, so `max` is NOT offered.
LOWKEY_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
PROVIDER_DEFAULT = "provider_default"

ALLOWED_EFFORT = {
    "local_session": CLAUDE_EFFORTS,
    "remote_api_session": CLAUDE_EFFORTS + (PROVIDER_DEFAULT,),
    "lowkey": LOWKEY_EFFORTS,
}
DEFAULT_EFFORT = {"local_session": "high", "remote_api_session": "max", "lowkey": "xhigh"}

RATE_LIMITER_MODES = ("sliding_window", "smooth_bucket")
# "cooldown" is the BASE cooldown of rate_limiter's 429 exponential backoff (0.20.11); the key
# keeps its pre-backoff name so files saved before the backoff still load unchanged.
_RL_NUMERIC = {"rpm": (1, 10000), "bucket_capacity": (1, 1000),
               "max_wait": (0, 3600), "cooldown": (0, 3600),
               "max_cooldown": (0, 3600), "backoff_multiplier": (1, 10), "max_retries": (0, 50)}
_RL_WHOLE = ("rpm", "bucket_capacity", "max_retries")

# A launcher alias: what csl / remote-session.sh / lowkey accept as a model name.
_ALIAS_RE = __import__("re").compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}")

REPO_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass
class State:
    effort: dict = field(default_factory=lambda: dict(DEFAULT_EFFORT))
    rate_limiter: dict = field(default_factory=dict)
    last_launched: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def effort_arg(lane: str, value: str) -> str | None:
    """What to pass to the launcher: None means pass NO effort override at all."""
    return None if value == PROVIDER_DEFAULT else value


def _validate_effort(lane: str, value) -> None:
    if lane not in ALLOWED_EFFORT:
        raise ValueError(f"unknown lane {lane!r}")
    if value not in ALLOWED_EFFORT[lane]:
        raise ValueError(f"{lane} effort must be one of {', '.join(ALLOWED_EFFORT[lane])}")


def _validate_rate_limiter(values: dict, allow_none: bool = False) -> None:
    for key, value in values.items():
        if value is None and allow_none:
            continue
        if key == "mode":
            if value not in RATE_LIMITER_MODES:
                raise ValueError(f"mode must be one of {', '.join(RATE_LIMITER_MODES)}")
        elif key in _RL_NUMERIC:
            lo, hi = _RL_NUMERIC[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ValueError(f"{key} must be a number in {lo}..{hi}")
            if key in _RL_WHOLE and not float(value).is_integer():
                raise ValueError(f"{key} must be a whole number")
        else:
            raise ValueError(f"unknown rate limiter setting {key!r}")


def _validate_last_launched(lane: str, alias) -> None:
    if lane not in ALLOWED_EFFORT:
        raise ValueError(f"unknown lane {lane!r}")
    if not isinstance(alias, str) or not _ALIAS_RE.fullmatch(alias):
        raise ValueError(f"{lane} last launched alias is not a valid alias")


def _unsafe_dir(config_dir: Path) -> str | None:
    real = Path(os.path.realpath(config_dir))
    if "/.claude/plugins/cache/" in str(real) + "/":
        return "preferences are not stored in an installed plugin copy"
    if not real.is_dir():
        return "config directory is missing"
    return None


def _parse(raw: bytes) -> tuple[dict, dict, dict]:
    """Return (effort, rate_limiter, last_launched) or raise ValueError with a short reason."""
    if len(raw) > MAX_BYTES:
        raise ValueError("preference file is too large")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("preference file is not valid JSON") from None
    if not isinstance(doc, dict):
        raise ValueError("preference file is not a JSON object")
    if doc.get("schema_version") not in READABLE_SCHEMAS:
        raise ValueError(f"unsupported schema_version {doc.get('schema_version')!r}")
    unknown = sorted(set(doc) - set(SECTIONS))
    if unknown:
        raise ValueError(f"unknown preference section(s) {', '.join(unknown)}")
    if doc["schema_version"] == 1 and "last_launched" in doc:
        raise ValueError("last_launched needs schema_version 2")
    effort = doc.get("effort", {})
    limiter = doc.get("rate_limiter", {})
    last = doc.get("last_launched", {})
    if not all(isinstance(x, dict) for x in (effort, limiter, last)):
        raise ValueError("preference sections must be JSON objects")
    for lane, value in effort.items():
        _validate_effort(lane, value)
    _validate_rate_limiter(limiter)
    for lane, alias in last.items():
        _validate_last_launched(lane, alias)
    return effort, limiter, last


def _read(path: Path) -> tuple[dict, dict, dict] | None:
    """None when absent; raises ValueError when present but unusable."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode):
        raise ValueError("preference file is a symlink")
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("preference file is not a regular file")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"preference file is unreadable ({errno.errorcode.get(exc.errno, exc.errno)})") from None
    return _parse(raw)


def load(config_dir: Path | str = REPO_CONFIG_DIR) -> State:
    """Read-only: never creates the file or the lock."""
    config_dir = Path(config_dir)
    state = State()
    try:
        found = _read(config_dir / PREFS_NAME)
    except ValueError as exc:
        state.warnings.append(f"{exc}; using built-in defaults and leaving the file untouched")
        return state
    if found:
        effort, limiter, last = found
        state.effort.update(effort)
        state.rate_limiter = dict(limiter)
        state.last_launched = dict(last)
    return state


@contextmanager
def _locked(config_dir: Path):
    lock_path = config_dir / LOCK_NAME
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write(config_dir: Path, doc: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=".session-menu.", suffix=".tmp", dir=config_dir)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, config_dir / PREFS_NAME)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(config_dir, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _update(config_dir: Path | str, mutate) -> str | None:
    """Apply `mutate(effort, limiter, last_launched)` under the lock. Returns a warning or None.

    Always writes schema 2, so a schema-1 file is migrated here (and only here), carrying every
    section it already had."""
    config_dir = Path(config_dir)
    reason = _unsafe_dir(config_dir)
    if reason:
        return f"{reason}; the choice applies to this run only"
    try:
        with _locked(config_dir):
            found = _read(config_dir / PREFS_NAME)
            effort, limiter, last = found if found else ({}, {}, {})
            mutate(effort, limiter, last)
            _write(config_dir, {"schema_version": SCHEMA_VERSION, "effort": effort,
                                "rate_limiter": limiter, "last_launched": last})
    except ValueError as exc:
        return f"{exc}; not saved (file left untouched), the choice applies to this run only"
    except OSError as exc:
        code = errno.errorcode.get(exc.errno, "error") if exc.errno else "error"
        return f"could not save preferences ({code}); the choice applies to this run only"
    return None


def save_effort(config_dir: Path | str, lane: str, value: str) -> str | None:
    _validate_effort(lane, value)          # a programming/user error, raised not warned

    def mutate(effort, _limiter, _last):
        effort[lane] = value
    return _update(config_dir, mutate)


def save_rate_limiter(config_dir: Path | str, values: dict) -> str | None:
    _validate_rate_limiter(values, allow_none=True)

    def mutate(_effort, limiter, _last):
        for key, value in values.items():
            if value is None:
                limiter.pop(key, None)
            else:
                limiter[key] = value
    return _update(config_dir, mutate)


def save_last_launched(config_dir: Path | str, lane: str, alias: str) -> str | None:
    """Record the alias a lane last STARTED (child spawned), never a mere selection."""
    _validate_last_launched(lane, alias)

    def mutate(_effort, _limiter, last):
        last[lane] = alias
    return _update(config_dir, mutate)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="session_menu_state.py",
        description="Read or set the session picker's saved preferences "
                    "(config/session-menu.local.json).",
        epilog="Lanes: " + ", ".join(ALLOWED_EFFORT) + ". Reading never writes anything.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get", help="print the saved-or-default effort for a lane")
    g.add_argument("lane", choices=list(ALLOWED_EFFORT))
    s = sub.add_parser("set", help="persist one lane's effort")
    s.add_argument("lane", choices=list(ALLOWED_EFFORT))
    s.add_argument("value")
    sub.add_parser("show", help="print the effective state as JSON")
    args = parser.parse_args(argv)

    if args.cmd == "set":
        try:
            warning = save_effort(REPO_CONFIG_DIR, args.lane, args.value)
        except ValueError as exc:
            print(f"session_menu_state: {exc}", file=sys.stderr)
            return 2
        if warning:
            print(f"session_menu_state: warning: {warning}", file=sys.stderr)
            return 1
        return 0
    state = load(REPO_CONFIG_DIR)
    for warning in state.warnings:
        print(f"session_menu_state: warning: {warning}", file=sys.stderr)
    if args.cmd == "get":
        print(state.effort[args.lane])
    else:
        print(json.dumps({"effort": state.effort, "rate_limiter": state.rate_limiter,
                          "last_launched": state.last_launched}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

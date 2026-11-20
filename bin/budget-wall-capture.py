#!/usr/bin/env python3
"""budget-wall-capture.py — deterministic budget-wall takeover capture (worker).

Classifies a StopFailure payload (Receipt 1 Q1 keys) and ONLY when it is a budget wall
on a non-loopback endpoint builds ONE takeover package (idempotent per session_id +
error line, via <root>/.seen). It NEVER launches a successor.

Timing (measured 2026-11-19, probes q1hook7/8/9, engine 2.1.327): at StopFailure the
engine gives only the FIRST hook process a ~100 ms window and kills everything it spawns
(same-group and setsid grandchildren alike died before their first write). So the
StopFailure hook is budget-wall-spool.sh (instant `cat` into <root>/.pending/), and THIS
script runs at SessionStart — which has full time (Q1/Q2 cat-hook captures prove it) —
via `--pending`:

Usage: python3 budget-wall-capture.py [--help] [--pending]   # default: read one payload on stdin
  --pending  process every spooled payload in <root>/.pending/ (the StopFailure window)
Env: FA_TAKEOVER_DIR (state root; invalid aborts loudly), FA_TAKEOVER_ROUTE (route alias;
default: first session-capable roster route), FA_TAKEOVER_ALLOW_LOOPBACK=1 (probe override:
allow a loopback endpoint — used only by the hermetic delivery probe), ANTHROPIC_BASE_URL
(the session's endpoint; loopback endpoints are ignored).

Emits one user-visible line as a systemMessage JSON on stdout and always appends it to
<root>/hook.log. Everything fails open — exit 0, always.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import fa_capture
import fa_transcript

USAGE = ("budget-wall-capture.py — budget-wall takeover capture (worker; runs at SessionStart "
         "via --pending because the StopFailure window is ~100 ms — measured). "
         "Usage: python3 budget-wall-capture.py [--help] [--pending]   "
         "Env: FA_TAKEOVER_DIR, FA_TAKEOVER_ROUTE, FA_TAKEOVER_ALLOW_LOOPBACK, ANTHROPIC_BASE_URL.")


def _is_loopback(url: str) -> bool:
    return fa_transcript.is_loopback_url(url)


def _log(root: Path, msg: str) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        with open(root / "hook.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _route_alias() -> str:
    try:
        return fa_capture.resolve_takeover_route(os.environ.get("FA_TAKEOVER_ROUTE") or None).alias
    except Exception:
        return "?"


def _emit(root: Path, line: str) -> None:
    _log(root, line)
    try:
        print(json.dumps({"systemMessage": line}))
    except Exception:
        try:
            print(line, file=sys.stderr)
        except Exception:
            pass  # the engine's pipes are gone; the hook.log line is the record


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help") for a in argv):
        print(USAGE)
        return 0
    unknown = [a for a in argv if a != "--pending"]
    if unknown:
        print(f"budget-wall-capture.py: unknown argument: {unknown[0]}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2
    if "--pending" in argv:
        # SessionStart mode: process every spooled StopFailure payload (full time here)
        try:
            root = fa_capture.default_root()
        except Exception as e:
            try:
                print(f"budget-wall-capture: {e}", file=sys.stderr)
            except Exception:
                pass
            return 0
        # pre-create .pending here (full time available) so the StopFailure spool hook
        # needs ZERO forks in its ~100 ms window (final review finding 8)
        try:
            (root / ".pending").mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        pending = root / ".pending"
        if pending.is_dir():
            for f in sorted(pending.glob("*.json")):
                try:
                    raw = f.read_text(encoding="utf-8")
                except Exception:
                    continue
                _capture_from(raw)
                try:
                    f.unlink()
                except Exception:
                    pass
        return 0
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    return _capture_from(raw)


def _capture_from(raw: str) -> int:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return 0
    except Exception:
        return 0
    session_id = payload.get("session_id") or "unknown"
    transcript_path = payload.get("transcript_path")
    text = payload.get("last_assistant_message") or ""

    # classify: the payload's error text is primary; the transcript's LAST API error is
    # the fallback (the hook never fired, or the text is only a generic error record).
    # A specific non-budget kind (overloaded, rate_limit) is never relabelled budget_wall.
    errs = []
    if transcript_path:
        try:
            records, _malformed = fa_transcript.load_records(transcript_path)
            errs = fa_transcript.api_errors(records)
        except Exception:
            errs = []
    kind = fa_transcript.classify_api_error(text)
    err_line = None
    if kind == "other" and errs:
        kind = errs[-1].kind
        err_line = errs[-1].line
    if kind != "budget_wall":
        return 0  # not a budget wall: stay silent, package nothing
    walls = [e for e in errs if e.kind == "budget_wall"]
    if walls:
        err_line = walls[-1].line  # ref the wall record in the manifest reason

    if _is_loopback(os.environ.get("ANTHROPIC_BASE_URL", "")) \
            and os.environ.get("FA_TAKEOVER_ALLOW_LOOPBACK") != "1":
        return 0  # probe/fake upstream: never package

    try:
        root = fa_capture.default_root()
    except Exception as e:
        # an invalid explicit override must not vanish silently: it cannot go to the
        # named root, so fall back to stderr (best effort) and give up quietly
        try:
            print(f"budget-wall-capture: {e}", file=sys.stderr)
        except Exception:
            pass
        return 0
    seen_dir = root / ".seen"
    seen_key = f"{session_id}-{err_line if err_line is not None else 'stop'}"
    seen = seen_dir / seen_key
    if seen.exists():
        takeover_id = seen.read_text(encoding="utf-8").strip()
        _emit(root, f"💸 free-agents: budget wall — takeover package ready "
                    f"({_route_alias()}). Nothing was launched. "
                    f"Run: fa-takeover launch {takeover_id}")
        return 0
    try:
        route = fa_capture.resolve_takeover_route(os.environ.get("FA_TAKEOVER_ROUTE") or None)
        reason = (f"budget wall — API error at transcript line "
                  f"{err_line if err_line is not None else 'stop'} (session {session_id})")
        pkg = fa_capture.build_package(
            transcript_path, cwd=payload.get("cwd") or os.getcwd(),
            dest_route=route.alias, dest_window_tokens=route.window_tokens,
            base_overhead_tokens=fa_capture.DEFAULT_BASE_OVERHEAD_TOKENS,
            reason=reason, scanner=fa_capture.require_scanner(), allow_findings=False)
        out = fa_capture.write_package(pkg, root)
        seen_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(seen_dir, 0o700)
        seen.write_text(pkg.takeover_id + "\n", encoding="utf-8")
        os.chmod(seen, 0o600)
        _emit(root, f"💸 free-agents: budget wall — takeover package ready "
                    f"({route.alias}). Nothing was launched. "
                    f"Run: fa-takeover launch {pkg.takeover_id}")
        _log(root, f"package: {out}")
        return 0
    except fa_capture.CaptureRefused as e:
        _log(root, f"budget-wall-capture: refused ({e.code}): {e.message}")
        _emit(root, f"💸 free-agents: budget wall — capture refused ({e.code}). "
                    f"Nothing was launched. See hook.log for why.")
        return 0
    except Exception as e:
        _log(root, f"budget-wall-capture: exception: {e!r}")
        return 0  # fail-open: the session teardown must never stall


if __name__ == "__main__":
    sys.exit(main())

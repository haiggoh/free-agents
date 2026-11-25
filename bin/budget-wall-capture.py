#!/usr/bin/env python3
"""budget-wall-capture.py — deterministic budget-wall takeover capture (worker).

Classifies a StopFailure payload (Receipt 1 Q1 keys) and ONLY when it is a budget wall
on a non-loopback endpoint builds ONE takeover package (idempotent per session_id +
error line, via <root>/.seen). It NEVER launches a successor.

Timing (measured 2026-11-19, probes q1hook7/8/9, engine 2.1.327): at StopFailure the
engine gives EVERY hook (they run in parallel — measured 2026-11-20) a ~100 ms window
and kills everything it spawns
(same-group and setsid grandchildren alike died before their first write). So the
StopFailure hook is budget-wall-spool.sh (instant `cat` into <root>/.pending/), and THIS
script runs at SessionStart — which has full time (Q1/Q2 cat-hook captures prove it) —
via `--pending`:

Usage: python3 budget-wall-capture.py [--help] [--pending]   # default: read one payload on stdin
  --pending  process every spooled payload in <root>/.pending/ (the StopFailure window)
Env: FA_TAKEOVER_DIR (state root; invalid aborts loudly), FA_TAKEOVER_ROUTE (route alias;
default: first live alias of config/session-preferences.json order), FA_TAKEOVER_ALLOW_LOOPBACK=1 (probe override:
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
import emoji_constants
import fa_capture
import fa_transcript

# D2: every notice line carries the free-agents mark (📡), never cost-tracker's 💸.
NOTICE_PREFIX = f"{emoji_constants.SESSION_EMOJI_FREE_API_STR} free-agents: budget wall — "

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


_MESSAGES: list = []  # flushed as ONE hook JSON object (two objects on stdout = unparseable)
_SEEN: set = set()    # D1: identical lines within one run emit once (order preserved)


def _notice(session_id, state: str, ref: str, next_step: str) -> str:
    return f"{NOTICE_PREFIX}{state} ({ref}) for session {str(session_id)[:8]}. {next_step}"


def _emit(root: Path, line: str) -> None:
    _log(root, line)
    if line in _SEEN:
        return
    _SEEN.add(line)
    _MESSAGES.append(line)


def _flush() -> None:
    _SEEN.clear()
    if not _MESSAGES:
        return
    text = "\n".join(_MESSAGES)
    _MESSAGES.clear()
    try:
        print(json.dumps({"systemMessage": text}))
    except Exception:
        try:
            print(text, file=sys.stderr)
        except Exception:
            pass  # the engine's pipes are gone; the hook.log lines are the record


SPOOL_ENDPOINT = "#fa-endpoint "
SPOOL_ENDPOINT_UNSET = "#fa-endpoint-unset"


def _parse_spool(text: str):
    """(endpoint, payload_raw). The spool hook records the DYING session's
    ANTHROPIC_BASE_URL as a header line, because this worker runs in the NEXT session,
    whose own endpoint says nothing about where the wall happened (PR #7 review: a
    free-lane next session on 127.0.0.1 silently dropped the wall). endpoint is "" when
    unset or not recorded — the wall text itself is only ever produced by a paid gateway."""
    first, _, rest = text.partition("\n")
    if first.startswith(SPOOL_ENDPOINT):
        return first[len(SPOOL_ENDPOINT):].strip(), rest
    if first.strip() == SPOOL_ENDPOINT_UNSET:
        return "", rest
    return "", text


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
            os.chmod(root / ".pending", 0o700)  # payloads hold the last assistant message
        except Exception:
            pass
        pending = root / ".pending"
        if pending.is_dir():
            for f in sorted(pending.glob("*.json")):
                try:
                    endpoint, raw = _parse_spool(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                status = _capture_from(raw, endpoint)
                try:
                    if status == "error":
                        # keep the evidence: an exception must not delete the only record of
                        # the wall (PR #7 review). Parked, not retried at every start.
                        failed = root / ".failed"
                        failed.mkdir(parents=True, exist_ok=True)
                        os.chmod(failed, 0o700)
                        f.rename(failed / f.name)
                        _log(root, f"budget-wall-capture: payload kept at {failed / f.name}")
                    else:
                        f.unlink()
                except Exception:
                    pass
        _flush()
        return 0
    try:
        raw = sys.stdin.read()
    except Exception:
        return 0
    _capture_from(raw, os.environ.get("ANTHROPIC_BASE_URL", ""))  # stdin = same session
    _flush()
    return 0


def _capture_from(raw: str, endpoint: str) -> str:
    """Returns "skip" (not a wall / not applicable), "done" (packaged, refused or already
    claimed) or "error" (an exception: the caller keeps the payload)."""
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return "skip"
    except Exception:
        return "skip"
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
        return "skip"  # not a budget wall: stay silent, package nothing
    walls = [e for e in errs if e.kind == "budget_wall"]
    if walls:
        err_line = walls[-1].line  # ref the wall record in the manifest reason

    if _is_loopback(endpoint) and os.environ.get("FA_TAKEOVER_ALLOW_LOOPBACK") != "1":
        return "skip"  # the WALLED session ran on a probe/fake upstream: never package

    try:
        root = fa_capture.default_root()
    except Exception as e:
        # an invalid explicit override must not vanish silently: it cannot go to the
        # named root, so fall back to stderr (best effort) and give up quietly
        try:
            print(f"budget-wall-capture: {e}", file=sys.stderr)
        except Exception:
            pass
        return "error"
    if not transcript_path:
        _emit(root, _notice(session_id, "capture refused", "transcript_missing",
                            "Nothing was launched. See hook.log for why."))
        _log(root, f"budget-wall-capture: payload for session {session_id} has no transcript_path")
        return "done"
    seen_dir = root / ".seen"
    seen = seen_dir / f"{session_id}-{err_line if err_line is not None else 'stop'}"
    # ATOMIC claim (O_EXCL): two sessions starting at once must not both package one wall
    try:
        seen_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(seen_dir, 0o700)
        fd = os.open(str(seen), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # D1: an already-claimed wall emits NOTHING on stdout — the old code re-emitted the
        # ready/refused line for EVERY further spooled payload of the same wall (hook.log only).
        # An empty claim = another session is capturing this wall right now: say nothing.
        _log(root, f"already claimed {seen.name}")
        return "done"
    except Exception as e:
        _log(root, f"budget-wall-capture: cannot claim {seen}: {e!r}")
        return "error"

    def _settle(text):
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")

    try:
        route = fa_capture.resolve_takeover_route(os.environ.get("FA_TAKEOVER_ROUTE") or None)
        reason = (f"budget wall — API error at transcript line "
                  f"{err_line if err_line is not None else 'stop'} (session {session_id})")
        old = fa_capture.latest_package_for_session(root, session_id)  # before writing: newest wins
        pkg = fa_capture.build_package(
            transcript_path, cwd=payload.get("cwd") or os.getcwd(),
            dest_route=route.alias, dest_window_tokens=route.window_tokens,
            base_overhead_tokens=fa_capture.DEFAULT_BASE_OVERHEAD_TOKENS,
            reason=reason, scanner=fa_capture.require_scanner(), allow_findings=False)
        out = fa_capture.write_package(pkg, root)
        _settle(pkg.takeover_id)
        if old and old != pkg.takeover_id:
            fa_capture.mark_superseded(root, old, pkg.takeover_id)
        _emit(root, _notice(session_id, "takeover package ready", route.alias,
                            f"Nothing was launched. Run: fa-takeover launch {pkg.takeover_id}"))
        _log(root, f"package: {out}")
        return "done"
    except fa_capture.CaptureRefused as e:
        _settle(f"refused:{e.code}")
        _log(root, f"budget-wall-capture: refused ({e.code}): {e.message}")
        hint = ""
        if e.code == "secret_findings":
            # Review Focus 5: the refusal names the manual override, never a silent dead end
            hint = f" Manual override: fa-takeover capture {str(session_id)[:8]} --allow-findings."
        _emit(root, _notice(session_id, "capture refused", e.code,
                            f"Nothing was launched.{hint} See hook.log for why."))
        return "done"
    except Exception as e:
        _log(root, f"budget-wall-capture: exception: {e!r}")
        try:
            os.close(fd)  # may already be closed by _settle
        except OSError:
            pass
        try:
            seen.unlink()  # release the claim so a later manual/next run can retry
        except OSError:
            pass
        return "error"  # fail-open: the session teardown must never stall


if __name__ == "__main__":
    sys.exit(main())

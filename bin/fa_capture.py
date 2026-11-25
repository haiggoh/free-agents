#!/usr/bin/env python3
"""fa_capture.py — capture-package builder for the deterministic budget-wall takeover.

Plan v2 Task 1.2. Builds a takeover package (manifest.json + handoff.md) from a Claude
Code transcript: every verbatim instruction is always included (refuse, never trim),
the recent exchange is bounded by the destination window, tool outputs are referenced
never inlined, and a secret scan can refuse the package. Stdlib only.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fa_transcript

# Receipt 1 Q1 (2026-11-19, fake loopback upstream, --mode ok, engine 2.1.327): the single
# POST /v1/messages?beta=true request body was 47_787 bytes -> ceil(47787/4) = 11_947 tokens
# of Claude Code base overhead. The CLI passes this unless --base-overhead-tokens N is given.
DEFAULT_BASE_OVERHEAD_TOKENS = 11_947
MIN_BASE_OVERHEAD_TOKENS = 40_000   # never reserve less than this for the engine base
REPLY_RESERVE_TOKENS = 8_000        # reserved for the successor's first reply

# D6: the AUTO successor's handoff §4 is the free-session-handover skill's standing-rules
# template, VERBATIM (pinned against the skill file by test_standing_rules_match_skill so
# the two cannot drift). Single-line, whitespace-normalized form.
HANDOVER_STANDING_RULES = [
    "1. Autonomous: decide and record instead of asking; stop only for forks that cannot be weighed.",
    "2. Work only in the named worktree. Never switch the branch of a checkout the launcher resolves through.",
    "3. Commit every milestone, staged **by path** (never `git add -A`), with the running model's co-author trailer. Push the branch if told to; **never merge, tag or release**.",
    "4. TDD every change (fails for the right reason, then passes); mutation-check each new guard; never weaken a test to get green.",
    "5. Review each milestone with an independent dispatch (`bin/free-agent-tool.py`) and check every finding against the real code: reviewers hallucinate file names.",
    "6. Resolve model and roster facts when you need them, from the catalog, never from memory.",
    "7. Never run `remote-session.sh --stop`, never start another remote session, never kill anything listening on 4141–4151: one of them is the proxy you run on.",
    "8. Record progress in the handoff doc at each milestone, in the same commit.",
    '9. A dying session\'s "hand off to a new session" instruction is consumed by the successor it launched — that is YOU. Continue the original handoff directly; never launch yet another session to pass the same work onward. Measured 2026-10-26: a handoff session died with "hand the remaining tasks to a new glm flash session" as its last instruction, and its successor nearly launched a third session instead of continuing the work.',
]


class CaptureRefused(Exception):
    """code: over_budget | secret_findings | no_instructions | transcript_missing."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Package:
    takeover_id: str
    manifest: dict
    handoff_md: str


def estimate_tokens(text: str) -> int:
    """Rough char-based estimate, labelled as an estimate in the manifest."""
    return math.ceil(len(text) / 4)


def default_root() -> Path:
    """${XDG_STATE_HOME:-~/.local/state}/free-agents/takeovers, override FA_TAKEOVER_DIR.

    An override that is SET but invalid aborts loudly (never falls through).
    """
    override = os.environ.get("FA_TAKEOVER_DIR")
    if override:
        if not os.path.isabs(override):
            raise ValueError(f"FA_TAKEOVER_DIR must be an absolute path, got: {override!r}")
        parent = os.path.dirname(override) or "/"
        if not os.path.isdir(parent):
            raise ValueError(f"FA_TAKEOVER_DIR parent directory does not exist: {override!r}")
        return Path(override)
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "free-agents" / "takeovers"


# --- route seam (Task 0.3): the remote roster, resolved at RUN TIME — no model name
# is ever hardcoded. Roster field 7 (autocompact_default) is the repo's window proxy. ---
REPO_ROOT = Path(__file__).resolve().parents[1]
ROSTER_FILE = REPO_ROOT / "config" / "remote-agents.sh"
BROKEN_FILE = REPO_ROOT / "config" / "broken-nvidia-models.json"
SESSION_PREFS_FILE = REPO_ROOT / "config" / "session-preferences.json"  # full-SESSION order
WINDOW_TOKENS = {"1m": 1_000_000, "200k": 200_000, "256k": 256_000}
DEFAULT_WINDOW_TOKENS = 200_000  # "-" / "default" / empty -> conservative spoof window


@dataclass(frozen=True)
class Route:
    alias: str
    window_tokens: int
    provider: str


def _roster_entries() -> list:
    """Parse the LA_REMOTE_AGENTS array in config/remote-agents.sh:
    "alias|provider|model-id|display|tier|notes|autocompact_default"."""
    entries = []
    text = ROSTER_FILE.read_text(encoding="utf-8")
    for m in re.finditer(r'^\s*"([^"]+)"\s*,?\s*$', text, re.M):
        parts = m.group(1).split("|")
        if len(parts) < 3:
            continue
        autocompact = parts[-1] if len(parts) >= 7 else "-"
        entries.append((parts[0], parts[1], parts[2], autocompact))
    return entries


def _hidden_aliases() -> set:
    """Retired/hidden routes from config/broken-nvidia-models.json — never picked."""
    try:
        data = json.loads(BROKEN_FILE.read_text(encoding="utf-8"))
        return {m.get("alias") for m in data.get("models", []) if m.get("alias")}
    except Exception:
        return set()


def resolve_takeover_route(alias):
    """Resolve a route alias to its window. Raises on unknown or retired aliases —
    it never falls back silently. alias=None -> first live alias of config/session-preferences.json order."""
    entries = _roster_entries()
    hidden = _hidden_aliases()
    if alias is None:
        # The default is the first usable alias of the MEASURED full-session order
        # (config/session-preferences.json), never the roster's line order: dispatch-first
        # models must not lead a session (PR #7 review).
        by_alias = {a: (prov, _model, ac) for a, prov, _model, ac in entries}
        try:
            order = json.loads(SESSION_PREFS_FILE.read_text(encoding="utf-8")).get("order") or []
        except Exception as e:
            raise ValueError(f"cannot read the session order {SESSION_PREFS_FILE}: {e}") from e
        # 0.62.0 C7: the ladder KNOWS the limits — ordered + spill past a saturated/exhausted
        # provider, never a small-context or dispatch-only or broken model.
        try:
            import provider_limits as pl
            import rate_limiter as rl
            import session_ladder as sl
            limits = pl.load()
            roster = {a: (prov, model) for a, (prov, model, _ac) in by_alias.items()}
            import remote_provider_core as rpc
            keyed = {pid for pid, p in rpc.PROVIDERS.items() if p.key_name and p.key()}
            picked, reasons = sl.pick(
                [a for a in order if a in by_alias and a not in hidden],
                need_context=0, limits=limits, roster=roster,
                headroom=rl.provider_headroom,
                predicted_wait=lambda a: rl.stretched_wait(roster[a][0], 300),
                timeout=lambda a: 600.0,
                keyed=lambda prov: prov in keyed)
            for a, why in reasons:
                print(f"fa_capture: {why} [{a}]", file=sys.stderr)
            if picked:
                prov, _model, ac = by_alias[picked]
                return Route(alias=picked,
                             window_tokens=WINDOW_TOKENS.get(ac, DEFAULT_WINDOW_TOKENS),
                             provider=prov)
        except Exception:
            pass                                    # fall through to the plain order below
        for a in order:
            if a in by_alias and a not in hidden:
                prov, _model, ac = by_alias[a]
                return Route(alias=a, window_tokens=WINDOW_TOKENS.get(ac, DEFAULT_WINDOW_TOKENS),
                             provider=prov)
        raise ValueError(f"no usable session route: none of {SESSION_PREFS_FILE.name} "
                         f"(session-preferences) order {order} is a live roster alias")
    for a, prov, _model, ac in entries:
        if a == alias:
            if a in hidden:
                raise ValueError(f"route {alias!r} is retired/hidden (config/broken-nvidia-models.json)")
            return Route(alias=a, window_tokens=WINDOW_TOKENS.get(ac, DEFAULT_WINDOW_TOKENS),
                         provider=prov)
    raise ValueError(f"unknown route {alias!r} (not in the roster config/remote-agents.sh)")


def _resolve_scanner_script():
    """FA_SECRET_SCANNER (explicit; set-but-missing raises ValueError naming it), else the
    newest audit-loose-ends redact-secret.py under $HOME's plugin cache, else None."""
    override = os.environ.get("FA_SECRET_SCANNER")
    if override:
        if not os.path.isfile(override):
            raise ValueError(f"FA_SECRET_SCANNER={override!r} is not a file")
        return Path(override)
    home = Path(os.environ.get("HOME") or Path.home())
    base = home / ".claude" / "plugins" / "cache" / "haiggoh" / "audit-loose-ends"
    cands = []
    if base.is_dir():
        for d in base.iterdir():
            s = d / "scripts" / "redact-secret.py"
            if d.is_dir() and s.is_file():
                ver = tuple(int(x) for x in re.findall(r"\d+", d.name)) or (0,)
                cands.append((ver, s))
    return sorted(cands)[-1][1] if cands else None


def require_scanner():
    """The scanner every capture route uses by default (PR #7 review: a package bound for a
    free provider is never written unscanned unless the user opts out explicitly).
    Raises CaptureRefused("no_scanner") when none resolves."""
    scan = external_secret_scanner()
    if scan is None:
        raise CaptureRefused(
            "no_scanner",
            "no secret scanner found (install audit-loose-ends, or set FA_SECRET_SCANNER); "
            "refusing to write an unscanned package — `fa-takeover capture --no-scan` opts out")
    return scan


def external_secret_scanner():
    """Resolve the secret scanner at run time (Task 0.3 Step 3: free-agents itself ships
    none). Returns a callable text -> [masked findings], or None when not installed.
    An invalid FA_SECRET_SCANNER raises ValueError (explicit overrides fail loudly)."""
    script = _resolve_scanner_script()
    if script is None:
        return None

    def scan(text: str) -> list:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            f.write(text)
            path = f.name
        try:
            r = subprocess.run([sys.executable, str(script), "--scan-only", path],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                # fail CLOSED on scanner failure: an unparseable output must never read
                # as "clean" (final review finding 1c)
                return ["<secret-scanner failed rc=%d>" % r.returncode]
            return re.findall(r"would redact \d+: (\S+)", r.stdout + r.stderr)
        except Exception as e:
            return [f"<secret-scanner error: {e!r}>"]
        finally:
            os.unlink(path)

    return scan


def _session_id(records, transcript_path: str):
    for _ln, rec in records:
        sid = rec.get("sessionId") or rec.get("session_id")
        if sid:
            return sid
    return Path(transcript_path).stem


def _recent_entries(records) -> list:
    """(line, role, text) for assistant text and user prompts, chronological.

    Tool outputs never enter this list: message_text only yields text blocks, and
    tool_result carriers yield "" (excluded as instructions too).
    """
    out = []
    for ln, rec in records:
        if rec.get("type") == "assistant":
            text = fa_transcript.message_text(rec)
            if text.strip():
                out.append((ln, "assistant", text))
        elif rec.get("type") == "user":
            text = fa_transcript.message_text(rec)
            if text.strip() and not fa_transcript.is_harness_injection(rec, text):
                out.append((ln, "user", text))
    return out


def _git_state(cwd) -> dict:
    """Read-only git state of cwd; not a repo -> is_repo False, never raises."""
    state = {"is_repo": False, "head": None, "branch": None, "porcelain_paths": []}
    if not cwd:
        return state
    try:
        head = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        if head.returncode != 0:
            return state
        state["is_repo"] = True
        state["head"] = head.stdout.strip()
        branch = subprocess.run(["git", "-C", cwd, "branch", "--show-current"],
                                capture_output=True, text=True, timeout=10)
        state["branch"] = branch.stdout.strip() or None
        st = subprocess.run(["git", "-C", cwd, "status", "--porcelain=v1", "-z"],
                            capture_output=True, text=True, timeout=10)
        fields = st.stdout.split("\0")
        paths = []
        i = 0
        while i < len(fields):
            rec = fields[i]
            if not rec:
                i += 1
                continue
            paths.append(rec[3:])
            if (rec[0] in "RC" or rec[1] in "RC") and i + 1 < len(fields):
                i += 1  # -z rename/copy (R, C in X or Y, e.g. "RM"): next field is the orig path
            i += 1
        state["porcelain_paths"] = paths
    except Exception:
        pass
    return state


def build_package(transcript_path: str, *, cwd=None, dest_route: str,
                  dest_window_tokens: int, base_overhead_tokens: int, reason: str,
                  scanner=None, allow_findings: bool = False, now=None,
                  mode: str = "reconcile") -> Package:
    now = now or datetime.now(timezone.utc)
    if mode not in ("reconcile", "continue"):
        raise ValueError(f"unknown handoff mode {mode!r} (expected 'reconcile' or 'continue')")
    if not os.path.isfile(transcript_path):
        raise CaptureRefused("transcript_missing", f"transcript not found: {transcript_path}")
    records, malformed = fa_transcript.load_records(transcript_path)
    instructions = fa_transcript.user_instructions(records)
    if not instructions:
        raise CaptureRefused("no_instructions", "transcript carries no user instructions")
    errors = fa_transcript.api_errors(records)
    pending = fa_transcript.pending_tool_uses(records)
    todos = fa_transcript.last_todos(records)
    files = fa_transcript.files_touched(records)
    recent = _recent_entries(records)

    budget_tokens = (dest_window_tokens - max(base_overhead_tokens, MIN_BASE_OVERHEAD_TOKENS)
                     - REPLY_RESERVE_TOKENS)

    # Secret scan over everything that would enter the package, BEFORE the budget check.
    secret_scan = {"ran": False, "findings": []}
    if scanner is not None:
        findings = list(scanner("\n".join([i.text for i in instructions]
                                           + [e[2] for e in recent])) or [])
        secret_scan = {"ran": True, "findings": findings}
        if findings and not allow_findings:
            raise CaptureRefused(
                "secret_findings",
                f"secret scan found {len(findings)} finding(s); "
                "rerun with --allow-findings to record them in the manifest instead")

    # Verbatim instructions are ALWAYS included; if they alone exceed the budget, refuse.
    numbered = [f"{n}. (line {i.line}) {i.text}" for n, i in enumerate(instructions, 1)]
    instr_text = "\n".join(numbered)
    instr_tokens = estimate_tokens(instr_text)
    if instr_tokens > budget_tokens:
        raise CaptureRefused(
            "over_budget",
            f"instructions alone need ~{instr_tokens} tokens but the {dest_route} budget is "
            f"{budget_tokens} tokens (window {dest_window_tokens} - base "
            f"{max(base_overhead_tokens, MIN_BASE_OVERHEAD_TOKENS)} - reply reserve "
            f"{REPLY_RESERVE_TOKENS}); instructions are never trimmed")

    # Recent exchange fills what remains, newest first, printed chronological.
    included = []
    used = instr_tokens
    truncated = False
    for entry in reversed(recent):
        t = estimate_tokens(entry[2])
        if used + t <= budget_tokens:
            included.append(entry)
            used += t
        else:
            truncated = True
            break
    included.reverse()

    bytes_total = len(instr_text) + sum(len(e[2]) for e in recent)
    bytes_included = len(instr_text) + sum(len(e[2]) for e in included)

    takeover_id = "tk-" + now.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(4)
    nonce = secrets.token_hex(8)
    manifest = {
        "schema_version": 1,
        "takeover_id": takeover_id,
        "nonce_sha256": hashlib.sha256(nonce.encode()).hexdigest(),
        "created_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": reason,
        "source": {
            "session_id": _session_id(records, transcript_path),
            # realpath: _successor_transcript compares against globbed realpaths, so a
            # capture made through a symlink must still identify the source session
            "transcript_path": os.path.realpath(transcript_path),
            "transcript_sha256": hashlib.sha256(Path(transcript_path).read_bytes()).hexdigest(),
            "line_count": len(records) + malformed,
            "malformed": malformed,
        },
        "dest": {"route": dest_route, "window_tokens": dest_window_tokens,
                 "budget_tokens": budget_tokens},
        "counts": {"instructions": len(instructions), "recent_records": len(included),
                   "pending_tools": len(pending), "files_touched": len(files)},
        "git": _git_state(cwd),
        "completeness": {"instructions_complete": True, "recent_truncated": truncated,
                         "bytes_total": bytes_total, "bytes_included": bytes_included},
        "secret_scan": secret_scan,
    }
    handoff_md = _render_handoff(takeover_id, nonce, reason, errors, manifest, instructions,
                                 included, pending, todos, files, mode=mode)
    return Package(takeover_id=takeover_id, manifest=manifest, handoff_md=handoff_md)


def _render_handoff(takeover_id, nonce, reason, errors, manifest, instructions,
                    included, pending, todos, files, mode: str = "reconcile") -> str:
    g = manifest["git"]
    lines = []
    lines.append(f"# Takeover package {takeover_id}")
    lines.append("")
    lines.append(f"TAKEOVER-NONCE: {nonce}")
    lines.append("")
    lines.append("## 1. Why this exists")
    lines.append("")
    lines.append(reason)
    for e in errors:
        lines.append(f"API error at transcript line {e.line} (kind: {e.kind}): {e.text[:200]}")
    lines.append("")
    lines.append("## 2. State")
    lines.append("")
    if g["is_repo"]:
        lines.append(f"- git: HEAD {g['head']} on branch {g['branch']}")
        lines.append(f"- dirty paths ({len(g['porcelain_paths'])}): "
                     + (", ".join(g["porcelain_paths"]) if g["porcelain_paths"] else "none"))
    else:
        lines.append("- git: not a repository (or unreadable) — record state manually")
    lines.append("- todos: " + (json.dumps(todos) if todos else "none recorded"))
    if pending:
        lines.append("- pending tool calls (OUTCOME UNKNOWN — inspect, never re-run blindly):")
        for t in pending:
            lines.append(f"  - line {t.line} {t.tool_use_id} {t.name}"
                         " — OUTCOME UNKNOWN — inspect, never re-run blindly")
    else:
        lines.append("- pending tool calls: none")
    lines.append("- files touched: " + (", ".join(files) if files else "none recorded"))
    lines.append("")
    lines.append("## 3. The goal, verbatim")
    lines.append("")
    for n, i in enumerate(instructions, 1):
        lines.append(f"{n}. (line {i.line}) {i.text}")
    lines.append("")
    lines.append("## 4. Standing rules")
    lines.append("")
    if mode == "continue":
        # D6: the AUTO successor continues autonomously — the handover skill's rules verbatim.
        lines.extend(HANDOVER_STANDING_RULES)
    else:
        lines.append("- Your first turn is read-only: reconcile before acting.")
        lines.append("- Never re-run a pending tool call, push, or deploy without inspecting "
                     "its actual state first.")
        lines.append("- Preserve dirty work; never discard uncommitted changes.")
        lines.append("- Your first output is `## Takeover reconciliation` plus the nonce, "
                     "instructions understood, state, and a proposed next action — then WAIT "
                     "for the user.")
    lines.append("")
    lines.append("## 5. Next action")
    lines.append("")
    if mode == "continue":
        lines.append("Write `## Takeover reconciliation` with the nonce, then continue autonomously "
                     "from the source session's last unfinished request; do not wait.")
    else:
        lines.append("Produce the reconciliation, then wait")
    lines.append("")
    lines.append("## 6. Appendix: recent exchange (bounded)")
    lines.append("")
    c = manifest["completeness"]
    lines.append(f"completeness: instructions_complete={c['instructions_complete']} "
                 f"recent_truncated={c['recent_truncated']} "
                 f"bytes_included={c['bytes_included']}/{c['bytes_total']}")
    lines.append("")
    for ln, role, text in included:
        lines.append(f"- line {ln} ({role}): {text}")
    lines.append("")
    return "\n".join(lines)


def _write_file_durable(path: Path, data: str) -> None:
    """Write + fsync so a crash after return cannot leave an empty/partial package."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def write_package(pkg: Package, root) -> Path:
    """Atomic temp-dir + rename; refuses an existing id; dir 0700, files 0600.

    Sweeps stale .tmp-* dirs left by a SIGKILLed earlier write (final review finding 9).
    """
    root = Path(root)
    target = root / pkg.takeover_id
    if target.exists():
        raise FileExistsError(f"takeover package {pkg.takeover_id} already exists at {target}")
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    for stale in root.glob(".tmp-*"):
        if stale.is_dir():
            for f in stale.glob("*"):
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                stale.rmdir()
            except OSError:
                pass
    tmp = root / f".tmp-{pkg.takeover_id}-{secrets.token_hex(4)}"
    try:
        tmp.mkdir(mode=0o700)
        _write_file_durable(tmp / "manifest.json",
                            json.dumps(pkg.manifest, indent=2, sort_keys=True) + "\n")
        _write_file_durable(tmp / "handoff.md", pkg.handoff_md)
        os.chmod(tmp, 0o700)
        for name in ("manifest.json", "handoff.md"):
            os.chmod(tmp / name, 0o600)
        os.rename(tmp, target)
        # fsync the directory entry so the rename itself is durable
        dfd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        for f in tmp.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass
        raise
    return target


def mark_superseded(root, old_id: str, new_id: str) -> None:
    """Write <root>/<old_id>/SUPERSEDED containing new_id — atomic (temp + rename), 0600."""
    d = Path(root) / old_id
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".SUPERSEDED.tmp-{secrets.token_hex(4)}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_id + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, d / "SUPERSEDED")


def superseded_by(root, tk_id: str):
    """The id of the package that superseded tk_id, or None."""
    try:
        return (Path(root) / tk_id / "SUPERSEDED").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def latest_package_for_session(root, session_id: str):
    """Newest non-superseded tk-* package whose manifest source.session_id matches, or None."""
    root = Path(root)
    best, best_mtime = None, -1.0
    for d in root.glob("tk-*"):
        if not d.is_dir() or superseded_by(root, d.name):
            continue
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if (m.get("source") or {}).get("session_id") != session_id:
            continue
        mt = d.stat().st_mtime
        if mt > best_mtime:
            best, best_mtime = d.name, mt
    return best


def source_topic(transcript_path: str, session_id: str) -> str:
    """D9: the source session's title (last customTitle record) becomes the successor's
    opening topic. A generic "<username>-<suffix>" title is dropped ONLY on an exact
    whole-string match — the bare username is never surfaced. Control chars/newlines are
    stripped; the topic is cut at 60 chars (character boundary). Fallback: session <8>."""
    sid8 = str(session_id)[:8]
    title = None
    try:
        records, _malformed = fa_transcript.load_records(transcript_path)
        for _ln, rec in records:
            ct = rec.get("customTitle")
            if isinstance(ct, str) and ct.strip():
                title = ct
    except Exception:
        title = None
    if not title:
        return f"session {sid8}"
    clean = re.sub(r"[\x00-\x1f\x7f]", "", title).strip()
    if re.fullmatch(re.escape(getpass.getuser()) + r"-\S+", clean):
        return f"session {sid8}"
    return clean[:60]


def _main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print("fa_capture.py — capture-package builder library (used by bin/fa-takeover).")
        print("Usage: python3 fa_capture.py --help")
        print("Exposes build_package / write_package / default_root / estimate_tokens / "
              "CaptureRefused / Package. No runtime arguments.")
        return 0
    if argv:
        print(f"fa_capture.py: unknown argument: {argv[0]}", file=sys.stderr)
        print("Usage: python3 fa_capture.py --help", file=sys.stderr)
        return 2
    print("fa_capture.py: library module — import it; no CLI.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

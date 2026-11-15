#!/usr/bin/env python3
"""Pre-accept folder trust in Claude Code's config — the ONE sanctioned writer.

free-agents 0.59.0 (handover pre-trust). Task 1 probed the real CLI (2.1.325) and proved:
the trust check reads `projects[<key>].hasTrustDialogAccepted === true` from the global
config, where <key> is the REALPATH of the folder (symlink spellings are ignored); for a
linked worktree EITHER the worktree path or the main-checkout path satisfies it; a
subdirectory of a trusted repo inherits trust; with CLAUDE_CONFIG_DIR set the config is
`$CLAUDE_CONFIG_DIR/.claude.json`, else `~/.claude.json`.

This helper writes EXACTLY ONE field — `hasTrustDialogAccepted` — into the
`projects[...]` entries it resolves. It never touches any other key, never prints config
contents, and never prints anything but the summary/JSON fields below.

Usage:
  fa_pre_trust.py <dir> [--config PATH] [--dry-run] [--json]

Arguments:
  <dir>            target folder (pre-trusted when the keys it resolves are true)
  --config PATH    config file to use (default: $CLAUDE_CONFIG_DIR/.claude.json when
                   CLAUDE_CONFIG_DIR is set, else ~/.claude.json); a set-but-missing
                   --config aborts with exit 2 — explicit overrides fail loudly
  --dry-run        report what would be written, write nothing, no backup
  --json           print {"target","keys","already","wrote","verified","backup","reason"}
                   only — never config values
  --help           print this help and run nothing

Environment: CLAUDE_CONFIG_DIR (config location override, see above).

Exit codes: 0 = trusted (already or now, verified by re-read after 1 s);
            2 = usage error (unknown flag, missing explicit --config);
            3 = not possible (home folder — its trust is never persisted — or not a
                directory); 4 = write failed or did not hold.

Write procedure: backup the config (mode 600, `<config>.bak.pre-trust-<UTC-stamp>`,
keeping the 5 newest of that prefix only) -> load JSON -> set the one field on each
resolved key preserving all existing fields -> write a temp file in the same directory
with the original mode -> os.replace -> re-read after VERIFY_DELAY_S and confirm every
key is true.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass

VERIFY_DELAY_S = 1.0
BACKUP_PREFIX_SUFFIX = ".bak.pre-trust-"
TMP_SUFFIX = ".tmp.pre-trust-"
KEEP_BACKUPS = 5
# rotation manages ONLY backups this helper created: <cfg>.bak.pre-trust-<UTC-stamp>[-N].
# Receipt backups from the probe/dogfood (".bak.pre-trust-probe-…" / "-dogfood-…") must
# survive rotation (Task 6 review finding).
_STAMP_RE = re.compile(r"\d{8}T\d{6}Z(-\d+)?")


@dataclass
class PreTrustResult:
    target: str
    keys: list
    already: bool
    wrote: bool
    verified: bool
    backup: str | None
    reason: str
    exit_code: int


def _load_config(cfg: str):
    """Read + parse the config file; raises ValueError on unparseable content."""
    with open(cfg, "rb") as f:
        raw = f.read()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"unparseable config: {e}") from e


def _detect_layout(raw: bytes):
    """Return (indent|None, trailing_newline) matching the file's existing style.

    indent=None means minified (single-line) JSON -> dump compact.
    """
    indent = 2
    if b"\n" in raw:
        second = raw.split(b"\n", 1)[1]
        stripped = second.lstrip(b" ")
        if second != stripped:
            indent = len(second) - len(stripped)
        else:
            indent = None  # no indented second line: minified
    else:
        indent = None
    return indent, raw.endswith(b"\n")


def _git(args, cwd):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _abs(path, cwd):
    return path if os.path.isabs(path) else os.path.join(cwd, path)


def resolve_keys(target_dir) -> list:
    """The projects[...] keys Task 1 proved the CLI consults, realpath-spelled.

    [target, main-checkout (if a linked worktree), git toplevel (if different)],
    deduped, order preserved. The main-checkout key is derived ONLY when the common
    dir is a real <repo>/.git directory — a bare repo's common dir (e.g. ~/x/myrepo.git)
    would key the PARENT FOLDER of many projects, silently trusting all of them
    (Task 6 review). The home folder is never a key: its trust is never persisted.
    """
    home = os.path.realpath(os.path.expanduser("~"))
    target = os.path.realpath(target_dir)
    keys = [target]
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], target)
    if common is None:
        common = _git(["rev-parse", "--git-common-dir"], target)
        common = _abs(common, target) if common else None
    if common:
        common_rp = os.path.realpath(common)
        if os.path.basename(common_rp) == ".git" and os.path.isdir(common_rp):
            main = os.path.dirname(common_rp)
            if main not in keys:
                keys.append(main)
    top = _git(["rev-parse", "--path-format=absolute", "--show-toplevel"], target)
    if top is None:
        top = _git(["rev-parse", "--show-toplevel"], target)
        top = _abs(top, target) if top else None
    if top:
        top = os.path.realpath(top)
        if top not in keys:
            keys.append(top)
    return [k for k in keys if k != home]


def _resolve_config_path(config_path):
    if config_path is not None:
        return os.path.abspath(config_path), True
    ccd = os.environ.get("CLAUDE_CONFIG_DIR")
    if ccd:
        return os.path.join(ccd, ".claude.json"), False
    return os.path.join(os.path.expanduser("~"), ".claude.json"), False


def _rotate_backups(cfg: str) -> None:
    pattern = glob.escape(cfg) + BACKUP_PREFIX_SUFFIX + "*"
    baks = [p for p in glob.glob(pattern)
            if _STAMP_RE.fullmatch(os.path.basename(p).split(BACKUP_PREFIX_SUFFIX, 1)[1])]
    baks.sort(key=os.path.getmtime)
    for old in baks[:-KEEP_BACKUPS]:
        try:
            os.unlink(old)
        except OSError:
            pass


def pre_trust(target_dir, *, config_path=None, dry_run=False) -> PreTrustResult:
    home = os.path.realpath(os.path.expanduser("~"))
    target = os.path.realpath(target_dir)
    cfg, explicit = _resolve_config_path(config_path)
    if explicit and not os.path.isfile(cfg):
        return PreTrustResult(target, [], False, False, False, None,
                              f"explicit --config not found: {config_path}", 2)
    # Write through symlinks: os.replace on the link itself would swap the symlink for
    # a regular file (Task 6 review). realpath is identity for a normal config.
    cfg = os.path.realpath(cfg)
    if target == home:
        return PreTrustResult(target, [], False, False, False, None,
                              "home folder trust is never persisted", 3)
    if not os.path.isdir(target_dir):
        return PreTrustResult(target, [], False, False, False, None, "not a directory", 3)

    keys = resolve_keys(target_dir)
    fresh = not os.path.isfile(cfg)
    if fresh:
        data = {}
        raw = None
        mode = 0o600
        indent, trailing_nl = 2, True
    else:
        try:
            data = _load_config(cfg)
        except ValueError as e:
            return PreTrustResult(target, keys, False, False, False, None, str(e), 4)
        with open(cfg, "rb") as f:
            raw = f.read()
        mode = os.stat(cfg).st_mode & 0o777
        indent, trailing_nl = _detect_layout(raw)

    projects = data.get("projects", {})
    if projects is None:
        projects = {}
    if not isinstance(projects, dict):
        return PreTrustResult(target, keys, False, False, False, None,
                              "projects is not an object; refusing to write", 4)
    already = all(isinstance(projects.get(k), dict)
                  and projects[k].get("hasTrustDialogAccepted") is True for k in keys)
    if already:
        return PreTrustResult(target, keys, True, False, True, None, "already trusted", 0)
    if dry_run:
        return PreTrustResult(target, keys, False, False, False, None,
                              "dry run: would write the resolved keys, nothing written", 0)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = None
    if raw is not None:
        try:
            for attempt in range(1, 100):
                cand = cfg + BACKUP_PREFIX_SUFFIX + stamp + (f"-{attempt}" if attempt > 1 else "")
                try:
                    fd = os.open(cand, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    continue  # same-second collision (Task 6 review): suffix, retry
                with os.fdopen(fd, "wb") as f:
                    f.write(raw)
                backup = cand
                break
        except OSError as e:
            return PreTrustResult(target, keys, False, False, False, None,
                                  f"backup failed: {e.__class__.__name__}: {e}", 4)
        if backup is None:
            return PreTrustResult(target, keys, False, False, False, None,
                                  "backup failed: name collision", 4)

    for k in keys:
        entry = projects.get(k)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry["hasTrustDialogAccepted"] = True
        projects[k] = entry
    data["projects"] = projects

    tmp = cfg + TMP_SUFFIX + stamp
    try:
        # create with the target mode from the first byte — open()+chmod would leave a
        # world-readable window holding OAuth data (Task 6 review)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if indent is None:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(data, f, indent=indent, ensure_ascii=False)
            if trailing_nl:
                f.write("\n")
        os.replace(tmp, cfg)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return PreTrustResult(target, keys, False, False, False, backup,
                              f"write failed: {e.__class__.__name__}: {e}", 4)

    time.sleep(VERIFY_DELAY_S)
    try:
        data2 = _load_config(cfg)
    except ValueError as e:
        return PreTrustResult(target, keys, False, True, False, backup,
                              f"config unreadable after write: {e}", 4)
    proj2 = data2.get("projects", {})
    verified = isinstance(proj2, dict) and all(
        isinstance(proj2.get(k), dict) and proj2[k].get("hasTrustDialogAccepted") is True
        for k in keys)
    _rotate_backups(cfg)
    if verified:
        return PreTrustResult(target, keys, False, True, True, backup,
                              "wrote and verified", 0)
    return PreTrustResult(target, keys, False, True, False, backup,
                          "write did not hold after re-read", 4)


# NOTE: PreTrustResult.backup is the full backup path when one was created, else None.


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="fa_pre_trust.py",
        description="Pre-accept folder trust for a handover target by writing the ONE "
        "sanctioned key (projects[<realpath>].hasTrustDialogAccepted = true) into "
        "Claude Code's config. Writes nothing else; --help runs nothing.",
        epilog="Environment: CLAUDE_CONFIG_DIR overrides the config location "
        "($CLAUDE_CONFIG_DIR/.claude.json; default ~/.claude.json). "
        "Exit codes: 0 trusted (already or now, verified), 2 usage error, "
        "3 not possible (home folder / not a directory), 4 write failed or did not hold.",
    )
    ap.add_argument("dir", help="target folder to pre-trust")
    ap.add_argument("--config", metavar="PATH",
                    help="config file to use instead of the default resolution")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the resolved keys and write nothing")
    ap.add_argument("--json", action="store_true",
                    help="print only the result fields, never config contents")
    args = ap.parse_args(argv)

    res = pre_trust(args.dir, config_path=args.config, dry_run=args.dry_run)
    if args.json:
        print(json.dumps({"target": res.target, "keys": res.keys, "already": res.already,
                          "wrote": res.wrote, "verified": res.verified, "backup": res.backup,
                          "reason": res.reason}))
    else:
        verdict = {0: "trusted", 2: "usage error", 3: "not possible", 4: "failed"}[res.exit_code]
        line = f"pre-trust: {verdict} — {res.target} ({len(res.keys)} keys) — {res.reason}"
        print(line, file=sys.stdout if res.exit_code == 0 else sys.stderr)
    return res.exit_code


if __name__ == "__main__":
    sys.exit(main())

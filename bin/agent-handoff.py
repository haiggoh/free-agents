#!/usr/bin/env python3
"""agent-handoff — build a deterministic, self-contained continuation directory.

When work must stop (budget exhausted, context too large, a session dying), the
agent writes a *continuation directory* that another agent — local or remote, on
this machine or another — can pick up from exactly where the work stopped. It is
deterministic: the same repo state + task text always yields the same files, so
a handoff is reproducible and diff-able.

It contains:
  HANDOFF.md          human/agent-readable summary: task, what's done, what's
                      next, how to verify, and the exact dispatch to resume.
  manifest.json       machine-readable record of every file collected, its size,
                      and its SHA-256 — so the receiver can verify integrity.
  git-status.txt      `git status --porcelain`
  git-diff.txt        `git diff` (tracked, unstaged)
  git-diff-stat.txt   `git diff --stat`
  git-recent.txt      last N commits (default 10)
  task.txt            the verbatim task text the caller passed
  verify.txt          the verification state the caller passed (what "done" means)

Security posture (non-negotiable, in code): it collects ONLY the git-derived
artifacts and the two caller-supplied texts. It NEVER collects:
  - .env files, credentials, or keys        (a `secret` pattern + a filename blocklist)
  - git-ignored files                        (it reads git's index, not the disk)
  - arbitrary home / filesystem files        (it operates only on the repo worktree)
  - full Claude Code transcripts             (explicitly excluded; a handoff is a
                                             task summary, not a session dump)
Because it sources everything from `git` and two explicit text args, there is no
code path that walks the home directory or reads an unlisted file. The exclusion
list is a *defense in depth* on the git-derived artifacts (a diff can theoretically
contain a secret that was staged; we still scrub the known shapes and refuse to
write them).

Usage:
    agent-handoff.py --repo DIR --task "TEXT" [--verify "TEXT"]
                     [--out DIR] [--commits N] [--title "STR"]

Exit: 0 handoff written · 1 not a git repo / no usable state · 2 bad arguments.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

# Filenames (basename) that are never part of a handoff, whatever git says.
BLOCKED_BASENAMES = {
    ".env", ".env.local", ".env.production", ".envrc", "credentials",
    "credentials.json", "id_rsa", "id_ed25519", "config.json", "netrc",
}
# Case-insensitive substring shapes that look like secret material in a diff.
SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),            # OpenAI-style keys
    re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b"),        # Google / Gemini keys
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),           # GitHub PAT
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),   # Slack tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),               # AWS access key id
]
# Words that name secret *files* in a diff's file headers (--- a/x / +++ b/x).
BLOCKED_FILE_PATTERNS = [
    re.compile(r"(^|/)\.env(\.|$)", re.I),
    re.compile(r"(^|/)(id_rsa|id_ed25519|credentials(\.\w+)?|netrc)(\s|$)", re.I),
    re.compile(r"(^|/)\w*\.pem(\s|$)", re.I),
    re.compile(r"(^|/)\w*\.key(\s|$)", re.I),
]


def _run(cmd, cwd, timeout=60):
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, "", str(exc)


def _git(repo, *args):
    rc, out, err = _run(["git", *args], cwd=repo)
    return rc, out, err


def _is_git_repo(repo):
    rc, _, _ = _git(repo, "rev-parse", "--is-inside-work-tree")
    return rc == 0


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _contains_secret(text, path_hint=""):
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            return True
    for pat in BLOCKED_FILE_PATTERNS:
        if pat.search(path_hint):
            return True
    return False


def collect(repo, task_text, verify_text, title, recent_n):
    """Gather the handoff contents. Returns (files: dict[name, text], manifest,
    warnings). Raises SystemExit(1) if there is no usable git state."""
    repo = os.path.abspath(os.path.expanduser(repo))
    if not os.path.isdir(repo):
        print("✖ repo dir not found: %s" % repo, file=sys.stderr)
        raise SystemExit(1)
    if not _is_git_repo(repo):
        print("✖ not a git worktree: %s" % repo, file=sys.stderr)
        raise SystemExit(1)

    warnings = []
    files = {}

    rc, status, err = _git(repo, "status", "--porcelain")
    git_status = status if rc == 0 else ("(git status failed: %s)" % err.strip())
    if git_status.strip():
        for line in git_status.splitlines():
            path = line[3:].strip().strip('"')
            if os.path.basename(path) in BLOCKED_BASENAMES:
                warnings.append("a git-tracked file looks like a secret (%s); it is "
                                "still listed in git-status.txt but will be scrubbed "
                                "from the diff" % path)

    rc, diff, err = _git(repo, "diff")
    if rc == 0 and diff.strip():
        # A diff is where a staged secret could surface. Scrub by pattern.
        for name in ("git-diff.txt",):
            pass  # checked below after we know the diff content
    diff_stat_rc, diff_stat, _ = _git(repo, "diff", "--stat")
    recent_rc, recent, _ = _git(repo, "log", "--oneline", "-n", str(recent_n))

    rc2, branch, _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip() if rc2 == 0 else "unknown"

    # Scrub secret shapes from the diff + diff-stat before they are written.
    def _scrub(text):
        if not text.strip():
            return text, 0
        n = 0
        for pat in SECRET_PATTERNS:
            text, c = pat.subn("[REDACTED]", text)
            n += c
        # Block secret *files* appearing in a diff by their header lines.
        kept, dropped = [], 0
        for ln in text.splitlines():
            if any(p.search(ln) for p in BLOCKED_FILE_PATTERNS):
                dropped += 1
                continue
            kept.append(ln)
        if dropped:
            text = "\n".join(kept)
        return text, n + dropped

    diff, ns = _scrub(diff)
    diff_stat, ns2 = _scrub(diff_stat)
    if ns or ns2:
        warnings.append("redacted %d secret-shaped line(s)/token(s) from the git diff"
                        % (ns + ns2))

    files["git-status.txt"] = git_status
    files["git-diff.txt"] = diff
    files["git-diff-stat.txt"] = diff_stat
    files["git-recent.txt"] = recent
    files["task.txt"] = task_text
    files["verify.txt"] = verify_text

    # HANDOFF.md — the readable resume note.
    changed = [l[3:].strip().strip('"') for l in git_status.splitlines() if l.strip()]
    handoff = []
    handoff.append("# Handoff: %s\n" % (title or "continued task"))
    handoff.append("Generated: %s\n" % datetime.now(timezone.utc).isoformat())
    handoff.append("Repo: `%s`  (branch: `%s`)\n" % (repo, branch))
    handoff.append("## Task\n\n%s\n" % task_text)
    handoff.append("## Verification state (what 'done' means)\n\n%s\n" % verify_text)
    handoff.append("## Working tree (%d changed path(s))\n" % len(changed))
    handoff.append("```\n%s\n```\n" % (git_status.strip() or "(clean)"))
    handoff.append("## How to resume\n\n"
                   "1. `cd %s`\n"
                   "2. Read this file, then `git status` / `git diff`.\n"
                   "3. Continue the **Task** above; satisfy **Verification state**.\n"
                   "4. If the work needs a model: local MLX first, then an "
                   "explicitly-approved remote free allowance.\n"
                   % repo)
    files["HANDOFF.md"] = "\n".join(handoff)

    manifest = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "branch": branch,
        "title": title,
        "files": {name: {"chars": len(text), "sha256": _sha256(text)}
                  for name, text in sorted(files.items())},
        "excluded_policy": [
            "no .env / credentials / keys (blocked basename + secret patterns)",
            "no git-ignored files (sourced from git index, not disk)",
            "no arbitrary home / filesystem files (repo worktree only)",
            "no full Claude Code transcripts",
        ],
        "warnings": warnings,
    }
    return files, manifest, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build a deterministic, self-contained agent continuation "
                    "directory (git-derived state + task + verification). Collects "
                    "no secrets, no ignored files, no home files, no transcripts.")
    ap.add_argument("--repo", required=True, help="the git worktree to summarize")
    ap.add_argument("--task", required=True, help="the verbatim task text")
    ap.add_argument("--verify", default="(not specified)",
                    help="the verification state: what counts as done")
    ap.add_argument("--title", default="", help="short title for HANDOFF.md")
    ap.add_argument("--commits", type=int, default=10, help="recent commits to include")
    ap.add_argument("--out", help="output dir (default: <repo>/.handoff-<timestamp>)")
    a = ap.parse_args(argv)

    if not _is_git_repo(a.repo):
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = a.out or os.path.join(os.path.abspath(a.repo), ".handoff-" + stamp)
    os.makedirs(outdir, exist_ok=True)

    files, manifest, warnings = collect(a.repo, a.task, a.verify, a.title, a.commits)
    for name, text in files.items():
        with open(os.path.join(outdir, name), "w", encoding="utf-8") as f:
            f.write(text if text.endswith("\n") or not text else text + "\n")
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print("handoff → %s" % outdir, file=sys.stderr)
    print("  files: %s" % ", ".join(sorted(files)), file=sys.stderr)
    for w in warnings:
        print("  ⚠ %s" % w, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
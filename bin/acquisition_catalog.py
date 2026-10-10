"""acquisition_catalog.py — parse & validate model acquisition catalogues (PSV).

Why this exists
---------------
v0.16.0 promised a reader with load-time validation. Previously, catalogue
omissions and typos (wrong field count, bad repo IDs, duplicate aliases) were
silent until a downstream task crashed. This module makes those errors visible
at load time and provides a small CLI for CI gates.

Format
------
* Pipe-separated values (.psv). Lines starting with '#' are comments; blank
  lines are ignored.
* A comment line containing the exact field names declares the schema:
    # alias|label|repo|revision|subdir|size_GB|group|status|include_patterns|runtime
  (The line may appear after a "Schema:" comment line.) If absent, the default
  10-field list above is used.
* Data rows have EXACTLY that many '|' separated fields (fields may be empty).
  Example:
    qwen36-35b-a3b-4bit|Qwen3.6 35B-A3B MLX 4-bit — fast general operator|mlx-community/Qwen3.6-35B-A3B-4bit|main|Qwen3.6-35B-A3B-4bit|19.0|acquisition,wave1,rapid,qwen,operator|candidate||rapid-mlx

Validation (collects ALL problems per row)
------------------------------------------
- Field count matches header -> "expected N fields, got M"
- Required non-empty: alias, repo, revision, subdir, size_GB, runtime
- alias  ^[a-z0-9][a-z0-9._-]*$
- repo   ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$
- revision "main" or 7-40 lowercase hex
- size_GB parses as float > 0
- Duplicate alias across ALL input files -> "duplicate alias (first at <file>:<line>)"

CLI
---
  acquisition_catalog.py [--parse|--report] [--strict] [FILES...]
  --parse   : print JSON {"rows":[{"file","line",**fields}], "problems":[str]}
  --report  : human text (default)
  --strict  : exit 1 if any problem (default also exits 1 on problems; 0 only when clean)
  FILES     : optional positional paths; default = the three catalogues under ../config/
  Unknown flags -> exit 2
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class Row:
    file: str
    line: int
    fields: dict[str, str]

    @property
    def alias(self) -> str:
        return self.fields.get("alias", "")

    @property
    def repo(self) -> str:
        return self.fields.get("repo", "")

    @property
    def size_gb(self) -> Optional[float]:
        try:
            return float(self.fields.get("size_GB", ""))
        except ValueError:
            return None


@dataclass(slots=True)
class Problem:
    file: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.message}"


# ──────────────────────────────────────────────────────────────────────────────
# Constants & regexes
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_FIELDS = [
    "alias", "label", "repo", "revision", "subdir",
    "size_GB", "group", "status", "include_patterns", "runtime"
]

REQUIRED_FIELDS = {"alias", "repo", "revision", "subdir", "size_GB", "runtime"}

ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
REVISION_RE = re.compile(r"^(?:main|[0-9a-f]{7,40})$")

SUFFIXES_TO_DROP = (
    "-4bit", "-8bit", "-mlx", "-gguf", "-q4_k_m", "-q4", "-oq2",
    "-mtp", "-instruct", "-it"
)

DEFAULT_PATHS = [
    "config/model-catalog.acquisitions.rapid.psv",
    "config/model-catalog.acquisitions.gguf.psv",
    "config/model-catalog.acquisitions.omlx.psv",
]


# ──────────────────────────────────────────────────────────────────────────────
# Core parsing
# ──────────────────────────────────────────────────────────────────────────────

def _detect_header(lines: list[tuple[int, str]]) -> tuple[list[str], int]:
    """Return (field_names, header_line_index). header_line_index is 0-based."""
    for idx, (lnum, line) in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        # Look for the schema line: contains 'alias|' after '# '
        content = stripped[1:].strip()
        if content.startswith("alias|") and "|" in content:
            fields = [f.strip() for f in content.split("|")]
            return fields, idx
        # Also accept "Schema:" previous line convention - but we just scan all comment lines
    return DEFAULT_FIELDS, -1


def parse_file(path: Path) -> tuple[list[Row], list[Problem]]:
    rows: list[Row] = []
    problems: list[Problem] = []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        problems.append(Problem(str(path), 0, f"cannot read file: {e}"))
        return rows, problems

    # Keep line numbers (1-based)
    raw_lines = [(i + 1, line.rstrip("\n")) for i, line in enumerate(text.splitlines())]

    # Filter out comments and blank lines for data processing, but keep line numbers
    data_lines: list[tuple[int, str]] = []
    for lnum, line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        data_lines.append((lnum, line))

    if not data_lines:
        return rows, problems

    # Detect header from comment lines in raw_lines
    header_fields, _ = _detect_header(raw_lines)
    expected_count = len(header_fields)

    for lnum, line in data_lines:
        parts = line.split("|")
        if len(parts) != expected_count:
            problems.append(Problem(
                str(path), lnum,
                f"expected {expected_count} fields, got {len(parts)}"
            ))
            # Still create row with available fields for further validation
            fields_dict = {}
            for i, fname in enumerate(header_fields):
                fields_dict[fname] = parts[i] if i < len(parts) else ""
        else:
            fields_dict = dict(zip(header_fields, parts))

        row = Row(file=str(path), line=lnum, fields=fields_dict)
        rows.append(row)

        # Validation
        # Required non-empty
        for req in REQUIRED_FIELDS:
            if not fields_dict.get(req, "").strip():
                problems.append(Problem(str(path), lnum, f"missing {req}"))

        # Alias format
        alias = fields_dict.get("alias", "").strip()
        if alias and not ALIAS_RE.match(alias):
            problems.append(Problem(str(path), lnum, "invalid alias"))

        # Repo format
        repo = fields_dict.get("repo", "").strip()
        if repo and not REPO_RE.match(repo):
            problems.append(Problem(str(path), lnum, "invalid repo"))

        # Revision format
        revision = fields_dict.get("revision", "").strip()
        if revision and not REVISION_RE.match(revision):
            problems.append(Problem(str(path), lnum, "invalid revision"))

        # size_GB positive float
        size_gb_str = fields_dict.get("size_GB", "").strip()
        if size_gb_str:
            try:
                val = float(size_gb_str)
                if val <= 0:
                    problems.append(Problem(str(path), lnum, "size_GB not a positive number"))
            except ValueError:
                problems.append(Problem(str(path), lnum, "size_GB not a positive number"))

    return rows, problems


# ──────────────────────────────────────────────────────────────────────────────
# Load multiple files with duplicate detection
# ──────────────────────────────────────────────────────────────────────────────

def load(paths: Optional[list[Path]] = None) -> tuple[list[Row], list[Problem]]:
    if paths is None:
        base = Path(__file__).resolve().parent.parent
        paths = [base / p for p in DEFAULT_PATHS]

    all_rows: list[Row] = []
    all_problems: list[Problem] = []
    alias_first_seen: dict[str, tuple[str, int]] = {}  # alias -> (file, line)

    for path in paths:
        path = Path(path)            # callers pass str (CLI, env) or Path
        # Try .psv, then .psv.md, then .psv.txt
        candidates = [path]
        if path.suffix == ".psv":
            candidates.append(path.with_name(path.name + ".md"))   # JoyIA exports arrive as foo.psv.md
            candidates.append(path.with_name(path.name + ".txt"))

        found = None
        for cand in candidates:
            if cand.exists():
                found = cand
                break

        if found is None:
            continue  # silently skip missing

        rows, probs = parse_file(found)
        all_rows.extend(rows)
        all_problems.extend(probs)

        # Duplicate alias detection
        for row in rows:
            alias = row.alias
            if alias:
                if alias in alias_first_seen:
                    first_file, first_line = alias_first_seen[alias]
                    all_problems.append(Problem(
                        str(found), row.line,
                        f"duplicate alias (first at {first_file}:{first_line})"
                    ))
                else:
                    alias_first_seen[alias] = (str(found), row.line)

    return all_rows, all_problems


# ──────────────────────────────────────────────────────────────────────────────
# Normalisation & matching
# ──────────────────────────────────────────────────────────────────────────────

VENDOR_PREFIXES = ("nvidia", "ibm", "google", "meta", "microsoft", "mistralai", "deepseek-ai", "moonshotai")


def normalise(model_id: str) -> str:
    """Lowercase; take part after last '/'; drop known suffixes; remove non-alnum."""
    s = model_id.lower()
    if "/" in s:
        s = s.split("/")[-1]
    # Builds stack suffixes ("-mlx-4bit", "-instruct-4bit"): strip until none applies.
    changed = True
    while changed:
        changed = False
        for suf in SUFFIXES_TO_DROP:
            if s.endswith(suf) and len(s) > len(suf):
                s = s[:-len(suf)]
                changed = True
    # A served id names the model ("nemotron-3.5-lightning-30b-a3b") while the MLX build repeats
    # the vendor ("NVIDIA-Nemotron-3.5-…"). Drop ONE leading vendor token; the match stays exact.
    for vendor in VENDOR_PREFIXES:
        if s.startswith(vendor + "-"):
            s = s[len(vendor) + 1:]
            break
    # Remove non-alphanumeric
    return re.sub(r"[^a-z0-9]", "", s)


def match(model_id: str, rows: list[Row]) -> Optional[Row]:
    target = normalise(model_id)
    for row in rows:
        if normalise(row.repo) == target or normalise(row.alias) == target:
            return row
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────────────────────

def report(rows: list[Row], problems: list[Problem]) -> str:
    from collections import Counter
    by_file = Counter(r.file for r in rows)
    lines = []
    for f, cnt in sorted(by_file.items()):
        lines.append(f"{f}: {cnt} row(s)")
    lines.append(f"Total: {len(rows)} row(s)")
    lines.append("")
    if problems:
        for p in problems:
            lines.append(str(p))
        lines.append(f"\n{len(problems)} problem(s)")
    else:
        lines.append("OK")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="acquisition_catalog.py",
        description="Parse and validate model acquisition catalogues (PSV).",
        add_help=False
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--parse", action="store_true", help="Output JSON")
    group.add_argument("--report", action="store_true", help="Human report (default)")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any problem")
    parser.add_argument("--help", action="help", help="Show this help and exit")
    parser.add_argument("files", nargs="*", help="Catalogue files (default: three standard paths)")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code != 0 else 0

    # Resolve file paths
    if args.files:
        paths = [Path(f) for f in args.files]
    else:
        base = Path(__file__).resolve().parent.parent
        paths = [base / p for p in DEFAULT_PATHS]

    rows, problems = load(paths)

    if args.parse:
        out = {
            "rows": [
                {"file": r.file, "line": r.line, **r.fields}
                for r in rows
            ],
            "problems": [str(p) for p in problems]
        }
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(report(rows, problems) + "\n")

    # Exit code logic
    has_problems = len(problems) > 0
    if has_problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
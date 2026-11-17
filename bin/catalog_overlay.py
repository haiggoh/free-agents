#!/usr/bin/env python3
"""Where the per-user LOCAL model-catalog overlay lives (0.58.3, user 2026-11-14).

The Python twin of bin/la-catalog-overlay.sh — same resolution order, parity-tested in
tests/test_catalog_overlay.py:
  1. $LA_MODEL_CATALOG_LOCAL — explicit file; set but missing raises OverlayOverrideError
  2. ~/.claude/free-agents/model-catalog.local.psv — the per-user store every copy reads
  3. <config_dir>/model-catalog.local.psv — the legacy location
  4. None

Usage as a script:  catalog_overlay.py [--config-dir DIR]   (prints the path or nothing)
Environment: LA_MODEL_CATALOG_LOCAL (step 1). The migration lives in the shell helper only.
"""
import os
import sys
from pathlib import Path

NAME = "model-catalog.local.psv"


class OverlayOverrideError(ValueError):
    """LA_MODEL_CATALOG_LOCAL is set but names no file."""


# PSV row fields, in order: alias|name|repo|rev|subdir|size_gb|groups|tier|notes|serve
ROW_FIELDS = ("alias", "name", "repo", "rev", "subdir", "size_gb", "groups",
              "tier", "notes", "serve")


class OverlayWriteError(ValueError):
    """A row edit would produce an invalid store file."""


def read_rows(path: Path) -> "list[dict]":
    """Parse the overlay store: '#' comment lines skipped, one dict per data row.
    Malformed rows (wrong field count) raise — a half-written file must not pass silently."""
    rows = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) != len(ROW_FIELDS):
            raise OverlayWriteError(f"{path}:{n}: expected {len(ROW_FIELDS)} fields, got {len(parts)}")
        rows.append(dict(zip(ROW_FIELDS, parts)))
    return rows


def _validate_row(row: dict) -> None:
    if not row.get("alias"):
        raise OverlayWriteError("alias must be non-empty")
    for f in ROW_FIELDS:
        if "|" in row.get(f, "") or "\n" in row.get(f, ""):
            raise OverlayWriteError(f"field {f} contains a pipe or newline")
    try:
        float(row.get("size_gb") or 0)
    except ValueError:
        raise OverlayWriteError(f"size_gb is not a number: {row.get('size_gb')!r}") from None


def _comment_lines(path: Path) -> "list[str]":
    """The store's '#' comment and blank lines, in order — user annotations, preserved
    across rewrites (read BEFORE write_rows replaces the file)."""
    if not Path(path).exists():
        return []
    return [line for line in Path(path).read_text(encoding="utf-8").splitlines()
            if not line.strip() or line.lstrip().startswith("#")]


def write_rows(path: Path, rows: "list[dict]", comments: "list[str] | None" = None) -> None:
    """Atomically replace the store: validate every row, unique aliases, mode 600.
    `comments` defaults to the file's own comment/blank lines, written ahead of the rows."""
    seen = set()
    for r in rows:
        _validate_row(r)
        if r["alias"] in seen:
            raise OverlayWriteError(f"duplicate alias: {r['alias']}")
        seen.add(r["alias"])
    path = Path(path)
    if comments is None:
        comments = _comment_lines(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".psv.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for c in comments:
            f.write(c + "\n")
        for r in rows:
            f.write("|".join(r.get(fld, "") for fld in ROW_FIELDS) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def add_row(path: Path, row: dict) -> None:
    """Insert a row (appended at the end); duplicate alias raises."""
    rows = read_rows(path) if Path(path).exists() else []
    if any(r["alias"] == row.get("alias") for r in rows):
        raise OverlayWriteError(f"alias already present: {row.get('alias')}")
    rows.append({f: row.get(f, "") for f in ROW_FIELDS})
    write_rows(path, rows)


def remove_row(path: Path, alias: str) -> bool:
    """Remove the row with this alias; returns False when absent (not an error)."""
    if not Path(path).exists():
        return False
    rows = read_rows(path)
    kept = [r for r in rows if r["alias"] != alias]
    if len(kept) == len(rows):
        return False
    write_rows(path, kept)
    return True


def store_path() -> Path:
    return Path.home() / ".claude" / "free-agents" / NAME


def overlay_path(config_dir: Path) -> "Path | None":
    override = os.environ.get("LA_MODEL_CATALOG_LOCAL")
    if override:
        p = Path(override)
        if p.is_file():
            return p
        raise OverlayOverrideError(f"la-catalog-overlay: LA_MODEL_CATALOG_LOCAL={override} does not exist")
    store = store_path()
    if store.is_file():
        return store
    legacy = Path(config_dir) / NAME
    return legacy if legacy.is_file() else None


def main(argv: list) -> int:
    cfg = Path(__file__).resolve().parents[1] / "config"
    args = list(argv)
    while args:
        a = args.pop(0)
        if a in ("-h", "--help"):
            print((__doc__ or "").strip())
            return 0
        if a == "--config-dir" and args:
            cfg = Path(args.pop(0))
            continue
        print(f"catalog_overlay.py: unrecognised argument {a}\nusage: catalog_overlay.py [--config-dir DIR]",
              file=sys.stderr)
        return 2
    try:
        p = overlay_path(cfg)
    except OverlayOverrideError as e:
        print(e, file=sys.stderr)
        return 2
    print(p or "", end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

# Catalog overlay user store — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (native: the executor does every task, then one final review). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The per-user local model catalog overlay (`model-catalog.local.psv`) gets a stable home at `~/.claude/free-agents/model-catalog.local.psv`, so every copy of free-agents (canonical checkout AND installed plugin cache) reads the same file. That closes the 0.58.2 gap: the statusline (installed copy) showed `labsdiff-3b-4bit` while the banner (checkout) showed "Labs-Diffusion 3B 4-bit".

**Architecture:** One resolver, two thin front ends: `bin/la-catalog-overlay.sh` (bash 3.2-compatible; sourceable function + CLI with `path` / `migrate`) and `bin/catalog_overlay.py` (same rules for the Python readers), pinned to agree by a parity test. All six readers call it instead of hardcoding `<copy>/config/model-catalog.local.psv`. A one-time migration, run from `bin/csl`, moves the user's checkout file to the store and leaves a symlink behind (user's choice, 2026-11-14).

**Tech Stack:** bash 3.2+/4 (readers), zsh (one diagnostics reader), Python 3 stdlib, pytest.

**Spec:** this conversation's decisions (user 2026-11-14): store path `~/.claude/free-agents/model-catalog.local.psv`; migration = move + symlink, announced once; all six readers; ship as 0.58.3, full loop. **Revision (user, same day): "my local setup gets no special treatment — the migration coming with the next update must work for me and anyone else who already has a local catalog."** So: no hand-run migration on the user's machine; the migration finds overlays in EVERY install layout and runs from the normal update path for every kind of user.

## Global Constraints

- Store path: `$HOME/.claude/free-agents/model-catalog.local.psv` (same parent as the existing `themes/` store).
- Resolution order, identical in shell and Python: (1) `LA_MODEL_CATALOG_LOCAL` if set — must exist, else a loud error naming the variable (never fall through); (2) the store file if it exists; (3) the legacy `<config_dir>/model-catalog.local.psv` if it exists; (4) none.
- `install/download-models.sh`'s existing `--catalog` / `LA_MODEL_CATALOG` overrides keep winning outright (unchanged).
- Every new script supports `--help`, rejects unknown flags with exit 2 and a usage line on stderr.
- `bin/la-session-identity.sh` runs on EVERY statusline refresh: it sources the shell helper (no fork, no python) and on an override error prints to stderr and continues without the overlay (the statusline must never die).
- The migration never overwrites or deletes user data: a conflict (store and legacy both regular files with different content) only warns.
- **No special treatment (user):** the maintainer's machine is migrated by the same code path, triggered the same way, as any user's. Nothing in the code or the release steps names this machine, a checkout path, or a user.
- **Where an existing overlay can be (all scanned by `migrate`):** (a) the running copy's own `config/` (a git checkout, or a cache/brew dir the user edited in place); (b) every plugin-cache version dir `${CLAUDE_PLUGINS_DIR:-$HOME/.claude/plugins}/cache/*/free-agents/*/config/` — an update leaves the overlay behind in the OLD version dir because `scripts/package-release.sh` strips it from the new one; (c) every Homebrew version dir `<prefix>/Cellar/free-agents/*/libexec/config/` for prefixes `/opt/homebrew` and `/usr/local` (globbed, never by calling `brew` — it is slow). Overridable for tests: `LA_OVERLAY_SCAN_ROOTS` (':'-separated config dirs; set = exact list, a missing entry is just absent) and `LA_OVERLAY_BREW_PREFIXES` (':'-separated, default `/opt/homebrew:/usr/local`) — every test using the default scan pins the brew prefixes to tmp, because a default scan reaching the real `/opt/homebrew` could MOVE a real user file (found while writing Task 2's tests).
- **Triggers (every route an updated user passes):** a new entry in the plugin's existing `hooks/hooks.json` SessionStart list (runs from the newly installed copy in every Claude session; a no-op once migrated), and `bin/csl` startup (checkout and Homebrew users who never start a plugin session). Both call the same `la-catalog-overlay.sh migrate`; it is idempotent and silent when there is nothing to do.
- Version 0.58.3; CHANGELOG entry; `rtc-ship.sh --bump-versions`.

## Review Focus

1. **Plugin user whose overlay sits in an OLD cache version dir** (the common case after an update) → the SessionStart route of the NEW copy finds it and migrates it. Pinned in Task 2.
2. **The same overlay in several install dirs** (a user who copied it into each new version by hand) → all identical by `cmp`: one moves to the store, the rest become symlinks. Different contents anywhere → nothing changes, one warning lists every path and the store path to merge into, every run until resolved. Pinned in Task 2.
3. **Second run / already migrated** → silent no-op; the resolver returns the store path, so `download-models.sh` loads the file ONCE, not twice. Pinned in Tasks 2 and 3.
4. **No overlay anywhere** (most users) → every reader behaves exactly as today, no output from either trigger. Pinned in Tasks 1 and 2.
5. **A reader re-hardcodes the legacy path later** → the structural guard test in Task 3 fails (the overlay filename may appear only in the two helpers, `scripts/package-release.sh`, tests and docs).

---

### Task 1: The resolver (shell + Python, parity-pinned)

**Files:**
- Create: `bin/la-catalog-overlay.sh`, `bin/catalog_overlay.py`
- Test: `tests/test_catalog_overlay.py`

**Interfaces:**
- Produces (shell, sourced): `la_catalog_overlay_path <config_dir>` — prints the resolved path (no newline) or nothing; returns 0, or 2 after printing `la-catalog-overlay: LA_MODEL_CATALOG_LOCAL=<v> does not exist` to stderr.
- Produces (shell, CLI): `la-catalog-overlay.sh path [--config-dir DIR]` (default DIR = the script's `../config`), same output/exit codes; `--help`.
- Produces (Python): `catalog_overlay.overlay_path(config_dir: Path) -> Path | None`; raises `catalog_overlay.OverlayOverrideError(ValueError)` with the same message for a set-but-missing override.

- [ ] **Step 1: Write the failing tests** in `tests/test_catalog_overlay.py`, each with a tmp `HOME` and tmp config dir:
  `test_none_anywhere_returns_nothing`, `test_store_wins_over_legacy`, `test_legacy_used_when_no_store`, `test_override_wins`, `test_override_missing_is_loud` (shell exit 2 + stderr names `LA_MODEL_CATALOG_LOCAL`; Python raises `OverlayOverrideError`), and `test_shell_and_python_agree` (parametrized over the five states above, comparing `la-catalog-overlay.sh path --config-dir D` stdout with `str(overlay_path(D) or "")`), plus `test_help_and_bad_flag` (`--help` exit 0 mentions `path` and `migrate`; `--bogus` exit 2).
- [ ] **Step 2: Run** `pytest tests/test_catalog_overlay.py -q` — Expected: FAIL (files missing).
- [ ] **Step 3: Implement** both front ends per the Global Constraints order. The shell file must be safe to `source` from bash 3.2 (no `local -n`, no associative arrays) and only run its CLI when executed directly (`[ "${BASH_SOURCE[0]}" = "$0" ]`).
- [ ] **Step 4: Run** `pytest tests/test_catalog_overlay.py -q` — Expected: all pass.
- [ ] **Step 5: Commit** `bin/la-catalog-overlay.sh bin/catalog_overlay.py tests/test_catalog_overlay.py`.

### Task 2: Migration from every install layout (move + symlink), on every update route

**Files:**
- Modify: `bin/la-catalog-overlay.sh` (add `migrate [--config-dir DIR] [--dry-run]`)
- Modify: `bin/csl` (call `"$CSL_DIR/la-catalog-overlay.sh" migrate` once, before dispatch; never fatal: `|| true`)
- Modify: `hooks/hooks.json` (one more SessionStart command: `bash "${CLAUDE_PLUGIN_ROOT}/bin/la-catalog-overlay.sh" migrate --hook`; `--hook` prints its notice on stdout so the session shows it, and always exits 0)
- Test: `tests/test_catalog_overlay.py`

**Interfaces:**
- Consumes: Task 1's resolver.
- Produces: `la-catalog-overlay.sh migrate` — exit 0 in every non-error state; prints exactly one notice line (stderr; stdout with `--hook`) when it changes something or finds a conflict, nothing otherwise. Sources scanned = Global Constraints (a)+(b)+(c), or exactly `LA_OVERLAY_SCAN_ROOTS` when set.

- [ ] **Step 1: Write the failing tests** — every layout is synthetic under a tmp `HOME`/scan roots, none is this machine's:
  `test_checkout_user_moves_and_symlinks` (overlay in the running copy's config dir → store holds the original bytes, that path is now a symlink to the store, one notice containing `moved`);
  `test_plugin_user_overlay_in_old_cache_version_is_migrated` (overlay only in `cache/m/free-agents/0.57.0/config/`, migrate run with `--config-dir` = a fresh `0.58.3/config` without one → store gets it, old path symlinked);
  `test_brew_user_overlay_in_old_cellar_version_is_migrated` (same with `Cellar/free-agents/0.57.2/libexec/config/`);
  `test_identical_copies_everywhere_one_store_rest_symlinks`;
  `test_different_copies_warn_and_change_nothing` (every file byte-identical to before, the notice lists every path and the store path, exit 0);
  `test_store_exists_and_differs_from_a_legacy_copy_warns`;
  `test_second_run_is_silent`; `test_nothing_anywhere_is_silent`; `test_dry_run_changes_nothing`;
  `test_hook_mode_prints_on_stdout_and_exits_0_even_on_conflict`;
  `test_hooks_json_registers_the_migration_on_session_start` (parse `hooks/hooks.json`; the SessionStart list contains the `la-catalog-overlay.sh" migrate --hook` command).
- [ ] **Step 2: Run** them — Expected: FAIL.
- [ ] **Step 3: Implement `migrate`.** Collect existing REGULAR files (symlinks are already migrated); group by `cmp -s`. One distinct content: if the store is absent, `mkdir -p` its dir → `mv` the first source → store → `ln -s` store → that source (if `ln` fails, move the file back — no state where neither path holds the data); every other identical source → replaced by a symlink only after `cmp -s` against the store. More than one distinct content, or a store that differs from any source → change nothing, one notice. Plugin-cache and Cellar globs must tolerate no matches (bash 3.2: test `-e` on each expansion).
- [ ] **Step 4: Wire `bin/csl` and `hooks/hooks.json`**, run `pytest tests/test_catalog_overlay.py -q` — Expected: all pass.
- [ ] **Step 5: Commit** `bin/la-catalog-overlay.sh bin/csl hooks/hooks.json tests/test_catalog_overlay.py`.

### Task 3: Point all six readers at the resolver + structural guard

**Files:**
- Modify: `install/download-models.sh:233-237,262-265` (replace the two `model-catalog.local.psv` candidates with one resolved path, appended after the base `model-catalog.psv` candidates; a resolver error aborts like a missing `--catalog` file)
- Modify: `bin/la-model-profile.py:45` (replace the entry with `overlay_path(CONFIG_DIR)` when not None)
- Modify: `bin/la-detect-backend.py:253` (`overlay_path(...)`; None → return None as today)
- Modify: `bin/la-disk-inventory.sh:52` (resolved path instead of `$HERE/../config/model-catalog.local.psv`)
- Modify: `bin/local-inference-readonly-inventory.zsh:447` (call `la-catalog-overlay.sh path` — it is zsh, so execute, don't source)
- Modify: `bin/la-session-identity.sh` `_la_registry_display` (source the helper once; on error, stderr + no overlay)
- Modify: `README.md` (the overlay's new location, one sentence)
- Test: `tests/test_catalog_overlay.py`, `tests/test_la_session_identity.sh`

**Interfaces:**
- Consumes: Task 1's `la_catalog_overlay_path`, `overlay_path`.

- [ ] **Step 1: Write the failing tests:** `test_no_reader_hardcodes_the_overlay_path` (scan `bin/`, `install/`, `config/*.sh` for the literal `model-catalog.local.psv`; allowed only in `bin/la-catalog-overlay.sh`, `bin/catalog_overlay.py`; the test must FAIL if the scan finds zero files at all — empty extraction is a failure); `test_download_models_lists_store_overlay_once` (tmp HOME store overlay with one unique alias + legacy symlink to it → `install/download-models.sh --list` output contains that alias and the `Catalog: <store path>` line exactly once); `test_model_profile_reads_store_overlay` (`la-model-profile.py`'s catalog files include the store path under a tmp HOME); and in `tests/test_la_session_identity.sh` a case with `HOME=<tmp>` whose store overlay row `zz-store-only|ZZ Store Model — x|r|main|zz-store-only-dir` makes `MODEL_ALIAS=zz-store-only-dir` resolve to `ZZ Store Model`.
- [ ] **Step 2: Run** them — Expected: FAIL (readers still hardcode).
- [ ] **Step 3: Change the six readers** as listed.
- [ ] **Step 4: Run** `pytest tests/test_catalog_overlay.py -q && bash tests/test_la_session_identity.sh` — Expected: pass; then the full suite `pytest -q tests -n 12 -m 'not load_sensitive'` — Expected: no new failures.
- [ ] **Step 5: Commit** the readers, README and tests by path.

### Task 4: Release 0.58.3

- [ ] **Step 1:** CHANGELOG `## [0.58.3]` entry (what moved, the symlink, the six readers, the conflict rule); `rtc-ship.sh --bump-versions <worktree> 0.58.3`; commit.
- [ ] **Step 2:** Full ship loop from the canonical checkout: `rtc-ship.sh <worktree> 0.58.3 "<title>"` — Expected: `done — v0.58.3 is live`.
- [ ] **Step 3: Dogfood — no special treatment.** Before the ship, take a timestamped backup of any existing overlay (the generic backup-before-edit rule, not a migration step). Do NOT run `migrate` by hand. After the refresh, the migration must happen through a normal route: the user's next `csl` launch or next Claude session (the SessionStart hook of the installed 0.58.3). Then verify by outcome: the store file exists, `cmp` against the backup = identical, the old path is a symlink, and the INSTALLED copy's `la-session-identity.sh` with `MODEL_ALIAS=labsdiff-3b-4bit`, `ANTHROPIC_BASE_URL=http://localhost:8000` → `Labs-Diffusion 3B 4-bit`.

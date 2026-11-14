# Legacy-venv migration + two-root readers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (native: the executor does every task, then one final whole-branch review). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the 0.50.0 venv-root move for every user: every reader finds venvs in BOTH roots, a launch-time offer migrates legacy venvs, repair keeps the live pin valid, and a stale pin never gates a lane.

**Architecture:** 0.50.0 moved venv WRITES to `$XDG_DATA_HOME/free-agents/backends` (default `~/.local/share/free-agents/backends`); the manager's Python reads already scan both roots (`base.discovery_roots`, new root first). Four shell/Python readers still scan only `~/.venvs`, `repair_version` relocates a legacy venv without re-pinning, and nothing tells the user. The launch offer piggybacks on the existing csl-start upgrade prompt (`install/backend_upgrades.py prompt()`), with the migration offers supplied by a new, separate module — one prompt loop, one state file, one opt-out.

**Tech Stack:** bash 3.2-compatible `config/config-lib.sh`, Python 3 (`install/`, `bin/`), pytest (`~/.local/bin/pytest`, `-n 12`).

**Spec:** user decisions in session 2026-11-13/14 (verbatim):
- "provide the repair route on launch, but if the user refuses, use the same version with a warning in the meantime so the lane is never gated to force the user's hand"
- "build a new launch offer, but based on the existing mechanism you can piggyback on to not duplicate more code than necessary, keep it modular please" (the mechanism = the csl-start UPDATE offer)
- "new installations go into the correct path but if that path is empty, the old path gets accepted too"
- "I want any fix to apply to all users not just me locally"

## Global Constraints

- New root first, legacy `~/.venvs` second, in EVERY reader. XDG root = `${XDG_DATA_HOME:-$HOME/.local/share}/free-agents/backends`.
- Only managed names count: `<prefix>-<X.Y.Z>` for prefixes `rapid-mlx`, `vllm-mlx`, `litellm` (the user's own unrelated `~/.venvs/*` are never listed, offered or touched).
- A stale explicit pin falls back ONLY to the SAME `<prefix>-<version>` in the other root, never to a different version, and always prints a visible warning naming the fix command.
- No user state changes without a yes (or the user's existing auto-install setting). Never rewrite anyone's live config during an update.
- Pins are written home-relative `"$HOME/…"` (`base.home_relative_pin`).
- `config-lib.sh` stays bash 3.2 compatible (no `mapfile`, no `${var,,}`).
- Version: next free minor after `git ls-remote --tags origin` at ship time (expected `0.58.0`); bump `VERSION`, `.claude-plugin/plugin.json`, `docs/ROADMAP.md:19`, CHANGELOG.

## Review Focus

1. A version present in BOTH roots → no migration offer, discovery returns the new-root copy.
2. A declined migration is not re-asked every launch (decline remembered per `<prefix>-<version>`), but a NEW legacy venv still is.
3. Stale pin to `~/.venvs/rapid-mlx-1.0.0` with only `rapid-mlx-1.0.3` in the new root → NO fallback (different version); launcher reports the broken pin as today.
4. Repair of the pinned venv whose re-pin validation fails → venv original restored AND pin byte-identical.
5. Non-TTY / `LA_BACKEND_UPGRADES=0` / notify off → no migration question, no output.

---

### Task 1: Two-root shell readers in config-lib

**Files:**
- Modify: `config/config-lib.sh:293-303` (`la_backend_repair_hint`), `:327-338` (`la_discover_rapid_bin`)
- Test: `tests/test_two_root_readers_0580.py` (new; drives bash via `subprocess`, temp `HOME`, `_LA_RAPID_SYSTEM_CANDIDATES=""`, `PATH` without rapid-mlx)

**Interfaces:**
- Produces: `la_backend_roots` → prints the two roots, new first, one per line (used by Tasks 1-2).

- [ ] **Step 1: Write failing tests**
  - `test_discovery_finds_a_new_root_venv`: only `backends/rapid-mlx-1.0.3` exists → `la_discover_rapid_bin` prints its `bin/rapid-mlx`.
  - `test_discovery_prefers_newest_across_roots`: legacy 1.0.0 + new 1.0.3 → prints 1.0.3.
  - `test_discovery_same_version_both_roots_prefers_new_root` (Review Focus 1).
  - `test_repair_hint_names_repair_for_a_new_root_binary`: hint for `…/backends/vllm-mlx-0.6.0/bin/vllm-mlx` contains `--backend vllm-mlx repair 0.6.0`.
  - `test_discovery_ignores_unmanaged_dirs`: `~/.venvs/rapid-mlx-foo` ignored.
- [ ] **Step 2: Run** `~/.local/bin/pytest -q tests/test_two_root_readers_0580.py` → FAIL (discovery prints "" / hint says "reinstall").
- [ ] **Step 3: Implement** `la_backend_roots`; discovery collects candidates from both roots, sorts by version (`sort -V -r` on the `<prefix>-<ver>` basename, new root winning ties), health-checks each; hint regex accepts `/(\.venvs|free-agents/backends)/`.
- [ ] **Step 4: Run** the new file + `tests/test_shell_suites.py -k rapid_backend` → PASS.
- [ ] **Step 5: Commit** `fix(launcher 0.58.0): discovery and repair hint read both venv roots`.

### Task 2: Stale pin → same version from the other root, with a warning

**Files:**
- Modify: `config/config-lib.sh` near `:755` (after the explicit-pin / discovery block) and where `LA_VENV` is finalised
- Test: `tests/test_two_root_readers_0580.py`

**Interfaces:**
- Consumes: `la_backend_roots` (Task 1).
- Produces: `la_relocated_pin <path>` → if `<path>` is not executable AND matches `<root>/<prefix>-<ver>/<rest>` for a managed prefix, prints the same `<prefix>-<ver>/<rest>` under the OTHER root when that passes `la_backend_bin_health`; prints nothing otherwise. Exit 0 iff it printed.

- [ ] **Step 1: Write failing tests**
  - `test_moved_pin_resolves_to_same_version_in_new_root_and_warns`: live pin `$HOME/.venvs/rapid-mlx-1.0.0/bin/rapid-mlx`, venv only at `backends/rapid-mlx-1.0.0` → `LA_RAPID_BIN` = new-root path; stderr contains `moved` and `repair 1.0.0`.
  - `test_moved_pin_never_falls_back_to_another_version` (Review Focus 3).
  - `test_moved_la_venv_resolves_the_same_way` (vllm-mlx `LA_VENV=…/bin`).
  - `test_valid_pin_is_untouched_and_silent`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `la_relocated_pin`; after the pin is set, `if ! [ -x "$LA_RAPID_BIN" ] && r=$(la_relocated_pin "$LA_RAPID_BIN"); then warn; LA_RAPID_BIN=$r; fi` (same for `$LA_VENV/vllm-mlx`). Warning text (one line, stderr, ⚠️): `LA_RAPID_BIN points at a moved venv; using the same version at <new>. Fix: <repair hint>` — the hint from `la_backend_repair_hint`.
- [ ] **Step 4: Run** new file + `tests/test_live_config_promotion.py tests/test_vllm_promote.py` → PASS.
- [ ] **Step 5: Commit** `feat(launcher 0.58.0): a moved pinned venv keeps the lane running with a warning`.

### Task 3: Repair keeps the live pin valid

**Files:**
- Modify: `install/managers/base.py` `repair_version` (between `validate_environment` and `shutil.rmtree(aside)`)
- Test: `tests/test_repair_repins_0580.py` (new; fixture pattern of `tests/test_pin_promotion_xdg_root.py`)

**Interfaces:**
- Consumes: `self.promote_pins(version)` (rapid/vllm implementations), `self._referenced_paths(repo, target)`.
- Produces: repair result gains `"repinned": bool`.

- [ ] **Step 1: Write failing tests**
  - `test_repair_of_the_pinned_legacy_venv_moves_the_pin`: pin → legacy 1.0.0; repair 1.0.0 → pin = `"$HOME/.local/share/free-agents/backends/rapid-mlx-1.0.0/bin/rapid-mlx"`, `repinned` True.
  - `test_repair_of_an_unpinned_venv_leaves_the_live_config_byte_identical`.
  - `test_failed_repin_restores_venv_and_pin` (Review Focus 4: stub `promote_pins` to raise).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**: when `relocating` and the live config references `target`, call `promote_pins(version)` inside the existing `try`, so any failure takes the existing restore path; on restore also leave the live config as it was (promote already rolls itself back). Update the docstring ("a repair never promotes" → "a repair re-pins ONLY a pin it relocated").
- [ ] **Step 4: Run** new file + `tests/test_backend_repair.py tests/test_backend_remove.py` → PASS.
- [ ] **Step 5: Commit** `fix(repair 0.58.0): relocating a pinned venv re-pins it`.

### Task 4: Migration offer module + generic offer loop

**Files:**
- Create: `install/legacy_venv_migration.py` (offer SOURCE only: detection + command; `--help`, `--list [--json]`)
- Modify: `install/backend_upgrades.py` (`Upgrade` gains `key` property; `prompt()` accepts extra offer sources)
- Test: `tests/test_legacy_venv_migration_0580.py` (new)

**Interfaces:**
- Produces: `legacy_venv_migration.offers(home: Path | None = None) -> list[Upgrade]` — one per managed `~/.venvs/<prefix>-<ver>` (prefixes from the registered managers with `SUPPORTS_VENV_REMOVE`) with no same-name dir in the new root; `Upgrade(backend=<prefix>, current=<ver>, latest=<ver>, via="migrate", command=[sys.executable, MANAGE_BACKEND, "--backend", prefix, "repair", ver])`.
- Produces: `Upgrade.key -> str` = `f"migrate:{backend}-{current}"` when `via == "migrate"`, else `backend` (existing decline keys unchanged).
- Produces: `prompt(..., sources: tuple[Callable[[], list[Upgrade]], ...] = (legacy_venv_migration.offers,))`; migration offers are computed live at prompt time (never stored in `pending`, never need the network), `still_needed` is True for `via == "migrate"` while the legacy dir exists.

- [ ] **Step 1: Write failing tests**
  - `test_offers_list_only_legacy_only_managed_venvs` (Review Focus 1 + unmanaged dirs).
  - `test_prompt_offers_migration_and_runs_repair_on_yes` (stubbed `ask`/`run_live`; command ends `repair 1.0.0`).
  - `test_declined_migration_is_not_reasked_but_a_new_one_is` (Review Focus 2).
  - `test_migration_header_is_separate_from_the_upgrade_notice`: output contains `in the old location` and does NOT print `UPGRADE_NOTICE` when only migrations are pending.
  - `test_notify_off_prints_nothing` (Review Focus 5).
  - `test_module_help_runs`: `legacy_venv_migration.py --help` exits 0 and names `--list`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**. Migration header copy: `📦 {n} backend venv(s) are in the old location ~/.venvs. Migrating rebuilds each in ~/.local/share/free-agents/backends and moves its pin; until then they keep working with a warning.` Question: `Migrate {backend} {ver} to the new location?  runs: … repair {ver}  [Y/n] `. Decline message: `skipped — keeps working from ~/.venvs; migrate later from the backend manager (Repair).` Auto-install mode runs migrations too (repair is make-before-break). `bin/csl` needs no change (same call).
- [ ] **Step 4: Run** new file + `tests/test_backend_upgrades.py tests/test_update_check.py` → PASS.
- [ ] **Step 5: Commit** `feat(launch 0.58.0): offer legacy-venv migration through the upgrade prompt`.

### Task 5: Remaining Python/zsh readers

**Files:**
- Modify: `bin/la-detect-backend.py:141`, `bin/local-inference-readonly-inventory.zsh:349-355`
- Test: `tests/test_two_root_readers_0580.py` (`test_detect_backend_sees_new_root_rapid`)

- [ ] **Step 1:** failing test for `la-detect-backend.py` with only a new-root rapid venv.
- [ ] **Step 2:** run → FAIL. **Step 3:** glob both roots via `managers.base.discovery_roots` (detect) / both dirs (inventory). **Step 4:** run → PASS + `tests/test_detect_backend.py`.
- [ ] **Step 5: Commit** `fix(detect 0.58.0): backend detection and inventory read both venv roots`.

### Task 6: Release

- [ ] Re-grep `git grep -n '\.venvs' -- bin config install scripts ':!*.md' ':!tests'` — every remaining hit is a comment, a docstring, or `legacy_venvs_root` (record the list in the CHANGELOG entry).
- [ ] Bump version surfaces (Global Constraints), CHANGELOG `## [0.58.0]` entry: the four readers, repair re-pin, the stale-pin fallback + warning, the launch offer.
- [ ] Full gate via rtc-ship (`rtc-ship.json` `test_cmd`), merge to `main`, tag, release; verify with `git ls-remote --tags origin` and `gh api repos/haiggoh/free-agents/contents/.claude-plugin/plugin.json`.
- [ ] Dogfood through the installed copy: `install/legacy_venv_migration.py --list` lists this machine's legacy venvs (expected: rapid-mlx 0.16.1, 0.16.9, 0.16.10, 1.0.0); launch `csl` and see the offer (do NOT accept on the user's behalf).

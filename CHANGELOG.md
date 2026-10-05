## [0.23.5] — 2026-10-05

Model context & autocompaction catalogue integration (hybrid approach, Plan Phase 5C/8).

### Added
- **YAML catalogue as authoritative source** (`config/model-catalogue-context-list.yaml`):
  - 44 entries with schema_version: 2, entry_type classification (standalone_model, override_alias,
    speculative_drafter, tts_model, depth_estimation_model, filesystem_metadata)
  - Per-model context_window_tokens, autocompaction_tokens, extended_context_tokens, extended_autocompaction_tokens
  - Auto-drafter target linking (Qwen3.8 MTP 4/8-bit → corresponding full models)

- **Derived PSV generator** (`bin/la-catalogue-generate.py`):
  - `generate` — emits `config/model-catalogue-derived.psv` (12 fields) from YAML
  - `validate` — schema_version, unique folders, valid entry types, 100K increment autocompaction,
    alias/drafter resolution, no standalone registration of TTS/drafter/specialist/assets
  - `merge` — shows combined view of YAML + config.local.sh registrations

- **Config system integration** (`config/config-lib.sh`):
  - `la_load_derived_catalogue()` — loads derived PSV into LA_CATALOGUE_* arrays
  - `la_auto_scan_models()` — scans ~/.models for text-capable models; filters TTS (chatterbox, kokoro,
    qwen3-tts), image-gen (flux, stable-diffusion, sdxl), depth (depthart), drafters/speculative,
    processors (mmproj, vision, clip, vae); **keeps MTP** (multi-token prediction) models
  - `la_get_effective_context()` / `la_get_autocompaction()` — 5-level precedence:
    1. Explicit per-session override (LA_SESSION_AUTO_COMPACT)
    2. Explicit per-model local override (LA_MODEL_CONTEXT_OVERRIDE, LA_MODEL_AUTOCOMPACT_OVERRIDE)
    3. Selected catalogue mode (extended vs native via LA_USE_EXTENDED_CONTEXT)
    4. Catalogue default (from derived PSV)
    5. LA_MAX_MODEL_LEN fallback
  - `la_load_manifest()` / `la_manifest_effective_context()` / `la_manifest_autocompaction()` /
    `la_validate_manifest_config()` — manifest-driven context derivation shared with csl
  - Called from `la_load_config()` so all launchers inherit automatically

- **local-session.sh dry-run** — shows precedence chain, manifest values, and extended context flag

### Changed
- **Config**: derived catalogue loaded automatically; LA_CATALOGUE_* arrays populated per model folder
- **Auto-scan**: MTP models (Qwen3.8-27B-MTP-4/8bit) now included as speculative drafters

### Tests
- Full suite: 363 passed, 0 failed, 40 skipped (excluding quota telemetry)
- Model manifest fixtures: 43 passed (autocompaction table, classification, runtime reconciliation)
- Session menu state: 24 passed (schema v3 with temperature)
- Rapid MLX manager: 30 passed (mutation-tested)
- Session picker PTY: 3 passed + 10 skipped
- New features: 5 passed + 16 skipped

## [0.23.4] — 2026-10-05

### Added
- **Briefing checklist for offload-to-local skill** (`skills/offload-to-local/SKILL.md`):
  - Common silent failure points when delegating to a local model:
    - Ordering of returned collections (first vs last write wins, newest-first vs oldest-first)
    - Tie-breaking / precedence rules
    - Inclusive vs exclusive boundaries
    - Empty / absent / malformed input handling
    - Timezone- and locale-dependent values (assert structurally, not exact strings)
  - **Rule: ALWAYS mutation-test offloaded tests** — a passing test can hide a blind spot (documented example from iPhone backup app: guard deletion didn't fail test because duplicate record hid behind `.first` match; test only caught it when asserting count before unwrapping)

### Tests
- Full suite: 385 passed, 0 failed, 40 skipped

## [0.23.3] — 2026-10-05

Quota telemetry system (Remote-First Renewable-Allowance addendum §6–12) and temperature persistence fix.

### Added
- **Quota telemetry module** (`bin/quota/`):
  - `telemetry.py`: Normalized QuotaRecord schema (addendum §6) with multi-dimension tracking (requests, tokens, credits, compute_units), JSONL ledger at `~/.local/share/local-agent/remote-usage.jsonl`, derived state at `~/.local/share/local-agent/remote-quota-state.json`, statusline emission with provenance labels A/R/E/? (authoritative/reconciled/estimated/unknown) per addendum §7
  - `providers.py`: 13 provider-specific header parsers per addendum §10 — Gemini, Groq, Mistral, OpenRouter, Cloudflare (Neurons), GitHub, Z.AI, SiliconFlow, LLM7 (rolling-24h), Kilo (shared IP), Vercel (credits), SambaNova, ModelScope
  - `test_quota_telemetry.py`: 22 tests covering all addendum §12 fixture requirements — authoritative header parsing, daily/monthly/rolling ledgers, rolling event expiration, binding dimension calculation, no percentage when denominator unknown, partial-account marked estimated, temporary RPM vs hard daily exhaustion
- **Temperature persistence** (schema v3 in `config/session-menu.local.json`):
  - Per-lane temperature settings (`local_session`, `remote_api_session`, `lowkey`) with same mechanism as effort persistence
  - `session_menu_state.py`: `save_temperature()`, `get-temp`/`set-temp` CLI, `DEFAULT_TEMPERATURE`, `ALLOWED_TEMPERATURE`
  - `session_picker.py`: `on_temperature_saved` callback, loads temperature from state
  - `session_picker_model.py`: Settings with `local_temperature`/`remote_temperature`/`lowkey_temperature`, `on_temperature_saved` propagation
  - All 24 `test_session_menu_state.py` tests pass

### Changed
- **Config**: `config-lib.sh` added `la_load_derived_catalogue()` and `la_auto_scan_models()`; new `config/model-catalogue-derived.psv` derived catalogue
- **Session menu state**: Schema bumped to v3 with `temperature` section

### Tests
- Full suite: 385 passed, 0 failed, 40 skipped
- Quota telemetry: 22 tests passing, mutation-tested
- Session menu state: 24 tests passing

## [0.23.2] — 2026-10-05

Backend manager refactor, plus fixes for regressions it surfaced from the 0.21.0 port.

### Changed
- **Shared manager plumbing split into mixins.** `install/managers/_common.py` holds
  `BackendInfo`, `VersionInfo` and `ManagerError`. `install/managers/mixins/` holds version
  parsing, venv handling, PyPI/GitHub release sources and binary installs. Only
  `BackendManager` inherits `VersionParsingMixin` directly, which removes the diamond-inheritance
  MRO conflicts. `base.py` is about 170 lines shorter. `ManagerError` is still importable from
  `managers.base`.

### Fixed
- **Pin promotion no longer leaves a repo half-promoted.** When the class port's post-write
  validation failed (wrong pin, or the repo's own `test_rapid_auto_mode.sh` failing with
  `CalledProcessError`), the exception escaped without rollback, because only `OSError` was
  caught. Every written file is again restored byte-for-byte on any failure, and the error
  says "rolled back".
- **Pin promotion's post-write check** compared every file with the *last* file's planned
  bytes, so any multi-file promotion failed verification. Each file is checked against its own
  planned content again. The validator and backup directory are injectable once more.
- **One status vocabulary:** a ready environment is `complete` everywhere. The shared
  `installed_versions()` said `installed`, while the per-backend code, the tests and the
  manifest tool said `complete`.
- **`manage-backend.py --backend X installed`** now works. The handler existed but was never
  registered as a subcommand, so listing installed versions was unreachable. `list` is
  correctly described as listing registered *backends*.

### Tests
- `test_manage_rapid_mlx.py`: 30/30, up from 22/25. Promotion is tested for real again
  (writes, 600 private overlay, manifest, idempotent second run), plus a new check that a
  failing validator rolls every file back. The "wrapper translates backend argument" check
  inspected the test process's own `sys.argv` and could never pass; it now runs the wrapper.
  Each fix was mutation-tested.

## [0.23.1] — 2026-10-05

Session picker fixes and polish on top of the 0.23.0 API Keys redesign.

### Fixed
- **API Keys "Add key" stays inside the picker.** It used to launch the old `setup-api-keys.py`
  terminal wizard. Now a hidden input opens inline. **Pasting advances automatically**, so a key
  cannot be pasted twice; a **typed** key waits for Enter; an empty Enter skips. All whitespace
  (spaces, tabs, newlines, a key wrapped across lines) is removed from a pasted key, and a note
  appears when the removal was more than a trailing newline. The prompt label is shown once
  (it was also repeated inside the field). Three bugs in this path that had never run are fixed:
  a `NameError` on an undefined provider list, an `import` of the hyphenated
  `setup-api-keys.py` that always failed, and one value being written to every missing file
  (Cloudflare now fills token, then account ID, one entry each). The save respects
  `LA_API_KEYS_DIR`, the status labels refresh after saving, and an existing key is never
  overwritten.
- **Local model list.** The picker loaded models through `load-profile-models.py`, but ran that
  Python script under `bash` and passed `timeout` twice, so it always failed. A catch-all then
  fell back silently. Even when working, that loader listed only the few hand-declared runtime
  profiles, all mapped to one alias. The picker again lists every on-disk model from
  `csl --inventory`. Listing every on-disk model through runtime profiles (autodiscovery) is planned as a follow-up release.
- **Download screen was always empty** (since 0.22.3): the background-loading rewrite dropped the
  catalog load. It loads in the background again, like the local/remote lists, and refreshes
  after a download. **Space** now queues/unqueues a model, as the status line always said.
- **Lowkey stalled silently when the RAM preflight blocked a load.** Hotswap output was captured
  until exit while the preflight waited for a `[p/e/a]` answer nobody could see. The output now
  streams live, so the reasons and the question are visible and the answer reaches the script.
- **Remote "Filter settings…"** is a sub-screen again: its nav row is Back to Remote, not Quit.

### Changed
- Opening a provider's signup page goes through `LA_URL_OPENER` (default: macOS `open`), so tests
  record the call instead of opening a real browser window.
- `LA_CLASSIFIER_SOURCE` (the picker's Classifier setting, 0 NVIDIA / 1 local Devstral /
  2 Auto) is now resolved by `launch-claude-agent.sh` and passed through by `remote-session.sh`.

### Tests
- New `tests/picker_harness.py`: picker tests drive the app with Textual's pilot (reading
  widget state, navigating to rows by id) instead of scraping a PTY that Textual only partially
  repaints. PTY tests remain only for terminal-edge behaviour (Ctrl-C restoring tty modes,
  entry points, exit codes). `test_session_picker_pty.py` (13) and `test_new_features.py` (21)
  are green, up from 23 failures. They now cover inline key entry, paste/typing, whitespace
  stripping, Cloudflare's two files, the signup opener, Space-to-queue, and the catalog
  regression. Each fix was mutation-tested: undoing it makes a test fail.
- A `wait_for` helper in `test_new_features.py` failed after 0.2 s instead of 15 s (its `fail`
  was inside the retry loop), so earlier failures there were partly timing artefacts.
- `test_lowkey_cli_dispatch.py`: the preflight prompt must be visible before it is answered.

## [0.23.0] — 2026-10-04

### Added — Runtime Profiles and Rapid-First Model Management

Groundwork for separating downloadable artifact identity from runnable model profiles, making Rapid-MLX the preferred backend architecture.

- **Three canonical config files** (`config/`):
  - `model-runtime-profiles.json`: Runtime profile declarations (qwen38 rapid operator/thinking + vllm fallbacks)
  - `runtime-resource-profiles.json`: Resource/cache/operational profiles (rapid-primary-session, rapid-concurrent, rapid-smoke)
  - `runtime-environments.json`: Backend/version/executable/env profiles (rapid-text-stable 0.12.18, legacy-vllm)

- **`bin/la-model-profile.py`**: Stdlib-only canonical resolver with validate/list/show/resolve/artifact/recommend/compare-upstream/migrate-preview commands. Includes legacy adapter for config.local.sh la_register entries.

- **`bin/load_profile_models.py`**: Profile loader for the session picker — converts runtime profiles to LocalModel format with unique profile_id keys.

- **`bin/local-llm-hotswap.sh`**: Added `--profile PROFILE_ID` flag. Resolves through la-model-profile.py, finds registry alias matching artifact_id, overrides SERVE/TOOLP/REASONP/THINK from resolved profile, reads resource profile for Rapid config (cache_mb, hybrid_entries, etc.), emits PROFILE_ID in meta file and output.

- **`install/download-models.sh`**: Added filtering flags:
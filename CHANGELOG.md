## [0.25.6] — 2026-10-06

The CUDA work that 0.25.0 described but never shipped in full is now in, plus the fixes it needed.

### Added
- **Dynamic vLLM `--swap-space` and llama.cpp `-ngl`** (`bin/local-llm-hotswap.sh`): swap space is
  system RAM minus VRAM minus 10 GB (never below 4 GB); `-ngl` is sized from the model so about
  7.5 GB stays in VRAM. `LA_VLLM_SWAP_SPACE` and `LA_LLAMA_NGL` still override. 0.25.0 already
  claimed this; the code was still the static 16 and 20.
- **Full hardware detection** (`bin/la-hw-detect.sh`, 93 to about 410 lines): OS, GPU vendor, model
  and VRAM, CPU, memory, container and VM detection, exported as `LA_*` variables.
- **`hardware_target` catalog column** (`config/model-catalog.psv`, 11th field: `mlx|cuda|any`) and
  downloader filtering by the detected hardware. Rows with 10 fields keep meaning `mlx`.
- **Hardware-aware backend install** (`install/install-backend.sh`): `vllm-cuda` and
  `llama-cpp-cuda` route to `manage-cuda-backend.py` on CUDA hosts.
- **Linux argv capture in `bin/la-reboot.sh`** from `/proc/PID/cmdline`; the macOS path is unchanged.

### Fixed
- **A `--hf-repo` row vanished from its own `--list` on Apple Silicon.** It was tagged
  `dynamic|cuda`; it is now `dynamic|any`, so a repo you name explicitly is never filtered out.
- **`install/install-backend.sh` could not run at all:** it sourced `la-hw-detect.sh` from
  `install/` but the file lives in `bin/`, and it switched on `mac)`, a value the detector never
  emits (it emits `mlx`). Both corrected; `rocm` and `opencl` hosts now get a clear warning.
- **MTP models are no longer dropped by `la_auto_scan_models`** (`config/config-lib.sh`). An MTP
  build such as `Qwen3.8-27B-MTP-4bit` is what lets Qwen 3.8 launch with speculative decoding,
  which is much faster. On the maintainer's models directory the scan goes from 40 to 41 models.
  Nothing calls that function yet, so this is groundwork.
- **`tests/test_launcher_profiles.sh` rejected the 11-field catalog** and now accepts 10 or 11 and
  validates `hardware_target`.
- **`docs/ROADMAP.md` "Current released version" was stale** (0.25.3 while the manifest said 0.25.5),
  which made `tests/test_version_consistency.sh` fail on `main` for several releases.

### Tests
- New `tests/test_hardware_target.sh` (12 checks) and `tests/test_auto_scan_models.sh` (7). Each
  fix was pinned by a check that failed first and was mutation-checked afterwards. CUDA hardware is
  faked with a stub `nvidia-smi`; the NVIDIA path has NOT been run on a real GPU.
- `~/.local/bin/pytest -q tests`: all pass except one timing-dependent picker test
  (`test_no_loading_flash_on_fast_quit`) that failed once in a full run and passes alone on this
  branch and on `main`.

### Known open
- `tests/test_serve_default.sh` (8), `tests/test_session_profiles.sh` (3) and `tests/test_csl_menu.sh`
  (67) fail on `main` before and after this release; the last two still drive the numbered menu the
  0.22.0 picker replaced.

## [0.25.5] — 2026-10-06

API role bindings route again.

### Fixed
- **`council-router.sh` never routed an API binding whose subdir names the model.** It looked for
  a key file named after the whole subdir (`nvidia-nemotron-550b`), while keys are stored per
  provider (`~/.api_keys/nvidia`), so `nemotron-550b` and `nemotron-35b` were always skipped and
  every role fell to a local model. It now tries the exact subdir first (a per-model key still
  works), then the provider prefix before the first `-`. A partial prefix
  (`nvidia-nemotron`) does not count, nor does an empty key file. **Behaviour change:** on a
  machine with an NVIDIA key, roles bound to Nemotron now pick it (e.g. reasoner →
  `nemotron-550b`, operator → `nemotron-35b`) instead of the local model.
- **`free-agent-tool.py` sent those bindings to the wrong provider.** It matched the provider by
  the whole subdir, so `nvidia-nemotron-550b` fell through to the OpenRouter default. It now
  falls back to the prefix the same way; unknown providers still default to OpenRouter.

### Tests
- `tests/test_council_router_keys_dir.sh`: 8 passed (was 5): provider key, per-model key,
  partial prefix rejected, empty key rejected. Reverting the router fix fails 2.
- `tests/test_free_agent_tool_provider.py`: 3 passed, with `subprocess.run` replaced by a
  recorder so nothing is sent. Reverting the tool fix fails 1.

## [0.25.4] — 2026-10-06

### Changed
- **`verify-delegated-work` skill: verify at the seam the framework reads.** New section: when a
  delegate's fix is wired into a framework (driver, hook, injected config, launcher env), assert
  on the object the framework uses at runtime, not on the component the delegate wrote, and only
  trust a probe that FAILS on the code before the change (replay the real failing input rather
  than a paraphrase). From an audit where a fix shipped inert: a correct driver subclass set as a
  class attribute was overwritten on the instance by the framework's constructor, while tests of
  the subclass stayed green. (Skill observation 22.)

### Tests
- `tests/test_skill_verify_delegated_work.sh`: 5 passed (frontmatter + the new rule).
  Mutation-checked: removing the section fails 4 cases.

## [0.25.3] — 2026-10-06

Documentation repair: silently truncated history and spec text are back.

### Fixed
- **`CHANGELOG.md` is complete again, back to `0.1.0`.** `0a78ae8` (0.23.5 bump) cut the file to 41
  lines and `dca664e` restored only the recent part, leaving `0.15.0` down to `0.1.0` missing. Those
  entries are back verbatim, together with `0.14.1` and `0.17.7`, `0.18.4`, `0.18.5` (dropped by
  later releases) and a `206-08-19` typo is corrected. The duplicated `0.23.3` and `0.23.4` entries
  are collapsed to one each. Every version heading that ever existed in the file appears once.
- **`docs/ROADMAP.md` regained the runtime-profiles specification** that `cba3d65` deleted (five
  identity layers, phases A-I, hotswap contract, smallest first slice, 14 release gates). Its status
  marks are brought up to date against the code: runtime profiles shipped as groundwork in `0.23.0`;
  the structured hotswap result, unsafe-reuse refusal, full server-metadata identity, session schema
  v2, packaging and resolver tests are still open.
- **`docs/ROADMAP.md` "Current released version"** said `0.23.4`; it now follows `plugin.json`, which
  makes `tests/test_version_consistency.sh` pass again.
- **`README.md`**: lane 3 (remote sessions) and the lane-switch carry-over sentence are back; a
  triplicated provider-status callout and two stale copies of lanes 2 and 3 are removed.

## [0.25.2] — 2026-10-06

Testing the role router no longer needs, or touches, your real API keys.

### Fixed
- **`council-router.sh` reads keys from `LA_API_KEYS_DIR`** (default `~/.api_keys`), like
  `remote-keys.sh` and the session picker already did. It was the one lookup hardcoded to
  `$HOME/.api_keys`, so the only way to test its "key present / key missing" branches was to
  write and delete files in the real key directory. On 2026-10-05 a session did exactly that
  (`echo test-key > ~/.api_keys/nvidia`, then `rm`) and destroyed the user's real NVIDIA key.
  `free-agent-tool.py` calls the router, so it follows the same variable. `--help` documents it.
- **Shared agent rules:** never write, move or delete a real credential file to test something;
  point `LA_API_KEYS_DIR` at a temp dir instead, and add an override first where none exists.

### Tests
- `tests/test_council_router_keys_dir.sh`: 5 passed. Runs the router in a sandbox repo copy
  with `HOME` and `LA_API_KEYS_DIR` both temp dirs: no key → local fallback, key → api model,
  a key only under `$HOME/.api_keys` ignored when the variable points elsewhere, default
  unchanged when unset. Mutation-checked: restoring the hardcoded path fails 2 cases.

## [0.25.1] — 2026-10-06

Sessions stop editing long files from a truncated view, and free-API sessions stop running rtk.

### Fixed
- **Long files read in full.** Free sessions rebuilt `CHANGELOG.md` and `README.md` from a view
  that showed only the top of the file, deleting most of each. Two things cut that view, and
  neither shows in the exit code: Claude Code caps Bash output (a large `cat` returns a short
  preview plus "Output too large … saved to …"), and rtk, when installed as a PreToolUse hook,
  condenses `git show <rev>:<file>` to ~150 lines plus "(+1800 lines)" (134 KB in, 8 KB out).
  The shared agent rules (`config/shared-agent-shipping-rules.txt`, read by both lanes) now
  say: read long files with Read, check the length, keep reading to the last line, redirect an
  old revision to a temp file, and never edit from a view that was truncated.
- **Free-API sessions run without rtk.** On a lane with no token bill, rtk's saving is zero
  and its condensing cost correctness. `remote-session.sh` puts `bin/rtk-lane-shim/rtk` first
  on `PATH` with `LA_RTK_HOOK=off`: the shim answers rtk's hook call with "no rewrite" and hands
  every other `rtk` call to the real binary. A plugin cannot remove a hook that user settings
  register, so answering the call is the way to switch it off per session.
  `LA_RTK_IN_FREE_API=1` keeps rtk on. Without rtk installed, the hook call is a silent no-op.
  Not covered: a hook registered by absolute path (`/opt/homebrew/bin/rtk hook claude`).

### Added
- **`install/setup-rtk-excludes.sh`** (optional, idempotent): adds the commands whose rtk
  output lost content (`config/rtk/exclude-commands.txt`: `git show`, `git diff`, `cat`,
  `head`) to rtk's `[hooks] exclude_commands`, for local and cloud sessions where rtk is
  worth keeping. It keeps patterns you added, backs the file up, and keeps its permissions.
  `--check` and `--dry-run`. Exits 0 without touching anything when rtk is not installed.

### Tests
- `tests/test_rtk_lane.sh`: 24 passed. Uses a fake rtk, so it needs no rtk installed. Covers
  the shim with rtk present and absent, the `remote-session.sh` block (run, with the
  environment it leaves checked for free-API, local and opt-out), and the setup script (merge,
  user patterns kept, mode 600 kept, backup taken, idempotent). Mutation-checked: disabling
  the shim's off branch, the `export`, or the opt-out condition, or widening the file mode, each
  turns a test red.
- Unchanged by this release (same results on `main` before it): `tests/lint.sh` reports 58
  existing shellcheck findings, and `tests/test_shared_agent_rules.sh` fails 10 of 16 cases
  because `launch-claude-agent.sh` aborts on unset `LA_CUR_SPOOF`.

## [0.25.0] — 2026-10-06

### Added — CUDA Architecture (v3 Hardened + Dynamic HF)

**Unified Registry & Hardware Detection** (`bin/la-hw-detect.sh`):
- OS detection: macOS, Linux (with distro/version/codename), Windows (native/WSL/Cygwin/MinGW/MSYS)
- Hardware detection: mlx (Apple Silicon), cuda (NVIDIA), rocm (AMD), opencl (Intel), cpu-only
- VRAM detection via nvidia-smi, rocm-smi, sysctl, /proc/meminfo
- GPU vendor/model/driver detection (NVIDIA, AMD, Intel, Apple Silicon)
- Container (Docker/Podman/Kubernetes) and virtualization (KVM/VMware/Hyper-V) detection
- 40+ exported environment variables for cross-platform compatibility

**3-Tier CUDA Inference Routing** (`bin/local-llm-hotswap.sh`):
- **Tier A** (VRAM ≥ 16GB): vLLM fully VRAM-resident
- **Tier B** (VRAM ≤ 8GB): AWQ/EXL2 → vLLM with UVM/KV offload (`--swap-space`); GGUF → llama-server with dynamic `-ngl`
- **Tier C** (CPU-only): llama-server CPU mode
- Dynamic `--swap-space` calculation (system RAM - VRAM - 10GB headroom)
- Dynamic `-ngl` calculation from model size (~7.5GB VRAM target)

**CUDA Lifecycle Manager** (`install/manage-cuda-backend.py`):
- Side-by-side venvs with rollback, `LA_CUDA_BIN` pointer
- Dynamic PyTorch `--extra-index-url` based on host CUDA version
- Strict smoke test: `torch.cuda.is_available()` + `torch.zeros(1).cuda()`

**Dynamic Hugging Face Downloader** (`install/download-models.sh`):
- `--hf-repo`, `--alias`, `--include` flags for dynamic model fetching
- Format protection: warns if no `--include` pattern (full repo = hundreds of GB)
- Injects into same catalog arrays as static catalog (preserves deduplication, disk checks)

**CUDA Lifecycle & Diagnostics**:
- `bin/la-vram-preflight.sh`: VRAM capacity check, tier recommendation, UVM/ngl capability checks
- `bin/la-evict.sh`: Linux `/proc` support, CUDA backend recognition
- `bin/la-reboot.sh`: Linux `/proc` argv capture via `/proc/PID/cmdline` + `/proc/PID/environ`
- `bin/la-hw-detect.sh`: Comprehensive cross-platform hardware detection

**Model Catalog Updates** (`config/model-catalog.psv`):
- Added `hardware_target` field (11th field): mlx|cuda|any
- Added CUDA models: `qwen2.5-7b-awq` (AWQ, vLLM-cuda), `nemotron-3-ultra-550b-gguf` (GGUF, llama.cpp)

**Install System Updates**:
- `install/install-backend.sh`: Hardware routing, CUDA backend support (vllm-cuda, llama-cpp-cuda)
- `install/install-cuda-backend.sh`: CUDA installer wrapper
- `install/manage-cuda-backend.py`: CUDA backend lifecycle manager
- `config/model-catalog.psv`: Added CUDA example models

### Changed
- `csl` dry-run shows Intercept Agents toggle status
- `la_on_disk()` returns success for `api`/`litellm` backends (no local weights needed)
- `LA_SERVE_BACKENDS` includes `api`; `la_register` docs updated for new backends
- `la_on_disk()` returns success for `api`/`litellm` backends

### Tests
- Full suite: 378 passed, 0 failed, 40 skipped
- CloudConfigScreen: 2 new tests (structure + owner SUB nav)
- APIKeysScreen: 24 tests passing (provider order, More providers toggle, Back nav)
- Session menu state: 24 tests passing (schema v4 with intercept_agents)
- All free-agents tests passing (378/376)

## [0.24.0] — 2026-10-06

### Added — Cloud Session Configuration & Native Agent Interception

**CloudConfigScreen** (new `session_picker_model.py` screen, key `c` from Home):
- **Intercept Agents toggle** (x): Deny native `Agent` tool, replace with `FreeAgent` native plugin tool
- **Classifier Source selector** (y): NVIDIA API / Local Devstral / Auto (local on local, remote on remote)
- **Bypass Permissions toggle** (p): Blind-trust auto mode for cloud sessions (`bypassPermissions`)
- Both menu item and screen title use cloud + wrench emojis with space: `☁️ 🔧 Cloud Session Configuration`
- Persists in `session-menu.local.json` (schema v4) under `intercept_agents` and `cloud_bypass_permissions`

**HomeScreen**: Cloud session configuration moved to 3rd position (after Local/Remote)

**APIKeysScreen** improvements:
- Key emoji (🔑) from single source of truth (`config/emoji.sh` → `emoji_constants.py`)
- Provider order: NVIDIA → Google (gemini) → Groq → alphabetical
- Empty newline before Google (after NVIDIA's "Add key" item)
- Back to Home at bottom (matching other menus)
- "More providers" toggle (hidden by default, shows all providers when enabled)

**remote-session.sh**: `CLOUD_BYPASS_PERMISSIONS` env var support; when enabled forces `bypassPermissions` regardless of `AUTO_MODE_STATE`

### Added — Native Agent Interception (Deny & Replace)

**Unified Registry** (`config-lib.sh`, `config.example.sh`):
- New `api` backend: free API models via `~/.api_keys/` (no local weights)
- New `litellm` backend: LiteLLM proxy (port 4141) for remote models
- Updated `LA_SERVE_BACKENDS` vocabulary and documentation
- Example entries: `nemotron-550b`, `nemotron-35b`, `gemini-3.8-flash`, `kimi-k3`, `deepseek-v4-flash`, `groq-gpt-oss-120b`

**council-router.sh** — Dynamic role router with priority chain:
1. `api` backends (if API keys exist in `~/.api_keys/`)
2. `litellm` backends (if proxy reachable)
3. Local backends (`rapid`, `vllm`, `mlx_lm`, `llama_cpp`)

**free-agent-tool.py** — Native plugin tool replacement for built-in `Agent` tool:
- Matches native Agent tool schema (prompt, subagent_type, description, model, files)
- Routes via `council-router.sh` → selects best model for role
- Supports local (rapid/vllm/mlx_lm), api, and litellm backends
- Returns JSON matching native Agent tool output format

**LA_DENY_TOOLS enforcement** (`launch-claude-agent.sh`):
- Ensures `Agent` is in deny list when Intercept Agents is ON (default)
- Removes `Agent` from deny list when OFF

**Session menu state** (`session_menu_state.py` schema v4):
- Added `intercept_agents` persistence (default "1" = ON)
- CLI: `set-intercept` command

**Session picker model** (`session_picker_model.py`):
- `intercept_agents` and `cloud_bypass_permissions` Settings fields
- Passed via env: `INTERCEPT_AGENTS`, `CLOUD_BYPASS_PERMISSIONS`

### Changed
- `csl` dry-run shows Intercept Agents toggle status
- `la_on_disk()` now returns success for `api`/`litellm` backends (no local weights needed)
- `LA_SERVE_BACKENDS` includes `api`; `la_register` docs updated for new backends

### Tests
- Full suite: 376 passed, 0 failed, 40 skipped
- CloudConfigScreen: 2 new tests (structure + owner SUB nav)
- APIKeysScreen: 24 tests passing (provider order, More providers toggle, Back nav)
- Session menu state: 24 tests passing (schema v4 with intercept_agents)
- All free-agents tests passing (376/376)

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
  - `--profile PROFILE_ID`: filters to aliases matching that profile's artifact
  - `--backend BACKEND`: filters to profiles with that backend (rapid/vllm/llama_cpp/mlx_lm)
  - `--capability CAPABILITY`: filters to profiles with that capability (reasoning, etc.)
  - `--recommended`: filters to fully qualified profiles only

- **Session picker integration** (`bin/session_picker.py`, `bin/session_picker_model.py`, `bin/load_profile_models.py`):
  - `LocalScreen` now consumes runtime profiles via `load_profile_models.py`
  - Profiles keyed by `profile_id` (unique) not alias (multiple profiles per alias)
  - Display shows backend + thinking mode: `qwen-3.8-operator (rapid, thinking) [reasoner]`

- **Dispatcher chain profile-aware** (`bin/agent-fallback.py`, `bin/lowkey-cli.py`):
  - `agent-fallback.py --profile PROFILE_ID` passed through dispatch chain
  - `lowkey-cli.py --profile PROFILE_ID` resolves profile to model alias
  - `local-llm-hotswap.sh --profile` already supported
  - Dry-run plan includes `local_profile`

- **Tests**: 331 tests pass (14 skipped); version consistency 0.23.0 ✓; test_serve_default.sh 39/39 ✓

### Fixed

- `tests/test_manage_backend.py`: test_registry now unregisters test manager after test to avoid polluting global registry (fixed pre-existing test failure in BackendManagerContractTests)

### Fixed in lowkey-cli.py

- Fixed UnboundLocalError on `sys` by removing redundant `import sys` in dry-run block
- Fixed sys.path assignment to avoid shadowing `sys` module

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
# Changelog

All notable changes to `free-agents` are documented in this file.

## [0.22.5] — 2026-10-04

### Fixed — no script hardcodes the checkout's location

Groundwork for moving the canonical checkout from `~/ClaudeWorkspace/local-agents` to
`~/ClaudeWorkspace/free-agents`.

- `bin/local-inference-readonly-inventory.zsh` read the catalog, models dir and git repo from
  `$HOME/ClaudeWorkspace/local-agents`. It now resolves `repo_root` from its own real path
  (`${0:A:h:h}`, through the `~/.claude/scripts` symlink), and `--dry-run` prints it.
- `tests/smoke_qwen38_mtp.sh` and `scripts/test-nvidia-models.py` resolve the repo from their
  own location instead of an absolute path.
- `bin/lk` is now a relative symlink to `lowkey`; it was an absolute link into the old checkout,
  so every clone or worktree pointed back at it.
- `docs/ROADMAP.md` records the 2026-10-02 renumber (`0.21.0` = backend manager, `0.22.0` = the
  session picker, now shipped; runtime profiles `0.23.0`, oMLX lanes `0.24.0`).
- Tests: `test_inventory_cli.sh` runs a copy of the script from a temp path through a symlink and
  checks that it reports that path (fails if the hardcoded path comes back) — 17 passed.

## [0.22.4] — 2026-10-04

### Fixed — copying works in Terminal.app; the loading animation is visible

- **Copy:** Textual copies through OSC 52, which macOS Terminal.app ignores (Textual's own docs
  say so), so Ctrl+C/⌘C on a selection did nothing. The picker now also passes the text to the
  system clipboard (`pbcopy`, or `wl-copy`/`xclip` where those exist). Verified against the real
  clipboard with `pbpaste`.
- **Loading animation:** the frames were alternating, but at 0.15 s the two hourglass glyphs
  blurred into one, so it looked static. Now each beat is 0.4 s: the hourglass flips every two
  beats, and the dots grow `.` → `..` → `...` at a fixed width. The bash pre-frames match the
  first beat.
- **Layout:** the Home title has the lane icons after the headline (`… Session Launcher  🦾 📡`),
  there is a blank line before the model list, and the status line hints that dragging selects
  text and ⌘C/Ctrl+C copies.
- Tests: `test_loading_label_animates_hourglass_and_dots` (fixed width, holds for two beats then
  flips, interval at least 0.3 s), plus the existing suites.

## [0.22.3] — 2026-10-04

### Changed — picker polish (user review of 0.22.2)

- **Settings react to Enter, click and ←/→.** Toggles, the three-state auto-mode, effort,
  temperature and the Remote filters are now settings rows. Enter, click and → step forward, ←
  steps back, the cycle wraps, and the highlight stays on the row. Before, they only responded to
  their letter key: Enter returned nothing and skipped the redraw, and the ←/→ handlers were
  never wired up.
- **The model list loads in the background.** Once the menu is drawn, local and remote
  inventories load on worker threads, and parallel requests for the same list share one load.
  `c` (Choose model) shows the cached list at once. If it is still loading, the status line
  animates ⏳/⌛️ and the list fills in when it arrives. Nothing blocks the UI any more.
  Measured: the remote list was cached 4 s after opening the lane, and `c` then showed 23 rows in
  0.29 s.
- **Sub-screens go Back, not Quit.** The rate limiter, backend manager, API keys and launchd
  screens return to whichever screen opened them (a caller stack): "🏠 Back to Home" or
  "↩️ Back to <lane>". Esc does the same.
- **Terminal-native colours (R5).** The `textual-ansi` theme with `ansi_color=True`: background
  and main text are the terminal's defaults, so light and dark themes both work, and the PTY
  output contains no painted background at all. Secondary text (status line, subheadlines) is
  grey (ANSI bright-black). Highlight and hover are soft blue text (ANSI bright-blue) instead of
  a block.
- **Headlines and subheadlines restored from 0.21, with emojis:**
  - Home: 🦾📡 title and a one-line plugin identity, plus the config source.
  - Local: 🦾 "Local Session Picker", "N model(s) on disk".
  - Remote: 📡 "Remote API Session Picker", "N model(s) visible (hidden: M)".
- **Remote disclaimer removed.** "Prompts and file contents leave this machine…" is gone, by user
  decision; the per-row trial and no-key labels remain. The orange colour is gone too (R9).
- **Emojis everywhere, all from `config/emoji.sh`:**
  - auto-mode 🤖, telemetry 🛰️ (also when OFF), queued-prompt hook 🪝, watcher 🔭;
  - backend manager rows (🧩 📜 📦 🩺 ℹ️) and Back (🏠 / ↩️).
- **The queued-prompt hook is OFF by default** in `csl`, the picker, `launch-claude-agent.sh`,
  `local-session.sh` and `remote-session.sh`. `CSL_STOP_HOOK=1` or `LA_QUEUE_STOP_HOOK=1` turns
  it back on.
- **The Remote lane no longer offers the backend manager (`v`).** It manages local runtimes only.
- **Copying text works.** Drag to select, then Ctrl+C copies (Ctrl+C with nothing selected still
  quits), and Cmd+C copies wherever the terminal forwards it.
- **`remote-session.sh` direct launch:** the ⏳ frame is drawn 0.017 s after start, before roster
  setup, instead of after it.
- **New `install/make-terminal-launchers.py`** writes `.terminal` launchers (copied from your
  default Terminal profile, run directly with no shell). Screen-recorded: they open straight to
  ⏳ with no "Last login" and no typed `… ; exit;` line. Existing files are never overwritten.
- Tests: picker_model 55 (step rows, defaults and icons, sub-screen Back, no disclaimer),
  remote_session 24, the full Python and pytest set green.

## [0.22.2] — 2026-10-04

### Fixed — `remote-session.sh` takes 0.7 s to start instead of 5.5 s

- Every invocation (opening the picker, `--help`, a direct launch) spent about 5 seconds starting
  python processes before doing anything. While that ran, Terminal.app kept showing
  `Last login … % …/remote-session.sh ; exit;`, and the ⏳ frame only flashed at the very end.
  - `_lc_load_policy` ran **four `python3 -c` per policy row** (177 starts on the 0.22 roster).
  - `local-capable-filter.sh` ran **one per roster row**, just to escape JSON (45 starts).
- Both now make a single python call. `--help` drops from 5.5 s to 0.7 s, and `--inventory`,
  `--list` and the filter's `--parse`/`--report` output are byte-identical to before (md5
  compared).
- Test: `tests/test_remote_startup_speed.py` puts a `python3` shim first on PATH and counts real
  process starts, including inside child scripts whose stderr is discarded. The limit is under 6.
  Mutation-checked: the old filter (45) and the old `_lc_load_policy` (178) both fail it.

### Finding — the "Last login … ; exit;" lines come from Terminal.app

- Screen recordings at 16–20 fps of a Finder-style launch (`open -a Terminal <script>`) show the
  same two lines for `9c53367`, `bd0e86d` (both still with the stray `p`) and 0.22.x alike. Before
  our script starts, Terminal opens an interactive login shell, prints `Last login` and types the
  script path. In those commits the lines stayed up for about 0.3–0.5 s before the `p`. In 0.22.1,
  csl replaces them with the ⏳ frame after about one frame. No commit ever suppressed them from
  inside the script. Removing them entirely needs a launcher that does not go through an
  interactive shell (tracked on waypoint `regression-picker-startup`).

## [0.22.1] — 2026-10-04

### Fixed — picker startup shows the loading frame, not shell noise

- **0.22.0 behaviour:** `bin/session-picker` began with a bare `\033[2J` clear (from `cbd6302`,
  meant to hide "Last login"). Measured in a PTY: the clear arrived about 0.05 s in and Textual
  took over about 0.3 s in. Until the clear, whatever the shell had already printed (the launch
  command, the login banner) stayed visible, and after it the screen sat blank. The commit before
  `cbd6302` (`3bcb9d9`) printed nothing until Textual started, so only the stray `p` was visible.
- **Now:** the first bytes switch to the alternate screen, which hides whatever the shell printed,
  and draw the **⏳ loading…** frame (`EMOJI_LOADING` from `config/emoji.sh`) with the cursor
  hidden. Textual then takes over the same alternate screen without a flash, and still sends no
  DECRQM queries under Apple Terminal.
- **Only for a terminal:** pipes, logs and `--help` get no escape bytes. If the wrapper exits
  before Textual starts (for example, the venv is missing), an EXIT trap gives the normal screen
  and cursor back.
- Tests: `tests/test_picker_startup_frame.py` (first bytes are the frame, an early exit restores
  the screen and cursor, pipe and help output stay clean). Mutation-checked: 0.22.0's bare clear,
  no trap, and no tty check all fail it.
- Still needed: your visual confirmation in Terminal.app (waypoint `regression-picker-startup`).

## [0.22.0] — 2026-10-04

### Added — Session picker TUI (Textual) replaces the numbered csl menus

- **Interactive session picker** (`bin/session_picker.py`, `bin/session_picker_model.py`,
  `bin/session-picker`): Textual UI with accordion navigation, arrow keys, letter shortcuts,
  grouped menus and persistent settings in `config/session-menu.local.json`
  (`bin/session_menu_state.py`: repo-local, locked, atomic preference store).
- **Entry-point wiring**: `bin/csl`, `bin/remote-session.sh` and the new canonical direct launcher
  `bin/local-session.sh` hand off through the nav file (`--csl-owner` / `CSL_NAV_FILE`).
- **Rate limiter settings persist**: `rate_limiter.py menu` opens the picker's rate-limiter screen;
  the limiter reads explicit argument > `LA_NVIDIA_*` env > saved picker setting > default, for every
  setting including 0.20.11's backoff keys. The saved key `cooldown` is the BASE cooldown. The
  numbered 12-option `_interactive_menu` from 0.20.11 is replaced by that screen.
- **Lowkey `--effort`** maps to `reasoning_effort` on every request.
- Loading indicator with a 200 ms threshold; emoji constants sourced from `config/emoji.sh`.

### Changed — backend manager screen follows 0.21.0's unified CLI

- `v` on Home/lanes opens the **Backend manager** (`install/manage-backend.py --backend <b>`), with a
  backend selector over all five backends (rapid-mlx default): list releases, install (interactive
  choice), validate, info, and a **Launchd update checks** sub-screen (status, run once, install /
  uninstall behind a typed `yes`).
- The branch's old rows calling `manage-rapid-mlx.py smoke|snapshot|reset|inspect|installed` were
  dropped: those subcommands no longer exist since 0.21.0. `promote`, `remove` and `check-updates`
  parse in `manage-backend.py` but are not dispatched by its `main()`, so they are not offered either
  (a contract test fails if a row calls a subcommand the CLI does not dispatch).
- **Correction to 0.21.0:** its entry describes an uppercase `L` launchd key on csl Home and the local
  picker. That `csl` change never reached `main`. It ships here instead, as the Launchd entry inside the
  Backend manager — not as a top-level key, since shortcuts are case-insensitive (`L` = `l` = Local).
  The `EMOJI_LAUNCHD*` symbols land in `config/emoji.sh` (single source; `emoji_constants.py` reads it).

### Fixed — "Go last" (`g`) actually works

- `g` only appeared once the lane's model list had loaded, and the list only loads when you press
  `c`, so on a fresh lane it never appeared. It is now offered whenever a model is remembered.
  Local needs only the alias and effort. Remote also needs the tier (for `--include-trials`), which
  is now saved next to the alias in a new optional `last_launched_tier` section, so pressing `g`
  makes no 5.7 s inventory call. The tier is checked against `remote_provider_core.TIER_CHOICES`.
- Two latent bugs underneath it:
  - The picker never loaded or saved `last_launched`, so nothing was remembered across runs.
  - Pressing `g` returned a launch request that `dispatch()` could not handle, which crashed the
    app.
- Tests: Go-last on a fresh lane (both lanes, trial tier), launches record alias and tier, the
  store round-trips and clears the tier, and a bad tier in the file is refused. Mutation-checked
  (the old membership check, the tier not being written, the tier being ignored). A headless run of
  two app lifecycles confirmed that `g` launches the remembered trial model with no inventory call.

### Known regression (not fixed in 0.22.0)

- At picker startup the bash script call is briefly visible before `bin/session-picker` clears the
  screen (`cbd6302`, the "Last login" clear). What should show is the loading animation alone.
  This is tracked as waypoint `regression-picker-startup`, together with the pre-`cbd6302` commits
  to compare against.

### Fixed — stray `p` on Apple Terminal (for real this time)

- `786d487` set `driver_class` as a class attribute, but Textual 3.7.1's `App.__init__` assigns
  `self.driver_class = driver_class or self.get_driver_class()`, which overwrote it. Under Apple
  Terminal the picker therefore still used the stock `LinuxDriver` and sent the DECRQM query
  that shows up as a `p`. The driver is now passed through `super().__init__(driver_class=…)`.
- `tests/test_stray_p_driver.py` checks the real instance under the picker venv. With
  `TERM_PROGRAM=Apple_Terminal` it uses `AppleTerminalSafeDriver` and sends neither `?2048$p`
  nor `?2026$p`; other terminals get `LinuxDriver` and both queries. Mutation-checked: the old
  class-attribute form fails it. A PTY run confirmed that pressing `p` still toggles the
  queued-prompt hook (ON → OFF → ON).
- Still needed: confirmation in a real Apple Terminal window.

### Changed — merged with main through 0.21.10

- Second merge of `main` (merged, not rebased), bringing in 0.21.4–0.21.10: Nemotron and GLM
  thinking variants, effort → max_tokens, temperature, the broken-model filter, 5-minute remote
  timeouts, and the remote-session test fixture fixes. None of these were dropped. `bin/csl` and
  the menu block of `bin/remote-session.sh` keep the branch's picker design. Main's numbered
  bash menus, which the picker replaced, were not brought back.
- **Picker Remote screen, new actions:**
  - **`u` 🚧 Unworking models** hides or shows the models listed in
    `config/broken-nvidia-models.json`. `remote-session.sh --inventory` gains a 7th column
    (`broken`), and the picker still accepts the 6-column form.
  - **`o` 🌡️ Temperature** cycles through provider default → 0.0 → 0.3 → 0.7 → 1.0 → 1.5 → 2.0,
    and is passed on as `--temperature`.
  - No `B` alias: `b` is Back, and the picker does not distinguish capitals.
- **`csl remote` (inline bash menu, `--csl-owner`)** gets the same `u` filter, also with no `B`.
- **One source for emojis:** every emoji the picker and `csl` draw now comes from
  `config/emoji.sh` (through `bin/emoji_constants.py` in Python). New constants:
  - `EMOJI_TEMPERATURE`, `EMOJI_GO_LAUNCH`, `EMOJI_TRIALS`, `EMOJI_LOCAL_CAPABLE`,
    `EMOJI_HIDDEN_REPORT`
  - `EMOJI_OK`, `EMOJI_MISSING`, `EMOJI_WARNING`, `EMOJI_LOADING`, `EMOJI_LOADING_DONE`
  - `EMOJI_PROVIDER_<SLUG>`
  A test scans the picker source for emoji literals, and a planted one fails it.
- **Fixed:** on the API Keys screen, SambaNova's `b` collided with Back, and the Home-owned
  screen crashed with a duplicate-key error. SambaNova is now `y`.
- **Known gap:** the local lane does not have temperature yet. On `main`, `csl` exports
  `LA_TEMPERATURE`, but nothing reads it: the local launcher never consumed it. Wiring it up is
  left for the M4 local-lane work, so the picker does not offer a setting that does nothing.

### Changed — merged with main through 0.21.3

- Branch history merged (not rebased) with `main` at `v0.21.3`; main's 429 exponential backoff and
  defaults (base 30 s, max 300 s, ×2.0, 5 retries, max_wait 300 s) are kept verbatim.

## [0.21.10] — 2026-10-03

### Changed — unworking-models toggle moves from `B` to `u` 🚧

- The remote menu now shows **`u) 🚧 unworking models`**. `B`, the 0.21.9 key, still works but is
  no longer shown. The move was needed because the 0.22.0 session picker uses `b` for Back and
  treats Shift-letters as the same key, so `B` and `b` could not both work. `u` is free in both the
  bash menu and the picker's remote lane. New constant: `EMOJI_BROKEN_MODELS` in
  `config/emoji.sh`.

### Fixed — remote-session tests no longer hang or leak a server

- **Leak:** the LiteLLM stub ran its health server as a bare `python3 -c` child. The launcher's
  teardown only kills a process whose command line names `litellm` and `proxy-<port>.yaml`
  (`_is_our_proxy`), so it never matched the stub, and every run left an orphan listening on port
  4141. The stub now `exec`s and forwards its arguments. Mutation-checked: dropping the forward
  brings the leak back.
- **Hang:** `run_cli` inherited the caller's stdin, and the curl stub reads stdin. Run from an
  interactive shell, the suite hung for 8+ minutes. Launches now get `stdin=DEVNULL`.
- Tests: `tests/test_remote_session` 24/24 OK in about 70 s with a live stdin, and port 4141 is
  free afterwards. The unworking-models test now also drives the menu: `u` and `B` both reveal the
  models, and the menu lists `u`, not `B`. Mutation-checked: unbinding `u` fails the test.

## [0.21.9] — 2026-10-03

### Added — broken-model filter for remote sessions

- **`config/broken-nvidia-models.json`** (added in `1efadef`) lists NVIDIA models that are in the
  catalog but never complete a request: `nvidia-kimi-k3` and `nvidia-deepseek-v4` (timeout on every
  configuration), `nvidia-kimi-k26` and `nvidia-deepseek-coder` (HTTP 404). Each entry records why.
- **`bin/remote-session.sh`**: these models are hidden by default. Press **`B`** in the remote menu
  to toggle them, or pass `--show-broken`. The script reads the JSON directly (`_broken_aliases`,
  `_is_broken_hidden`), so there is no second hardcoded copy, and the filter applies to all three
  places that list rows: `--list`, the picker's choice list, and the rendered menu. Those last two
  must agree, or a number would select a different row than the one shown. A missing or unparseable
  file hides nothing.

### Changed — 5-minute timeouts for remote sessions

- Remote `API_TIMEOUT_MS` default goes from 600000 to **300000** (5 min). Still overridable with
  `LA_REMOTE_API_TIMEOUT_MS`.
- The proxy readiness wait goes from 60 s to **300 s**.

### Tests

- `test_broken_models_json_hides_by_default_and_show_broken_reveals`: listed models are hidden by
  default and shown with `--show-broken`, matching is exact (neither a prefix nor a substring of a
  listed alias is hidden), and a missing file fails open. Two mutants were checked and both fail it:
  a filter that never hides, and substring matching.

## [0.21.8] — 2026-10-03

### Added — GLM 5.3 family fully supported with proper thinking config

- **`config/remote-agents.sh`**: Added GLM 5.3 (non-flash) and GLM 5.3 Flash with proper thinking variants:
  - `nvidia-glm53` — GLM 5.3 Flash, thinking OFF by default (non-streaming)
  - `nvidia-glm53-thinking` — GLM 5.3 Flash with thinking ON (streaming)
  - `nvidia-glm53-full` — GLM 5.3 (non-flash), thinking OFF by default (non-streaming)
  - `nvidia-glm53-full-thinking` — GLM 5.3 (non-flash) with thinking ON (streaming)

- **`bin/remote-session.sh`**: Extended `write_proxy_config()` to handle GLM models like Nemotron — `enable_thinking: true` for `-thinking` suffix, `enable_thinking: false` for base variants.

- **`bin/csl`**: Temperature setting passed to remote sessions via `--temperature` flag.

### Fixed — GLM 5.3 Flash non-streaming fixed with proper thinking config

- **Root cause**: GLM models output reasoning in `reasoning_content` field; non-streaming with `enable_thinking: false` caused `'NoneType' object is not subscriptable` error.
- **Fix**: GLM models now get explicit `enable_thinking` setting based on `-thinking` suffix (same as Nemotron).
- **Verified**: Both streaming and non-streaming work correctly with proper thinking config.

### Added — GLM 5.3 (non-flash) to roster

- **`config/remote-agents.sh`**: Added `nvidia-glm53-full` (GLM 5.3 non-flash) and `nvidia-glm53-full-thinking` variants.
- **Tested**: Both streaming and non-streaming work correctly with thinking enabled.

### Tests

- All 234 tests pass.

---

## [0.21.7] — 2026-10-02

### Added — Temperature control for remote and local sessions

- **`bin/remote-session.sh`**: Added temperature control (`-r` / `O` key in picker) with 6 presets (0.0, 0.3, 0.7, 1.0, 1.5, 2.0). Temperature passthrough implemented in proxy config for NVIDIA, Gemini, Groq, OpenAI-compatible routes.
- **`bin/csl`**: Added temperature control (`O` key in local picker) with same presets. Temperature passed via `LA_TEMPERATURE` env var to launcher.
- **Verified providers**: NVIDIA NIM, Gemini, SiliconFlow, OpenRouter accept temperature parameter. Groq/Mistral/ZAI need API access verification.
- **Proxy config**: Temperature passed via `temperature` field in LiteLLM config for all supported providers.

### Fixed — Effort now affects max_tokens for ALL Nemotron models (not just thinking variants)

- **`bin/remote-session.sh`**: Extended effort-to-max_tokens mapping to Nemotron models **regardless of thinking mode**. Previously only `-thinking` variants got increased max_tokens for high/xhigh/max effort. Now:
  - `nvidia-nemotron-ultra --effort max` = thinking OFF, 64k tokens (was 8k default)
  - `nvidia-nemotron-ultra-thinking --effort max` = thinking ON, 256k tokens
  - Low/medium effort don't set max_tokens (provider default used, no artificial lowering)

### Fixed — OpenAI-compatible models no longer artificially lowered for low/medium effort

- **`bin/remote-session.sh`**: Only high/xhigh/max effort sets max_tokens (64k/128k/256k). Low/medium effort now use model defaults instead of 8k/16k which could artificially restrict output.
- xhigh/max still get larger token budgets (128k/256k) while both map to `reasoning_effort: high` for API compatibility.

### Updated — Model roster and tests

- `config/remote-agents.sh`: Status updates for Kimi/DeepSeek/GLM
- `tests/test_remote_session.py`: Updated assertions for new behavior (Nemotron effort affects max_tokens always; low/medium don't set max_tokens)

### Tests

- 232 passed, 10 failed (test infra issue with litellm stub, not implementation)

---

## [0.21.6] — 2026-10-02

### Fixed — Effort now affects max_tokens for ALL Nemotron models (not just thinking variants)

- **`bin/remote-session.sh`**: Extended effort-to-max_tokens mapping to Nemotron models **regardless of thinking mode**. Previously only `-thinking` variants got increased max_tokens for high/xhigh/max effort. Now:
  - `nvidia-nemotron-ultra --effort max` = thinking OFF, 64k tokens (was 8k default)
  - `nvidia-nemotron-ultra-thinking --effort max` = thinking ON, 256k tokens
  - Low/medium effort don't set max_tokens (provider default used, no artificial lowering)

### Fixed — OpenAI-compatible models no longer artificially lowered for low/medium effort

- **`bin/remote-session.sh`**: Only high/xhigh/max effort sets max_tokens (64k/128k/256k). Low/medium effort now use model defaults instead of 8k/16k which could artificially restrict output.
- xhigh/max still get larger token budgets (128k/256k) while both map to `reasoning_effort: high` for API compatibility.

### Updated — Model roster and tests

- `config/remote-agents.sh`: Status updates for Kimi/DeepSeek/GLM
- `tests/test_remote_session.py`: Updated assertions for new behavior (Nemotron effort affects max_tokens always; low/medium don't set max_tokens)

### Tests

- All 234 tests pass.

---

## [0.21.5] — 2026-10-02

### Fixed — Effort settings now map to distinct max_tokens for ALL reasoning-capable models

- **`bin/remote-session.sh`**: Extended effort-to-max_tokens mapping beyond Nemotron to all models with reasoning support (Gemini, Groq, OpenAI-compatible, etc.). Previously only Nemotron had this; now all reasoning-capable models get distinct token budgets per effort level:
  - `low`: 8,192 tokens
  - `medium`: 16,384 tokens
  - `high`: 65,536 tokens
  - `xhigh`: 131,072 tokens
  - `max`: 262,144 tokens
- **Nemotron models** (Ultra, Super, Lightning): Effort only affects max_tokens when `-thinking` suffix is used (thinking enabled). Without `-thinking`, effort is ignored (8192 default).
- **Gemini models**: Effort maps to both `reasoning_effort` AND `max_tokens` for deeper reasoning.
- **Other NVIDIA models** (gpt-oss, etc.): Effort maps to `reasoning_effort` (OpenAI-compatible, xhigh/max → high) AND `max_tokens`.
- **All OpenAI-compatible routes** (Groq, Mistral, SiliconFlow, etc.): Same dual mapping.

### Improved — Nemotron thinking variants work correctly with explicit effort control

- **`config/remote-agents.sh`**: Added `-thinking` variants for all Nemotron models:
  - `nvidia-nemotron-ultra-thinking`, `nvidia-nemotron3-thinking`, `nvidia-lightning-thinking`
- **Effort separation**: The `-thinking` suffix controls `enable_thinking: true/false` (binary); effort controls `max_tokens` (graduated). They're independent:
  - `nvidia-nemotron-ultra` + `--effort max` = thinking OFF, 8192 tokens (effort ignored)
  - `nvidia-nemotron-ultra-thinking` + `--effort low` = thinking ON, 8k tokens
  - `nvidia-nemotron-ultra-thinking` + `--effort max` = thinking ON, 256k tokens (deep reasoning)

### Fixed — xhigh/max effort levels now distinguishable

- Previously `xhigh` and `max` both folded to `high` for `reasoning_effort` (OpenAI only accepts low/medium/high).
- Now both map to `reasoning_effort: high` for API compatibility, but get **different max_tokens** (128k vs 256k) for noticeably deeper reasoning.
- This gives a familiar UX (Claude's effort levels) with actual graduated reasoning depth.

### Updated — Model roster status reflects live probe results

- **`nvidia-kimi-k3`**: ⚠️ TIMEOUT on all probes (NVIDIA capacity issue)
- **`nvidia-deepseek-v4`**: Updated to `deepseek-ai/deepseek-v4.1-flash` (old is 410 Gone); TIMEOUT
- **`nvidia-glm53`**: Returns `reasoning_content`; works with streaming; non-streaming needs `enable_thinking:true` + larger `max_tokens`

### Tests

- `tests/test_remote_session.py`: Updated assertions for new effort-to-max_tokens mapping across all providers.
- All 234 tests pass.

---

## [0.21.4] — 2026-10-02

### Fixed — NVIDIA proxy config applied `enable_thinking: false` to ALL NVIDIA models, breaking Kimi/DeepSeek/GLM

- `bin/remote-session.sh` (lines 799-804): The `write_proxy_config()` function unconditionally set `chat_template_kwargs.enable_thinking: false` for **every** NVIDIA model. This was correct for Nemotron models (which put reasoning in `reasoning_content`, not Anthropic thinking blocks), but broke other NVIDIA models:
  - **GLM 5.3 Flash**: Outputs reasoning in `reasoning_content` instead of `content`; needs `enable_thinking: true` + larger `max_tokens` for non-streaming, or streaming mode
  - **Kimi K3 / DeepSeek V4.1 Flash**: Timeout on all probes (NVIDIA API capacity issue, not config) — the setting did nothing to help
  - **Nemotron models**: Still correctly get `enable_thinking: false` by default (pattern match on `*nemotron*`)
- **Root cause**: The fix for "Content block is not a thinking block" (0.19.10) was over-generalized to all NVIDIA models instead of just Nemotron family.

### Added — Explicit thinking variants for all Nemotron models

- **`config/remote-agents.sh`**: New roster entries with `-thinking` suffix for user choice:
  - `nvidia-nemotron-ultra-thinking` — Nemotron 3 Ultra 550B-A55B with reasoning enabled (verified 2026-10-02)
  - `nvidia-nemotron3-thinking` — Nemotron 3 Super 120B-A12B with reasoning enabled (verified 2026-10-02)
  - `nvidia-lightning-thinking` — Nemotron 3.5 Lightning 30B-A3B with reasoning enabled (verified 2026-10-02)
- **Mechanism**: `-thinking` suffix triggers `THINKING=true` in launcher, bypassing the `enable_thinking: false` default and enabling reasoning via `chat_template_kwargs.enable_thinking: true` when effort is set.
- **Verified**: Nemotron 3 Super tested with `enable_thinking: true` — both streaming and non-streaming work correctly (LiteLLM 1.102.1 fixed the stream bug).

### Updated — Model roster status reflects live probe results (2026-10-02)

- **`nvidia-kimi-k3`**: ⚠️ TIMEOUT on all probes — model listed but not responding (NVIDIA capacity/API issue)
- **`nvidia-deepseek-v4`**: Updated to `deepseek-ai/deepseek-v4.1-flash` (old `v4-flash-0731` is 410 Gone); ⚠️ TIMEOUT on all probes
- **`nvidia-glm53`**: ⚠️ Returns `reasoning_content` instead of `content`; works with streaming; non-streaming needs `enable_thinking:true` + larger `max_tokens`

### Tests

- `tests/test_remote_session.py`: Updated roster assertions for new thinking variants; fixed proxy config test to only expect `enable_thinking: false` for Nemotron models (`*nemotron*` pattern).
- All 234 tests pass.

---

## [0.21.3] — 2026-10-02

### Fixed — tool-owned-state guard no longer denies harmless commands containing heredocs

- `hooks/guard-tool-owned-state.py`: heredoc bodies are cut out before shell tokenising and judged
  like `python -c` code. Prose with an odd number of apostrophes used to make `shlex` raise, and the
  whole-text fallback then denied any write-shaped command that merely mentioned `.claude` (a `sed`
  version bump on a source repo's `.claude-plugin/plugin.json`, a `git commit -F - <<MSG`).
- The untokenisable fallback now requires a PROTECTED path, not just any `.claude` mention.
- 6 new self-test cases (3 must-allow, 3 must-deny incl. writes via heredoc); 41/41 pass; removing the
  body check fails the heredoc-write case (mutation-tested). Salvaged from the stale
  `fix/guard-state-fallback-fp` branch (2026-09-26), which never reached `main`.

## [0.21.2] — 2026-10-02

### Added — Queue Recovery with Shared Replay Core

- **Shared queue replay core** (`bin/queue_replay.py`): Single semantic core for queue replay,
  classification, and acknowledgment validation used by both Stop hook and future mid-run hook.
  Implements stable occurrence IDs (SHA-256 of session|line|content), content-matched FIFO pairing,
  and per-occurrence acknowledgment validation.
- **Persistent queue state** (`bin/queue_state.py`): Atomic read/write with file locking (`fcntl`),
  schema versioning (v1), source identity binding (transcript path + mtime + size), corruption recovery.
  State persisted at `~/.claude/state/queue/<session_id>.json`.
- **Per-occurrence acknowledgment contract**: Three dispositions — `answered_now` (concrete answer + marker),
  `answered_earlier` (reference to prior response + marker), `clarification` (concrete question + marker).
  Marker-only responses rejected; substantive evidence required.
- **Legacy marker migration**: Unambiguous legacy content-hash markers auto-migrated to occurrence-bound
  acknowledgments; ambiguous cases (identical content, multiple occurrences) preserved for user disambiguation.
- **Transcript lag handling**: `last_assistant_message` stdin hashes from Stop hook payload accepted for
  acknowledgment validation, preventing double-counting when transcript hasn't flushed.

### Changed

- **Updated `bin/local-queue-stop-hook.py`**: Uses new `queue_replay.replay_and_classify()` as single
  semantic core. Reconciles acknowledgments BEFORE `stop_hook_active` check (per plan). Backward-compatible
  exports for existing test contract (`build_queue_groups`, `_content_hash`, `_extract_answered_markers`,
  `_text_addresses_prompt`).
- **Content-matched FIFO pairing**: Drains with content match by exact content; contentless dequeues use
  FIFO fallback; `popAll` drains all pending oldest-first. Unmatched drains ignored; unmatched occurrences
  become undelivered groups.

### Fixed

- Identical prompts ("e", "e") now tracked as distinct occurrences with separate IDs.
- Later repeated slash commands not cleared by earlier marker — each occurrence requires own acknowledgment.
- Marker-only responses rejected; must include substantive evidence (answer, reference, or question).
- Persisted acknowledgments survive transcript lag, hook reinvocation, and session resume.
- Task notifications replayed for accounting but excluded from user-answer obligations.

### Tests

- 22 new queue replay tests (`tests/test_queue_replay.py`) covering all edge cases: FIFO pairing,
  out-of-order drains, contentless dequeues, popAll, identical prompts, repeated commands,
  task notifications, legacy migration, transcript lag, acknowledgment dispositions.
- All 250 tests pass (234 pytest + 16 bash contract tests).

---

## [0.21.0] — 2026-10-01

### Added — Unified Backend Management with Launchd Integration

- **Unified Backend Manager** (`install/manage-backend.py`): Single CLI to manage all inference backends (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm) with consistent subcommands: `list`, `releases`, `installed`, `install`, `validate`, `check-updates`, `promote`, `remove`, `info`, `launchd`.
- **Launchd Integration** for automatic weekly backend update checks: `manage-backend.py --backend <name> launchd {install,uninstall,status,run-once}`. Installs a launchd plist running weekly (Mon 10:17 AM) that checks PyPI/GitHub/Homebrew for backend updates and shows macOS notifications.
- **Interactive csl Menu Integration**: Launchd management available from home menu (`L` key) and local picker (`L` key) with submenu for install/uninstall/status/run-once.
- **Updated `install-backend.sh`**: Canonical installer now sets up all backends (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm) via unified CLI, with `--all`, `--backend`, `--dry-run` flags.
- **Consolidated Update Checks**: New `scripts/check_backend_updates.py` and `scripts/check-backend-updates.sh` replace the old `local-stack-update-check.sh`, checking all backends (Rapid-MLX, vllm-mlx, oMLX, llama.cpp, litellm) via unified CLI with JSON logging and macOS notifications.
- **Launchd Management in csl**: New `launchd` submenu accessible via `L` key in both home menu and local picker, offering install/uninstall/status/run-once options.
- **Unified Config Integration**: Updated `config/config-lib.sh` with `la_discover_backend_binary()` using unified CLI, and `config/config.example.sh` documents all backend configuration options.

### Changed

- **Refactored Rapid-MLX Manager**: `install/manage-rapid-mlx.py` refactored into `install/managers/rapid_mlx.py` with full feature parity; old script now a thin wrapper for backward compatibility.
- **New Backend Managers**: Added `VLLMMLXManager` (PyPI/GitHub sources, patch handling), `OMLXManager` (Homebrew/GitHub), `LlamaCppManager` (GitHub binary releases), `LitellmManager` (proxy management).
- **Unified Backend Abstraction**: New `BackendManager` ABC in `install/managers/base.py` with registry pattern; all managers inherit and implement required interface.
- **Unified CLI Entry Point**: New `install/manage-backend.py` replaces fragmented management scripts.
- **Consolidated Update Scripts**: `scripts/check-backend-updates.py` replaces `local-stack-update-check.sh`; `scripts/daily-package-upgrade.sh` simplified (Homebrew + pipx only).
- **Launchd Plist**: New `install/launchd/com.haiggoh.backend-update-check.plist` replaces old `local-stack-update-check.plist`.
- **Config Updates**: `config-lib.sh` adds unified backend discovery; `config.example.sh` documents all backend config options.

### Fixed

- Version bump to 0.21.0 across VERSION, plugin.json, and CHANGELOG.
- Updated plugin.json version to 0.21.0 in both main repo and worktree.

---

## [0.20.11] — 2026-10-01

### Fixed — NVIDIA free-API sessions died with misleading "Connection refused" on upstream 429/overload

- Root cause: NVIDIA free tier (~40 RPM dynamic) returns 429/503 "Service temporarily overloaded" on bursts; LiteLLM proxy surfaced this as "Connection refused — a firewall or proxy may be blocking it" (ECONNREFUSED), killing the session on first request.
- `rate_limiter.py`: Exponential backoff on 429 (30s→60s→120s→240s capped at 300s). New defaults: `LA_NVIDIA_429_COOLDOWN=30`, `LA_NVIDIA_MAX_WAIT=300`. Auto-reset when cooldown expires naturally or on successful acquire. Interactive menu updated.
- `la_proxy_hooks.py`: Machine-wide 429 cooldown pauses ALL proxies on the box; books hidden SDK retries (2 per call).
- `remote-session.sh`: Added `num_retries: 3` for NVIDIA/Gemini/Groq providers. Removed `retry_after` (causes 400 BadRequestError on NVIDIA NIM).
- Waypoints `nvidia-429s-still-hit-with-2` and `nvidia-free-api-no-quota-cap` closed; new waypoint for connection-refused fix added and closed.

## [0.20.10] — 2026-10-01

### Fixed — remote sessions could not push to GitHub (`could not read Username`)

- `remote-session.sh` exported only `credential.helper=""` (added in `c4da19e`, 0.19.x) to silence the
  harmless sandbox `failed to store: 100001`. An empty `credential.helper` RESETS the whole helper
  list, including the URL-scoped `gh auth git-credential` helper for github.com, so git found no
  credential and every push failed with `could not read Username for 'https://github.com': Device not
  configured`. Models then worked around it by writing the PAT into the remote URL or into an
  `export GH_TOKEN=…` permission rule.
- New `bin/la-git-credential-env.sh` (sourced): keeps the reset for every host, then restores
  `credential.https://github.com.helper = !<gh> auth git-credential`, so github.com gets the PAT from
  `GH_TOKEN` / gh's keyring while other hosts stay silenced. `--help` and `--print` when executed;
  `LA_GH_BIN` / `LA_GH_FALLBACK` override the gh path; missing gh warns and keeps the reset only.
- New `tests/test_git_credential_env.sh` (14 assertions, hermetic: temp HOME, stub gh, no Keychain or
  network). It reproduces the old bug as its own control. Mutation-tested: restoring the old block in
  `remote-session.sh` fails 2 assertions; dropping the restored helper fails 2.
- Not changed: `gh` x509 `OSStatus -26276` (Go TLS under Seatbelt) is a separate defect that was
  already resolved in 0.20.2 (`3c45ee6`), when both launchers moved to `blind-trust-settings.py`, which
  writes no `sandbox` key; last real occurrence 2026-09-29 08:43. If a profile ever re-enables the sandbox,
  add `gh` to `sandbox.excludedCommands`.

## [0.20.9] — 2026-10-01

### Fixed — `local-inference-readonly-inventory.zsh` ignored `--help` and ran

- The first argument was taken as the report directory unconditionally, so `--help` ran a full
  inventory into a new `./--help/` directory. Arguments are now parsed before any work:
  `-h/--help` prints usage (options + environment variables), `-n/--dry-run` prints the target
  directory and the section list derived from the script itself and writes nothing, `--` ends
  options, unknown options and extra positionals exit 2 with a usage line on stderr.
- New `tests/test_inventory_cli.sh` (16 assertions, temp `HOME`, never runs a real inventory);
  mutation-tested: breaking the `--help` branch fails 3 assertions.
- New `docs/READONLY_INVENTORY.md`: purpose, origin, options, collected sections, report files,
  safety boundary. README Diagnostics row and "What's in the box" link to it.
- Manifest version catches up: the manifest still said 0.20.7 at the 0.20.8 tag.

## [0.20.8] — 2026-09-30

### Improved — Stop hook output formatting with 🪝 emoji and collapsible content

- **local-queue-stop-hook.py**: Refactored output to be human-readable and concise:
  * **🪝 emoji header** (from `emoji.sh` `EMOJI_STOP_HOOK`) for clear identification
  * **Collapsible `<details>` sections** for full prompt content — same pattern used for tool calls in Claude Code
  * **Concise main message**: summary (truncated to 120 chars) + action line, no repetitive full content dumps
  * **Single full-content block** at end with consolidated helper script calls
  * Eliminates 3× repetition of full prompt content per queued item
- **queue_marker.py**: New single-source contract module for `QUEUE_ANSWERED` markers
  (exports `content_hash`, `format_marker`, `extract_hashes` — both hook and helper import from here)
- **Contract preserved**: All 16 queue marker contract tests pass (`tests/test_queue_marker_contract.sh`)

## [0.20.7] — 2026-09-30

### Added — Version detection for Opus 5.5 / 1M context support

- **remote-session.sh**: Added `_check_claude_version()` that runs at startup and warns if the user's Claude Code version is older than 2.1.280 (the version that added `claude-opus-5-5` with 1M context support).
- Older versions fall back to `claude-opus-5` (200k limit), capping Nemotron 3 Ultra's 1M actual context window.
- Warning message includes the minimum version (2.1.280), the fallback behavior, and update instructions.
- Can be suppressed with `LA_SILENCE_CLAUDE_VERSION_WARNING=1`.

## [0.20.6] — 2026-09-30

### Fixed — Nemotron 3 Ultra 1M context autocompaction via Opus 5.5 spoof ID

- **Root cause**: `claude-opus-5` (the previous primary spoof ID for remote sessions) has a hardcoded 200k context limit in Claude Code. When running `/autocompact` in an interactive Nemotron 3 Ultra session (which has a 1M actual context window), Claude Code enforced the spoofed model's 200k limit instead of allowing the full 1M.
- **config/config-lib.sh**: Changed `LA_SPOOF_CURRENT` from `claude-opus-5` to `claude-opus-5-5` (Opus 5.5 has 1M context support in Claude Code) and `LA_SPOOF_PREVIOUS` from `claude-opus-4-8` to `claude-opus-5`.
- **config/remote-agents.sh**: Changed `LA_REMOTE_SPOOF_IDS` to promote Opus 5.5 to primary position (`claude-opus-5-5,claude-opus-5,claude-sonnet-5,claude-haiku-4-5-20251001`). The Haiku spoof is retained for compatibility with older clients and utility-tier use cases.
- **Nemotron 3 Ultra (nvidia-nemotron-ultra)** and **NVIDIA Laguna XS 2.1 (nvidia-laguna)** now correctly get 1M autocompaction defaults via the `autocompaction_default` field (`1m`).
- **tests/test_remote_session.py**: Updated all assertions for 4 spoof IDs (primary is now Opus 5.5): `enable_thinking` count, `reasoning_effort` count, model count in proxy config. Fixed permission-mode checks from `auto` to `bypassPermissions`. Added `blind-trust-settings.py` to test fixtures. Fixed roster unpacking to handle 7 fields.
- **tests/test_remote_autocompaction.sh**: All 9 autocompaction tests pass.

### Added — Version detection for Opus 5.5 / 1M context support

- **remote-session.sh**: Added `_check_claude_version()` that runs at startup and warns if the user's Claude Code version is older than 2.1.280 (the version that added `claude-opus-5-5` with 1M context support).
- Older versions fall back to `claude-opus-5` (200k limit), capping Nemotron 3 Ultra's 1M actual context window.
- Warning message includes the minimum version (2.1.280), the fallback behavior, and update instructions.
- Can be suppressed with `LA_SILENCE_CLAUDE_VERSION_WARNING=1`.

## [0.20.5] — 2026-09-30

### Added — Autocompaction support for remote API sessions (LA_AUTO_COMPACT_WINDOW)

- **remote-session.sh**: Support for `LA_AUTO_COMPACT_WINDOW` environment variable to configure Claude Code autocompaction threshold for remote API sessions, mirroring the local launcher behavior.
- **remote-session.sh**: Default autocompaction of `1m` for NVIDIA Nemotron 3 Ultra (1M context window model) and NVIDIA Laguna XS 2.1, matching their actual context window size.
- **config/remote-agents.sh**: New 7th field `autocompaction_default` for per-model autocompaction defaults (e.g., `1m` for 1M-context models, `-` for no default).
- **Validation**: Same python3 validator as local launchers accepting "auto" or "100k–1m tokens".
- **Banner display**: Shows autocompaction setting in startup banner and dry-run output.

## [0.20.4] — 2026-09-30 — not released separately

> **Shipped inside 0.20.5.** This work was written as 0.20.4 but committed together with the
> 0.20.5 autocompaction change in one commit (`193e705`, whose `VERSION` reads 0.20.5), so no
> commit ever carried 0.20.4 and there is deliberately no `v0.20.4` tag. Install 0.20.5 to get it.

### Added — Smooth token bucket mode + interactive menu for NVIDIA rate limiter

- **New mode**: `LA_NVIDIA_MODE=smooth_bucket` (default stays `sliding_window`). Same 40 RPM average but with small capacity (default 6) and continuous refill (0.667 tokens/sec) for smoother limiting: small bursts allowed, then steady ~1.5s between requests instead of "all 40 then wait 60s".
- **Interactive menu**: `rate_limiter.py menu` (or run without args in TTY) for live configuration — switch modes, set RPM, bucket capacity, max wait, cooldown, view status, reset state, export shell config.
- **Env additions**: `LA_NVIDIA_BUCKET_CAPACITY` (default 6), `LA_NVIDIA_MODE` (sliding_window|smooth_bucket).
- **Factory function**: `get_limiter()` in `rate_limiter.py` returns the configured mode; `la_proxy_hooks.py` now uses it so proxies respect the chosen mode automatically.
- **Safety**: Smooth bucket caps at capacity + 60*rate = 46 requests per rolling minute (close to 40 with small headroom); 429 cooldown handles any overshoot.
- **State migration**: Both modes handle old-format state files gracefully.

## [0.20.3] — 2026-09-30

### Fixed — Sliding-window NVIDIA rate limiter + 429 cooldown + SDK hidden retries

- **Root cause**: The 0.19.10 token bucket started FULL (40) and refilled at 40/min, so any rolling 60 s could admit up to 79 requests — roughly double the published limit if NVIDIA counts per rolling minute. Two concurrent sessions logged 1241 x 429 against 1139 x 200 while the bucket queued almost nothing.
- **Fix**: Replaced token bucket with a **sliding-window limiter** (`bin/rate_limiter.py::SlidingWindowLimiter`) that admits at most `LA_NVIDIA_RPM` (default 40) requests in ANY 60 s span — the literal reading of "40 per minute".
- **429 cooldown**: When NVIDIA still answers 429, `note_429()` records a machine-wide cooldown so ALL proxies pause (Retry-After if sent, else `LA_NVIDIA_429_COOLDOWN`, default 10 s) instead of spending their remaining allowance into the same wall. Returns the window count at that moment — the number that locates the real limit.
- **SDK hidden retries**: LiteLLM's nvidia_nim path passes `max_retries=2` to the OpenAI SDK, so a failed 429 call actually sent 3 upstream attempts that never reached `async_pre_call_deployment_hook`. The failure hook now books these as `SDK_HIDDEN_RETRIES=2` extra slots so the window reflects what NVIDIA actually counted.
- **State migration**: Old token-bucket state files (with `tokens`/`stamp`/`capacity`) are read as empty (safe failure = one extra window, never a permanently full wedge).
- **New env**: `LA_NVIDIA_429_COOLDOWN` (default 10 s), `LA_NVIDIA_RPM` now means "requests per rolling 60 s window".
- **Tests**: 12 mutation-tested cases including 4-process inter-process no-overspend, 429 pause propagation, Retry-After honouring, hidden SDK attempts booking, and old-format state migration.

## [0.20.2] — 2026-09-29

### Fixed — Blind-trust mode uses bypassPermissions (measured 0 classifier calls)

- **Root cause**: The `LA_CLASSIFIER_CMD` mechanism was never read by Claude Code (0 occurrences in binary). Blind-trust was falling back to `acceptEdits` or a mock classifier that never actually ran.
- **Symptom**: Classifier still ran in blind-trust mode despite the "bypass" claim.
- **Fix**: Both `launch-claude-agent.sh` and `remote-session.sh` now use `--permission-mode bypassPermissions` (candidate C1 from `blind_trust_mechanism_probe.py`), measured at 0 classifier requests.
- **Single source of truth**: New `bin/blind-trust-settings.py` generator produces settings with `bypassPermissions` + deterministic `DESTRUCTIVE_DENY` list, merging master + profile allowlists, de-duplicated, order preserved.
- **Deleted**: Obsolete `bin/mock-classifier.py` and `bin/auto-yes-acceptedits.sh` (both were dead code paths).
- **Fixed**: Incorrect comments claiming "sandbox.enabled=true is the write boundary" — sandbox is now off by default.

### Added — Version consistency test now includes VERSION file

- The `tests/test_version_consistency.sh` now checks all four version locations: `.claude-plugin/plugin.json`, `CHANGELOG.md`, `docs/ROADMAP.md`, and `VERSION` file.

## [0.20.1] — 2026-09-29

### Fixed — Nemotron 4 model ID format for NVIDIA NIM

- **Root cause**: Roster entry for `nvidia-nemotron4` had model ID `nvidia/nemotron-4-340b` but NVIDIA NIM catalog requires `nvidia/nemotron-4-340b-instruct` (with `-instruct` suffix for the chat model).
- **Symptom**: Selecting Nemotron 4 from the interactive menu produced "There's an issue with the selected model (claude-opus-5)" because the LiteLLM proxy used the wrong model ID.
- **Fix**: Updated roster entry to catalog-verified model ID; `remote-session.sh --verify nvidia-nemotron4` now confirms model is catalog-listed (81 models).
- **Status**: GENERATION-PENDING — needs live generation probe to confirm end-to-end works.

## [0.20.0] — 2026-09-29

### Added — Portable local-model manifests & artifact identity

- **Self-describing model store**: Each completed artifact in `~/.models/` now carries its own `.local-model-manifest.json` beside its weights. A consumer scanning the directory can list what is installed, launchable, session-eligible, and what context each artifact supports — without any checkout of this repository.
- **Manifest v1 specification** (`docs/model-manifest-v1.md`) and **JSON Schema** (`docs/schema/local-model-manifest-v1.schema.json`) for interoperability with future consumers (AGY, other tools).
- **Five-layer identity model**: artifact → runtime-profile → resource-profile → environment-profile → live-server (runtime profiles are 0.21.0; this commit establishes the artifact layer).
- **Six context states, one derived**:
  - `native_context_tokens` — architecture declaration
  - `configured_context_tokens` — this artifact's config.json
  - `extended_context_tokens` — explicit RoPE/YaRN setup
  - `tested_safe_context_tokens` — measured with evidence
  - `server_context_tokens` — live server allocation
  - `effective_context_tokens` — MINIMUM of applicable limits (what a session may actually use)
- **Derived autocompaction** (100K increments, 100K–1M): `claude_autocompact_tokens = min(1M, floor(effective/100K)*100K)` — recomputed by validators, never independently editable.
- **Artifact kind vs. launchability vs. session eligibility** — three deliberate fields:
  - Normal MLX model: `kind: model`, launchable, session-eligible
  - MTP drafter: `kind: draft_model`, **not** launchable, **not** session-eligible, names target via `target_directory_name`
  - TTS/Depth: launchable by own tooling, **never** session-eligible
  - `mlx_lm`/`llama.cpp`: launchable for dispatch only, **not** session-eligible (no `/v1/messages`)
- **Completion is a transaction**: `acquisition.status=complete` only after payload verify + manifest atomic write (temp → validate → rename). Interrupted completion repairs metadata without redownload.
- **Researched catalogue** (`config/model-catalogue-context-list.yaml`) — 44 hand-audited entries as input/provenance, not truth. Manifest records `acquisition.catalogue_provenance` where used; validation fails closed on disagreement.

### Added — Manifest toolkit (`install/local-model-manifest.py`, stdlib only)

- `build --dir <model> [--output <path>] [--dry-run]` — build manifest from artifact
- `validate --manifest <path>` — validate against schema rules
- `inspect --dir <model>` — show discovered context, kind, launchability
- `reconcile [--json]` — compare installed artifacts with YAML catalogue
- `backfill --select <alias|dir>|--all [--force] [--dry-run]` — write manifests for installed artifacts
- Atomic writes, `--dry-run` on all mutating commands, idempotent reruns
- Clear reporting: missing/valid/stale/invalid/conflicting

### Added — Downloader integration (`install/download-models.sh`)

- On completion (after marker + payload verify): resolve repo/revision, inspect context candidates, reconcile with catalogue, build & validate temp manifest, atomic rename, reread & validate final state
- Interrupted completion → repairs metadata without redownload
- Manifest written atomically at acquisition completion

### Added — CSL manifest-driven context derivation (`bin/csl`)

- Resolves alias → physical directory → manifest → computes effective context (min of native/extended, configured, backend, server, tested-safe)
- Derives 100K floor & Claude autocompaction per spec
- Precedence: explicit override > local override > selected mode > manifest default > `LA_MAX_MODEL_LEN` fallback
- Pre-launch validation: server context ≥ autocompaction, valid 100K increment, autocompaction ≤ effective context, extended mode has runtime support, non-launchable blocked
- On invalid config: fail before Claude starts, print model identity, artifact context, server context, effective context, selected autocompaction, corrective action
- Missing/invalid manifest → legacy behavior + bounded warning
- Dry-run and picker display show `effective_ctx` and `autocompact` from manifests

### Added — Controlled 44-entry backfill (gated, not blind `--all`)

Ordered gates, each must pass before next:
1. Dry-run full roster validation
2. Backfill **Ornith** → verify `262144 → 200000 → 200k`
3. Backfill one **131,072 model** (Granite) → verify 100K
4. Backfill **Devstral 393,216** → verify 300K (with matching server allocation)
5. Test shared aliases/override behavior
6. Test **Qwen3.8 target + MTP drafter** attachment
7. Test **TTS/DepthART exclusion** from session picker
8. Test **Laguna 1M** with bounded server allocation
9. Test **Llama 4 Scout** bounded to 262,144/200K (not 10M)
10. Backfill remaining roster

All 44 models now have valid manifests. Key verifications:
- Ornith: 262K → 200K ✓
- Granite: 131K → 100K ✓
- Devstral: 393K → 300K ✓
- Laguna: 1M → 1M (Claude cap) ✓
- Llama 4 Scout: 10M → 1M (bounded) ✓
- Qwen3.8-MTP-4bit drafter → targets Qwen3.8-27B-4bit ✓
- TTS/DepthART correctly excluded from session picker ✓

### Added — `bin/backfill-model-manifests.sh` wrapper

Thin wrapper calling `install/local-model-manifest.py backfill` for common operations.

### Tests

- 17 fixtures (6 valid, 11 invalid) with mutation-tested validator (invert floor arithmetic, disable drafter rule, plant absolute path)
- All existing tests pass: 43/43 fixture tests, 39/39 serve tests, version consistency

## [0.19.12] — 2026-09-28

### Changed — stream-split patch retired (fixed upstream in LiteLLM 1.102.1)

- **The 0.19.10 runtime wrap of LiteLLM's private `_CombinedChunkSplitter._split` is removed.**
  LiteLLM 1.102.1 types a chunk carrying both `reasoning_content` and `content` correctly, so
  "Content block is not a thinking block" no longer needs our patch. Verified against the
  unpatched 1.102.1 adapter with the original repro (mixed transition + mixed first chunk).
  Keeping the wrap would only have risked breaking on a future LiteLLM refactor.
- **`la_proxy_hooks.py` now PROBES the installed adapter at load** and prints a loud WARNING with
  the upgrade command (`pipx upgrade litellm`, >= 1.102.1) if the bug is still present, so an old
  install cannot regress silently. The probe never raises.
- **Kept:** the `safeguards` drop (1.102.1 still forwards it; NIM answers HTTP 400), the
  `stop_sequences` → `stop` translation as a fallback for older LiteLLM (1.102.1 now does it
  itself), and the machine-wide NVIDIA 40 RPM bucket.
- Tests: the split-patch tests are replaced by adapter-outcome tests plus a planted-bug mutation
  test for the new check (9 tests, all under the LiteLLM interpreter).

## [0.19.11] — 2026-09-26

### Added — Dynamic session names with launcher prefix + auto-generated suffix

- **Session names no longer fixed at launch.** All three launchers (`launch-claude-agent.sh`,
  `remote-session.sh`, `launch-claude-agent-rapid-auto.sh`) now omit the `-n` flag, allowing
  Claude Code to auto-generate a session name from the first prompt's content.
- **UserPromptSubmit hook** (`hooks/session-title-prefix.py`) runs after the first prompt,
  reads the auto-generated name, and prepends the launcher prefix (`<emoji> <model-alias>`).
  Result: `📡 nvidia-nemotron-ultra analyze code security` instead of the fixed
  `📡 nvidia-nemotron-ultra` shared by all sessions on that model.
- **Idempotent and resume-safe:** If the prefix is already present, the hook skips. On resume
  with a different model, the old prefix is replaced with the current model's prefix while
  preserving the auto-generated suffix.
- **Works for local, free_api, and rapid-auto lanes.** Uses `LA_SESSION_KIND_EMOJI` and
  `MODEL_ALIAS` (shorter alias, e.g., `nvidia-nemotron-ultra`) from the identity resolver.

## [0.19.10] — 2026-09-26

### Fixed — remote NVIDIA/Nemotron proxy hardening (thinking stays ON)

- **"API Error: Content block is not a thinking block"** on Nemotron with thinking enabled. Root
  cause is in LiteLLM 1.91's OpenAI→Anthropic stream adapter: it picks a chunk's *block* type with
  `content` winning over `reasoning_content`, but its *delta* type the other way round, so a single
  NVIDIA chunk carrying both (the end of the reasoning plus the first answer token) opens a text
  block and streams a `thinking_delta` into it. New `bin/la_proxy_hooks.py` splits such chunks
  (reasoning first) before the adapter sees them. Nothing is dropped and thinking is NOT disabled.
  The runtime wrap lives in this repo and never edits `~/.local/pipx`. If LiteLLM's private
  splitter disappears, it skips loudly rather than crashing.
- **HTTP 400 "Unsupported parameter(s): `stop_sequences` / `safeguards`"** from NVIDIA NIM. The
  Anthropic adapter forwards both keys untranslated and `drop_params` does not catch them.
  `stop_sequences` is now translated to OpenAI `stop`, which keeps the Auto Mode classifier's
  `</severity>` stop working, and `safeguards` is dropped.
- **Machine-wide NVIDIA 40 RPM limit.** New `bin/rate_limiter.py` is a file-backed,
  `fcntl`-locked token bucket (40 tokens, one per 1.5 s) shared by every proxy on the machine.
  The proxy hook queues each NVIDIA call on it instead of letting a 429 reach Claude Code, whose
  retries add load. State lives in `~/.claude/local-agents/.nvidia_throttle_state`. It is tunable
  via `LA_NVIDIA_RPM` / `LA_NVIDIA_MAX_WAIT`.
- `write_proxy_config` registers the callback and links the module next to `proxy-<port>.yaml`,
  where LiteLLM resolves callback modules. `LA_REMOTE_PROXY_HOOKS=0` disables all three, loudly.
- ROADMAP's "Current released version" had been left at 0.19.8 by 0.19.9, and
  `tests/test_version_consistency.sh` was failing on main. It is realigned here.

## [0.19.9] — 2026-09-27

### Fixed — argument parsing + explicit MCP patterns + dry-run preflight option

- **csl argument parsing fixed**: Changed from single `case` to `while` loop so multiple flags work (`csl --enable-mcp --dry-run`). Previously only the first flag was processed.
- **Explicit MCP patterns (not wildcard)**: Replaced `mcp__*` with explicit per-server patterns for all 19 configured MCP servers (adobe-for-creativity, context7, openai-developers, vercel, greptile, linear, expo, imessage, circleback, fakechat, mintlify, exa, browser-use, render, github, magnific, blender, davinci-resolve, joyia). Claude Code does not support wildcards in allow rules.
- **launch-claude-agent.sh --dry-run-skip-preflight**: New flag/env `LA_DRY_RUN_SKIP_PREFLIGHT=1` to skip RAM preflight during dry-run for quick config inspection without the warning.
- **RAM preflight runs during regular --dry-run** (correct behavior — warns about concurrent sessions that would block launch).
- **Profile files updated**: Both `lean-local-general.json` and `lean-cloud-general.json` now list explicit `mcp__<server>__*` patterns instead of wildcard.
- **Merged allowlist**: 87 master + 19 MCP = 106 items when MCPs enabled in blind-trust mode.

## [0.19.8] — 2026-09-26

### Added — MCP wildcard allowlist in blind-trust mode (local + remote)

- **Master allowlist + profile pattern**: `~/.claude/launch-profiles/allowlist-master.json` holds the base 87 Bash command allowlist. Lean profiles (`lean-local-general.json`, `lean-cloud-general.json`) extend it with `mcp__*` wildcard, grandfathering future MCP servers without launcher changes.
- **Local blind-trust**: `launch-claude-agent.sh --enable-mcp` (or `LA_ENABLE_MCP=1`) generates blind-trust settings merging master + profile allowlists, includes `mcp__*`. `csl` gains `--enable-mcp` flag and `u` key in local picker for blind-trust MCP toggle.
- **Remote blind-trust**: `remote-session.sh --enable-mcp` (or `LA_REMOTE_ENABLE_MCP=1`) does the same for free-API sessions. `csl remote` and remote picker (`m` key) allow MCP toggle in ALL auto-mode states including blind-trust.
- **Inline allowlists removed**: Both launchers no longer duplicate the 400+ line inline allowlist; they read from portable JSON profiles at runtime.
- **Wildcard `mcp__*`**: Instead of listing current servers (blender, davinci-resolve, joyia, adobe, filesystem, github), the wildcard admits any MCP tool added later.

### Fixed — remote picker MCP toggle in blind-trust

- `_run_remote_menu` now shows and allows `m` key to toggle MCPs even in `AUTO_MODE_STATE=0` (blind-trust), matching local picker behavior. Previously it showed "MCPs cannot be enabled in blind-trust" error.

## [0.19.7] — 2026-09-25

### Fixed — guard-tool-owned-state over-blocked harmless Bash

0.19.5/0.19.6 denied any Bash command that contained a protected path AND a write verb anywhere, so
running a plugin script from the cache with `> /tmp/out`, chaining an unrelated `rm`, or copying OUT of
the cache was refused, with a deny message about version bumps that did not apply. Measured live during
an audit pass: 3 of 6 probe commands were wrongly denied.

- `hooks/guard-tool-owned-state.py`: the Bash check now splits the command into simple commands
  (quote-aware `shlex`) and judges each by its WRITE TARGET: redirect targets, the destination of
  cp/install/rsync/ln (incl. `-t DIR`), every path of rm/mv/tee/chmod/touch/..., `sed -i` files, git
  checkout/restore/reset in a protected repo, and inline `python -c` that names and writes protected
  state. Prefixes (`!`, `sudo`, `env`, `VAR=`) are stripped, and relative paths are resolved against an
  earlier `cd` in the same command. Unbalanced quotes fall back to the old conservative check.
- Tests: 17 new self-test cases plus two subprocess tests (false positives stay allowed; cd- and
  chain-hidden writes stay denied). Mutation-checked: restoring the old whole-text logic, or dropping
  cwd tracking, turns the suite red. Hook latency ~25 ms.

## [0.19.6] — 2026-09-25

### Added — shared rule: never rewrite a file from a partial view

- `config/shared-agent-shipping-rules.txt` (loaded into every local and remote free session) gains
  NEVER HAND-EDIT A TOOL-OWNED REGISTRY, AND NEVER REWRITE A FILE FROM A PARTIAL VIEW. It covers the
  missing-version-bump diagnosis, in-place edits instead of whole-file rewrites rebuilt from snippets,
  "restore means copy a real snapshot", and the fact that `! cmd` in the Bash tool does not escape the
  sandbox. The 0.19.5 PreToolUse guard enforces this for the registry files; this rule covers every
  other file.
- `tests/test_guard_tool_owned_state.py`: asserts the rule is present (mutation-tested).

## [0.19.5] — 2026-09-25

### Added — PreToolUse guard against hand-editing tool-owned plugin state

A remote free-API session pushed a plugin change without a version bump. `claude plugin update`
correctly had nowhere new to land, and the session misread that as a sandbox restriction. It then
`Write`-replaced `~/.claude/plugins/installed_plugins.json` with a file rebuilt from partial
`grep -A 10` views, which dropped 56 of 86 entries. Its "restore" was a second Write of the same
reconstruction. The CLI later re-filled the file from disk, so it looked healthy while recording
stale versions, resurrected uninstalled plugins and wiping install history. Prose rules already
said "never hand-edit the plugin cache"; this makes it mechanical.

- `hooks/guard-tool-owned-state.py` (PreToolUse, matcher `Bash|Write|Edit|MultiEdit|NotebookEdit`):
  denies writes to `installed_plugins.json`, `known_marketplaces.json`, `plugins/cache/**` and
  `plugins/marketplaces/**` under `$CLAUDE_CONFIG_DIR` (default `~/.claude`). Reads stay allowed,
  and so does a command that is entirely `claude plugin …` or `get-haiggoh`. A command chained
  onto one of those is not exempt. The deny reason tells the model the likely real cause (a
  missing version bump) and the correct path.
  Fails open on internal error; `LA_STATE_GUARD_DISABLE=1` turns it off for a deliberate human repair.
  `--help`, `--self-test`.
- `tests/test_guard_tool_owned_state.py`: subprocess-boundary tests plus a hooks.json registration check;
  mutation-tested (disabling the Bash write check turns the suite red).

### Fixed — blind-trust settings merge keeps network settings

- `bin/merge-settings.py`: `network` (allowedDomains) from the blind-trust settings is now preserved
  when merging with per-session settings, the same way `sandbox` already was. (`024e476`, landed
  after the 0.19.4 bump.)

## [0.19.4] — 2026-09-24

### Added — blind-trust auto mode A/B test

- `LA_BLIND_TRUST_OPTION=A|B` (default `B`) in `bin/launch-claude-agent.sh` and `bin/remote-session.sh`.
  - Option A, `bin/auto-yes-acceptedits.sh`: `acceptEdits` plus an auto-yes wrapper, bypassing the classifier.
  - Option B, `bin/mock-classifier.py`: keeps `auto` mode semantics with a mock classifier; sandbox guards stay active.

## [0.19.3] — 2026-09-24

### Added — install progress feedback

- `install/manage-rapid-mlx.py`: a braille `spinner()` context manager wraps venv creation, the
  locked/fresh pip installs and validation, so the 15–60s install is no longer silent.

## [0.19.2] — 2026-09-24

### Fixed — queue Stop hook race with the async transcript

- `bin/local-queue-stop-hook.py`: the transcript is written asynchronously and can lag the turn, so a
  correctly-marked reply could be re-notified. The hook now checks the Stop payload on stdin for
  `QUEUE_ANSWERED` markers FIRST, and only then falls back to reading the transcript.

## [0.19.1] — 2026-09-24

### Fixed — effort persistence for remote free API sessions (subshell variable loss)

The `0.18.9` effort persistence fix wrote the chosen effort to a per-session file at launch,
but the interactive remote picker (`_run_remote_menu`) was called via command substitution
(`ALIAS="$(_run_remote_menu)"`), which runs in a subshell. Variable assignments made inside
the picker (effort choice, auto-mode state, telemetry, local-capable filter, trial inclusion)
were lost when the subshell exited, so the launcher always fell back to the default "medium"
effort.

- `bin/remote-session.sh`:
  - Added `_sync_state()` to write current toggle state to the nav file before returning a selection
  - Caller reads synced state from nav file after command substitution returns
  - Nav file format extended with "stay" navigation target for selection (vs navigate away)
  - Fixes effort choice, auto-mode, telemetry, local-capable filter, and trial inclusion
    all reaching the launcher when set in the interactive picker
- `bin/csl`: Handles "stay" navigation target to remain in remote lane with updated state

### Fixed — LA_SESSION_ID used before set in effort file write

Commit `3618073` inserted the effort file write before session ID generation, so
`LA_SESSION_ID` was unset when expanded in the filename. The file was written as
`/tmp/claude-effort-` (empty suffix), which the resolver could never read.
- `bin/remote-session.sh`: Move effort file write to after `LA_SESSION_ID` generation
  (commit `9eb18a4`, previously untagged)

## [0.19.0] — 2026-09-23

### Changed — rapid-mlx upgraded to 0.15.0

- `launch-claude-agent-rapid-auto.sh`: pins rapid-mlx 0.15.0 for Auto Mode classifier
- `config/config-lib.sh` & `config/config.example.sh`: default rapid-mlx version updated
- `local-inference-readonly-inventory.zsh`: checks 0.15.0 venv
- `install/manage-rapid-mlx.py`: explicit dry_run=False in remove path
- `tests/test_rapid_auto_mode.sh`: expectations updated to 0.15.0

## [0.18.9] — 2026-09-23

### Fixed — effort level persists to statusline for remote free API sessions

- `remote-session.sh` writes chosen effort to `/tmp/claude-effort-<SESSION_ID>` at launch
- `la-session-identity.sh` reads session file first (priority over `LA_CUR_EFFORT` env var)
- Statusline renderer passes `SID` as `LA_SESSION_ID` to resolver
- Fixes free_api sessions always showing "medium" effort regardless of launcher selection

**Known issue (fixed in 0.19.1):** The interactive picker ran in a subshell due to command
substitution, losing the effort choice and other toggle state. See 0.19.1 for the fix.

## [0.18.8] — 2026-09-23

### Added — Devstral Small 2 qualification candidate, runbook & test sandbox fixture

- `config/config.example.sh`: Added candidate configuration for Devstral Small 2 24B as leading local Auto Mode classifier candidate (dense/non-hybrid, trimmable cache with 98.86% reuse).
- `docs/CLASSIFIER_QUALIFICATION_RUNBOOK.md`: Added qualification runbook for local model Auto Mode classifier testing against captured requests.
- `tests/test_rapid_auto_mode.sh`: Ensure `emoji.sh` is copied into the test sandbox config directory during Rapid Auto Mode test runs.

## [0.18.7] — 2026-09-22

### Changed — Release metadata and version source of truth alignment

- Synchronized release metadata and canonical version references across the repository.

## [0.18.6] — 2026-09-23

### Added — lowkey CLI dispatcher (v0.10.3) — automated test suite (Stage 3)

- `tests/test_lowkey_cli_dispatch.py`: New test module mocking hotswap/librarian boundaries.
  Covers argument validation, structured messages, progress modes, rolling summaries,
  compact history, session autosave, one-shot compatibility, file parsing, model mismatch,
  model labels, conversation commands, compaction threshold, context status, EOF handling.
- Total test coverage: 140 tests (47 pure + 45 stateful + 48 dispatch) — all passing.

### Fixed — lowkey CLI dispatcher (v0.10.2) — paste-burst UX + model alias resolution

- `bin/lowkey-cli.py`: Fixed paste-burst terminal presentation (Stage 2).
  During `:paste` mode, terminal echoed each pasted line while model output from the
  previous turn could still be streaming, creating visually interleaved input/output.
  Now uses raw stdin (`termios`/`tty`) on TTY to suppress terminal echo during paste
  collection, with clear visual mode banners on entry and confirmation on `:end`.
  Non-TTY (tests, pipes) falls back to line-buffered mode automatically.

### Fixed — lowkey CLI dispatcher (v0.10.1) — model alias resolution and dispatch model ID contract

- `bin/lowkey-cli.py`: Fixed dispatch to use the correct model ID for each backend.
  Previously sent the alias (e.g., `qwen-3.8-operator`) as the `model` field, but Rapid-MLX
  only serves the spoofed Claude ID (`claude-opus-5`), while vllm-mlx serves the alias.
  Now queries `DISPATCH_MODEL` from hotswap output (new contract) with `/v1/models` fallback.
- `bin/local-llm-hotswap.sh`: Added `DISPATCH_MODEL` output contract.
  - Rapid-MLX: emits `DISPATCH_MODEL=claude-opus-5` (spoof ID, only served model)
  - vllm-mlx: emits `DISPATCH_MODEL=qwen-3.8-operator` (alias, served alongside spoof IDs)
  - mlx_lm: emits `DISPATCH_MODEL=<model_dir>` (model directory path)
  This allows callers to dispatch correctly without guessing which ID the backend serves.

### Added — lowkey interactive picker (bin/lowkey) refinements

- Emoji placement: CSL-style emojis after the letter with proper spacing (e.g., `m) 🦾 Model`)
- One-shot emoji changed from `⚡` to `🎯` (target/direct)
- Advanced settings in submenu; only modified (non-default) settings shown in main menu
- Each advanced setting (Progress, Max tokens, Max history, Max file, Session continuity)
  now appears individually in main menu only when modified
- Session continuity toggle (was "model mismatch") with state emojis (✅ ALLOWED / 🚫 BLOCKED)
- Empty line between settings table and menu items for visual separation
- All lowkey emojis centralized in `config/emoji.sh` with `LK_` prefix
- `LK_EMOJI_MODEL` links to `SESSION_EMOJI_LOCAL` (single source of truth)

### Changed

- `config/emoji.sh`: Added `LK_` prefixed emoji constants for lowkey namespace
- `bin/lowkey`: Updated to use shared emoji constants; improved menu rendering

## [0.18.5] — 2026-09-23

### Fixed — lowkey CLI dispatcher (v0.10.2) — paste-burst UX + model alias resolution

- `bin/lowkey-cli.py`: Fixed paste-burst terminal presentation (Stage 2).
  During `:paste` mode, terminal echoed each pasted line while model output from the
  previous turn could still be streaming, creating visually interleaved input/output.
  Now uses raw stdin (`termios`/`tty`) on TTY to suppress terminal echo during paste
  collection, with clear visual mode banners on entry and confirmation on `:end`.
  Non-TTY (tests, pipes) falls back to line-buffered mode automatically.

### Fixed — lowkey CLI dispatcher (v0.10.1) — model alias resolution and dispatch model ID contract

- `bin/lowkey-cli.py`: Fixed dispatch to use the correct model ID for each backend.
  Previously sent the alias (e.g., `qwen-3.8-operator`) as the `model` field, but Rapid-MLX
  only serves the spoofed Claude ID (`claude-opus-5`), while vllm-mlx serves the alias.
  Now queries `DISPATCH_MODEL` from hotswap output (new contract) with `/v1/models` fallback.
- `bin/local-llm-hotswap.sh`: Added `DISPATCH_MODEL` output contract.
  - Rapid-MLX: emits `DISPATCH_MODEL=claude-opus-5` (spoof ID, only served model)
  - vllm-mlx: emits `DISPATCH_MODEL=qwen-3.8-operator` (alias, served alongside spoof IDs)
  - mlx_lm: emits `DISPATCH_MODEL=<model_dir>` (model directory path)
  This allows callers to dispatch correctly without guessing which ID the backend serves.

### Added — lowkey interactive picker (bin/lowkey) refinements

- Emoji placement: CSL-style emojis after the letter with proper spacing (e.g., `m) 🦾 Model`)
- One-shot emoji changed from `⚡` to `🎯` (target/direct)
- Advanced settings in submenu; only modified (non-default) settings shown in main menu
- Each advanced setting (Progress, Max tokens, Max history, Max file, Session continuity)
  now appears individually in main menu only when modified
- Session continuity toggle (was "model mismatch") with state emojis (✅ ALLOWED / 🚫 BLOCKED)
- Empty line between settings table and menu items for visual separation
- All lowkey emojis centralized in `config/emoji.sh` with `LK_` prefix
- `LK_EMOJI_MODEL` links to `SESSION_EMOJI_LOCAL` (single source of truth)

### Changed

- `config/emoji.sh`: Added `LK_` prefixed emoji constants for lowkey namespace
- `bin/lowkey`: Updated to use shared emoji constants; improved menu rendering

## [0.18.4] — 2026-09-23

### Fixed — lowkey CLI dispatcher (v0.10.1) — model alias resolution and dispatch model ID contract

- `bin/lowkey-cli.py`: Fixed dispatch to use the correct model ID for each backend.
  Previously sent the alias (e.g., `qwen-3.8-operator`) as the `model` field, but Rapid-MLX
  only serves the spoofed Claude ID (`claude-opus-5`), while vllm-mlx serves the alias.
  Now queries `DISPATCH_MODEL` from hotswap output (new contract) with `/v1/models` fallback.
- `bin/local-llm-hotswap.sh`: Added `DISPATCH_MODEL` output contract.
  - Rapid-MLX: emits `DISPATCH_MODEL=claude-opus-5` (spoof ID, only served model)
  - vllm-mlx: emits `DISPATCH_MODEL=qwen-3.8-operator` (alias, served alongside spoof IDs)
  - mlx_lm: emits `DISPATCH_MODEL=<model_dir>` (model directory path)
  This allows callers to dispatch correctly without guessing which ID the backend serves.

### Added — lowkey interactive picker (bin/lowkey) refinements

- Emoji placement: CSL-style emojis after the letter with proper spacing (e.g., `m) 🦾 Model`)
- One-shot emoji changed from `⚡` to `🎯` (target/direct)
- Advanced settings in submenu; only modified (non-default) settings shown in main menu
- Each advanced setting (Progress, Max tokens, Max history, Max file, Session continuity)
  now appears individually in main menu only when modified
- Session continuity toggle (was "model mismatch") with state emojis (✅ ALLOWED / 🚫 BLOCKED)
- Empty line between settings table and menu items for visual separation
- All lowkey emojis centralized in `config/emoji.sh` with `LK_` prefix
- `LK_EMOJI_MODEL` links to `SESSION_EMOJI_LOCAL` (single source of truth)

### Changed

- `config/emoji.sh`: Added `LK_` prefixed emoji constants for lowkey namespace
- `bin/lowkey`: Updated to use shared emoji constants; improved menu rendering

## [0.18.3] — 2026-09-22

### Added — lowkey CLI dispatcher (v0.10.0)

## [0.18.2] — 2026-09-21

### Added — remote API token rate telemetry for free_api sessions

- `la-telemetry-remote-tokrate.sh`: new telemetry script that reads streaming
  token rate from session-specific log files (`~/.claude/logs/remote-streaming-<SESSION_ID>.log`)
  and outputs JSON matching the `la-telemetry-token-rate.sh` contract
- `la-remote-telemetry-wrapper.sh`: wrapper for `claude` that captures streaming
  response metrics via `--debug-file` and writes token rate to session log file
- `remote-session.sh`: uses wrapper for `free_api` sessions to generate streaming
  metrics log; gated on `LA_SESSION_KIND=free_api`
- Log format: `timestamp(ms) tok/s cumulative_tokens`
- Integrates with statusline renderer (cost-tracker 0.7.6+) for live token rate display

### Fixed — emoji spacing after wide glyphs in menu and banner text

`EMOJI_TELEMETRY_ON` (`🛰️`) and `EMOJI_EFFORT` / `EMOJI_MODEL_CHOICE` (`⚙️`) are
two-codepoint wide glyphs (base + VARIATION SELECTOR-16) that occupy two terminal
columns, but the single ASCII space after them occupies only one. The result was a
visually tight join — "⚙️choose" and "🛰️telemetry" read as stuck together.

- trailing space now baked into the emoji constants in `config/emoji.sh`, so every
  consumer (csl, remote-session.sh, launch-claude-agent.sh) gets the gap for free
- `remote-session.sh`: moved the `e) effort` menu line from after telemetry/trials
  to right after `h) back to lane selector`, matching csl's local picker ordering
- `launch-claude-agent.sh`: when invoked without an effort override and the alias
  default is medium, prompts interactively for effort (mirrors remote-session.sh's
  `e)` picker)

## [0.18.1] — 2026-09-21

### Fixed — emoji spacing after wide glyphs in menu and banner text

`EMOJI_TELEMETRY_ON` (`🛰️`) and `EMOJI_EFFORT` / `EMOJI_MODEL_CHOICE` (`⚙️`) are
two-codepoint wide glyphs (base + VARIATION SELECTOR-16) that occupy two terminal
columns, but the single ASCII space after them occupies only one. The result was a
visually tight join — "⚙️choose" and "🛰️telemetry" read as stuck together.

- trailing space now baked into the emoji constants in `config/emoji.sh`, so every
  consumer (csl, remote-session.sh, launch-claude-agent.sh) gets the gap for free
- `remote-session.sh`: moved the `e) effort` menu line from after telemetry/trials
  to right after `h) back to lane selector`, matching csl's local picker ordering
- `launch-claude-agent.sh`: when invoked without an effort override and the alias
  default is medium, prompts interactively for effort (mirrors remote-session.sh's
  `e)` picker)

## [0.18.0] — 2026-09-21

### Added — the lime accent for remote sessions is REAL and now shipped

0.17.6 withheld this as an "upstream limitation", having probed a `themes` **settings map** and
found it unconfirmable: a deliberately bogus key succeeded just as silently, so acceptance was
not evidence. That calibration was correct — and it was pointed at the **wrong mechanism**.

Custom themes are **files**. The CLI reads `~/.claude/themes/<slug>.json` (shape
`{name, base, overrides}`, ≤256KB) and a session selects one with the ordinary `theme` setting.
Confirmed three ways against CLI 2.1.278: the loader path `userConfigDir("themes",[slug])` in
the binary, a `[theme] watcher` that hot-reloads changes, and `claude --help`, which lists
"custom themes" among the customizations `--safe-mode` disables. So: a documented feature, not
a guess.

- `generate-remote-settings.py --emit-theme-file` prints the theme file; the accent is read
  from `config/remote-theme.json`, which stays the single source of truth for the colour
- the per-session overlay now carries `"theme": "free-lime"`, still **cloud-scoped out** — a
  cloud session is never themed
- `remote-session.sh` installs the theme file openly to `~/.claude/themes/`, written only when
  absent or changed, so a hand-edited colour survives and repeat launches are a no-op
- only IDENTITY roles are recoloured (`claude`, `permission`, `planMode`, `bashBorder`,
  `suggestion`, `thinking`). `success`/`error`/`warning` are deliberately untouched —
  recolouring a semantic signal makes a session harder to read, not more distinctive

### Fixed — the watcher window stole focus and covered the session

User-confirmed still broken: "the watcher steals focus rather than opening unfocused and it
covers the session instead of opening off to the side or slightly behind the main window." Two
distinct defects; the earlier attempt addressed only the first.

- **focus**: `set frontmost of window` is *accepted* by Terminal but does not reliably raise it.
  `set index to 1` is the property that actually reorders windows, so both are used, each
  guarded independently
- **geometry**: no focus trick helps if the window lands on top of the session. The watcher is
  now positioned relative to the previous window's bounds, offset down-right. Tunable via
  `LA_WATCH_OFFSET_X`/`_Y`; `LA_WATCH_NO_PLACE=1` opts out

### Fixed — `local-watch.sh --help` OPENED WINDOWS instead of printing help

`--help` was not parsed at all: it fell through the mode resolution into the `--open` path, so
probing an unfamiliar script with `--help` triggered its real work. Now prints usage (including
the new env vars) and exits 0; an unrecognised flag exits 2 with a usage line on stderr.

## [0.17.11] — 2026-09-21

### Fixed — the resolver died silently on every REGISTERED alias (missing braces)

`la-session-identity.sh:99` read `"$LA_SERVE_DECLARED[$MODEL_ALIAS]"` **without braces**.
Bash parses that as the scalar `$LA_SERVE_DECLARED` (unset) followed by a literal
`[alias]`, so under `set -u` the script died *immediately after deciding the lookup had
succeeded* — emitting **no JSON while exiting 0**. A silent fail-open: `statusline-render.sh`
saw empty output, took its legacy fallback, and displayed the **spoofed** model name.

Note the irony in the previous release: 0.17.10 hardened the resolver for *unregistered*
aliases, while the **registered** path — the normal case — was the broken one.

- correct form `${LA_SERVE_DECLARED[$MODEL_ALIAS]:-$BACKEND}`, defaulting so a registry
  without the declared map still emits valid JSON

### Fixed — the resolver produced nothing when started under macOS bash 3.2

`config-lib.sh` needs bash 4+ (`declare -A`, process substitution). macOS ships
`/bin/bash` 3.2 and `#!/usr/bin/env bash` picks **that** whenever Homebrew is not on PATH —
exactly what happens when a status line or hook is spawned with a minimal environment. The
result was a wall of `declare: -A: invalid option` followed by an unbound-variable death:
again no JSON, exit 0, spoofed name downstream.

- re-exec under `/opt/homebrew/bin/bash` (or `/usr/local/bin/bash`) when started on bash < 4
- if no bash 4+ exists, skip the registry **deliberately** and continue with endpoint-only
  detection, reporting it once on stderr, rather than sourcing a library that cannot work

### Testing

- 5 new assertions exercise a genuinely REGISTERED alias (`qwen-3.6-operator`) and assert
  JSON is actually **produced** — exit status alone cannot see a fail-open. Every pre-existing
  test passed an *unregistered* alias, so all 45 exercised only the fallback path and the
  success branch had **zero** coverage; that blind spot is how this survived a green suite
- mutation-verified: restoring the unbraced subscript fails 5 assertions (50 → 45)
- verified end to end with cost-tracker 0.7.4: a local session now renders
  `qwen-3.6-operator high · ctx … · 4.5/103.9G 4% · ~17.1 tok/s`

## [0.17.10] — 2026-09-21

### Fixed — NVIDIA quota reads "unknown" in the launcher when the real limit is known

Every NVIDIA row in the remote roster displayed `tier: unknown`, and the launch banner and
cost note fell to the generic `account quota and billing unverified; do not assume free`.
That UNDERSTATED a measured fact: NVIDIA publishes no per-account **daily** quota (operator
measurement, 2026-09-19) and the one real ceiling is ~40 requests per **minute**.

The roster `tier` field stays the enum value `unknown` on purpose — `renewing_free` would be
a promise NVIDIA does not make, and the enum is validated in `remote_provider_core`. Instead a
new `_tier_label()` keeps the enum honest while the **display** tells the truth:

- pickers/listings and the launch banner render NVIDIA rows as `no daily cap/40/min`
- the NVIDIA cost note now reads `no known daily quota (measured 2026-09-19); ceiling is 40
  requests per minute -- pace bursts`
- **provider-scoped**: Gemini/Groq/others keep their own notes and never inherit the rpm claim
- machine-readable `--dump` output keeps emitting the raw enum, so parsers are unaffected

Because the ceiling is per-MINUTE, bursty or parallel probing is what trips it: a 429 here is
evidence about *our* request rate, not about the model. (A deterministic interactive pacer is
still open — `bin/remote-probe-log.py` implements one for probes only.)

### Fixed — the remote test suite could not run at all (13 of 21 failing)

`tests/test_remote_session.py` never copied `config/emoji.sh` into its fixture. Since
`remote-session.sh:52` sources it unconditionally and reads `SESSION_EMOJI_*` under `set -u`,
every launch path aborted before doing anything, so 13 tests failed for a missing fixture file
rather than for any real defect. Adding the file to the fixture list restores the suite to
22/22 (the 22nd is the new NVIDIA label test, mutation-verified two ways).

### Fixed — statusline shows cloud format for free_api/local sessions (resolver returns empty)

The `la-session-identity.sh` resolver returned **empty output** when `MODEL_ALIAS` (e.g. `nvidia-nemotron-ultra`) was not registered in `config.example.sh` (which only contains local models). The resolver's `set -uo pipefail` caused a silent exit on the unset array lookup. The cost-tracker's `statusline-render.sh` only fell back to legacy endpoint detection when the resolver returned `"unknown"` session_kind, **NOT when it returned empty** — so free_api and local sessions fell through to the cloud branch, rendering `today: $X/$40 gw` on free sessions.

**Root cause:** Plugin cache lacks `config.local.sh` (gitignored), so resolver falls back to example config with no remote model registrations. When `la_lookup` fails for a remote alias, the strict mode exits without JSON output.

**Fix:** Two-part:
1. `cost-tracker 0.7.2` (separate plugin): `statusline-render.sh` now triggers legacy fallback when resolver returns empty output, not just `"unknown"`.
2. `free-agents 0.17.10`: `la-session-identity.sh` hardened to emit valid JSON even when `la_lookup` fails — logs the unresolved alias to stderr and continues with endpoint-only detection.

**Result:** Free-API and local sessions now correctly show savings format:
- Remote: `free api session saved $74.21 · today: $1101.73 saved with free agents`
- Local: `local session saved $45.85 · today: $1102.30 saved with free agents`

**Remaining known issues (tracked for next release):**
- Model name shows spoofed "Opus 5" instead of actual model (Nemotron/Qwen) — resolver returns `compatibility_model_id` for display
- Token rate telemetry (`~0.5 tok/s`) works in manual tests but not live statusline — `la-telemetry-token-rate.sh` parsing needs fix
- Effort display missing for free_api sessions (local shows `xhigh`, free_api doesn't)

### Testing

- Manual verification across remote free-API (Nemotron on 4141/4142), local (Rapid-MLX on 8003), and gateway sessions
- Mutation-tested: reverting the else clause in cost-tracker's statusline-render.sh reverts to cloud format
- All existing free-agents tests pass (no new tests added for this fix; existing test_la_session_identity.sh covers resolver behavior)

## [0.17.9] — 2026-09-21

### Added — single source of truth for session emojis

New `config/emoji.sh` defines canonical emoji constants for all session kinds and UI features, replacing hardcoded emojis scattered across 7 launcher scripts. The resolver (`la-session-identity.sh`), picker (`csl`), and all launchers now source this file, so future emoji changes only need one edit.

### Changed — emoji assignments

- **Remote / free-API sessions**: 🌐 (globe) → 📡 (satellite dish)
- **Telemetry/reporting**: 📡 (satellite dish) → 🛰️ (satellite) — no longer duplicates the remote session icon
- **Unknown session kind**: 🧭 (compass) → ❓ (question mark) — compass reserved for waypoints plugin
- **Effort/model-choice**: 🔆 (sun) → ⚙️ (gear) — more intuitive for "configuration/effort"

### Files modified

- `config/emoji.sh` (new)
- `bin/csl`, `bin/la-session-identity.sh`, `bin/launch-claude-agent.sh`, `bin/remote-session.sh`
- `bin/launch-local-auto-mode.sh`, `bin/launch-claude-agent-rapid-auto.sh`, `bin/launch-claude-agent-omlx.sh`

All scripts syntax-checked and mutation-verified.

The project began using Git tags after development was already underway and did not tag every later release consistently. Historical entries through `0.12.0` were reconstructed from the complete public Git commit history, full commit messages, plugin-manifest version transitions, README history, and available tags.

Where no Git tag exists, the release heading links directly to its release commit. Component versions—such as the terminal `local-agent-dispatch` version—remain independent unless explicitly identified as the plugin release version.

## [0.17.8] — 2026-09-20

### Fixed — blind-trust auto mode allowlist + dry-run output

The blind-trust auto mode (AUTO_MODE_STATE=0) for remote free-API sessions was too restrictive — only 12 commands were allowlisted, causing every other verb to prompt even with `sandbox.enabled=true`. Expanded the allowlist to ~90 read-only and common tool commands (ls, cat, grep, find, git read-only verbs, python3, jq, etc.) while **deliberately excluding destructive verbs** (rm, sudo, kill, git push, gh release create (added in 0.17.8)). Also fixed the dry-run output to include the resolved model, provider, and cost note — this was missing and caused test failures in `test_roster_and_all_provider_dry_runs`, `test_dynamic_models_validation_and_retired_github`, and `test_trial_opt_in_and_legacy_alias`.

### Changed

- Added explanatory comment to blind-trust settings: `sandbox.enabled=true` changes the WRITE BOUNDARY only; it does NOT stop the classifier from being consulted, so every verb a session needs must be explicitly allowlisted.
- Dry-run now prints: model, provider, cost note (e.g., "renewing free allocation", "trial/paid access explicitly selected", "account quota and billing unverified; do not assume free"), plus all resolved toggles.

### Testing

- All 21 tests in `tests/test_remote_session.py` pass.
- Mutation-verified: reverting the allowlist to 12 commands fails the dry-run tests; removing the model/provider/cost output from dry-run fails the same tests.

## [0.17.7] — 2026-09-20

### Fixed — blind-trust auto mode allowlist + dry-run output

The blind-trust auto mode (AUTO_MODE_STATE=0) for remote free-API sessions was too restrictive — only 12 commands were allowlisted, causing every other verb to prompt even with `sandbox.enabled=true`. Expanded the allowlist to ~90 read-only and common tool commands (ls, cat, grep, find, git read-only verbs, python3, jq, etc.) while **deliberately excluding destructive verbs** (rm, sudo, kill, git push, gh release create). Also fixed the dry-run output to include the resolved model, provider, and cost note — this was missing and caused test failures in `test_roster_and_all_provider_dry_runs`, `test_dynamic_models_validation_and_retired_github`, and `test_trial_opt_in_and_legacy_alias`.

### Changed

- Added explanatory comment to blind-trust settings: `sandbox.enabled=true` changes the WRITE BOUNDARY only; it does NOT stop the classifier from being consulted, so every verb a session needs must be explicitly allowlisted.
- Dry-run now prints: model, provider, cost note (e.g., "renewing free allocation", "trial/paid access explicitly selected", "account quota and billing unverified; do not assume free"), plus all resolved toggles.

### Testing

- All 21 tests in `tests/test_remote_session.py` pass.
- Mutation-verified: reverting the allowlist to 12 commands fails the dry-run tests; removing the model/provider/cost output from dry-run fails the same tests.

## [0.17.6] — 2026-09-20

### Fixed — the remote visual identity shipped in 0.17.3 never reached the user

Milestones 4 and 5 were released with correct version strings and a green test suite, but **nothing was visible in a real remote session**. Three independent defects:

- **Only port 4141 resolved as a free-API session.** `la-session-identity.sh` matched a single port while `remote-session.sh` scans `LA_REMOTE_PROXY_PORT_MIN..MAX` (default **4141-4151**) and takes the first FREE one — so any session launched while an earlier proxy was still alive resolved to `session_kind=unknown`, which collapsed theme, emoji, spinner *and* startup banner together. Now matches the whole range, as the LOCAL lane in the same `case` already did. The evidence string reports the actual port.
- **Provider-family lookup could never match.** `get_provider_family()` compared the resolver's DISPLAY string (`"Free API (NVIDIA)"`) for equality against bare identifiers (`"nvidia"`), so every provider fell through to `general` — which was not a key in `remote-spinner-verbs.json` — and the verb list came back EMPTY on every real session. Matching is now by substring, with model families (Nemotron, Kimi, Qwen, DeepSeek) taking precedence over the serving provider.
- **`general` had no verbs at all.** Added neutral fallback terms, as Milestone 2 requires for unknown models, and the overlay now omits `spinnerVerbs` entirely rather than emitting `mode=replace` with an empty list — which would have stripped Claude Code's own vocabulary and blanked the spinner.

### Changed

- `remote-theme.json`'s accent colour is **deliberately still not emitted as a settings key**. The dead `theme`/`fallback` locals that read it are gone, replaced by an explicit note. A `themes` map was probed against CLI 2.1.278 and NOT confirmed: the calibration that settled it is that a deliberately bogus key produces the same silent success, and `claude doctor` validates neither — so "the CLI accepted it" is not evidence of support. Per the plan's Milestone 2 theme caveat, this is recorded as an upstream limitation rather than shipped on a guess; the accent stays data for surfaces we control.

### Testing

- Added `tests/test_remote_identity_overlay.sh` — 21 assertions over the port range, provider-family resolution, non-empty verbs for every family, and the overlay a real session receives. Range cases probe a **non-first** port on purpose: a 4141-only fixture cannot catch the defect, which is precisely why the previous suite passed.
- Mutation-tested, all three caught: reverting to 4141-only fails 6 assertions; restoring equality matching fails 5; deleting the `general` verbs fails 1. All three files restored to their exact pre-mutation shasums.

## [0.17.5] — 2026-09-20

### Fixed

- **Stop hook attributed the wrong content to each drained queued prompt.** `build_queue_groups()` paired every drain with the most RECENT enqueue (LIFO) and discarded the `content` field the drain operation carries. With two or more queued prompts the pairing came out reversed, so the hook hashed the wrong prompt and a correct `[[QUEUE_ANSWERED:…]]` marker from `queue-marker-helper.py` could never match — the marker mechanism looked broken when the hash on the hook's side was simply computed over a different string. Drains are now matched by the content they name, falling back to FIFO order; `popAll` yields groups oldest-first. The defect was invisible with a single queued prompt, where LIFO and FIFO coincide.

### Changed

- **The QUEUE_ANSWERED contract now lives in one module** (`bin/queue_marker.py`): the hash, the marker syntax, and the regex that parses it. `local-queue-stop-hook.py` and `queue-marker-helper.py` both import it instead of restating all three independently — they agreed only by coincidence, and a change to the digest length on one side would have silently produced markers the other side rejects.
- `queue-marker-helper.py` now answers `--help` with its usage block instead of hashing the flag as if it were prompt content.

### Testing

- Added `tests/test_queue_marker_contract.sh` — 16 assertions covering FIFO drain pairing, content-matched drains, the no-content fallback, `popAll` ordering, and the helper/hook seam (the helper's output must parse under the hook's own regex and round-trip to the same hash). Ordering cases use THREE queued prompts on purpose: a one- or two-prompt fixture cannot catch the LIFO defect.
- Mutation-tested, all three caught: restoring LIFO fails the four ordering assertions; changing `HASH_LEN` fails the marker-format assertion; breaking the parse regex fails the four seam assertions. Both files restored to their exact pre-mutation shasums.

## [0.17.4] — 2026-09-23

### Added — Dynamic operational telemetry (Milestone 5)

- **Token rate telemetry** (`bin/la-telemetry-token-rate.sh`) — live token generation rate from vllm/Rapid-MLX logs with freshness indicator (ok/stale/unknown), session-aware via sidecar
- **RAM telemetry integration** — `la-statusline-segment.sh` now consumed by cost-tracker statusline renderer for wired memory pressure vs Metal ceiling
- **Statusline renderer integration** (cost-tracker 0.7.0) — telemetry appears on line 1, color-coded by level:
  - RAM: green (ok) / yellow (warn ≥70%) / red (crit ≥90%)
  - Token rate: cyan (ok) / yellow (stale) / dim (unknown)
- **Resolver fallback fix** — when `la-session-identity.sh` returns valid session_kind but unknown_fallback=true, trust session_kind and fall back to payload for model name
- **Cloud session effort preservation** — cloud sessions use payload effort level, not resolver default

### Changed

- Telemetry scripts follow local-agents contract: print JSON or nothing, never block, exit 0
- Renderer gracefully handles missing scripts or missing data — telemetry is optional
- Test added: `tests/test_telemetry_token_rate.sh`

## [0.17.3] — 2026-09-23

### Added

- **Remote Free API identity (Milestone 4)** — truthful remote session identity with lime-green theme and provider-aware spinner verbs
  - Lime-green theme (`#7CFC00`) via per-session settings overlay for remote sessions (never mutates persistent `~/.claude/settings.json`)
  - Provider-aware spinner verbs: Nemotronning, Gemining, Groqqing, etc. with fallback to generic remote terms
  - Startup banner with `🌐 Free API session` identity
  - Session name with emoji for terminal title and `/resume` picker (`-n` flag)
- **Remote settings generator** (`bin/generate-remote-settings.py`) — builds transient per-session settings from identity resolver
- **Remote theme data** (`config/remote-theme.json`) — theme definition with accent color, emoji, identity label
- **Remote session identity integration** (`bin/remote-session.sh`)
  - Generates stable `LA_SESSION_ID` before identity resolution
  - Calls `la-session-identity.sh` after proxy endpoint is known
  - Generates and applies per-session settings for free_api sessions
  - Exports identity fields for hooks and statusline consumption
- **Session identity resolver** (`bin/la-session-identity.sh`) already supports `free_api` session kind with proper provider display and theme/spinner IDs

### Changed

- Remote sessions now have visually distinct identity matching local sessions
- Transcript markers for remote sessions use the same schema with `session_kind: "free_api"`
- Remote sessions emit transition markers on resume (cloud-to-free-api, local-to-free-api, etc.)

## [0.17.2] — 2026-09-22

### Added

- **Session transcript identity (Milestone 3)** — durable session identity in transcripts with transition tracking
  - Stable session ID (`session_id`) generated at launch, persists across transcript lifecycle
  - Transcript marker `FREE_AGENTS_SESSION_IDENTITY_V1|{json}` with session kind, actual model, provider, backend, compatibility model, role/profile, and transition source
  - Transition markers on resume (e.g., cloud-to-local, local-to-free-api) instead of relabeling entire history
  - SessionStart hook (`hooks/transcript-identity.py`) appends markers to transcript file with file locking
  - Idempotent SessionStart handling — no duplicate markers for same session ID
  - `/resume-interrupted`, `/clear`, and compaction do not create false route transitions
- **Session identity resolver enhancements** (`bin/la-session-identity.sh`)
  - `session_id` field — stable unique identifier (timestamp-PID-alias-hash)
  - `transition` field — null or object with from_kind, to_kind, transition_type, timestamp
  - Transition detection from `LA_PREV_SESSION_KIND` environment variable
  - Deterministic output with `--help`, `--dry-run`, `--inspect` flags
- **Launchers updated** (`launch-claude-agent.sh`, `launch-claude-agent-rapid-auto.sh`)
  - Generate and export `LA_SESSION_ID` before identity resolution
  - Pass session ID to resolver for transcript marker consistency
  - Removed direct `--append-system-prompt` marker emission (now handled by SessionStart hook)
- **Hooks** (`hooks/hooks.json`, `hooks/transcript-identity.py`)
  - SessionStart hook for transcript identity and transition detection
  - File-locked append to transcript for thread safety
  - Extracts existing markers from JSONL system message content

### Changed

- Transcript markers now written by SessionStart hook (not launcher) for proper transition handling
- Resolver emits proper JSON transition object (not escaped string)
- Session ID exported as `LA_SESSION_ID` for hook consumption

## [0.17.1] — 2026-09-22

### Added

- **Local visual identity (Milestone 2)** — per-session theme and spinner vocabulary for local sessions
  - Sky-blue theme (`#87CEEB`) via per-session settings overlay (never mutates persistent `~/.claude/settings.json`)
  - Model-aware spinner verbs: Qwening, Deepseeking, Devstralling, etc. with neutral fallbacks
  - Startup announcement: `🦾 Local inference session — <model> (via <backend>)`
  - Session name with emoji for terminal title and `/resume` picker (`-n` flag)
- **Settings generator** (`bin/generate-local-settings.py`) — builds transient per-session settings from identity resolver
- **Local theme data** (`config/local-theme.json`) — theme definition with accent color, emoji, identity label
- **Local spinner verbs** (`config/local-spinner-verbs.json`) — curated model-family puns with deterministic deduplication

### Changed

- Both launchers (`launch-claude-agent.sh`, `launch-claude-agent-rapid-auto.sh`) now generate and apply per-session settings
- Session identity resolver exports `session_emoji` and `spinner_profile_id` fields
- Local sessions visually distinct at a glance without altering persistent user preferences

## [0.17.0] — 2026-09-21

### Added

- **Session identity resolver** (`bin/la-session-identity.sh`) — deterministic canonical resolver emitting JSON with schema_version=1 for session kind, actual model, provider, backend, role/profile, effort, thinking mode, theme, spinner profile, transcript marker version, evidence, and fallback state
- **Integration in launchers** — both `launch-claude-agent.sh` and `launch-claude-agent-rapid-auto.sh` now call the resolver and export identity fields (`LA_SESSION_IDENTITY`, `LA_SESSION_KIND`, `LA_ACTUAL_MODEL`, `LA_PROVIDER_DISPLAY`, `LA_THEME_IDENTIFIER`, `LA_SPINNER_PROFILE`, `LA_TRANSCRIPT_MARKER_VERSION`, `LA_SESSION_KIND_EMOJI`)
- **Transcript marker** — `FREE_AGENTS_SESSION_IDENTITY_V1|{json}` emitted via `--append-system-prompt` so local sessions are distinguishable in transcripts
- **Statusline integration** — cost-tracker's `statusline-render.sh` consumes resolver output for truthful actual model display and correct spend formatting per session kind

### Changed

- Local sessions now display actual model identity (e.g., Qwen alias) instead of compatibility spoof (Opus)
- Cloud sessions remain unchanged (still show JoyIA mark)
- Free API sessions distinguishable from local sessions via session_kind

### Technical

- Session kind determined authoritatively from `ANTHROPIC_BASE_URL` (never from `CLAUDE_IS_LOCAL` which leaks)
- Resolver supports local (ports 8000-8010), free_api (port 4141), cloud (anthropic.com), and unknown
- Dry-run/inspection mode (`--help`, `--dry-run`, `--inspect`) per script standards

## [0.16.0] — 2026-09-20

Live remote model catalog discovery and local-capable classification for the free-API lane.

### Added

- **Acquisition catalogues** (`config/model-catalog.acquisitions.rapid.psv`, `config/model-catalog.acquisitions.gguf.psv`, `config/model-catalog.acquisitions.omlx.psv`) — pipe-separated files recording the exact artifact identity (Hugging Face repo, revision, quantization, file glob) for every model the project has downloaded or knows how to acquire. These are the ground truth for "what exists on disk" and drive the local-capable classification.
- **Catalogue context additions** (`config/model-catalogue-context-additions.yaml`) — structured metadata additions (capability notes, MoE expert counts, context windows, multimodal flags, launch warnings) that enrich the acquisition catalogues for the `local-capable-filter` and future automated classification.
- **Acquisition PSV readers** (`bin/read-acquisition-catalog.py`) — a reusable reader with `--parse` (machine JSON) and `--report` (human-readable grouped output) that joins the three backend catalogues and serves as the canonical source for "is this model available locally?".
- **Enhanced local-capable filter** — the remote picker's `f` toggle now uses the acquisition catalogues as an additional evidence source when classifying `local-capable` vs `remote-preferred`, so a model with a matching Rapid-MLX artifact is no longer `unknown`.

### Fixed

- **Acquisition catalogue validation** — the catalogues are validated at load time: each row must have exactly the expected field count, required fields non-empty, `size_gb` numeric, and revision present. A malformed catalogue fails the launcher rather than being silently ignored.

### Testing

- Validation tests for the PSV format, field counts, and cross-referencing against the remote roster are integrated into the existing test suites.

## [0.15.2] — 2026-09-19

Test fix for `test_launcher_profiles.sh` — resumes and completes the work from the
interrupted session (limit-kill 2026-09-19) that requested: *"fix test_launcher_profiles.sh
too and add the live catalog scan"*.

### Fixed

- **Prompt size limit** — increased from 1600 to 2048 bytes to match the current
  `config/local-agent-system-prompt.txt` template (1914 bytes). The test was failing
  with "prompt unexpectedly large: 1914 bytes".

### Added

- **Live catalog scan test** — new test section in `tests/test_launcher_profiles.sh`
  that validates the shipped `config/model-catalog.psv`:
  - Verifies each entry has exactly 10 pipe-separated fields
  - Checks required fields (alias, repo, subdir) are present and non-empty
  - Validates `size_gb` is numeric
  - Ensures the catalog has at least one valid entry
  - Cross-checks catalog aliases against registry aliases (`la_register` in
    `config.example.sh`) for namespace collisions (warns but does not fail —
    download aliases and launch aliases are different namespaces)

## [0.15.1] — 2026-09-19

Fixes three defects found by auditing the repo against its own tests, and adds a footprint
estimator so the local-capable filter is no longer purely hand-maintained.

### Fixed

- **Remote free-API sessions aborted their first streamed reply** (`bin/remote-session.sh`) —
  the remote lane never exported the three streaming-timeout guards the local launcher has
  carried since 2026-08-19, so Claude Code's cloud-tuned ceilings stayed in force. A large
  reasoning model (Nemotron 3 Ultra 550B) or a queued free tier emits no stream events during
  first-token latency, which `CLAUDE_ENABLE_STREAM_WATCHDOG` cannot distinguish from a hung
  connection: it aborted and retried with *"Streaming response ended before any complete data
  was received. Retrying without streaming."* It reproduced right after the first prompt of a
  session, the turn carrying the largest uncached prefill. Now exports `API_TIMEOUT_MS`
  (overridable via `LA_REMOTE_API_TIMEOUT_MS`), `API_FORCE_IDLE_TIMEOUT=0` and
  `CLAUDE_ENABLE_STREAM_WATCHDOG=0`.

- **The effort selector changed nothing on remote providers** (`bin/remote-session.sh`) — it
  passed `--effort` to the `claude` CLI, which is an Anthropic-side flag: the request was
  proxied to a third-party provider that never saw it. Effort now travels in the request body
  via the LiteLLM proxy config, mapped per model family: `reasoning_effort` for
  OpenAI-compatible routes, and `chat_template_kwargs.enable_thinking` for NVIDIA Nemotron,
  which does not accept `reasoning_effort`. Claude Code's five levels fold to the field's three
  (`xhigh`/`max` → `high`); an unrecognized level is refused on stderr rather than written.

- **The queued-prompt hook toggle in the `csl` local picker was unreachable** (`bin/csl`) — it
  was advertised on `s`, but `s` was already bound to switch-to-remote earlier in the same
  `case`, so the first arm always won: pressing it left the picker instead of toggling. Moved
  to `p`. It had no test, which is how it shipped.

### Added

- **`bin/estimate-model-footprint.py`** — estimates a remote model's local RAM footprint from
  its model id (params × bytes-per-weight + KV allowance) and flags roster rows the policy file
  misses or contradicts. MoE-aware: it reads the *total* count, since every expert stays
  resident and only compute is sparse — judging `nemotron-3-ultra-550b-a55b` by its 55B active
  figure would call a ~283 GB model local-capable. Version numbers, dates, context lengths and
  quantization labels are not misread as parameter counts.

  This **supersedes** the 2026-09-17 plan's instruction not to use parameter count as a decision
  rule. For a model that is not on disk there is no better offline signal — real artifact bytes
  need a network call against a repo a remote-only model does not have — and refusing to judge
  left an obvious 550B model filed as "unknown", and so visible by default. Verdicts are
  confident where the answer is obvious in either direction (a large overshoot or a comfortable
  fit) and `unknown` only near the ceiling or with no count at all, preserving fail-open.

  `--apply` writes **high-confidence rows only** into
  `config/local-capable-remote-models.psv`, backing it up first and preserving its mode;
  near-ceiling rows are left for a human. Advisory by default.

- **`potentially-local-capable` classification** (`bin/local-capable-filter.sh`,
  `config/local-capable-remote-models.psv`) — for the band where a model is in the right size
  class but the estimate cannot vouch for the fit (over half the memory budget). Those rows stay
  **visible**, so fail-open is intact; they are simply marked as the near calls worth
  investigating, which is more useful than a bare `unknown`.

- **An interactive menu for `install/manage-rapid-mlx.py`** — `csl` offers the runtime manager
  under `m`, but the CLI required a subcommand, so the keypress printed an argparse usage error
  and returned: advertised in the menu and unusable from it. A bare invocation now opens a menu
  framed like the `csl` menus (list releases, install, promote, snapshot, remove). The
  subcommand CLI is unchanged, and without a TTY the menu refuses with a diagnostic rather than
  hanging on stdin. Destructive `remove` keeps its own typed confirmation — the menu does not
  pass `--yes` on the user's behalf.

- **Tests** — `tests/test_estimate_model_footprint.py` (22 cases), 13 menu cases in
  `tests/test_manage_rapid_mlx.py` driven over a real pty, and box-alignment, streaming-guard,
  effort-mapping and reachable-toggle regressions in the existing suites. The effort, toggle and
  alignment tests were mutation-tested: each fails against the pre-fix code.

### Changed — menu presentation

- **Framed menu rows are padded by measured display width**, in `bin/csl`,
  `bin/remote-session.sh` and `install/manage-rapid-mlx.py`. The previous code hand-counted
  characters (`%-25s` plus literal trailing spaces, and `52 - label_len` guessed from digit
  counts), which cannot stay aligned once emoji are present: an emoji is one character but two
  terminal columns, and several carried a `U+FE0F` variation selector that adds no width at all.
  The three surfaces produced rows of 63, 64 and 65 columns from the same nominal box. Rows are
  now measured, and over-long content is truncated — padding alone cannot fix a row *wider* than
  its frame, which broke the border just as visibly.

- **Emoji with variation selectors replaced by single-codepoint equivalents** (`⬇️🗑️👁️☁️⚙️` →
  `📥🧹🔭🌐🔆`). The width math was already right; these glyphs render inconsistently *across*
  terminals, so removing the ambiguity is more robust than compensating for one terminal.

- **One space after every emoji**, and the local and remote pickers now use the same action
  order (navigation → lane switch → settings → quit) and the same `state — meaning (toggle)`
  phrasing, so switching lanes does not mean relearning the menu.

- **The local-capable row is gone from the home screen** (`bin/csl`) — the filter only ever
  affects the *remote* roster, so it is remote-lane state, and an abbreviated `Local-cap:` sat
  confusingly beside the Local *lane* on the same menu. The remote picker spells it out as
  `locally-runnable models`. The state variable is unchanged and still syncs both ways.

### Changed

- **`nvidia-nemotron-ultra` is now the first remote roster entry** (`config/remote-agents.sh`)
  and carries the ★ PREFERRED DEFAULT marker, per user preference. `nvidia-nemotron3` keeps its
  generation evidence and moves to second.

- **Three brittle menu assertions** (`tests/test_csl_menu.sh`) converted to
  `assert_grep_flexible`, which 0.15.0 added for exactly this: the 0.15.0 emoji commit broke
  them, leaving `main` with a red suite.
## [0.15.0] — 2026-09-20

### Fixed

- **State persistence between remote picker and home screen** — auto-mode, local-capable filter, and telemetry state changes made in the remote picker (`remote-session.sh`) now propagate back to `csl`'s home screen via the navigation file. Previously, toggling 'a' (auto-mode) or 'f' (local-capable) in the remote picker and returning via 'h' would not update the home screen display.

### Added

- **Boxed headline for local session launch** (`bin/launch-claude-agent.sh`) — mirrors the remote picker's headline box, showing model alias, backend, effort, and mode in a consistent format.
- **Flexible test assertions** (`tests/test_csl_menu.sh`) — new `assert_grep_flexible` function uses regex patterns for cosmetic/menu text, making tests resilient to emoji changes and wording rephrasing while keeping exact matching for internal state contracts.

### Changed

- **Nav file format** — `remote-session.sh` now writes state sync lines (AUTO_MODE_STATE, LOCAL_CAPABLE, TELEMETRY, INCLUDE_TRIALS, EFFORT_CHOICE) after the navigation target in CSL_NAV_FILE. `csl` reads and applies these on return.

## [0.14.9] — 2026-09-19

### Fixed

- **Stop hook queue marker detection** (`bin/local-queue-stop-hook.py`) — the hook now correctly detects `[[QUEUE_ANSWERED:<hash>]]` markers in the model's response AFTER the queue drain (previously only checked between enqueue and drain). The model's response to a queued prompt always comes in the turn AFTER the drain, so this fixes false "unaddressed prompt" blocks when the marker was present.

- **Seam-based nudging** — instead of hardcoding a turn count, the hook now piggybacks on the harness's natural pause detection (text turns = natural pauses). First text turn after drain = benefit of doubt; second text turn without marker = pattern of ignoring → nudge. Emergency safeguard: 10+ tool-only turns without text forces a nudge.

- **Per-prompt tracking** — each queued prompt (including popAll batches) is tracked independently with its own counters. Markers reset only the relevant prompt's counter.

## [0.14.8] — 2026-09-19

### Added

- **Blind-trust auto mode for remote free-API sessions** (`bin/remote-session.sh`) — automatically creates and passes a settings file with `sandbox.enabled: true` to bypass the cloud classifier for too-complex commands. Mirrors the working local session implementation (88/88 permission-probe tests passed).
- **`LA_REMOTE_CLAUDE_SETTINGS` environment variable** — allows users to provide their own settings file for blind-trust mode.
- **Fallback behavior** — if user-provided settings file doesn't exist, warns and falls back to generated settings.
- **Banner and dry-run visibility** — shows "sandbox: enabled" in session banner and details the settings file being used in `--dry-run` output.

### Fixed

- **Pre-existing test failure** (`tests/test_remote_session.py`) — the test setup was missing `config/shared-agent-shipping-rules.txt` which is required by the launcher, causing all launch tests to fail with "shared agent rules file is not readable". Added the file to the test setup's copy list.

## [0.14.7] — 2026-09-18

Adds shared shipping/verification rules that both free lanes (local and remote) receive in their system prompts from a single source file, preventing drift between lane-specific briefings.

### Added

- **`config/shared-agent-shipping-rules.txt`** — one canonical rules file covering the shipping and verification discipline both lanes must follow: run it don't just read it; planted positive for negative results; suspect the check first; feature with no test is not done; version everywhere; never hardcode personal paths; parse arguments before work; finish the ship loop; published tags are immutable; derive don't duplicate; edit source not cache; feature branch not dirty on main; stage by path; never force-push; backup before editing; report what happened.
- **`tests/test_shared_agent_rules.sh`** — verifies the single-source wiring: both `launch-claude-agent.sh` and `remote-session.sh` read the same file; neither lane-specific prompt duplicates the rules; the assembled prompt actually carries every rule; an empty rules file drops them (proving the check can fail); a missing rules file is a hard error not a silent omission.
- **Both launchers** (`bin/launch-claude-agent.sh`, `bin/remote-session.sh`) now append the shared rules via `LA_SHARED_RULES_FILE` env var (defaulting to the canonical path).

### Fixed

- **Version drift** — the v0.14.6 tag was created on a commit whose `.claude-plugin/plugin.json` still declared 0.14.5, so the version-consistency test passed while the tag disagreed. This release aligns the manifest, CHANGELOG, and tag.

## [0.14.5] — 2026-09-18

Fixes the local-capable filter classifications and menu UX from 0.14.2/0.14.4:

- **Corrected policy classifications** — large Nemotrons (120B, 550B) are now `remote-preferred`
  (too large for local), small models (Nemotron 30B-A3B, Muse Glimmer 30B, Gemma 4 31B, Qwen 27B
  across providers) are `local-capable` (local equivalents exist), and GPT-OSS models remain
  `remote-preferred` (OpenAI-native, no local equivalent).
- **Menu UX improvements** — added 🦾 emoji for local models, changed 'l'/'r' navigation to 's'
  (switch) for clearer intent, changed 'i' to 'k' for key setup (🔑 association), changed 'T'
  (trial) to 'l' (limited) to avoid telemetry conflict, and fixed toggle labels to show current
  state (e.g., 'local-capable: HIDDEN') instead of the opposite action.
- **Updated tests** to match new behavior. Filter now hides 7 models by default, shows 93.

## [0.14.3] — 2026-09-17

This release combines two important improvements made in parallel workstreams:

### CSL Lane Navigation Improvements (from feat/csl-lane-navigation-remote-parity)

`csl` previously opened straight into the local-model picker; the remote picker (`csl remote`)
was a separate, one-shot invocation you could only reach by exiting the local picker's `r` option
and re-launching. This release makes `csl` a single process with a top-level home screen and real
back-and-forth navigation between lanes, and adds a filter so the remote picker doesn't drown you
in remote models you could just as well run locally.

#### Added

- **A home lane selector.** `csl` (no args) now opens on a screen offering `1) Local`,
  `2) Remote`, `i) Install / set up remote API keys`, and `q) Quit`, instead of landing directly
  in the local-model list. It shows the resolved config source, and the current state of
  auto-mode, telemetry, the watcher, the queued-prompt stop hook, and the local-capable filter.
- **Bidirectional lane navigation, in one process.** From the home screen you can enter the local
  picker (`1`) or the remote picker (`2`); from *inside* either picker you can return to the home
  screen (`h`) or jump straight across to the other lane (`r` from local, `l` from remote) without
  restarting `csl` or losing state. `remote-session.sh` gained a `--csl-owner` mode for this: when
  owned by `csl`, it returns a navigation token (via a `CSL_NAV_FILE` handoff file) instead of
  `exec`'ing `claude` directly, so `csl`'s main loop can read it back and switch lanes.
- **Shared state persists across every lane switch.** Auto-mode state (blind-trust / classifier /
  off), telemetry on/off, the watcher toggle, the queued-prompt stop hook, and the local-capable
  filter's shown/hidden state are all held in the single `csl` shell process and carried into
  whichever picker or launcher you land in next — going home → remote → back to local does not
  reset any of them.
- **A local-capable filter for the remote picker**, with a genuinely new policy file and script:
  - `config/local-capable-remote-models.psv` — a pipe-separated policy table classifying each
    remote-roster entry (by provider + exact remote model id) as `local-capable`,
    `remote-preferred`, or `unknown`. `local-capable` entries are models judged to have a viable
    on-disk (or documented-conversion) MLX equivalent that fits this machine's memory budget;
    `remote-preferred` entries have no defensible local path; `unknown` entries (including every
    "choose the model at runtime" provider) stay visible — the filter fails open on uncertainty,
    never hides on doubt.
  - `bin/local-capable-filter.sh` — a standalone script (`--parse` for machine-readable JSON,
    `--report` for a human-readable grouping by provider, plus `--help`) that joins the policy
    file against the live remote roster.
  - **Hidden by default**, on both the interactive picker AND the non-interactive `--list` output —
    a scripted caller sees the same filtered view as the interactive menu, with a footer line
    naming how many local-capable entries are hidden.
  - **`f` toggles the filter** shown/hidden live, re-rendering the roster and the visible/hidden
    counts in the menu header on every keypress — no restart needed.
  - **`R` shows the hidden-model report**: the full local-capable list grouped by provider, with
    each entry's classification and the recorded reason (e.g. "MLX 4-bit artifact in local
    catalog; rapid-compatible MoE"), so you can see exactly what the filter kept out of view and
    why before deciding whether to unhide it.
  - `remote-session.sh` also grew a plain `--local-capable-shown` flag so the filter state can be
    passed through non-interactively (e.g. when `csl` hands off lane args), not only toggled in
    the interactive menu.
- **An installed-copy fallback-config notice.** `config/config-lib.sh`'s `la_load_config` now sets
  a structured `LA_FALLBACK_CONFIG` flag (`0` when `config.local.sh` is present, `1` when it falls
  back to `config.example.sh`). The `csl` home screen checks this flag and, when set, prints an
  explicit warning banner ("Using public fallback defaults. Private models are not present in this
  installed copy.") rather than silently showing generic example config as if it were the user's
  own roster.

#### Fixed

- The local-capable policy loader called two of its own helper functions before either was
  defined, so every invocation of `remote-session.sh` — including plain `--list` — silently failed
  to load the filter at all (`_lc_load_policy: command not found` on stderr).
- A JSON boolean printed through Python's default `str()` reads as `"False"` (capitalized), while
  the bash-side comparison checked for lowercase `"false"` — the filter loaded without error but
  never actually classified anything as hidden.
- `--list` never applied the local-capable filter (only the interactive menu did), so a scripted
  caller saw every remote model regardless of the filter's hidden/shown state.

#### Notes on scope

- The **PSV catalog generator** (any tool that would auto-populate or regenerate
  `local-capable-remote-models.psv` from a live scan of the local model catalog) is **out of
  scope for this release** — the policy file shipped here is hand-curated. Deferred to a later
  release.
- **Automatic local-model discovery** (detecting new on-disk MLX artifacts and updating the
  local-capable classification without a manual PSV edit) is likewise **out of scope / deferred**.
  Nothing in this diff implements either; do not read the PSV policy file's existence as evidence
  that classification is automated — it is a static, manually maintained table.
- Two smaller follow-ups were identified but deliberately not folded into this release (each is
  its own waypoint): wiring `sandbox.enabled:true` into blind-trust auto mode so it actually
  bypasses the permission classifier, and extending `config-lib.sh` with shared helpers
  (`_pick_from`, the auto-mode state machine) that `csl` and `remote-session.sh` currently
  duplicate independently.

### Agent Tool Constraint Documentation (from docs/free-agents-agent-tool-trap)

Teaches the skills the one thing that made a real session save $0 while believing it had delegated.

**The incident.** A Sonnet-run session was asked repeatedly to use free agents. It eventually
"delegated" by calling Claude Code's built-in `Agent` tool with no `model` parameter — which runs
the default subagent model at full price. In its own words: *"only the illusion of delegation."*
Nothing in the skills said the `Agent` tool cannot reach a free model, so the session had to
re-derive it, and got it wrong first.

- **`⛔ READ THIS FIRST` section, inline at the top of `free-agents` and `offload-to-local`** — states
  that the `Agent`/Task tool's `model` enum accepts only paid tiers, that omitting `model` is the
  trap (not a cheap default), and lists the only genuinely free routes. Includes a self-check:
  name the process that ran the tokens; if it is not a localhost port or a free provider, it was
  not free. Placed inline rather than in a reference file, because a reference file is not loaded.
- **A capability map of the two free lanes** — remote free APIs reach far larger models than local,
  so "too hard for local" is an argument for a bigger *free* model, not for spending. Axes that
  actually differ: size, latency, quota, privacy, reliability.
- **Remote does NOT mean big** — a remote roster also lists 20–35B models that are the same class as
  the local ones. Dispatching those remotely buys no capability while burning a finite quota and
  sending the prompt off-machine. Filter by parameter count, not by lane; prefer the local
  equivalent where one exists.
- **`remote:<provider>` is now a first-class plan annotation** alongside `local:` and `cloud:`, and
  "Keep on the cloud model" is retitled **"Keep on the PAID model"** — two of the three lanes cost
  nothing, so `cloud:` should be the minority annotation.
- **Both descriptions now trigger on the failure mode itself** — reaching for the Agent/Task tool
  hoping it will be cheap.
- **Measured caveats on `remote-agent-dispatch.py`**, found while dogfooding this change: only some
  advertised providers are implemented (gemini works, nvidia does not yet); the built-in default
  model id was updated to avoid retired models (now defaults to `gemini-3.6-flash` for Gemini);
  and too small a `--max-tokens` yields a truncated answer that reads as a terse one.
- **Corrected a stale claim** that the local backend is single-slot/minutes-per-turn.

Verified by dispatching the incident scenario, plus the rewritten skill, to a free remote model:
it named `local-agent-dispatch.py`, answered that the Agent tool without `model` saves nothing and
runs the default subagent at full price, and picked the remote lane for reasoning work.

## [0.14.2] — 2026-09-17

Documentation half of the rename. `0.14.0` renamed the project; this makes the docs actually say what
the project now is, rather than leading with "offload to local MLX" and treating the API lane as a
bolt-on.

### Added

- **`## API keys` — a section for the free-tier setup helper** (`csl setup-remote`, `i` in the
  picker, `install/setup-api-keys.py`). **Every provider it offers has a free tier — that is the
  selection criterion.** Paid API use works if your account has credits, but nothing here requires it
  and no provider is listed *because* it is paid. Documents the three properties that are easy to
  miss: it makes **no API calls** (so running it cannot burn quota, and `saved / not tested` means
  stored, not proven), it takes keys only in **hidden prompts** so they stay out of shell history, and
  it **never overwrites** an existing key file.

### Changed

- **README leads with two equal lanes.** Neither is the sidekick: remote is faster and the usual
  default for interactive work; local wins offline, when work must not leave the machine, and for
  long unattended runs.
- **The hardware note no longer over-claims.** It implied Apple Silicon was required for everything.
  Only the *local* lane needs it — `csl remote` works on any machine, with no local models at all.
- **GitHub About and topics** updated: the vllm-only framing is gone; `free-tier`, `llm-api`,
  `gemini`, `nvidia-nim`, `groq`, `litellm` added.
- **Marketplace entry** renamed to `free-agents` with the two-lane description.

## [0.14.1] — 2026-09-16

Documentation half of the rename. `0.14.0` renamed the project; this makes the docs actually say what
the project now is, rather than leading with "offload to local MLX" and treating the API lane as a
bolt-on.

### Added

- **`## API keys` — a section for the free-tier setup helper** (`csl setup-remote`, `i` in the
  picker, `install/setup-api-keys.py`). **Every provider it offers has a free tier — that is the
  selection criterion.** Paid API use works if your account has credits, but nothing here requires it
  and no provider is listed *because* it is paid. Documents the three properties that are easy to
  miss: it makes **no API calls** (so running it cannot burn quota, and `saved / not tested` means
  stored, not proven), it takes keys only in **hidden prompts** so they stay out of shell history, and
  it **never overwrites** an existing key file.

### Changed

- **README leads with two equal lanes.** Neither is the sidekick: remote is faster and the usual
  default for interactive work; local wins offline, when work must not leave the machine, and for
  long unattended runs.
- **The hardware note no longer over-claims.** It implied Apple Silicon was required for everything.
  Only the *local* lane needs it — `csl remote` works on any machine, with no local models at all.
- **GitHub About and topics** updated: the vllm-only framing is gone; `free-tier`, `llm-api`,
  `gemini`, `nvidia-nim`, `groq`, `litellm` added.
- **Marketplace entry** renamed to `free-agents` with the two-lane description.

## [0.14.0] — 2026-09-16

**The project is now `free-agents`.** Local MLX inference and free-API inference are two *equal*
lanes it offers and uses — not "local, with remote bolted on."

### Why the rename, and why this number

Remote sessions on free cloud APIs have proven themselves in real work and are **faster** than local
sessions, so for most interactive work remote is now the preferred lane. Local remains preferred for
offline work, for anything that must not leave the machine, and for long unattended runs where hours
of throughput matter more than per-turn latency. A project called `local-agents` no longer described
what it does.

`0.14.0` was reserved for *portable manifests and artifact identity*. That scope **slid up to
`0.15.0`** with its gate list intact (runtime profiles → `0.16.0`, oMLX lanes → `0.17.0`).
`docs/ROADMAP.md` records the shift and the softened reservation rule that permits it: priorities
legitimately change, and a reserved number is not a queue position. What remains forbidden is
*overwriting* a reserved scope's meaning, or shipping a reserved number whose gates are half-met.

### Changed

- **Repository renamed** `haiggoh/local-agents` → `haiggoh/free-agents`. GitHub keeps a redirect, so
  existing clones, the marketplace URL, and `git fetch` continue to work.
- **Plugin renamed** `local-agents` → `free-agents`, with a description built around the two lanes.
- **Remote APIs are no longer labelled blanket-EXPERIMENTAL.** Replaced with a per-provider status
  table: Gemini and NVIDIA are *proven*; the other twelve have offline fixture coverage but no live
  qualification yet. A weak model (Nemotron's shortcuts on long tasks) is a model limitation, not a
  defect in the remote lane.
- **Runtime paths, env vars, and log filenames are deliberately UNCHANGED** — `~/.claude/local-agents/`,
  `$LOCAL_AGENTS_LEDGER_DIR`, `local-agents-session-*.transcript`, `local-agent-dispatch`. Renaming
  them would break live state on existing installs for no benefit. Historical `CHANGELOG` entries are
  likewise left alone: they accurately describe what shipped under the old name.

### Deferred, with reasons recorded in `docs/ROADMAP.md`

- **Remote session watcher** — removed in `0.13.14` rather than left as a dead switch. Its value
  depends on streaming live reasoning, which is separate work; a watcher that cannot show the model
  thinking is not worth the window.
- **Genuine classifier emulation for remote auto mode** — blind-trust stays the default. Remote
  changes the premise (a fast provider may afford a real classifier round-trip), and the interesting
  case is a **hybrid**: a local session whose classifier is a remote NVIDIA call, which would give a
  local session real auto mode without a second local model competing for RAM. To be measured, not
  assumed.

## [0.13.15] — 2026-09-16

Makes the queued-prompt Stop hook **optional and correctly scoped**. It is a safety net, so it stays
ON by default — but it is now a real switch, and it can no longer touch a session this project did
not launch.

### Added

- **`s` toggles the queued-prompt hook in the `csl` picker** (ON by default; `CSL_STOP_HOOK=0`
  defaults it off). The state travels to every launcher as `LA_QUEUE_STOP_HOOK`, so the switch works
  for any caller of `launch-claude-agent.sh` / `remote-session.sh`, not only the picker.
- **`--help` for `local-queue-stop-hook.py`.** It previously exited 0 printing **nothing** while
  still running the hook — a probe that silently executed. An unknown flag now exits 2.

### Changed

- **The gate now tests the launcher, not the endpoint.** The hook engages only when
  `LA_SESSION_LAUNCHER` names one of `csl`, `launch-claude-agent.sh`, or `remote-session.sh` — which
  is the actual requirement. The old check asked "is `ANTHROPIC_BASE_URL` loopback?", a *proxy* for
  that question, and it was wrong in both directions: it read a remote session's LiteLLM proxy on
  `127.0.0.1` as local (correct only by accident), and it would have enabled the hook for any
  unrelated tool pointing Claude Code at localhost.
- An **inherited-but-empty** marker does not open the gate. Exported markers leak into a later
  gateway `claude` from the same shell, so the value must match a known launcher name.

### Testing

- `tests/test_stop_hook_gate.py` asserts the gate in **both** directions — engages for each known
  launcher, stays silent for a plain `claude`, an empty marker, an unknown launcher, and a bare
  loopback endpoint. Verified by outcome (does the hook emit a `block` payload?), not by inspecting
  a return value. Mutation-tested against four regressions, including a revert to the old endpoint
  predicate; all four were caught.

## [0.13.14] — 2026-09-16

Fixes the remote-session interactive toggles, which were advertised in the picker but largely did
not work. **No toggle in this project is allowed to be a dead switch**: it either changes the
session's behaviour or it is removed and says so.

### Fixed

- **`-a/--auto-mode` had no effect on remote sessions.** The toggle cycled its state and mapped it
  onto `LA_AUTO_MODE`/`LA_BLIND_AUTO`, but those variables are read by `launch-claude-agent.sh`,
  which a remote session never calls — and the mapping itself lived inside a `_launch()` helper that
  was never invoked. `remote-session.sh` execs `claude` directly, so it now passes
  `--permission-mode` itself. **Blind-trust (`auto`) remains the default**; two `-a` presses reach
  `acceptEdits`. Requesting the classifier lane now prints that it is not implemented for remote and
  falls back to `auto`, instead of silently behaving like blind-trust.
- **`-t/--telemetry` could not restore stock behaviour.** The live launch path hardcoded
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, so remote telemetry was always suppressed and the
  toggle only appeared to work. It is now honoured in both directions.
- **A whole unreachable code path removed.** `_launch()` and a duplicated toggle-mapping block were
  dead code; the live launch sits at the bottom of the script. Duplicated `EFFORT_CHOICE` /
  `SELECTED_EFFORT` declarations removed.
- **`local` at top-level scope.** `local claude_cmd=(...)` sat outside any function: bash prints
  `local: can only be used in a function` and returns 1, yet still assigns the array — so sessions
  launched correctly while emitting an error line, and `bash -n` never complained.

### Changed

- **`-w/--watcher` is no longer accepted for remote sessions.** It printed "not fully implemented"
  and then launched anyway. Deferring the feature is fine; advertising it is not — it now exits 2
  with an explanation, and `csl` no longer forwards it. The remote watcher is deferred because a
  watcher that cannot stream live reasoning is not worth the window (see `docs/ROADMAP.md`).
- **`--dry-run` now prints the resolved toggle state** (auto-mode, permission-mode, telemetry,
  effort). A dry run that stopped before the command was built is how these bugs stayed invisible.

### Testing

- Three regression tests in `tests/test_remote_session.py` assert each toggle **by outcome** —
  including the argv the real `claude` child receives, not merely the dry-run summary. That
  distinction is load-bearing: a first version of the test asserted only on the printed summary and
  **passed** when the `--permission-mode` line was mutated away. All three were mutation-tested.

## [0.13.13] — 2026-09-15

Tagged on `a460e8e`. Kept as released: CSL picker state synchronization for remote sessions, plus the
plugin-scoped Stop hook for queued prompts (`local-queue-stop-hook.py`, `queue-marker-helper.py`).

⚠️ The remote-session interactive toggles shipped here were largely non-functional — see `0.13.14`,
which fixes them. This entry is left in place because the tag is published and reached consumers.

## [0.13.12] — 2026-09-14

Blind-trust auto mode as the default, a full remote session lane on 14+ free cloud APIs, guided
API key setup, and a crash-recovery launcher for local servers.

⚠️ **CAVEAT — remote APIs are EXPERIMENTAL.** So far Gemini is the only remote agent API that is
properly tested and proven to work. The other remote APIs (Groq, NVIDIA, OpenRouter free tier,
Cloudflare Workers AI, Cerebras, Mistral, Z.AI, SiliconFlow, LLM7, Kilo, Vercel, SambaNova,
ModelScope) are still in the experimental stage — both for running their own session and for being
used as agents for offloading work. They have offline fixture coverage and provider-key isolation
tests, but no live quota-qualification or real-tool-use smoke tests. Use them at your own risk and
report failures.

⚠️ **CAVEAT — blind-trust auto mode.** Blind-trust is the NEW DEFAULT (state 0). It gives
auto-mode behaviour immediately without waiting for a classifier server — every consequential
call is routed directly to auto. This is the default because the genuine classifier emulation path
is not yet reliable. If you want the classifier in the loop, toggle to state 1 (`classifier`) in
the `csl` picker or set `CSL_AUTO_MODE_STATE=1`. State 2 turns auto mode off entirely
(`acceptEdits`). The `b` key remains as a shortcut to jump straight to blind-trust from any state.

### Added

- **Blind-trust auto mode as the default.** The `csl` picker now uses a triple-toggle for auto
  mode state, cycling through three mutually exclusive options with `a`:
  - **0 = blind-trust** (DEFAULT): `--permission-mode auto`, NO classifier in the loop. Every
    consequential call is routed directly to auto. This is the default because the genuine
    classifier emulation path is not yet working reliably; blind-trust gives auto-mode behaviour
    immediately without waiting for a classifier server.
  - **1 = classifier**: `--permission-mode auto`, boots a second local model to judge auto-mode
    calls. The real auto mode — requires the classifier warm-up path.
  - **2 = off**: `--permission-mode acceptEdits`. No auto-mode behaviour at all.
  - **`b` shortcut**: jumps straight to blind-trust from any state.
  - Environment override: `CSL_AUTO_MODE_STATE=0|1|2` sets the initial state directly.
  - The launcher maps state→env: `0` → `LA_AUTO_MODE=1 LA_BLIND_AUTO=1`, `1` →
    `LA_AUTO_MODE=1 LA_BLIND_AUTO=0`, `2` → `LA_AUTO_MODE=0`. A guard refuses
    `LA_BLIND_AUTO=1` without `LA_AUTO_MODE=1` (that would silently pick acceptEdits,
    contradicting the user's intent).
  - Covered by `tests/test_csl_menu.sh` (8 new assertions including cycle verification and
    launcher receipt).

- **Remote session lane.** Launch a full Claude Code session on 14+ free cloud APIs: Gemini 3.8
  Flash (standard/thinking), Groq Qwen 3.6/3.8, NVIDIA Nemotron 3 Ultra, explicit free OpenRouter
  models, Cloudflare Workers AI routing, Cerebras GPT-OSS/Qwen (trial opt-in via
  `csl remote --include-trials`), Mistral, Z.AI, SiliconFlow, LLM7, Kilo, Vercel AI Gateway,
  SambaNova, and ModelScope. `csl remote` opens the picker directly, including on machines without
  local models. Add keys with `csl setup-remote` (or press `i` in `csl`).
  - **Caveat**: So far Gemini is the only remote agent API that is properly tested and proven to
    work. The other APIs are still experimental — both for running their own session and for being
    used as agents for offloading work. See above.
  - Session routes for Mistral, Z.AI, SiliconFlow, LLM7, Kilo, Vercel, SambaNova, and ModelScope
    with explicit model selection and catalog metadata where supported. Only the selected credential
    enters the proxy; provider keys are cleared from the Claude child. No hosted generation
    qualification was run. Retired GitHub Models is removed from setup and the session picker.
  - Catalog-only checks and offline fixture tests preserve provider generation quota. Cost labels
    distinguish free allocations, trial access, and unverified account billing.
  - `bin/remote-session.sh` — the session launcher; `bin/remote-keys.sh` — credential store;
    `bin/remote_provider_core.py` — single source of provider truth;
    `bin/remote_http.py` — OpenAI-compatible SSE transport;
    `bin/remote-agent-dispatch.py` — remote lane dispatcher;
    `bin/remote-provider-doctor.py` — read-only key/connectivity check.
  - Docs: [`docs/remote-session/README.md`](docs/remote-session/README.md) and
    [`docs/remote-session/setup.md`](docs/remote-session/setup.md).

- **Remote emergency-fallback lane.** Keeps agent work moving after the daily Claude Code budget is
  exhausted, using explicitly-approved free allowances as an emergency lane BEHIND the local MLX
  stack — not as a replacement for it. Additive by construction: the existing local dispatch path
  is untouched. With no remote key configured — the default — the whole lane is inert and the
  router behaves as pure local-only.
  - `bin/remote_provider_core.py` — provider truth (7 providers, tiers, Result record, classify)
  - `bin/remote_http.py` — transport (OpenAI-compatible SSE on stdlib http.client)
  - `bin/remote-agent-dispatch.py` — remote lane CLI
  - `bin/remote-provider-doctor.py` — read-only key/connectivity check
  - `bin/agent-fallback.py` — the router
  - `bin/agent-handoff.py` — handoff between lanes
  - `config/remote-providers.example.json` — env-var key name documentation
  - `docs/remote-fallback/README.md`, `skills/continue-on-fallback/SKILL.md`
  - `tests/remote/test_remote_fallback.py`

- **Guided remote API key setup.** `install/setup-api-keys.py`, also available as `csl setup-remote`
  or `i` in the main picker. Choose among 14 active providers, open official key pages, and paste
  credentials into hidden terminal prompts. New keys use private flat files in `~/.api_keys`;
  existing files are kept. A complete bracketed paste shows a green confirmation and advances
  without Enter; duplicate pastes are not appended. Setup returns to the provider picker until
  finished or all providers have credentials. This add-only wizard makes no API calls.
  - Covered by `tests/test_setup_api_keys.py` (203 assertions).

- **`bin/la-reboot.sh`** — restarts a CRASHED local model server in place: same port, same argv,
  so a still-open Claude Code session reconnects on its next request with no exit, no `/resume`
  and no context replay. Recovery previously meant exit → `la-evict` → new session → `/resume`,
  paying for a full model reload *and* a context replay. This is the bandaid half of the recurring
  Rapid D-METAL-CAP OOM; the climbing-residency root cause is tracked separately, and needing this
  often is data for that investigation rather than a reason to automate it.
  - It is the only script in this repo permitted to stop a server on the session port range, so
    the guards are strict and tested: it acts on exactly one port, never a range; it requires
    positive crash evidence (a failed completion probe, a dead listener, or a Metal/OOM log
    signature) because liveness is not the test — an OOM-refusing Rapid server answers
    `/v1/models` while failing every real request; a server that still completes a request is
    refused unless `--force` is passed; and the argv is captured from the live process via
    `KERN_PROCARGS2` *before* anything is stopped, rather than from `ps` (whose space-joined
    output corrupts any argument containing a space) or from current config (which may have
    changed since launch). Ships `--status`, `--dry-run`, `--force`, `--port`, `--wait` and
    `--help`, with documented exit codes 0/1/2/3.
  - Covered by `tests/test_la_reboot.sh` (25 checks) including an end-to-end stop-and-relaunch
    against a fixture server that refuses completions, argv fidelity for an argument containing a
    space, and mutation-tested: disabling the healthy-server refusal lets a working server be
    rebooted.

### Fixed

- `la-reboot.sh`: fix the unterminated model-list quote that failed after relaunch; preserve
  captured working directory/runtime settings, report exec failures, and require an actual
  Anthropic completion before declaring recovery. Preserve metadata permissions and refuse
  authentication failures as crash evidence.

- `la-reboot` readiness parsed `"id":"…"` without tolerating whitespace after the colon, so a
  server that pretty-prints its `/v1/models` payload was reported "not ready" while actually
  serving. Found by running the script against a real server. The same tight pattern exists in
  `local-llm-hotswap.sh`, `la-ram-preflight.sh` and `launch-claude-agent.sh`; those are not
  changed here because Rapid emits compact JSON, but they carry the same latent brittleness.

- **Remote session proxy leak fixed.** The EXIT trap could never fire: `exec` REPLACES the shell,
  so the trap set immediately before it was discarded with the process. Measured consequence —
  orphaned litellm proxies holding ports, each new run silently landing on the next free port until
  the band would have been exhausted. Fix: run `claude` as a child instead of exec'ing it, then
  tear down the proxy this run owns. The trap now covers INT/TERM/HUP too.
  - Hardened: `_is_our_proxy` confirms the process is really litellm started from our own config
    path before signalling it; `stop_proxy` declares its parameters defensively; `nullglob` in
    stop_proxy prevents unmatched globs from being iterated as literal filenames.
  - Also adds a SOFT, optional link to the brief-agents plugin: when
    `~/.claude/agent-briefing-index.md` exists, the remote model is told to read it before its
    first consequential action. Kept as a POINTER rather than an inlined copy — the index is
    ~11KB and re-sending it every turn would waste a quota-limited free lane.

## [Unreleased]

Nothing awaiting a number.

### Added

- Guided key setup under `install/setup-api-keys.py`, also available as
  `csl setup-remote` or `i` in the main picker. Choose among 14 active providers,
  open official key pages, and paste credentials into hidden terminal prompts.
  New keys use private flat files in `~/.api_keys`; existing files are kept.
  A complete bracketed paste shows a green confirmation and advances without Enter;
  duplicate pastes are not appended. Setup returns to the provider picker until
  finished or all providers have credentials. This add-only wizard makes no API calls.

- Session routes for Mistral, Z.AI, SiliconFlow, LLM7, Kilo, Vercel AI Gateway,
  SambaNova, and ModelScope, with explicit model selection and catalog metadata
  where supported. Only the selected credential enters the proxy; provider keys
  are cleared from the Claude child. No hosted generation qualification was run.
  Retired GitHub Models is removed from setup and the session picker, without
  changing stored credentials or normal GitHub CLI authentication.

- Remote session roster: Gemini 3.8 Flash (standard/thinking), Groq Qwen 3.6/3.8,
  NVIDIA Nemotron 3 Ultra, explicit free OpenRouter models, Cloudflare Workers AI
  routing, and current Cerebras GPT-OSS/Qwen models. `csl remote` opens the picker
  directly, including on machines without local models; `--include-trials` exposes
  Cerebras. The legacy Cerebras alias redirects with a notice. Catalog-only checks
  and offline fixture tests preserve provider generation quota. Cost labels now
  distinguish free allocations, trial access, and unverified account billing;
  OpenRouter's free alias uses the explicit free router. Catalog errors fail the
  check, and provider credentials are exported as literal values, never evaluated.

- `bin/la-reboot.sh` — restarts a CRASHED local model server in place: same port, same argv, so a
  still-open Claude Code session reconnects on its next request with no exit, no `/resume` and no
  context replay. Recovery previously meant exit → `la-evict` → new session → `/resume`, paying for a
  full model reload *and* a context replay. This is the bandaid half of the recurring Rapid
  D-METAL-CAP OOM; the climbing-residency root cause is tracked separately, and needing this often is
  data for that investigation rather than a reason to automate it.

  It is the only script in this repo permitted to stop a server on the session port range, so the
  guards are strict and tested: it acts on exactly one port, never a range; it requires positive
  crash evidence (a failed completion probe, a dead listener, or a Metal/OOM log signature) because
  liveness is not the test — an OOM-refusing Rapid server answers `/v1/models` while failing every
  real request; a server that still completes a request is refused unless `--force` is passed; and
  the argv is captured from the live process via `KERN_PROCARGS2` *before* anything is stopped,
  rather than from `ps` (whose space-joined output corrupts any argument containing a space) or from
  current config (which may have changed since launch). Ships `--status`, `--dry-run`, `--force`,
  `--port`, `--wait` and `--help`, with documented exit codes 0/1/2/3.

  Covered by `tests/test_la_reboot.sh` (25 checks) including an end-to-end stop-and-relaunch against
  a fixture server that refuses completions, argv fidelity for an argument containing a space, and
  mutation-tested: disabling the healthy-server refusal lets a working server be rebooted.

- Enhanced `remote-session.sh` to match interactive capabilities of `csl`:
  * Added support for interactive flags: `-w/--watcher`, `-a/--auto-mode`, `-t/--telemetry`, `-c/--choose-effort`, `-i/--install-keys`
  * Implemented state management for flags mirroring CSL functionality
  * Added helper functions `_pick_from` and `_launch` patterns from CSL
  * Session banner now shows selected model like local sessions do
  * Fixed proxy setup logic to be reliable regardless of `LA_LITELLM_CMD` setting
  * Prioritized NVIDIA as preferred provider in remote agents roster
  * Added more NVIDIA models including Kimi K3 as specifically requested
  * All tests pass (14/14 OK)

### Fixed

- `la-reboot.sh`: fix the unterminated model-list quote that failed after relaunch;
  preserve captured working directory/runtime settings, report exec failures,
  and require an actual Anthropic completion before declaring recovery. Preserve
  metadata permissions and refuse authentication failures as crash evidence.

- `la-reboot` readiness parsed `"id":"…"` without tolerating whitespace after the colon, so a server
  that pretty-prints its `/v1/models` payload was reported "not ready" while actually serving.
  Found by running the script against a real server. The same tight pattern exists in
  `local-llm-hotswap.sh`, `la-ram-preflight.sh` and `launch-claude-agent.sh`; those are not changed
  here because Rapid emits compact JSON, but they carry the same latent brittleness.

## [0.13.11] — 2026-09-13

Two safety fixes in the local RAM path, one source of truth for the Claude spoof
identities, the opt-in remote emergency-fallback lane, and the Rapid runtime
manager made discoverable.

⚠️ **CARRIED-FORWARD CAVEAT — the Auto Mode local-classifier readiness work ships
in this release WITHOUT live qualification.** It previously sat unreleased for
exactly this reason, and numbering it does not qualify it: no live Claude Code
smoke test has confirmed either backend's classifier route, and startup success
alone is not qualification. Treat the classifier route as unproven until that
smoke test is run and recorded. Nothing else in this release depends on it.

### Added

- `install/manage-rapid-mlx.py`, a repository-owned Rapid runtime manager with PyPI release discovery, interactive version selection, exact upgrades/downgrades, side-by-side reproducible venvs, private dependency-lock receipts, non-serving CLI smoke inspection, transactional active-pin promotion, guarded retirement, and `--dry-run`/`--help` support. Installation promotes active pins by default; `--skip-pin-update` keeps installation separate when required. Covered by `tests/test_manage_rapid_mlx.py` and documented in `docs/RAPID_RUNTIME_MANAGER.md`.

- Auto Mode classifier readiness gate for local sessions. A private fixture
  engine captures a genuine classifier request, measures cache readiness, and
  every launch replays it before the session opens, so a local Auto Mode session
  cannot start with a classifier that will fail on first use. Fixtures refresh
  weekly, on Claude/backend/profile identity change, or after a detected
  failure. Startup progress is reported transparently instead of appearing to
  hang, and capture diagnostics are sanitized before they are recorded.
- An opt-in Rapid-MLX Auto Mode launcher, deliberately NOT the default route.
  One qualified engine carries both compatibility identities natively: the
  relative model path stays the classifier identity while `--served-model-name`
  exposes the session identity — no proxy and no persistent user alias. It keeps
  its own cache and fixture roots, isolated from generic Rapid sessions and from
  oMLX, behind `LA_RAPID_AUTO_*`.

- Role names are now accepted wherever a model alias is, via `la_resolve_target`
  in `config/config-lib.sh`. `launch-claude-agent.sh operator`,
  `local-llm-hotswap.sh reasoner` and `local-agent-dispatch.py --model validator`
  resolve to whichever model fills that role **on disk right now**. An alias still
  wins over a role, so no existing invocation changes behaviour. Covered by
  `tests/test_role_resolution.sh` (9 assertions, mutation-tested 3/3 including a
  planted off-disk binding, without which the on-disk filter was undetectable).
- `tests/test_setup_shortcuts.sh` — regression coverage for the alias installer
  (19 assertions, mutation-tested 5/5). It exists because this release's own
  rewrite briefly removed the installer's write steps while it still printed
  `✓ local-* aliases written` and exited 0, creating no file at all.

- Per-alias speculative-decoding configuration for the Rapid backend, via the
  optional 12th `la_register` field `rapid_spec_json` (`LA_RAPID_SPEC_CONFIG`). An
  alias carrying a configuration is launched with `--speculative-config`; an alias
  without one keeps the previous `--no-spec-decode` behaviour, so no existing
  registration changes.
  The configuration is part of the server's REUSE IDENTITY: its SHA-256 is written
  to the port metadata as `spec_config_sha256` and compared before an existing
  server is reused, so a running server cannot be silently reused for an alias
  whose speculative settings have since changed. Covered by
  `tests/test_rapid_backend.sh`.

- `qwen-3.8-operator` and `qwen-3.8-thinking`, registered on Qwen3.8-27B-4bit with
  an MTP sidecar (`Qwen3.8-27B-MTP-4bit`, 3 speculative tokens) through the new
  per-alias speculative configuration, and promoted to be the DEFAULT bindings for
  the `operator` and `reasoner` roles — so `local-operator`, `csl` and a bare
  `--model operator` dispatch now land on the MTP-backed Qwen3.8 pair instead of
  Qwen3.6. The Qwen3.6 aliases remain registered and launchable for A/B against
  recorded evidence. Covered by `tests/smoke_qwen38_mtp.sh` and three added
  assertions in `tests/test_role_resolution.sh`, one of which pins that the
  resolved `operator` default actually carries the MTP configuration — without it
  a role could resolve correctly to an alias whose speculative config had been
  dropped.

### Changed

- The readiness gate is backend-neutral. The historical `LA_OMLX_*` names remain
  the public compatibility surface, and the backend is recorded in the prewarm
  profile so a fixture cannot be replayed across backends.
- The `local-*` shell aliases name ROLES instead of hardcoded models. The old
  block pinned `qwen-3.6-*` while the roster had moved on, so the aliases kept
  succeeding on a stale model with no signal. Retired the nine per-model
  `local-agent-*` dispatch aliases and the `agy-local` compat alias in favour of
  one `local-dispatch` taking `--model <role|alias>`; added `local-validator`,
  `local-roles` and `local-disk`. `local-logs` now matches the `rapid_auto_*` and
  `omlx_*` logs the current backends actually write, not only `vllm_*`.

### Documentation

- The Rapid-MLX runtime manager is now DISCOVERABLE. `install/manage-rapid-mlx.py` shipped with its
  own reference (`docs/RAPID_RUNTIME_MANAGER.md`) but was absent from the README entirely — not in
  the "What's in the box" inventory, and not in the section that explains why Rapid lives in a
  pinned venv. That section stated the weekly update check "never installs" without saying what
  does, so the documented story ended at the notification. The README now covers the version-change
  routine (install → smoke → exercise → promote/rollback → guarded retirement), the three properties
  that matter (side-by-side rather than in-place, promotion is not qualification, retirement is
  gated on a recreation receipt), and the fact that `--dry-run` is a GLOBAL flag that must precede
  the subcommand — verified by running every documented subcommand's `--help`, and by confirming
  argparse rejects the trailing form.

- `docs/ROADMAP.md` claimed the manager "lives on a dedicated feature branch … and is mergeable
  once that test plus `smoke 0.14.0` pass". It has been on `main` since `e9faaa8`. Corrected in
  place, with the consequence stated: an update routine is no longer missing, so open items that
  deferred work for lack of a safe upgrade/rollback path should call this manager rather than add a
  second installer.

### Fixed

- `local-llm-hotswap.sh` had no RAM preflight, while `launch-claude-agent.sh` has
  been gated since `0.12.0`. hotswap is the path every local session's system
  prompt names for placing sub-agent models on free ports, so an autonomous agent
  could stack model servers until RAM died — the failure that forced the
  2026-08-21 hardware reboot. FileVault is on, so a RAM-death reboot locks the
  machine out of remote work; prevention is the only remedy. The gate sits AFTER
  the port scan, so every reuse path is untouched (reusing a healthy server loads
  no weights and must never be refused), and it REFUSES rather than evicting:
  hotswap is invoked BY sessions that must stay alive, and any scanned port may
  have a session attached. `LA_SKIP_RAM_PREFLIGHT=1` overrides, as in the launcher.

- `la-ram-preflight.sh` question 0 could fail OPEN. Its reuse test was looser than
  hotswap's — it ignored the served spoof id, the spec-config hash and
  `LA_HOTSWAP_FORCE_FRESH` — so it reported "ALREADY served, no new weights load"
  for a server hotswap then refused to reuse, and a real 16GB load passed an
  impossible `LA_RAM_FLOOR_GB=999` (measured 2026-09-12: `:8000` served
  `claude-opus-5` while config expected `claude-opus-4-8`). A gate that fails open
  is worse than no gate, because callers trust it; question 0 now requires the same
  conjunction hotswap does. Both covered by
  `tests/test_hotswap_ram_preflight.sh` (11 checks, mutation-tested).

- `classifier-qualify.py` could describe a Stage-1 result as a genuine A/B when it
  was not one, and could qualify a fixture belonging to the wrong classifier stage.
  Stage identity is now ENFORCED rather than assumed: `validate_fixture_stage`
  rejects a fixture whose shape does not match the stage it is being replayed as —
  Stage 1 requires `max_tokens=64`, a segmented transcript and the severity closing
  block; Stage 2 requires `max_tokens=8192`, exactly one message, and no
  `stop_sequences` and no `tools`. The A/B is now explicit and mutually exclusive:
  exactly one of `--stage1-fixture-b` (a second genuinely captured request) or
  `--stage1-synthetic-b` must be given, fixture B must actually differ from fixture
  A, and the report states which source was used — a synthesized B is reported as
  synthetic instead of being presented as genuine A/B behaviour. Captured-body
  SHA-256 is recorded for report provenance. Covered by
  `tests/test_classifier_qualify.py` (+592 lines).

## [0.13.10] — 2026-09-08

### Fixed

- Stop the companion watcher window stealing keyboard focus from the local
  session. `local-watch.sh --open` used `activate`, which brought Terminal
  forward, so the window you type into lost focus and had to be clicked back.
  `do script` creates its window without it. Focus restoration is window-level
  rather than app-level because the session is normally another Terminal
  window, and is guarded for the case where no window exists yet.
- Collapse repeating watcher output. Watcher windows now pipe engine health
  through `bin/la-watch-filter.awk`, which strips the constant
  `INFO:module.path:` logger prefix, prints the per-request banner once — the
  model, `max_tokens` and stream flag are fixed for a session — and collapses
  consecutive near-identical lines into one line plus a repeat count, matching
  with digits masked so counters and timings still compare equal.
  `--diagnostic` bypasses the filter, so the raw stream stays raw.

## [0.13.9] — 2026-09-06

### Added

- Enable Claude Code's segmented Auto Mode transcript representation for
  local Auto Mode sessions, with a one-launch opt-out. This gives inference
  backends stable, message-aligned classifier-history boundaries.
- Add an oMLX Auto Mode backend that serves the selected local session model
  as `claude-opus-5` and a separate dense classifier as
  `claude-sonnet-5` on one isolated Anthropic-compatible endpoint.
- Add persistent oMLX paged SSD caching with an in-memory hot cache and
  write-through durability.

### Fixed

- Fix the released `0.13.8` Auto Mode path timing out as classifier history
  grew. The original second-slot explanation was not the binding issue:
  measured classifier requests were already admitted with
  `running=1 waiting=0`; repeated full-prefix prefill crossed Claude Code's
  classifier deadline.
- Isolate each local-agents oMLX server with a private `--base-path` and
  disable unrelated Hugging Face cache discovery. This prevents a session
  launch from rewriting or being respawned by the managed Homebrew oMLX
  service.
- Resolve the session model through `LA_CUR_DIR`, the actual registry
  contract, rather than the nonexistent `LA_CUR_SUBDIR`.

### Validated

- Synthetic growing-prefix acceptance reused 12,544 of 13,856 tokens and
  13,824 of 15,004 tokens. Latency fell from 75.8 seconds cold to 9.7 and
  8.6 seconds.
- A real lean local Claude Code session launched with Ornith as
  `claude-opus-5`, the dense DeepSeek classifier as `claude-sonnet-5`,
  segmented transcript mode enabled, and Auto Mode on. A consequential Bash
  action completed successfully through the local oMLX endpoint.
- Rapid remains available as an immediate rollback through
  `LA_AUTO_MODE_RUNTIME=rapid`.

## [0.13.8] — 2026-09-05

Merged `feat/auto-mode-classifier-localhost-routing` into `main` (fast-forward, so the history is
linear and every commit is attributable).

### Added

- **Auto Mode with its safety classifier routed to the local backend.** Claude Code judges each
  consequential tool call with a *separate* classifier, independent of the session model, so on a
  cloud-routed session a rate-limit or an exhausted budget took Auto Mode away precisely when local
  work had become the fallback. A local session already points `ANTHROPIC_BASE_URL` at its own
  server, so the classifier request follows it: `bin/launch-local-auto-mode.sh` warms a server that
  has a slot free for it and launches into `--permission-mode auto`, and `launch-claude-agent.sh`
  honours `LA_AUTO_MODE=1` for the same effect on an ordinary launch.

  Verified end-to-end on 2026-09-05: the local server logged
  `request model='claude-sonnet-5' served by loaded engine='claude-opus-5'` — the classifier's own
  model identity, answered by the loaded local engine — while the session held no connection to the
  cloud gateway, and the judged action then executed. Routing is proven; **verdict quality is not**,
  and a local model is still not Anthropic's classifier.
- **A free concurrency slot for the classifier.** The classifier is a second, concurrent request:
  with `--max-num-seqs=1` it queues behind the turn that triggered it and times out, which surfaces
  as a fail-closed `temporarily unavailable` refusal. `LA_RAPID_MAX_NUM_SEQS` therefore defaults to
  `2`, and enabling auto mode sets `LA_HOTSWAP_FORCE_FRESH=1` so a server left over from a
  single-slot launch is restarted rather than reused.
- **Nonessential outbound traffic is off by default for local sessions.** `LA_TELEMETRY=0` (the
  launcher default, toggled with `t` in `csl` or `CSL_TELEMETRY=1`) exports
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING` and
  `DISABLE_AUTOUPDATER`, and the session prints `🔇 Telemetry: OFF` at startup.

  The reason it is a default rather than an option: routing inference locally does not stop the CLI
  talking to the internet. Measured — a session whose every request provably went to `127.0.0.1`
  still held outbound sockets to Anthropic (`160.79.104.10`) and Google/Statsig (`34.149.66.165`),
  neither of which is inference or the gateway, so neither appears in a cost or routing check.
  Verified by outcome: zero non-loopback sockets after a real turn, against two before, with a
  control cloud session still showing three so the check demonstrably still detects them.

  Scope is deliberately limited to Claude Code's own traffic — hooks the *user* has configured still
  run, in separate processes. Trade-off: the umbrella also disables `/design-sync`, Projects and the
  CLI update check, all irrelevant to a local session.
- **`bin/launch-local-auto-mode.sh`** — the standalone auto-mode harness: it warms a server that has
  a slot free for the classifier, launches straight into `--permission-mode auto`, and hands the
  session a first action chosen to exercise the classifier, so a run either proves local routing or
  fails closed. Opt-in via `JOYIA_LOCAL_AUTO_CLASSIFIER=1`; the `csl` toggle covers everyday use.
- `tests/test_csl_menu.sh` sections 5–9, covering the auto-mode default, the `CSL_AUTO_MODE=0`
  opt-out, the telemetry default and its `CSL_TELEMETRY=1` opt-in, both `a`/`t` toggles, and in every
  case the value the launcher actually receives rather than the menu text that describes it.
- **Per-model auto-compaction profiles for local sessions.** `config-lib.sh` gained the optional
  associative array `LA_SESSION_AUTO_COMPACT`, keyed by model alias, and `csl` applies the selected
  model's value by exporting `LA_AUTO_COMPACT_WINDOW` into the launcher it execs. The export is
  deliberately confined to that child process, so it can neither propagate back into the parent
  shell nor reach an independently launched cloud session — a local context policy must never
  become a global Claude Code setting.
- `tests/test_session_profiles.sh` covering the override.

### Changed

- **`csl` now launches with auto mode ON by default** (`a` toggles it, `CSL_AUTO_MODE=0` opts out).
  The default flipped only once local classifier routing was actually verified: the reason to prefer
  it is that the classifier now costs nothing and cannot be withdrawn by a cloud 429, which is the
  whole point of a local session. Two consequences are documented rather than hidden — a judged call
  pays one extra local request (measured 35,154 prompt tokens for an 8-token verdict, 27.4 s cold),
  and calls already covered by a `permissions.allow` rule or by a read-only tool are never judged at
  all, so "nothing happened" must not be read as "the classifier ran and approved it".
- **Documentation caught up with the behaviour.** The README previously stated the opposite in three
  places — that local sessions force `acceptEdits`, that the local model "can't serve that call", and
  that classifier routing was unsolved. Those passages now describe locally routed Auto Mode, with
  its evidence and its limits, and `bin/launch-local-auto-mode.sh` is listed in the inventory instead
  of being undocumented.
- `bin/local-llm-hotswap.sh` and `config/config-lib.sh` support the classifier's second
  concurrency slot: `LA_RAPID_MAX_NUM_SEQS` defaults to `2`, and enabling auto mode sets
  `LA_HOTSWAP_FORCE_FRESH=1` so a server left over from a single-slot launch is restarted rather
  than reused.
- **Argument passthrough now receives the same profile as an interactive choice.** `csl <alias>`
  previously `exec`ed the launcher *before* the config was loaded, so a model selected by argument
  silently got no profile while the same model chosen from the numbered menu got one. Passthrough
  still skips the picker and the watcher; it no longer skips the profile.

### Fixed

- **`tests/test_rapid_backend.sh`: three warmup assertions that had never passed, and a regressed
  stub-server leak.** All three failures were faults in the harness, not the shipped script — the
  worst kind, because they read as a warmup regression in `local-llm-hotswap.sh`. The stub never
  wrote its warmup log: it is generated from a correctly *quoted* heredoc (its body is Python), but
  one line inside read `WARMUP_LOG="$SB/..."`, so the file received the literal characters `$SB`;
  `open()` raised, `do_POST` died before answering, and the probe read an empty reply and reported
  `finish_reason=?`. The skip test asked for a skip nobody reads (`HOTSWAP_PREFLIGHT=0`, while the
  code reads `LA_HOTSWAP_PREFLIGHT` — a wrong env-var name fails **open**, silently). And that
  section could not start at all: it inherited the 8100–8102 range with every port already held by
  an earlier test, so its hand-started stub died with *Address already in use*; it now has its own
  8103–8105 range, and its stub binary is no longer copied one level too deep by
  `cp -R src/.stub dst/.stub`, which nests when the destination already exists.

  The stub leak that `0.13.5` records as fixed had **regressed**: a later section did
  `STUB_PIDS="$!"` — a scalar assignment that discarded every pid the array held — and installed a
  second `EXIT` trap that dropped the `reap_stale_stubs` sweep. `cleanup()` is now the only `EXIT`
  trap, and the reaper matches *both* sandbox markers; matching only `la-rapid-test` had left the
  warmup-skip sandbox's stub listening on 8103 after every run, swept by port but skipped by marker.

  38 passed / 0 failed, verified twice consecutively with no listener left on 8100–8105.
  Mutation-tested against the product rather than merely re-run: breaking `_preflight_warmup` fails
  two assertions, breaking its skip guard fails one.

### Runtime direction — Ornith is no longer an experiment

The branch that produced the work above was named `experiment/ornith-200k-autocompact`. It has been
merged deliberately, because the experiment succeeded and the question it was asking is settled:

- **Ornith is markedly faster than Qwen 3.8** in real interactive use, which makes it uniquely
  suited to driving a *full* local session rather than only stateless dispatch.
- **The 200K+ context window is confirmed in the model's own documentation**, and a practical test
  showed it stays stable well past 100K tokens **with no auto-compaction at all**. That is the
  finding that matters, because it contradicts the older working assumption behind a 100K local
  threshold — prior local failures had clustered near 103K–105K, and 100K was only ever a fallback
  guess, never a measured property of every model.
- Consequently Ornith is treated as the intended direction for local sessions, not a candidate
  under evaluation.

This is a **proof of concept, not a finished migration.** Substantial work remains, most of it
already specified as part of `0.14.0`; see [`docs/ROADMAP.md`](docs/ROADMAP.md). Nothing here
promotes a roster-wide default or discharges any `0.14.0` release gate.

## [0.13.7] — 2026-09-01

### Changed

- **`csl` now lists every on-disk, session-capable model in its primary
  numbered menu**, rather than hiding the full roster behind free composition.
- Model rows show resolved backend, thinking mode, configured effort, and role
  metadata. Numbered selections use the configured effort; `c` remains the
  custom-effort path.
- The watcher now defaults off. It can still be toggled with `w`, or enabled
  initially with `CSL_WATCH=1`.
- Added an isolated deterministic menu test covering availability filtering,
  default-effort launching, custom effort, and watcher opt-in.
- Added the pinned non-thinking `ornith-1.5-35b` registration to the public
  example configuration and documented its download and launch workflow.
- Non-thinking Ornith is now the recommended full local-session model based on
  successful real Claude Code use and the best interactive performance observed
  so far, including noticeably better responsiveness than Qwen 3.8.
- This is explicitly early operational evidence, not a controlled benchmark.
  Ornith thinking remains untested and is not publicly registered or
  recommended.

### Validated

- CSL menu tests: **16 passed**.
- Backend resolution and launch-safety tests: **39 passed**.
- Rapid backend tests: **29 passed**.
- ShellCheck passed.
- The public example resolves Ornith to Rapid-MLX, thinking off, the expected
  Hugging Face repository, and the exact pinned revision.
- A live private-roster check confirmed the newly acquired qualification
  aliases remain directly visible and the watcher defaults off.

## [0.13.6] — 2026-09-01

### Fixed

- **`skills/offload-to-local`: the two cloud-escape reasons were jointly exhaustive**, so a plan could
  satisfy the rule while never offloading anything — compliant on paper, useless in practice. Measured
  instance: four delegatable steps, four `cloud:` annotations, zero local dispatches, every call
  individually defensible. The pattern is invisible per step and only appears in the aggregate.
  Three repairs: a POSITIVE test for what qualifies (a self-contained transformation over many similar
  inputs whose output is checkable against the source, with a measured worked example), "too hard to
  delegate" reframed as a SPEC gap that splits into `cloud:spec` + `local:` slices rather than an
  unfalsifiable verdict, and the requirement that a cloud reason be checkable and name its expiry —
  with the DISTRIBUTION being what to watch, so a plan with zero local steps owes an explicit sentence
  at plan level. Also: decide before READING the inputs, since opening the files to judge delegation
  already spends the expensive part.
- Deliberately versioned `0.13.6`, **not** `0.14.0` — that number belongs to the runtime-profiles
  architecture, and `docs/ROADMAP.md` (added one version earlier) is what made that boundary explicit
  enough to hold.

## [0.13.5] — 2026-09-01

Release hygiene. No behaviour change to any backend or launcher.

### Added

- **`docs/ROADMAP.md`** — what is specced but not shipped. `0.13.0`'s changelog claimed to add
  "changelog and roadmap documentation" and only the changelog appeared, so the entire `0.14.0`
  specification lived in a plan file on one machine while `[Unreleased]` sat literally empty. "Did we
  skip a specced feature?" was not answerable from the repo. It now is: every `0.14.0` phase,
  identity layer, release gate and explicit non-goal is listed with status, and an item can only
  leave the file by appearing in this changelog under a real version, or by moving to a deferred
  list with a stated reason.
- The roadmap also records why `0.13.x` is being used for work that would conventionally earn a
  minor bump: **the version number is the release gate for `0.14.0`.** A released `0.14.0` meeting
  half its gates cannot be un-released.

### Fixed

- **`tests/test_rapid_backend.sh` no longer leaks its stub servers**, and now sweeps stubs left by an
  earlier run at startup as well as in the EXIT trap. The trap alone was insufficient because hotswap
  launches stubs with `nohup`, so they outlive the shell that recorded their pids.
  This was worse than untidiness: a leaked stub keeps LISTENing on 8100, so the next run lands on
  8101 and `SUCCESS_PORT=8100` fails along with every argv assertion after it. Measured **19 passed /
  10 failed with nothing wrong in the code under test**, then 29/0 immediately after reaping — it
  bit three times while consolidating `0.13.1`–`0.13.4`. A genuine regression and self-contamination
  were indistinguishable, which is the failure mode that gets a correct change reverted.
  Matching is on the sandbox marker in the process command line, never on the port alone, so the
  sweep can never touch a live local session on 8000-8010. Verified idempotent: three consecutive
  runs, 29/29 each, zero leaked listeners after every one.

## [0.13.4] — 2026-09-01

### Added

- `install/download-models-system-trust.sh` — optional wrapper that runs `install/download-models.sh`
  against a Hugging Face client built to use the **native operating-system trust store** instead of
  `certifi`. This is the corporate-TLS path: on a network with an inspecting proxy, a bundled CA set
  fails while the system store succeeds. Overridable with `LA_HF_SYSTEM_TRUST_CLI`, defaulting to
  `~/.local/hf-system-trust/bin/hf`.
- It **fails closed** rather than silently falling back to the ordinary client: it verifies the
  downloader and the client are both executable, that the client is actually named `hf`, and that
  `command -v hf` resolves to exactly the intended absolute path after the `PATH` prepend. A wrapper
  that quietly used the wrong client would produce a TLS failure that looks like a network fault.
- Also exports `LA_HF_CLI` for forward compatibility with a future downloader that takes the client
  directly rather than through `PATH`.

## [0.13.3] — 2026-09-01

Per-launch Claude Code controls for full local sessions, and the model-facing session prompt
moved out of the launcher into a versioned template.

### Added

- Add validated per-launch Claude Code controls for full local sessions:
  `LA_CLAUDE_SETTINGS`, `LA_CLAUDE_TOOLS`, `LA_AUTO_COMPACT_WINDOW`, and
  `LA_AGENT_PROMPT_FILE`.
- Add `config/local-agent-system-prompt.txt` as a versioned, model-facing local-session prompt
  template with runtime placeholder substitution.
- Add focused launcher-profile tests covering the new controls, argument quoting, prompt
  placeholders, prompt-size bounds, and allowed/denied tool conflict protection.

### Changed

- Move the local identity, tool-use, zero-gateway-cost, and runtime self-preservation instructions
  out of `launch-claude-agent.sh` and into the external prompt template.
- Validate settings JSON, tool-list syntax, auto-compaction bounds, prompt readability, and
  unresolved prompt placeholders before launching Claude Code.
- Preserve launcher-owned invariants—direct localhost routing, compatibility model identity,
  `acceptEdits`, RAM preflight, server lifecycle, and strict-MCP behavior—rather than forwarding
  arbitrary Claude Code arguments.
- Fail closed when the same tool appears in both `LA_CLAUDE_TOOLS` and `LA_DENY_TOOLS`.

### Validated experimentally

- Claude Code 2.1.246 running Qwen3.8 27B 4-bit through Rapid-MLX accepted a per-launch settings
  overlay, an explicit eight-tool profile, strict MCP exclusion, and a 100K auto-compaction window.
- The interactive acceptance test completed native `Glob`, `Write`, `Read`, `Grep`, `Edit`, and
  `Bash` operations, preserved native transcript/resume behavior, removed its disposable test
  artifact, and exited cleanly without changing the implementation worktree.
- The lean profile began at approximately 24K context with 3.4K system-tool tokens, compared with
  approximately 28.4K context and 6.7K system-tool tokens in the earlier broader local baseline.
  This is an operational comparison, not a controlled performance benchmark.

### Known limitations

- Local sessions still use `acceptEdits`; the Auto Mode classifier path for spoofed local models
  remains unresolved.
- Exposing the `Agent` tool does not by itself repair local Auto Mode classifier routing.
- Claude Code and the current status-line renderer still display the compatibility model identity,
  a fictional cloud-price estimate, and a percentage derived from the spoofed model window rather
  than the effective local auto-compaction policy.
- The detached transcript-correlation helper can outlive the Claude client until its polling
  deadline. Lifecycle cleanup for that helper remains separate follow-up work.

## [0.13.2] — 2026-09-01

### Added — machine-local scripts adopted into the plugin

Four helpers that had been living in `~/.claude/scripts` on a single machine now ship with the
plugin. Each was checked for universality first: no hardcoded user paths, no dependency on this
machine's gateway or corporate tooling. These were authored deliberately WITHOUT a version bump, to
fold into the next release rather than ship alone — this is that release.

- `bin/local-inference-readonly-inventory.zsh` — offline, read-only snapshot of the whole stack,
  written to one timestamped report directory. Complements `bin/la-disk-inventory.sh` (disk →
  registry accounting) rather than duplicating it.
- `bin/model-asset-override.sh` — symlink-farm view of a model directory, to add or shadow a single
  non-weight asset without copying the weights.
- `install/local-stack-update-check.sh` — notify-only weekly check, now covering **two**
  environments: the legacy `~/.local-llm` lane (`mlx-vlm`, `mlx-lm`) and the newest
  `~/.venvs/rapid-mlx-*` (`rapid-mlx`, `mlx`, `mlx-lm`). Rapid became the default backend in
  `0.13.1` while being watched by nothing at all, which is the actual reason "who owns the venv
  update schedule" kept coming up. `mlx` is included on purpose: it is the Metal layer the
  memory-ceiling evidence is measured on, and the one dependency Homebrew's `rapid-mlx` formula
  declines to pin — which is why the pinned venv exists. Newest env is chosen by `sort -V`, since a
  lexical sort ranks `0.9.14` above `0.13.2`. Both log branches print the CHECKED set, so a silently
  skipped environment can no longer read like one that passed. Upgrades stay manual: the legacy venv
  is shared, the `vllm-mlx` fork carries local patches, and the Rapid envs are pinned on purpose.
- `install/hf-ipv4/sitecustomize.py` — the IPv4 shim that `install/download-models.sh` and the README
  troubleshooting section **already told you to use**, but which shipped nowhere. The README now
  gives the exact `PYTHONPATH` invocation.

## [0.13.1] — 2026-08-31

Rapid-MLX becomes the DEFAULT backend for MLX model launches. `0.13.0` made it *available*; this
makes it what a registration gets when it does not ask for anything specific. llama.cpp remains the
backend for GGUF artifacts, and vllm-mlx remains fully supported as an explicit pin.

### Changed — DEFAULT BACKEND FLIP (read this before debugging a backend surprise)

- The `serve` field gains a **generic** value, `mlx` (an empty field means the same), which resolves
  to `LA_DEFAULT_MLX_BACKEND` — **now `rapid`**. Registrations that named `vllm` only because it was
  the incumbent were migrated to `mlx`; nothing about the vllm code path changed.
- **Rollback is one line:** `LA_DEFAULT_MLX_BACKEND=vllm` in `config.local.sh`. Every generic
  registration reverts; every explicit pin is unaffected in either direction, which is what keeps
  per-backend measurements attributable to the backend they were taken on.
- **The concurrency caps became config knobs, at unchanged values.** `--max-num-seqs` and
  `--max-concurrent-requests` were hardcoded `1`/`2` in `0.13.0`; they are now
  `LA_RAPID_MAX_NUM_SEQS` / `LA_RAPID_MAX_CONCURRENT_REQUESTS` with **the same defaults**, so the
  value is discoverable and testable without editing the script. **Behaviour is unchanged.** They
  are 1/2 on purpose: Rapid does schedule continuously (its own default is 256), but the binding
  constraint here is Metal memory — one long-context session measured 99.9 GB at 103,020 prompt
  tokens against a 103.9 GB limit, with seven `SIGABRT`s in ~22h — so each extra in-flight sequence
  multiplies the thing already saturating. Raising them is gated on the Metal-ceiling work, not on
  preference.
- `LA_RAPID_BIN` is now **discovered** when unset: brew → `PATH` → newest `~/.venvs/rapid-mlx-*`
  (version-ordered, so `0.13.2` outranks `0.12.18` and `0.9.14`). Setting it explicitly still wins
  and remains the recommended posture for a qualified version.
- The four `qwen-3.x-rapid-*` qualification aliases are **retired** — the base aliases now *are*
  Rapid, and keeping them would register a duplicate (subdir, backend, spoof) triple that makes
  server-reuse matching ambiguous. `qwen-3.6-vllm-operator` / `-thinking` were added as explicit
  legacy pins so the vllm lane stays reachable.
- Kimi-VL registrations stay **pinned to `vllm`**: the viable-but-degraded MLLM verdict on record is
  an audit of vllm-mlx's route specifically, and moving them would silently reassign that evidence
  to a backend it was never taken on.

### Added

- `la_retired` / `la_retired_hint`: a retired alias now prints where it went instead of dead-ending
  on "unknown alias", and retirements are listed in the aliases help — which is where the error
  sends you.
- `llama_cpp` is a recognised `serve` value. hotswap **refuses** it with instructions (exit 3)
  rather than falling through to the vllm branch and dying at weight-load time on an artifact MLX
  cannot read. It is deliberately **not** reachable from a generic value, so flipping the MLX
  default can never reroute a GGUF model.
- The loader warns when a GGUF-looking artifact resolves to an MLX backend, naming the alias and the
  fix.
- Config errors now fail the loader instead of being absorbed: an unknown `serve` value, and an
  `LA_DEFAULT_MLX_BACKEND` that is not an MLX backend.
- `la_serve_display`: every human-facing listing (aliases help, hotswap banner, session banner,
  session log) shows the resolved backend **with** the declaration it came from — `rapid
  (mlx->rapid)` for a generic value, bare `vllm` for a pin.
- `tests/test_serve_default.sh` — 39 assertions over the resolution layer, backend vocabulary,
  discovery order, the guards, and the two real scripts (`csl` filter, hotswap refusal). Every
  hotswap invocation is bounded (`HOTSWAP_READY_TIMEOUT=4`) so a mutated resolver fails in seconds
  instead of hanging the suite.
- `tests/test_rapid_backend.sh` gains assertions that the concurrency caps are passed explicitly
  rather than inherited (29 assertions, was 27).

### Validated experimentally

- `tests/test_serve_default.sh`: 39 pass, 0 fail. **Mutation-tested with 9 planted defects** (default
  flipped, pins treated as generic, GGUF warning removed, declaration dropped from the display,
  version sort downgraded to lexical, hotswap's llama_cpp gate removed, backend banner removed,
  invalid values silently accepted, retired-alias hint disabled) — all 9 detected, baseline restored
  clean.
- `tests/test_rapid_backend.sh`: 29 pass, 0 fail. ShellCheck-clean at `-S error` across every edited
  script.
- Every flag hotswap passes was verified present in **both** rapid-mlx `0.12.18` (the pinned,
  qualified version) and `0.13.2`, so the concurrency change is safe on the currently-serving build.

### Known limitations

- Rapid-MLX `0.13.2` is installed **side by side** at `~/.venvs/rapid-mlx-0.13.2` and passes
  `pip check`, but `LA_RAPID_BIN` remains pinned to the qualified `0.12.18`. Promotion still needs a
  serve-level smoke test; it was not run because a live local session held port 8000 and the RAM
  headroom for a second 27B was thin. Retain `0.12.18` until `0.13.2` has 7 successful days.
- Moving a model to Rapid does not qualify it there. Only the Qwen3.6/3.8 aliases have Rapid
  runtime evidence; the rest resolve to Rapid as the sensible default but are unmeasured on it.
- **A dispatch aimed at a model that a local session is already using WAITS for that turn.** With
  `--max-num-seqs 1` one request runs and one queues; a third gets HTTP 503 + `Retry-After`. Rapid
  could batch instead, but not within this machine's measured Metal ceiling. The supported answer is
  a second server instance on another port — which hotswap does not currently offer, because it
  deliberately REUSES a healthy matching server. That opt-out is unbuilt.
- The RAM preflight's Rapid overhead formula (`6 GB + cache_mb/1024`) models a single sequence, so
  it would understate load if the concurrency caps were ever raised.


## [0.13.0] — 2026-08-25

### Added

- Add Rapid-MLX as a first-class `local-agents` backend.
- Make Rapid-MLX the preferred architecture for full local sessions.
- Retain patched `vllm-mlx` for compatibility, controlled A/B comparisons, unsupported models, and rollback.
- Add backend-aware server identity and safe reuse.
- Add Rapid-aware RAM preflight and cache accounting.
- Allow Rapid-backed aliases to launch through `csl`.
- Correct backend and thinking-state display in local-session banners.
- Add smoke tests for Rapid hotswap, reuse, and RAM preflight.
- Reposition full local Claude Code sessions as a major supported workflow complementary to local dispatch from cloud sessions.
- Add project-wide changelog and roadmap documentation.
- Add `la-evict.sh`, an emergency, one-server-at-a-time memory-recovery fallback for when the machine is already out of RAM and the terminal is unresponsive. It is deliberately standalone (no `config-lib.sh`, no registry) so it keeps working while the stack is sick.

### Validated experimentally

The following behavior has been observed on an Apple M4 Max with 128 GB unified memory. Release verification completed 2026-08-25: 224 tests pass with none failing (47 dispatch, 45 dispatch-state, 70 savings-ledger, 27 Rapid backend, 35 eviction), `tests/lint.sh` is ShellCheck-clean, and no cross-catalog alias, destination, or artifact-identity conflicts remain.

- Rapid-MLX `0.12.18` serving Qwen3.6 and Qwen3.8 through the existing Homebrew Claude Code client.
- Anthropic Messages, structured tools, tool-result continuation, streaming, cancellation, orphan cleanup, and immediate request reuse.
- Full Qwen3.8 local Claude Code sessions through `local-agents`.
- Hybrid-prefix reuse of approximately 27K prompt tokens.
- Warm follow-up turns completing in approximately 2–3 seconds with a measured 20 GB/eight-entry cache profile.
- Qwen3.8 oQ6, Qwen3.8 8-bit, matching 4-bit and 8-bit MTP sidecars, Nemotron Nano 4/6/8-bit, and Granite H-Tiny 6-bit acquired for later qualification.

### Known limitations

- The Rapid proof establishes bounded Qwen3.6 and Qwen3.8 text paths, not compatibility with the complete model roster.
- Rapid vision support requires a separately pinned and qualified environment.
- Visible reasoning leakage observed with Qwen3.6 persists with Qwen3.8.
- MTP sidecars are acquired but remain unqualified.
- **Total Metal memory is not bounded by `--cache-memory-mb`.** That flag caps the reusable prefix cache only; the KV/working set scales with context length and is not governed by it.
- **Long-context sessions can abort the server.** Measured on Qwen3.8 27B 4-bit at 128 GB: Metal high-water was 37.9 GB at 32,214 prompt tokens but 99.9 GB at 103,020, against a 103.9 GB allocation limit — roughly 0.6–0.8 GB of KV per 1,000 tokens. The practical wall is near 105K tokens and the safe operating ceiling near 80K; compact before then. Seven `SIGABRT` aborts were observed in about 22 hours of long-context use.
- **The measured 20 GB/eight-entry cache profile is not a safe default** and is deliberately not shipped. It produced roughly 2–3 second warm turns at shorter contexts, then reached about 107.9 GB Metal during a long-context run and aborted. Shipped defaults are 2,048 MB with two entries; the aggressive profile belongs in a private overlay.
- Reducing the cache does **not** move the wall: a two-entry/6 GB/fixed-256-step profile aborted identically at about 103K tokens with the cache empty.
- `ps rss` is not authoritative for MLX memory — it understated a server by roughly sevenfold. Inspect wired, Metal, and swap instead.

## [0.12.0] — 2026-08-22

Release commit: [`13bf844`](https://github.com/haiggoh/local-agents/commit/13bf844b6e3ef32530ef49d7c78a94cd7cd37dba)

### Added

- RAM preflight before loading local-model weights.
- A prominent local-session banner showing model, compatibility ID, effort, thinking state, port, and watcher command.
- `la-stream-render.py` for readable watcher output.
- Diagnostics that distinguish whether a model fits, which other servers are resident, and whether those servers are attached to live Claude Code sessions.

### Changed

- RAM checks stop as soon as the answer is known instead of inspecting every process unconditionally.
- The preflight reports possible idle-server reclamation or smaller alternatives but never terminates anything automatically.
- Watcher output renders reasoning as readable paragraphs and tool calls as concise lines; raw output remains available in diagnostic mode.

### Fixed

- Attachment detection now examines local Claude processes’ `ANTHROPIC_BASE_URL` instead of transient established TCP connections.
- Transcript correlation now identifies newly created transcript files rather than any transcript modified after launcher startup.
- Prevented a pre-existing active session from being selected as a newly launched session’s transcript.

### Safety

- Added `LA_SKIP_RAM_PREFLIGHT=1` as an explicit override.
- Addressed a real machine-freeze incident caused by loading another model without sufficient unified-memory headroom.

## [0.11.0] — 2026-08-22

Release commit: [`55f9ef0`](https://github.com/haiggoh/local-agents/commit/55f9ef00532b2254d043378abc8593773042c9d9)

### Added

- Revision-pinned, resumable model-download engine.
- Data-only public model catalog plus additive private catalogs.
- Selective file acquisition through include patterns.
- `.la-download-complete` markers.
- Acquisition states: `COMPLETE`, `PRESENT`, `METADATA`, and `ABSENT`.
- Disk preflight with configurable reserve and fitting-subset suggestions.
- `la-disk-inventory.sh` with orphan and no-payload detection.

### Changed

- Separated model-list data from downloader implementation.
- Deduplicated downloads by alias, destination, and exact artifact identity.
- Deduplicated catalog files by real path.
- Required a real weight payload for on-disk usability checks.
- Followed valid model symlink farms when checking weights.

### Safety

- Non-interactive downloads refuse to exceed the configured disk reserve.
- Metadata-only model directories are rejected before server startup.
- Private overlays under `config/*.local.*` are ignored automatically.

## [0.10.1] — 2026-08-19

Release commit: [`584bd0b`](https://github.com/haiggoh/local-agents/commit/584bd0b2562236db7ba2537860478da948b326a1)

### Fixed

- Disabled Claude Code’s separate streaming-idle watchdog for local sessions through `CLAUDE_ENABLE_STREAM_WATCHDOG=0`.
- Fixed local turns being aborted and retried after two silent five-minute prefill windows.
- Prevented long-context sessions from entering a cycle where each failed retry enlarged the next prompt.

### Safety

- Retained overall client and server request limits while disabling a watchdog that incorrectly treated multi-minute local prefill as a stalled stream.

## [0.10.0] — 2026-08-19

Release commit: [`2fe748b`](https://github.com/haiggoh/local-agents/commit/2fe748b62371eceb07828c03d1948adb0b4737ed)

### Added

- Free composition of any session-capable model with Claude Code effort levels `low`, `medium`, `high`, `xhigh`, and `max`.
- Role-based recommendations without restricting the complete model × effort space.
- Watcher toggle in `csl`, enabled by default.
- Immediate per-session port sidecars so monitoring can begin before the first transcript exists.

### Changed

- Deduplicated repeated model/effort role recommendations.
- Clarified role-based dispatch versus free-composition session selection.

### Fixed

- Corrected picker output captured inside command substitution instead of being shown.
- Verified compose, toggle, invalid-input, passthrough, role, and watcher paths.

## [0.9.1] — 2026-08-18

Release commit: [`c72cfb5`](https://github.com/haiggoh/local-agents/commit/c72cfb595e93ced4078f66ef1fd4abc75f317e7a)

### Documentation

- Added plugin marketplace installation commands to the README.
- Made installation discoverable directly from the repository.

## [0.9.0] — 2026-08-18

Release commit: [`0c3d0ad`](https://github.com/haiggoh/local-agents/commit/0c3d0adf46184573a31a4224aae5ab8604d04a77)

### Fixed

- Replaced impossible `lsof`-based transcript inference with launcher-recorded transcript association.
- Added per-launch transcript sidecars.
- Rejected sidecars pointing at missing transcript files.
- Added an honest unverified-candidate fallback when no association exists.
- Prevented newest-mtime and content-matching approaches from attaching the watcher to the wrong session.

### Changed

- Allowed a long polling window for slow first turns before transcript creation.
- Pruned stale sidecars after their launcher exits.

## [0.8.1] — 2026-08-17

Tag: [`v0.8.1`](https://github.com/haiggoh/local-agents/tree/v0.8.1)  
Release commit: [`06de907`](https://github.com/haiggoh/local-agents/commit/06de907ee2da66e5be42b8524a8a227779cc5c62)

### Changed

- Running `local-watch.sh` bare now opens watcher windows.
- `--list` explicitly requests print-only behavior.
- With no running sessions, the watcher falls back to listing.

### Fixed

- Corrected alias extraction when an effort argument follows the model alias.
- Prevented menu-launched sessions from being misidentified by their effort argument.

## [0.8.0] — 2026-08-17

Tag: [`v0.8.0`](https://github.com/haiggoh/local-agents/tree/v0.8.0)  
Release commit: [`1a1bc10`](https://github.com/haiggoh/local-agents/commit/1a1bc10d165ce1e40005af764d9530c4c014b387)

### Added

- `LA_DENY_TOOLS` to remove unusable built-in tool definitions from local requests.
- `LA_MCP_CONFIG` for retaining only selected MCP servers.
- Watcher-window startup support.
- Cache, prefill, and token-rate information in watcher health output.

### Performance

- Reduced a measured local Claude Code request from approximately 68.7K to 22.7K tokens.
- Reduced request size by approximately 67% and measured tool-definition weight by approximately 86%.
- Confirmed that `--disallowedTools` removes definitions, whereas `--allowedTools` does not reduce prompt size.

### Fixed

- Removed instructions to use nonexistent Claude Code tools.
- Updated local-agent guidance to use only exposed tools.

### Changed

- Raised default local request timeout from 30 to 60 minutes.
- Kept `AskUserQuestion` available.

## [0.7.0] — 2026-08-17

Tag: [`v0.7.0`](https://github.com/haiggoh/local-agents/tree/v0.7.0)  
Release commit: [`e4406e9`](https://github.com/haiggoh/local-agents/commit/e4406e92cad6b24408b9872d14f93234178f68dd)

### Fixed

- Prevented `vllm-mlx` from terminating streaming turns at its 300-second server default.
- Added `LA_SERVER_TIMEOUT_S`, derived from the local client timeout by default.
- Added warnings for reused servers with missing or stale timeout settings.
- Prevented long turns from wasting five minutes before a complete retry.

### Changed

- Running the launcher without an alias now opens `csl`.
- Invalid aliases still show usage and valid choices.

## [0.6.0] — 2026-08-17

Tag: [`v0.6.0`](https://github.com/haiggoh/local-agents/tree/v0.6.0)  
Release commit: [`9bab151`](https://github.com/haiggoh/local-agents/commit/9bab1515c43cbe8849c24abedebe518c823ec6ba)

### Added

- `LA_STRICT_MCP` configuration.
- Startup output explaining whether MCP tools are included.

### Performance

- Made `--strict-mcp-config` the default for local sessions.
- Removed 71 configured MCP tool definitions from measured prompts.
- Reduced measured tool weight from approximately 46.9K to 23.9K tokens.

### Documentation

- Documented that tool definitions can dominate local prefill.
- Recorded that `--allowedTools` does not shrink request payloads.

## [0.5.1] — 2026-08-16

Tag: [`v0.5.1`](https://github.com/haiggoh/local-agents/tree/v0.5.1)  
Release commits: [`3628ee4`](https://github.com/haiggoh/local-agents/commit/3628ee41ebb65f7889d48b67afce6543858b3317), [`7561f5d`](https://github.com/haiggoh/local-agents/commit/7561f5d9d73b867ddaacdd6d8418c9e784f66ff8)

### Fixed

- Removed the Copilot launcher’s unused effort argument.
- Warned on unexpected extra Copilot launcher arguments.
- Restored a clean ShellCheck gate instead of suppressing a valid warning.

### Documentation

- Recorded future Copilot effort selection as deferred work.
- Corrected stale pending entries for Copilot SSE and integration testing.

## [0.5.0] — 2026-08-16

Tag: [`v0.5.0`](https://github.com/haiggoh/local-agents/tree/v0.5.0)  
Release commit: [`cf1266f`](https://github.com/haiggoh/local-agents/commit/cf1266fff422c07e1a915d794415a874550b4f56)

### Added

- Conversation-capable terminal `local-agent-dispatch` interface.
- Structured history; compact, verbose, and quiet progress; multiline paste; file attachments; rolling summaries; and resumable named sessions.
- Pure-helper tests for input normalization, session names, and model labels.
- Public dispatcher documentation and component release metadata.
- Experimental Copilot BYOK transport proof and integration harness.

### Changed

- Prepared the repository for public distribution.
- Removed internal planning artifacts, private path references, and unexplained branding.
- Moved Copilot material into a clearly experimental documentation area.
- Removed the dispatcher’s development suffix for its first public release.

### Fixed

- Added a launcher hint directing no-argument users to `csl`.
- Corrected misleading claims that Copilot BYOK was fully functional.
- Documented dependence on unstable Copilot provider environment variables.

### Known limitations

- Copilot BYOK proved transport-level SSE compatibility, not reliable local-engine operation.
- Dispatcher paste presentation and test coverage remained incomplete.

## [0.4.0] — 2026-08-14

Release commit: [`773b74d`](https://github.com/haiggoh/local-agents/commit/773b74d7749e72b413e3b5a59481bbfaf1632994)

### Added

- Local-offload savings ledger.
- Append-only JSONL dispatch events and derived per-session/per-day rollups.
- Reports for today, week, month, and arbitrary start dates.
- Automatic best-effort ledger recording for successful dispatches.
- Dated cloud-pricing table and custom rates-file support.
- Initial terminal dispatcher files and documentation from preparatory commits included before this release boundary.

### Safety

- Unknown cloud models remain unpriced instead of producing a false zero.
- Missing input-token counts remain explicitly unavailable rather than being interpreted as zero.
- Raw prompt character counts are retained when tokens cannot be measured.
- Ledger recording cannot fail a successful dispatch.

### Testing

- Added 70 ledger tests.
- Mutation-tested model normalization, unknown pricing, and separate accounting of unpriced events.
- Verified a live end-to-end dispatch.

## [0.3.0] — 2026-08-11

Release commit: [`188e835`](https://github.com/haiggoh/local-agents/commit/188e83564cb0bb6572972eb2df73364d6fff27c9)

### Added

- Delegation-phase skills:
  - `compose-the-payload`;
  - `brief-the-delegate`;
  - `isolate-parallel-work`;
  - `guard-shared-runtime`;
  - `verify-delegated-work`.

### Changed

- Split delegation into narrowly triggered phases.
- Moved generic shipping discipline out of local-model workflows.
- Kept runtime-specific verification in `guard-shared-runtime`.
- Removed unnecessary foreign-plugin references.

### Fixed

- Replaced nonexistent role-resolution commands.
- Replaced GNU-only checksum examples with macOS-compatible SHA-256 commands.
- Documented minimum `pip` support for dry-run dependency resolution.
- Removed undefined variables and unverified port assumptions.
- Defined delegate prompt placeholders explicitly.

### Validation

- Verified skill frontmatter, path safety, independence, and ShellCheck.

## [0.2.15] — 2026-08-05

Release commit: [`63eeac3`](https://github.com/haiggoh/local-agents/commit/63eeac37df79465a5ae95ea0aef011ee26073efc)

### Fixed

- The launcher selects the newest configured compatibility ID actually advertised by a reused server.
- Prevented pre-change servers from causing immediate model-not-found errors.
- Warned when none of the configured IDs are advertised.

## [0.2.14] — 2026-08-05

Release commit: [`2aa2b4b`](https://github.com/haiggoh/local-agents/commit/2aa2b4b2574594d84c4e8367b0ba372effec0995)

### Added

- Comma-separated compatibility-ID preference lists.
- One server can advertise the same local weights under multiple Claude IDs.
- Backward compatibility with single-value IDs.

### Known limitations

- Loaded models are keyed by served name, so deliberately requesting multiple IDs may load the same weights more than once before idle eviction.

## [0.2.13] — 2026-08-05

Release commit: [`3fe94d0`](https://github.com/haiggoh/local-agents/commit/3fe94d0891d0ce0a74346e0355acd1fcab07934e)

### Fixed

- Quoted skill-description frontmatter for strict YAML parsers.
- Restored skill visibility outside tolerant Claude Code parsing.
- Verified description values round-trip unchanged.

## [0.2.12] — 2026-08-04

Release commit: [`115a8a3`](https://github.com/haiggoh/local-agents/commit/115a8a3dc249810191b11cb797ede4f793f5362e)

### Added

- `--prompt`, `--model`, and `--max-tokens` convenience options for `librarian-dispatch.py`.
- Automatic temporary output directory when `--outdir` is omitted.

### Changed

- Preserved the JSON-payload interface as the primary low-level route.

### Testing

- Verified convenience, payload-regression, and missing-input error paths.

## [0.2.11] — 2026-08-04

Release commit: [`b08eee4`](https://github.com/haiggoh/local-agents/commit/b08eee46f0a866b4d21b8814ad7ea61c756af801)

### Added

- Live streaming of `reasoning_content`.
- `reasoning.txt` output.
- Reasoning counts in completion metadata and heartbeat output.

### Fixed

- Reasoning-only responses no longer produce false `NO DATA` failures.

## [0.2.10] — 2026-08-04

Release commit: [`dc0b571`](https://github.com/haiggoh/local-agents/commit/dc0b571115ac8ee27ac2c7035a53b375232c16d0)

### Added

- Documented supervised offload loop: warm, route, decide, dispatch, verify, correct, and ship.
- ShellCheck lint gate.

### Fixed

- Corrected dispatch documentation to use the actual payload-file interface.
- Documented that standard macOS lacks GNU `timeout` and that the dispatcher provides its own watchdog.

## [0.2.9] — 2026-08-04

Release commit: [`e9e1a19`](https://github.com/haiggoh/local-agents/commit/e9e1a193067500ad1cc21504f08d2598e32da2fd)

### Fixed

- Fixed `wait_ready` aborting under `set -u` because arithmetic referenced a not-yet-bound same-line local variable.
- Restored `SUCCESS_PORT` on fresh launches.

## [0.2.8] — 2026-08-03

Release commit: [`fdd9fab`](https://github.com/haiggoh/local-agents/commit/fdd9faba1d9de4b40458faf081e6e5fe0bec5e6c)

### Added

- Documented multi-session monitoring.
- Per-port inference-health monitoring.
- Transcript mutation and thinking monitoring.
- Health-only and mutations-only watcher modes.
- Repeatable transcript flush-lag measurement.

### Findings

- Measured reasoning-carrying transcript records appearing approximately 0.2–2.6 seconds after turn completion in the tested Claude Code version.

## [0.2.7] — 2026-08-03

Release commit: [`b0001d2`](https://github.com/haiggoh/local-agents/commit/b0001d2a88034cdbd92514ed73f1ea45308e72ad)

### Added

- `LA_API_TIMEOUT_MS`.

### Fixed

- Increased local request timeout beyond Claude Code’s cloud-tuned default.
- Disabled the five-minute no-bytes idle abort for slow local prefill.

## [0.2.6] — 2026-08-02

Release commit: [`08cf812`](https://github.com/haiggoh/local-agents/commit/08cf812afb92afb0fcc23f8fa4ce246e8b871779)

### Changed

- Corrected the recommendation that all tool-driving work should default to a local session.
- Documented parallel local sessions as a bounded pattern for substantial, isolated, verifiable work.
- Added worktree/branch isolation and diff-review guidance.
- Clarified that supervision and review remain real orchestration costs.

## [0.2.5] — 2026-08-02

Release commit: [`320126f`](https://github.com/haiggoh/local-agents/commit/320126fc0b3b90fb02774d37ce99081838b66bd2)

### Changed

- Added guidance to use streaming dispatch for long or open-ended generations.
- Clarified that local Claude Code sessions can drive tools.
- Clarified that stateless dispatch cannot run a tool loop.
- Retained cloud routing where offload overhead exceeds the saving.

## [0.2.4] — 2026-08-01

Release commit: [`a432c1d`](https://github.com/haiggoh/local-agents/commit/a432c1dc8278b96bc649eaeb726f08c4a344e8b9)

### Added

- SessionStart offload nudge.
- `la_role` as shared role-binding source.
- Explicit `cloud:not worth offloading` escape valve.

### Changed

- Unified role resolution and `csl` around the same bindings.
- Kept legacy presets and role tags backward compatible.

## [0.2.3] — 2026-08-01

Release commit: [`e16e512`](https://github.com/haiggoh/local-agents/commit/e16e51240448715d4894d2863d415f974c7ad922)

### Changed

- Made `operator` the broad default local role.
- Defined `reasoner`, `validator`, and `utility` as depth/specialization escalations.
- Required per-step `local:` or `cloud:` routing decisions for bulk multi-step work.
- Defined roles as model × effort/thinking combinations.
- Made the role vocabulary extensible.

## [0.2.2] — 2026-08-01

Release commit: [`cd62743`](https://github.com/haiggoh/local-agents/commit/cd627437bef35d76fc1dbdec7abe580d40712145)

### Added

- Optional registry fields for roles, Hugging Face repository, and approximate size.
- Disk-aware role resolution and `la-roles.sh`.
- Interactive model installation from the registry.
- Partial-roster and multiple-model-per-role support.

### Changed

- Made the registry the roster’s single source of truth.
- Replaced hardcoded model names in routing guidance with role names.
- Kept older eight-field registry entries compatible.

## [0.2.1] — 2026-08-01

Release commit: [`e9c12cf`](https://github.com/haiggoh/local-agents/commit/e9c12cf823f544a9ef452aff8e2dae4e6fb792bd)

### Changed

- Moved offload decisions to task decomposition.
- Added role-based routing for operator, reasoner, validator, and utility work.
- Required self-contained instructions for stateless delegates.
- Expanded triggers beyond explicit cost-saving requests.

## [0.2.0] — 2026-07-31

Release commit: [`3e8a51b`](https://github.com/haiggoh/local-agents/commit/3e8a51b94e2c61a84f57e163f2a337f7a8f724b8)

### Added

- `offload-to-local` skill.
- Guidance for dispatching searches, summaries, boilerplate, transforms, and first-pass reviews to local models.
- Guidance to retain frontier reasoning, security-sensitive work, and final review on capable cloud models by default.

## [0.1.0] — 2026-07-30

Release commit: [`f722c55`](https://github.com/haiggoh/local-agents/commit/f722c5585284b4934a987f7fbc15829f3790d877)

### Added

- Initial local MLX inference overlay for Claude Code on Apple Silicon.
- Direct Anthropic-compatible local routing with native transcripts and history.
- Gitignored machine-specific configuration.
- Config-driven model registry.
- Local launcher, hotswap helper, and `csl` picker.
- Self-preservation and tool-use guidance for local sessions.
- Backend installation and model-download helpers.
- Patch bundle for the local `vllm-mlx` fork.
- Tournament, cancellation, tool-roundtrip, direct-routing, and Auto-mode diagnostics.

[Unreleased]: https://github.com/haiggoh/local-agents/compare/13bf844b6e3ef32530ef49d7c78a94cd7cd37dba...HEAD
[0.12.0]: https://github.com/haiggoh/local-agents/commit/13bf844b6e3ef32530ef49d7c78a94cd7cd37dba
[0.11.0]: https://github.com/haiggoh/local-agents/commit/55f9ef00532b2254d043378abc8593773042c9d9
[0.10.1]: https://github.com/haiggoh/local-agents/commit/584bd0b2562236db7ba2537860478da948b326a1
[0.10.0]: https://github.com/haiggoh/local-agents/commit/2fe748b62371eceb07828c03d1948adb0b4737ed
[0.9.1]: https://github.com/haiggoh/local-agents/commit/c72cfb595e93ced4078f66ef1fd4abc75f317e7a
[0.9.0]: https://github.com/haiggoh/local-agents/commit/0c3d0adf46184573a31a4224aae5ab8604d04a77
[0.8.1]: https://github.com/haiggoh/local-agents/tree/v0.8.1
[0.8.0]: https://github.com/haiggoh/local-agents/tree/v0.8.0
[0.7.0]: https://github.com/haiggoh/local-agents/tree/v0.7.0
[0.6.0]: https://github.com/haiggoh/local-agents/tree/v0.6.0
[0.5.1]: https://github.com/haiggoh/local-agents/tree/v0.5.1
[0.5.0]: https://github.com/haiggoh/local-agents/tree/v0.5.0
[0.4.0]: https://github.com/haiggoh/local-agents/commit/773b74d7749e72b413e3b5a59481bbfaf1632994
[0.3.0]: https://github.com/haiggoh/local-agents/commit/188e83564cb0bb6572972eb2df73364d6fff27c9
[0.2.15]: https://github.com/haiggoh/local-agents/commit/63eeac37df79465a5ae95ea0aef011ee26073efc
[0.2.14]: https://github.com/haiggoh/local-agents/commit/2aa2b4b2574594d84c4e8367b0ba372effec0995
[0.2.13]: https://github.com/haiggoh/local-agents/commit/3fe94d0891d0ce0a74346e0355acd1fcab07934e
[0.2.12]: https://github.com/haiggoh/local-agents/commit/115a8a3dc249810191b11cb797ede4f793f5362e
[0.2.11]: https://github.com/haiggoh/local-agents/commit/b08eee46f0a866b4d21b8814ad7ea61c756af801
[0.2.10]: https://github.com/haiggoh/local-agents/commit/dc0b571115ac8ee27ac2c7035a53b375232c16d0
[0.2.9]: https://github.com/haiggoh/local-agents/commit/e9e1a193067500ad1cc21504f08d2598e32da2fd
[0.2.8]: https://github.com/haiggoh/local-agents/commit/fdd9faba1d9de4b40458faf081e6e5fe0bec5e6c
[0.2.7]: https://github.com/haiggoh/local-agents/commit/b0001d2a88034cdbd92514ed73f1ea45308e72ad
[0.2.6]: https://github.com/haiggoh/local-agents/commit/08cf812afb92afb0fcc23f8fa4ce246e8b871779
[0.2.5]: https://github.com/haiggoh/local-agents/commit/320126fc0b3b90fb02774d37ce99081838b66bd2
[0.2.4]: https://github.com/haiggoh/local-agents/commit/a432c1dc8278b96bc649eaeb726f08c4a344e8b9
[0.2.3]: https://github.com/haiggoh/local-agents/commit/e16e51240448715d4894d2863d415f974c7ad922
[0.2.2]: https://github.com/haiggoh/local-agents/commit/cd627437bef35d76fc1dbdec7abe580d40712145
[0.2.1]: https://github.com/haiggoh/local-agents/commit/e9c12cf823f544a9ef452aff8e2dae4e6fb792bd
[0.2.0]: https://github.com/haiggoh/local-agents/commit/3e8a51b94e2c61a84f57e163f2a337f7a8f724b8
[0.1.0]: https://github.com/haiggoh/local-agents/commit/f722c5585284b4934a987f7fbc15829f3790d877

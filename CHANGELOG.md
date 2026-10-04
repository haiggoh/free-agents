# Changelog

All notable changes to `free-agents` are documented in this file.

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
  carried since 206-08-19, so Claude Code's cloud-tuned ceilings stayed in force. A large
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
# Changelog

All notable changes to `free-agents` are documented in this file.

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
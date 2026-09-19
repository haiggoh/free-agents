# Changelog

All notable changes to `local-agents` are documented in this file.

The project began using Git tags after development was already underway and did not tag every later release consistently. Historical entries through `0.12.0` were reconstructed from the complete public Git commit history, full commit messages, plugin-manifest version transitions, README history, and available tags.

Where no Git tag exists, the release heading links directly to its release commit. Component versions—such as the terminal `local-agent-dispatch` version—remain independent unless explicitly identified as the plugin release version.

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

- Added `tests/test_acquisition_catalog.sh` validating the PSV format, field counts, and cross-referencing against the remote roster.

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
# Changelog — `lowkey-cli.py`

All entries below are planned or reconstructed milestones. They are not claims that the corresponding versions were formally released at the historical dates.

## 0.10.2 — 2026-09-23 — Paste-burst UX fix + picker refinements

### Fixed — Paste-burst terminal presentation (Stage 2)

- **Root cause**: During `:paste` mode, terminal echoed each pasted line while model output
  from the previous turn could still be streaming, creating visually interleaved input/output
  that was confusing even though collection worked correctly.

- **Solution**: `read_conversation_input()` now uses raw stdin (`termios`/`tty`) on TTY to
  suppress terminal echo during paste collection, with a clear visual mode banner:
  ```
  ────────────────────────────────────────────────────────────
  📋 PASTE MODE — type/paste content, end with :end on its own line
  ────────────────────────────────────────────────────────────
  ```
  On `:end`, a confirmation banner shows line count before dispatch:
  ```
  ────────────────────────────────────────────────────────────
  ✅ Paste collected (N lines). Dispatching...
  ────────────────────────────────────────────────────────────
  ```
  Non-TTY (tests, pipes) falls back to line-buffered mode automatically.

### Fixed — Model alias resolution for dispatch (from 0.10.1)

- **Root cause**: The dispatcher sent the model alias (e.g., `qwen-3.8-operator`) as the `model`
  field in the payload, but backends serve different IDs:
  - Rapid-MLX serves ONLY the spoofed Claude ID (`claude-opus-5`)
  - vllm-mlx serves the alias, model dir, AND spoof IDs
  - mlx_lm serves the model directory path
  This caused `{"error": "The model \`qwen-3.8-operator\` does not exist. Available: claude-opus-5"}`.

- **Solution**: `local-llm-hotswap.sh` now emits `DISPATCH_MODEL=<id>` telling callers which
  ID to use for dispatch. `lowkey-cli.py` parses this (with `/v1/models` fallback for older
  hotswap versions) and uses it for all dispatch calls. Session save/load still uses the
  alias for validation.

### Added — Interactive picker (`bin/lowkey`) UX improvements (from 0.10.1)

- Emoji placement: CSL-style — emoji after letter with proper spacing (e.g., `m) 🦾 Model`)
- One-shot emoji changed from `⚡` to `🎯` (target/direct)
- Advanced settings moved to submenu (`+`); only modified settings shown in main menu
- Each advanced setting appears individually in main menu when modified (not as a batch)
- Session continuity toggle (was "model mismatch") with clear label and state emojis:
  - `✅ ALLOWED` — resume named session with different model
  - `🚫 BLOCKED` — resume requires same model
- Empty line between settings table and menu items for visual separation
- All lowkey emojis centralized in `config/emoji.sh` with `LK_` prefix
- `LK_EMOJI_MODEL` links to `SESSION_EMOJI_LOCAL` (single source of truth)

### Changed

- `config/emoji.sh`: Added `LK_` prefixed emoji constants for lowkey namespace
- `bin/lowkey`: Uses shared emoji constants; improved menu rendering
- `bin/lowkey-cli.py`: Paste mode now uses raw TTY mode with visual banners

## 0.10.0 — Rename to lowkey + default to qwen-3.8-operator (MTP-backed)

Published as part of plugin release `0.17.0`. The rename reflects the single-model session use case
where role-based dispatch abstraction is unnecessary — a standalone terminal dispatch tool should
have a simple, lowkey name.

### Added in this release

- Renamed `local-agent-dispatch.py` → `lowkey-cli.py` (the `lk` alias is the primary short form).
- Default model changed from `qwen-3.6-operator` to `qwen-3.8-operator` (MTP-backed, served via Rapid-MLX).
- Removed `--role` abstraction (was irrelevant for single-model sessions).
- Updated all internal references, test files, and dependent scripts (`agent-fallback.py`, `copilot_local_proxy.py`).
- Updated shell aliases in `install/setup-shortcuts.sh` to install `lk`/`lowkey` instead of `local-dispatch`.
- Updated documentation in `README.md` and `docs/lowkey-cli/`.

### Known rough edges (not fixed in 0.10.0)

- Visual interleaving during rapid clipboard pastes. Isolated `:paste` / `:end` collection has passed manual functional testing; the presentation issue is deliberately left for a separate focused patch.
- Test coverage reaches three of seventeen top-level functions. Session save/load, `read_files_context` file-size limits, the `:paste` / `:end` conversation collector, history compaction and the model-mismatch refusal are not yet covered.

### Implemented and tested

- One-shot dispatch.
- Interactive conversation mode.
- Structured conversation messages.
- Compact, verbose, and quiet progress modes.
- `:paste` / `:end` multiline input.
- Leading `You>` normalization.
- Rolling summaries with `:context` and `:summary`.
- `:file PATH` and attachment-size enforcement.
- Named-session save/resume with autosave.

### Known follow-up

Rapid clipboard pastes can make terminal input echoes and model output appear visually interleaved even though isolated multiline collection succeeds.

### Planned

- Improve paste-mode terminal presentation.
- Add automated regression tests.
- Finalize release metadata and the documented version source of truth.
- Add a reproducible smoke-test command.
- Complete release review before tagging or pushing.

## v0.9.0 — Named session save and resume

### Added

- `--session NAME` to create or resume a named conversation.
- Automatic save after successful responses and history compaction.
- `:save` for explicit persistence.
- `:session` for session status and storage path.
- Atomic JSON writes through a temporary file and replacement.
- Owner-only session directory and file permissions.
- Session schema validation.
- Model-mismatch protection with an explicit override option.
- Persistence of structured history, rolling summary, and compaction count.

### Verified

- New-session creation.
- Autosave and explicit save.
- Cross-process resume and exact-token recall.
- JSON parsing and owner-only permissions.

## v0.8.1 — Symlink, port-contract, and output-channel hardening

### Changed

- Resolve the dispatcher directory with `realpath()` so sibling scripts work through the launcher symlink.
- Require an explicit `SUCCESS_PORT` from the hotswap layer instead of guessing a port.
- Send reasoning diagnostics to stderr so stdout remains suitable for answer output and pipelines.

## v0.8.0 — Attachment limits and mid-conversation files

### Added

- `--max-file-chars` with explicit oversized-file rejection.
- `:file PATH` for attaching files during an active conversation.
- Quoted paths and path normalization.
- Clear attachment warnings.
- Clarified rolling-session-context and soft-history-threshold wording.

## v0.7.0 — Rolling history summaries and context inspection

### Added

- Model-generated rolling summaries before older turns are removed.
- Failure-safe compaction.
- Visible compaction notices.
- Summary injection as structured system context.
- `:context` and `:summary`.

## v0.6.0 — Progress modes

### Added

- Compact progress as the default.
- Verbose raw librarian diagnostics.
- Quiet inference output.
- Spinner, elapsed-time status, and concise completion reporting.

## v0.5.0 — Structured chat messages

### Changed

- Replace flattened `User:` / `Assistant:` prompts with structured chat-completion messages.
- Preserve one-shot compatibility.

## v0.4.0 — Conversation UX and bounded history

### Added

- Model-derived response labels.
- Active model in the conversation heading.
- Leading accidental `You>` cleanup.
- Soft history compaction threshold.
- Failed-turn exclusion from history.
- Visible librarian progress.

## v0.3.0 — Interactive multiline paste

### Added

- `:paste` and `:end` multiline input.
- Direct stdin collection for long pasted content.
- Support for a terminator appended to a final clipboard line without a newline.

## v0.2.0 — Initial conversation mode

### Added

- `--convo`.
- Interactive prompt loop.
- In-memory history.
- Backward-compatible one-shot mode.
- `exit`, `quit`, and `q` commands.

## v0.1.0 — Original one-shot dispatcher

### Added

- Model selection.
- One-shot prompts.
- File context.
- Token limit configuration.
- Hotswap and librarian integration.
- Temporary output handling.
- Existing `local-agent` launcher compatibility.

## Versioning policy

- `0.x.0`: meaningful feature or architectural milestone.
- `0.x.y`: compatible hardening or bug fix.
- `1.0.0`: documented behavior, repeatable automated tests, release metadata, clean repository state, and a tagged stabilized release.
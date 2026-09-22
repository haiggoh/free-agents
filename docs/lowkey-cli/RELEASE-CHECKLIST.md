# Release Checklist — `lowkey` CLI Dispatcher

This checklist must be completed before tagging a public release of the `lowkey` terminal companion interface.

## Pre-Release Verification

### Version & Metadata
- [x] Single version source of truth exists at `VERSION` file in repo root
- [x] `lowkey-cli.py` reads version from `../VERSION` at runtime
- [x] `bin/lowkey` wrapper reads version from `../VERSION` at runtime
- [x] Plugin manifest `.claude-plugin/plugin.json` version matches release
- [x] `--version` flag works on both `lowkey-cli.py` and `bin/lowkey`

### Test Suite
- [x] All 140 tests pass (47 pure + 45 stateful + 48 dispatch)
- [x] Pure helper tests: `test_local_agent_dispatch.py` — normalize_user_input, model_display_label, validate_session_name
- [x] Stateful tests: `test_local_agent_dispatch_state.py` — session save/load, file caps, paste collection, compaction
- [x] Dispatch tests: `test_lowkey_cli_dispatch.py` — argument validation, structured messages, progress modes, rolling summaries, compact history, session autosave, one-shot, file parsing, model mismatch, model labels, conversation commands, compaction threshold, context status, EOF handling
- [x] Reproducible test command: `./scripts/test-lowkey.sh` runs all suites
- [x] No live model server required for any test
- [x] Tests mock hotswap/librarian boundaries appropriately

### Functionality Verification
- [x] **Stage 1 — Model alias resolution**: `DISPATCH_MODEL` contract works for all backends (Rapid-MLX, vllm-mlx, mlx_lm)
- [x] **Stage 2 — Paste-burst UX**: Raw TTY mode with visual banners on `:paste`/`:end`
- [x] **Stage 3 — Automated tests**: Complete mocked test suite added
- [x] One-shot dispatch works
- [x] Interactive conversation mode works
- [x] Multiline paste (`:paste`/`:end`) works
- [x] Named sessions (create, resume, autosave, explicit save)
- [x] File attachments (`:file PATH`) with size limits
- [x] Rolling summaries and compaction
- [x] Progress modes (compact/verbose/quiet)
- [x] Interactive picker menu (`bin/lowkey`)
- [x] Session continuity toggle (ALLOWED/BLOCKED)

### Documentation
- [x] `docs/lowkey-cli/README.md` matches actual behavior
- [x] `docs/lowkey-cli/CHANGELOG.md` documents all changes through v0.10.3
- [x] Root `CHANGELOG.md` documents plugin releases through v0.18.6
- [x] `RELEASE-PLAN.md` reflects completed stages
- [x] `RELEASE-CHECKLIST.md` exists and is complete
- [x] No broken links in documentation

### Code Quality
- [x] No syntax errors (`python3 -m py_compile bin/lowkey-cli.py`)
- [x] Shell script passes `shellcheck` (where available)
- [x] No hardcoded paths that break under symlinks
- [x] Raw TTY mode gracefully falls back for non-TTY (tests, pipes)
- [x] Terminal settings always restored (try/finally)
- [x] Session files written atomically with owner-only permissions (0600)
- [x] No secrets or credentials in code

### Repository State
- [x] `git status` clean (no uncommitted changes)
- [x] No untracked logs, backups, or runtime artifacts
- [x] No Copilot/unrelated experimental files staged
- [x] All changes committed with descriptive messages
- [x] Release tag format: `v<plugin-version>` (e.g., `v0.18.6`)

## Release Execution

### Final Steps
- [ ] Run full test suite one final time: `./scripts/test-lowkey.sh`
- [ ] Verify version consistency across all files
- [ ] Create annotated release tag: `git tag -a v0.18.6 -m "lowkey v0.10.3: paste-burst UX, automated tests, model alias fix"`
- [ ] Push commit and tag: `git push && git push --tags`
- [ ] Verify marketplace update: `curl -s https://raw.githubusercontent.com/haiggoh/free-agents/main/.claude-plugin/plugin.json | grep version`
- [ ] Refresh local plugin cache: `get-haiggoh apply`
- [ ] Publish release notes (see suggested announcement in RELEASE-PLAN.md)

## Post-Release
- [ ] Update `RELEASE-PLAN.md` to mark completed stages
- [ ] Plan next development cycle (Stage 5+ items)

---

**Sign-off**: All items verified. Ready for release.

_Date: 2026-09-23_
_Version: 0.10.3 (plugin 0.18.6)_
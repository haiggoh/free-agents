# Handoff — Cloud screen completion + token-profile submenu, for a GLM 5.3 Flash session

_Written 2026-10-23 ~14:05 UTC by the Opus gateway session that designed it (stopped on budget).
Read this file first, then the spec and the plan below. Everything you need is in those three files;
do not rely on any chat._

## What to build

- **Spec (approved by the maintainer, every `[my call]` included):**
  `docs/superpowers/specs/2026-10-23-cloud-screen-and-token-profiles-design.md`
- **Plan (follow it task by task, receipts per task):**
  `docs/superpowers/plans/2026-10-23-cloud-screen-and-token-profiles.md`
- Release 1 = Tasks 0–9 (auto-sync move, spikes V1–V3, routing modes, honest-exhaustion fallback, hook +
  nudge + Cloud rows with info boxes, bypass default + eligibility probe). Release 2 = Tasks 10–12
  (measurement tool, token-savings submenu). Finish Release 1 to its push boundary before starting 2.

**Execution method (maintainer, 2026-10-23): NATIVE** — implement every task yourself in this session
(superpowers:executing-plans), not subagent-driven: subagents are slow on the NVIDIA free lane (every
subagent turn re-prefills its context). Ignore the plan header's "subagent-driven (recommended)" line.
At the end of Release 1, ask for one independent cross-family review of the branch diff via
`bin/free-agent-tool.py` with the diff attached as `"files"` and `"avoid_family": "zhipu"`.

## Order relative to your current branch

1. Your current branch `fix/rapid-auto-mode-tests` (handoff `docs/handoffs/2026-10-23-process-leaks.md`)
   comes FIRST. It now ships as **0.42.1** (0.41.2 and 0.42.0 were released meanwhile; rebase on
   `origin/main`, and update the "0.41.2" mentions in your uncommitted comments to 0.42.1).
2. Then this plan, in a NEW worktree `../free-agents-wt-cloud` on `feat/cloud-screen-completion` off
   `origin/main` (plan Task 0). Release 1 takes the next minor version at release time.

## Standing rules (unchanged from your previous handoff, plus three new ones)

- Commit by path (never `git add -A`), message ending `Co-Authored-By: GLM 5.3 Flash <noreply@z.ai>`.
- Push the feature branch; **never merge, tag or release** — the user or a paid session does that.
- Run the gates with `~/.claude/scripts/fa-ship.sh --test-only <worktree>`; record numbers in the
  Progress log below, in the same commit.
- Never restart, kill or reconfigure the endpoint serving your own session, or processes you did not start.
- **NEW: never touch the user's real `~/.claude`.** Every test that starts the picker or a settings
  writer sandboxes `CLAUDE_CONFIG_DIR`; `tests/conftest.py` fails the run if the real
  `~/.claude/settings.json` changes. In 0.42.0 a picker test outside the harness deleted a real
  setting — check every `Picker(` construction, subprocess probes included.
- **NEW: paid requests need the user.** Spike V3 and any eligibility-probe or cloud measurement run send
  a small paid request: stop and ask the user first (write the question into the Progress log and stop).
  Free-lane runs (V1, V2, Task 10 measurements) are fine.
- **NEW: never test cloud authentication from inside a session** — your Bash tool inherits the injected
  env. The probe's tests use a stub credential command and a stub `claude`.

## Progress log

- 2026-10-23 14:05 UTC — handoff written; spec + plan merged to `main` (commit referenced in `git log`).

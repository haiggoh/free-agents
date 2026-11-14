# HANDOFF — legacy-venv migration + two-root readers (free-agents 0.58.0)

Read this whole file first, then the plan it points at. Adopt the standing rules below for the
entire session, then continue autonomously from **Next action**.

## 1. Why this handoff exists

The user ordered it (2026-11-14 04:15 CET): `/free-agents:free-session-handover glm flash at max
effort`. The previous session (Claude Opus 5.5, paid gateway) wrote and committed the plan, then
handed execution to you. Nothing failed; nothing needs re-diagnosing.

Background, already established (do NOT re-investigate):
- free-agents 0.50.0 moved venv WRITES to `~/.local/share/free-agents/backends` (XDG). The
  manager's Python reads both roots (`install/managers/base.py` `discovery_roots`, new first).
- 0.57.1 (shipped, on `main`) fixed the PROMOTE writers (`base.home_relative_pin`, rapid/vllm
  `promote_pins`). Tests: `tests/test_pin_promotion_xdg_root.py`.
- Still old-root-only, verified 2026-11-14 on `main` cb00e37: `config/config-lib.sh:296-297`
  (repair hint), `:335` (`la_discover_rapid_bin`), `bin/la-detect-backend.py:141`,
  `bin/local-inference-readonly-inventory.zsh:349,355`. And `repair_version` relocates a legacy
  venv into the new root WITHOUT re-pinning ("a repair never promotes"), so repairing the pinned
  venv would leave `LA_RAPID_BIN` pointing at a deleted dir.

## 2. State

- Worktree: `~/ClaudeWorkspace/fa-venv-migration`, branch `feat/legacy-venv-migration`, based on
  `origin/main` `cb00e37` (v0.57.2).
- `5124914` docs(plan 0.58.0): the implementation plan. No code changed yet.
- Gates: none run on this branch yet.
- Progress log (append one line per receipt, commit it with that task's commit):
  - 2026-11-14 04:15 — plan committed (5124914); handoff written. Task 1 not started.

## 3. The goal, verbatim (user, 2026-11-13/14)

- "no i want any fix to apply to all users not just me locally"
- "shouldn't "repair venv" fix it? I think that would be logical if it had the ability to migrate
  or reinstall it in the required path. we could also make the path backward compatible (new
  installations go into the correct path but if that path is empty, the old path gets accepted too)"
- "and we already have a mechanism for suggesting a [update] on csl launch, we could add a
  recommended migration routine for all venvs if any of them are detected in the old directory
  but not the new one where they are expected" (user corrected: "I meant to say update, not backup")
- "provide the repair route on launch, but if the user refuses, use the same version with a
  warning in the meantime so the lane is never gated to force the user's hand"
- "build a new launch offer, but based on the existing mechanism you can piggyback on to not
  duplicate more code than necessary, keep it modular please"

## 4. Standing rules

1. Autonomous: decide and record instead of asking; stop only for forks that cannot be weighed.
2. Work ONLY in `~/ClaudeWorkspace/fa-venv-migration`. Never switch the branch of
   `~/ClaudeWorkspace/free-agents` (launchers resolve through it).
3. Commit every task, staged **by path** (never `git add -A` / `.`), ending with
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` then
   `Co-Authored-By: GLM 5.3 <noreply@z.ai>` (verbatim; never invent a "GLM 5.3 Flash" trailer).
   Push the branch (`git push -u origin feat/legacy-venv-migration`). **Never merge, tag or
   release** — Task 6's merge/tag/release steps are for the user's go; do its version bump,
   CHANGELOG, re-grep and full gate, then stop.
4. TDD every change: the new test fails for the RIGHT reason, then passes; mutation-check each
   new guard (revert the fix → test red); never weaken a test to get green.
5. Review each task with an independent dispatch (`python3 bin/free-agent-tool.py`, payload
   `{"prompt": ..., "files": [paths]}`) and check every finding against the real code —
   reviewers hallucinate file names and lines.
6. Resolve model/roster facts from the catalog when needed, never from memory.
7. Never run `remote-session.sh --stop`, never start another remote session, never kill anything
   listening on 4141–4151 or 8000–8010: one of them is the proxy you run on.
8. Record progress in §2 of THIS file at each task, in the same commit.
9. This handoff's "continue" is yours: never launch yet another session to pass it on.
10. Tests: `~/.local/bin/pytest` (pipx, has xdist). Picker/Textual tests need
    `~/.local/share/free-agents/picker-venv/bin/python -m pytest`. Full gate = the `test_cmd` in
    `rtc-ship.json` (`-n 12 -m 'not load_sensitive' --timeout=900`). A failure in a
    `load_sensitive`-marked module on a loaded box: rerun that file alone before calling it real.
11. Never write the expanded home path: `~/…` in tool args, `$HOME/…` in shell and pins.
12. `config/config-lib.sh` must stay bash 3.2 compatible. Every new script gets `--help`
    (verify by RUNNING it).
13. Never touch the user's live config (`~/.local/share/free-agents/config/config.local.sh`) or
    their real venvs: every test runs in a temp `HOME` (copy the fixture pattern of
    `tests/test_pin_promotion_xdg_root.py`).

## 5. Next action

Execute **Task 1** of `docs/superpowers/plans/2026-11-14-legacy-venv-migration.md` (same
worktree), then Tasks 2–5 in order, then Task 6 up to (not including) merge/tag/release. Use
`superpowers:executing-plans` (native: you implement every task; one final whole-branch review
dispatch at the end). When done, append the final state + gate counts to §2, commit, push the
branch, and report: commits, gate result line, and what the user must do to ship.

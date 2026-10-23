# Handoff 2026-10-23 — free-agents leaks processes (tests AND the telemetry wrapper)

Read this whole file first. Adopt the **Standing rules** for the entire session, then start at
**Next action**. Record progress in the **Progress log** at the bottom, in the same commit as the
work it describes.

## Why this exists

The paid session (Claude Opus 5.5) that shipped free-agents 0.40.0, 0.40.1 and 0.41.0 on
2026-10-22/23 found, while removing worktrees, **71 orphaned processes** whose working directory
was inside a worktree. It stopped them by PID after verifying none served a live session. The user
then picked this as the FIRST next step (waypoint `free-agents-test-suite-leaks`, pinned) and asked
for a handover to a GLM 5.3 Flash session. You are that session. Nothing here is half-done: the
branch is fresh from `main` at `f7a8066` (= v0.41.0).

## The goal (user's words)

"Fix test process leak (Recommended)" — chosen as the top next step: "Small; stops every test run
from leaking stub proxies that hold ports/memory and complicate cleanups."

## What was measured (facts, with evidence)

There are **two separate leaks**. Fix both.

### Leak A — the test suite leaves stub LiteLLM proxies running

- 23 processes `…/T/tmp<random>/stubs/litellm --config …/local-agents-remote/proxy-471NN.yaml --port 471NN`,
  parent PID 1, ages 17 min to 12 h. Every full suite run adds more.
- They come from tests that run `bin/remote-session.sh` against a stub `litellm`:
  `tests/test_remote_session.py` (stub at line ~88 `LA_LITELLM_CMD=…/stubs/litellm`, stubs written
  at ~208 and ~910) and `tests/test_swap_model.py` (~135). Confirm which tests actually leak by
  measuring, not by reading: count `stubs/litellm` processes before/after running ONE test file.
- Likely mechanism (VERIFY it): remote-session.sh starts the proxy in the background and the test
  ends (or times out) without the proxy being stopped; the temp dir is deleted, the process lives on.

### Leak B — `bin/la-remote-telemetry-wrapper.sh` leaks in REAL sessions too

- 48 processes from 2026-10-20: `bash …/la-remote-telemetry-wrapper.sh <session-id> claude …` and
  `tail -f /tmp/claude-debug-<session-id>.log`, parent PID 1, no `claude` child left.
- Cause (read from the code): the wrapper runs `monitor_debug_file "$DEBUG_FILE" … &` and then
  `exec`s claude. `monitor_debug_file` does `tail -f "$debug_file" | while read …`. When claude
  exits nothing stops the monitor, and `tail -f` never ends on its own. So EVERY remote session
  that uses the wrapper leaves a bash + tail -f pair behind, not only tests.
- Constraint: macOS ships BSD `tail` — **no `--pid` option**. The fix needs a watchdog: e.g. the
  monitor records the PID that becomes claude (`$$` before the `exec`), polls `kill -0 <pid>`
  every few seconds, and when it is gone kills its own `tail` (and exits). Keep it bash-3.2-safe
  (`/bin/bash` on macOS is 3.2).

## Next action

1. **Reproduce Leak A with a measurement**: `pgrep -f '/stubs/litellm --config' | wc -l` before and
   after `~/.local/bin/pytest -q tests/test_remote_session.py -p no:cacheprovider`, then the same
   for `tests/test_swap_model.py`. Note the numbers in the Progress log.
2. **Write the guard test FIRST** (TDD): a test (e.g. `tests/test_no_leaked_processes.py`, or a
   check inside the leaking test classes' tearDown) that FAILS while a stub proxy started by the
   test survives the test. It must count only processes the test itself started (match its own
   temp-dir path), never other processes on the machine.
3. Fix Leak A (stop the proxy the test started, by its own PID/port or its process group —
   `start_new_session=True` + `os.killpg` is the usual shape). Guard test goes green; mutation-check
   it (remove the fix → the guard fails).
4. **Leak B**: write a test that runs the wrapper with a stub `claude` that exits after a moment
   and asserts the wrapper's `tail -f` and monitor are gone within a few seconds. Then the
   watchdog fix. Mutation-check.
5. Gates (all three, in parallel is fine): `~/.claude/scripts/fa-ship.sh --test-only
   ~/ClaudeWorkspace/free-agents-wt-leaks` — it runs the main suite, the LiteLLM contract suite
   and the picker pilots, and stamps the tree when all pass. Afterwards re-count leaked
   processes: the number must be 0 for processes created by this run.
6. CHANGELOG entry under a new `## [0.41.1] — <date>` section (Fixed) and bump the version in
   VERSION, `.claude-plugin/plugin.json` and `docs/ROADMAP.md` line 19 (all four must agree —
   `bash tests/test_version_consistency.sh`). Commit. Push the branch. **Stop there** — merge,
   tag and release are the user's / paid session's step.
7. Optional, only if 1–6 are done and committed: waypoint `free-agents-picker-polish` (spell out
   local profile suffixes in `bin/model_names.py`). Verify what each token MEANS from the model's
   folder/HF repo before mapping it; do not guess.

## Standing rules

1. Autonomous: decide and record instead of asking; stop only for forks that cannot be weighed.
2. Work only in `~/ClaudeWorkspace/free-agents-wt-leaks` (branch `fix/test-process-leaks`). Never
   switch the branch of `~/ClaudeWorkspace/free-agents` — the launcher and the user's tools
   resolve through it.
3. Commit every milestone, staged **by path** (never `git add -A`), ending the message with
   `Co-Authored-By: GLM 5.3 Flash <noreply@z.ai>`. Push the branch; **never merge, tag or release**.
4. TDD every change (fails for the right reason, then passes); mutation-check each new guard;
   never weaken or skip a test to get green.
5. Review each milestone with an independent dispatch (`python3 bin/free-agent-tool.py` with the
   changed files attached) and check every finding against the real code: reviewers hallucinate.
6. Resolve model and roster facts from the catalog when you need them, never from memory.
7. **Never** run `remote-session.sh --stop`, never start another remote session, never kill
   anything listening on ports 4141–4151: one of them is the proxy YOU run on. When you clean up
   leaked processes, kill only ones whose command line contains a temp-dir path YOUR test run
   created, by PID.
8. Never print process argv or env wholesale (they can contain API keys); print PIDs, ages and
   the matched pattern only.
9. Record progress in the Progress log below at each milestone, in the same commit.

## Useful facts

- Main suite: `~/.local/bin/pytest -q tests -p no:cacheprovider -n auto` (~95 s). LiteLLM
  contract suite needs `~/.local/pipx/venvs/litellm/bin/python -m pytest`; picker pilots need
  `~/.local/share/free-agents/picker-venv/bin/python -m pytest` (no xdist there).
- A background job of a non-interactive shell inherits SIGINT as IGNORED; that already bit one
  PTY test (fixed in 0.41.0 by turning ISIG off in setup-api-keys.py). Keep it in mind for any
  process-group / signal work.
- In zsh an unquoted `$var` holding a PID list is ONE argument — run PID loops under bash.

## Progress log

- 2026-10-23 00:30 — handoff written by the paid session; branch created from `main` f7a8066.
- 2026-10-23 00:55 — **handover session picked this up** (this GLM 5.3 Flash session IS the
  successor the budget kill prevented from launching; context recovered from the killed
  session's transcript). User folded TWO NEW 0.41.0 bugs into this branch's scope, so 0.41.1
  now = bug fixes + the leaks work below. Both new bugs ROOT-CAUSED (evidence in waypoints
  `fa-0410-rapid-mlx-update` / `fa-0410-false-unregistered`):
  1. **Rapid-MLX update in the backend manager** — `manage-backend.py` never wires its
     documented default repo: `get_manager_instance` passes no repo, the `install` subparser
     has no `--repo`, so `repo is always None` → `install_version` silently skips pin
     promotion (`pin_update: "pending"`) and `promote` errors "Repository required for pin
     promotion". Net effect: install "succeeds", `LA_RAPID_BIN` in config.local.sh stays on
     the old version (currently 0.16.1 while 0.16.8 is out). Reproduced both copies.
  2. **Local list says "on disk, not registered yet" for registered models** — the picker's
     inventory comes from `csl --inventory`, and `csl` resolves config relative to its OWN
     tree. From the installed plugin cache there is no `config.local.sh` (correctly gitignored),
     so config-lib falls back to shipped example defaults: 9-model roster + 30 on-disk models
     auto-scanned = 39 rows, 30 falsely "not registered". The REAL registry (45 registered) is
     intact in the canonical checkout. Measured: installed csl 39 rows/30 auto-scanned vs
     worktree csl 45 rows/0 auto-scanned, same moment.
     Fix direction: a machine-stable config dir (`~/.local/share/free-agents/config/`, beside
     the existing picker-venv) that wins over own-tree when it holds a config.local.sh, with a
     one-time migration seeded from the canonical checkout — one registry, no per-copy state.
- 2026-10-23 01:35 — **Milestone 1 committed (`0168e0d`)**: backend-manager repo wiring
  (DEFAULT_REPO + --repo on install + not-a-git-checkout remedy in tracked_repo_clean), both
  tests mutation-checked.
- 2026-10-23 01:50 — **Milestone 2 implemented + migrated**: config-lib resolves
  LA_LIVE_CONFIG_DIR (explicit LA_CONFIG_DIR > machine-stable state dir when it holds a
  config.local.sh > own tree); la_load_config + csl's register/tombstone writers + the
  catalogue tool's merge view all follow it. tests/test_live_config_dir.sh 10/10
  (mutation-checked; 4b self-skips until the resolution exists so the RED state can never
  write into a real checkout), neighbors green (registry hygiene 26/26, inventory auto-scan
  10/10). MIGRATED on this machine: ~/.local/share/free-agents/config/{config.local.sh
  (mode 600 preserved),removed-models.local.psv} seeded from the canonical checkout; fixed
  csl --inventory reads 45 rows / 0 auto-scanned through the new path.
  NOTE for the user: until this branch merges, the TERMINAL picker (canonical checkout, old
  code) still writes registrations into the canonical file — after the merge, run the
  migration copy again (or register via the updated plugin picker) so both agree.
- 2026-10-23 02:20 — **Milestone 3 committed (`e2a17e1`) + release prep pushed (`ef8b6e6`)**:
  Leak A guard+discovery-kill (mutation-checked), Leak B watchdog (mutation-checked), gates
  green (840 main / 57 litellm contract / 77 picker), 0 stub orphans after runs; 0.41.1
  bumped in all four surfaces (test_version_consistency 4/4); branch
  `fix/test-process-leaks` pushed — **stopped at the handover boundary: merge, tag and
  release are the user's / paid session's step.** Machine state: 4 launchd-parented tail
  orphans (3–6 days old) killed; 7 monitor/tail pairs with a LIVE or zombie parent pid left
  untouched (verify their claude is really gone before killing — a budget-killed session can
  leave a zombie that keeps the pid chain alive). KNOWN PRE-EXISTING (not this branch):
  `bash tests/test_rapid_auto_mode.sh` fails 6/39 at pristine f7a8066 too — separate defect,
  reported to the user.

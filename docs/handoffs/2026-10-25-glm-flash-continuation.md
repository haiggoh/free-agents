# Handoff 2026-10-25 — free-agents waypoint queue, for a GLM 5.3 Flash session

You are a full free Claude Code session (GLM 5.3 Flash thinking, NVIDIA via remote-session.sh).
Read this file fully, adopt its standing rules for the whole session, then work the queue below
until every task that can be run autonomously is resolved. This file is UNTRACKED in the
canonical checkout — commit it into your first milestone's branch so it travels.

## Why this exists

The 2026-10-23/24 session (41a9f4ff, GLM 5.3 Flash, this same lane) shipped free-agents
0.41.1 → 0.43.6 (nine releases), closed twelve waypoints, ran three audits and the Phase-4
live dogfood, then handed the remaining autonomous queue to you. Nothing here is half-done:
every shipped item is tagged, released, installed and verified visible.

## The goal (user, verbatim)

"run until all tasks that can be run autonomously are resolved" — over the six items below,
in the order given. Items gated on the USER (taste, approvals, one physical keypress) are NOT
yours: propose, record, and move on.

## The queue, in order

1. **fa-thinking-toggle** — the session thinking toggle, both lanes. The full design is in the
   waypoint (`waypoints.py show fa-thinking-toggle`): Settings.thinking_session (bool|None,
   default None = ON for session launches), rows after effort/temperature on LocalScreen and
   RemoteScreen, info-box override notices, launcher env LA_THINKING_OVERRIDE consumed in
   remote-session.sh's thinking-resolution block (~line 795-825: Nemotron honors
   chat_template_kwargs; GLM on NIM is ALWAYS-thinking — reasoning_effort is the only dial —
   the constraint table is alias-suffix + that fact). Dispatch side: NO manual toggle — a
   `thinking: on|off|auto` field per dispatch-preferences entry, role-derived defaults
   (reasoner/validator on, operator/utility off), model-constraint override last, reported in
   the result JSON. TDD every layer.
2. **fa-dogfood-swap-completion** — ONE clean live mid-flight swap observation: launch a long
   background session on a SCRATCH port, swap to nvidia-glm53 (--force), watch the completion.
   Attempts 1-2 stalled (DeepSeek's ~10-min pre-flight; the shared 40 RPM limiter queue — that
   pacing is the system working). Use a FAST target and a swap timeout above the limiter queue.
   The scratch-band fix (0.43.6) keeps the pre-flight out of the live band.
3. **fa-gate-test-flakes** — three contention-flaky tests make the gates a retry lottery when
   the machine carries live sessions (each passes solo every time):
   test_memguard_inject.py::test_5_recent_activity_becomes_quiet,
   test_librarian_retries_after_a_429 (the plan's own documented flake), and the already-fixed
   RemoteLiveFilter (0.43.3, for the pattern). Find each timing dependency and make it
   condition-based, or add a contention-tolerant retry. FA_TEST_WORKERS=6 is the workaround,
   not the fix.
4. **fa-joyia-safeguards-request** — DRAFT (do not send) the JoyIA pass-through feature request:
   the gateway should pass the safeguards / safeguard_results fields through unchanged, so auto
   mode's safety checks stop billing as normal token usage. The verdict evidence is in the spec
   §8b (docs/superpowers/specs/2026-10-23-cloud-screen-and-token-profiles-design.md) and the
   cached verdict (~/.local/state/free-agents/eligibility.json). Present the draft to the user
   for sending.
5. **csl-cloud-session, Task 10** — the prefill measurement tool (bin/fa-prefill-measure.py per
   the plan: docs/superpowers/plans/2026-10-23-cloud-screen-and-token-profiles.md). The free-lane
   headless vehicle is VERIFIED (remote-session.sh <alias> -p works end-to-end on a scratch
   port — measured 2026-10-24). Build Task 10 + take the measurement receipt, then PROPOSE the
   Task 11 level contents and STOP — the user approves levels (a taste gate). Do NOT build Task
   11's rows without that approval.
6. **fa-profile-aware-residue** — the autodiscovery feature is CLOSED as already built. The
   residue: (a) the live auto-sync test is the USER's one keypress (Local screen → y) — note it
   to them, don't fake it; (b) the two Devstral folders (devstral-small2-uniform4 registered,
   Devstral-Small-2-24B-4bit not) — dedupe or register, your call, record it; (c) runtime-
   variant derivation is a TASTE question — add it to the user's pending-decisions note, don't
   build it.

## Standing rules (numbered, blunt)

1. Autonomous: decide and record instead of asking; stop only for taste gates and the two
   listed above (Task 11 approval, variant derivation). Propose in the handoff Progress log.
2. Work in a FRESH worktree per feature (`git worktree add -b <branch> ../free-agents-wt-<topic>
   origin/main`). Never switch the branch of ~/ClaudeWorkspace/free-agents (the launcher and
   the user's tools resolve through it).
3. Commit every milestone, staged BY PATH (never git add -A), message ending with BOTH:
   `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and
   `Co-Authored-By: GLM 5.3 <noreply@z.ai>`. Push the branch. Releases: run
   `~/.claude/scripts/fa-ship.sh <worktree> <version> "<summary>"` — full auto through release
   is the standing authorization. CHECK origin/main before every release: if the next version
   number was taken while you worked, renumber and rebase (it happened twice on 2026-10-24).
4. TDD every change (fails for the right reason, then passes); mutation-check each new guard;
   never weaken a test to get green. When a scripted edit's anchor isn't unique, READ the
   region and use the Edit tool — a python str.replace with a non-unique anchor applies
   cleanly SOMEWHERE ELSE.
5. Review each milestone with an independent dispatch (bin/free-agent-tool.py) and verify
   every finding against the real code.
6. Resolve model and roster facts from the catalog/config when you need them, never from memory.
7. PORTS: 4141, 4142 and 4143 serve LIVE sessions right now — never kill, restart or reconfigure
   anything on them, and never run remote-session.sh --stop. Your proxy picks a free port
   itself; the swap scratch uses MAX+1..+20 since 0.43.6.
8. PAID SPEND IS NOT AUTHORIZED for you. Every cloud request from a nested claude inherits the
   PAID gateway (measured: a $0.19 surprise) — never launch bare nested claude; free-lane runs
   go through remote-session.sh's own env on a scratch port.
9. Record progress in the Progress log below at each milestone, in the same commit. Amend the
   queue notes in ~/.claude/waypoints.json ONLY via waypoints.py.
10. Write every path under home as ~ or $HOME — never the expanded literal (a typo'd /Users/…
    once cost a session; CLAUDE.md 2026-10-24).

## Useful facts

- Main suite: `~/.local/bin/pytest -q tests -p no:cacheprovider -n auto` (~105 s). The machine
  may carry THREE live free-API sessions — run the gates with FA_TEST_WORKERS=6 if the timing
  tests flake (each passes solo; that's contention, not regression).
- The registry lives in the machine-stable state dir: ~/.local/share/free-agents/config/ (0.42.1+).
  Worktrees do NOT need a config.local.sh copy.
- The pickers need their venv: ~/.local/share/free-agents/picker-venv/bin/python.
- LiteLLM contract tests: ~/.local/pipx/venvs/litellm/bin/python -m pytest tests/test_litellm*.
- The three live sessions: ports 4141, 4142, 4143 (verified 2026-10-25 01:40 CEST). Your port
  will be 4144+ — never touch 4141-4143.

## Progress log

- 2026-10-25 01:45 — handoff written by session 41a9f4ff (the predecessor, still live in a
  Terminal window — do not kill 4141-4143). Queue adopted: start at item 1.

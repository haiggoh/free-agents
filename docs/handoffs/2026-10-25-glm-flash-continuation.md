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
- 2026-10-25 04:2x — item 1 (fa-thinking-toggle) implemented on feat/thinking-toggle in
  ../free-agents-wt-thinking-toggle (worktree of origin/main 5bbb4db; the handoff doc itself
  committed there first, 19c2286). TDD every layer, each new test watched to fail first:
  store schema 7 + thinking section (44 store tests green; schema guard mutation-checked:
  a simulated schema-6 reader refuses a thinking file), picker rows `j` on both lanes +
  always-exported LA_THINKING_OVERRIDE + GLM constraint in the info box (89 picker tests
  green), remote _resolve_thinking (alias suffix → override → constraint; 51 remote tests
  green incl. main-flow + --swap-model wiring + dry-run line), local lane via config-lib
  la_lookup (13/13 sandbox tests green; mutation-checked — reverting the guard fails 3),
  hotswap profile-mode pin, dispatch policy resolve_thinking (role defaults → constraint
  last; 43 dispatch tests green; the council-rule test mutation-checked). Design decision
  recorded: the picker row cycles ON (default) ↔ OFF only — None and True behave
  identically at launch, so the row never stores an indistinguishable third state; the
  tri-state stays in the API/store. Full suite ran as the gate (FA_TEST_WORKERS=6).
- 2026-10-25 07:1x — item 1 review (handoff rule 5): two dispatch attempts timed out (Gemini
  read-timeout ×2, DeepSeek 1800s hang — remote lanes degraded with 3 live sessions on the
  shared limiter); the third attempt via a prefs override (step5 → nemotron → local fallback,
  cap 2400s) landed from StepFun Step 5 Preview (family stepfun, cross-family ✓). Verdict
  FIX-FIRST, 6 findings; each verified against the code per rule 5: FALSE POSITIVES — the
  swap path's prov/model are locally established before my call (the reviewer flagged them as
  unseen), and the store tuple change has no external unpackers; CONFIRMED — the picker
  wiring was genuinely missing (hydration + on_thinking_saved never reached
  session_picker.py: the row would have reset every restart; the model-layer tests masked it
  exactly like observation 0031's harness-context trap), the GLM notice overclaimed with an
  explicit --effort, and the dispatch stderr stayed silent on a non-overridden GLM OFF.
  All three fixed TDD (pilot wiring test end-to-end; wording test; stderr disclosure test).
  Finding 5 (stored True not in the row cycle) verified benign: setting()'s except path
  routes it to the default, no crash — recorded, no change.

- 2026-10-25 08:3x — **0.44.0 SHIPPED** (tag v0.44.0, release published, installed copy verified,
  get-haiggoh sees 0.44.0 (current), brew bumped). One merge hiccup: the canonical checkout held the
  predecessor's UNTRACKED handoff copy at this path, so the ff-merge refused; verified it identical
  to the branch's commit up to the Progress log, removed the stale duplicate, re-shipped clean.
  0.44.0 carries: the thinking toggle + review fixes + the two gate-flake fixes (item 3) + the
  Task-10 prefill tool + the JoyIA DRAFT (item 4).
- 2026-10-25 1x:xx — the tool's FIRST LIVE RUN found two defects the stubbed tests could not
  (obs 0047 logged): a timed-out launch ORPHANED the launcher's LiteLLM proxy on scratch :4146
  (wrapper-only kill; the proxy ignored SIGTERM and needed SIGKILL), and the hardcoded 900s
  timeout was a false-failure generator under the shared limiter. Fixed in 0.44.1: the launch runs
  in its own session and the timeout kills the whole process GROUP; FA_PREFILL_TIMEOUT tunes it
  (default 1800s). The 0.44.1 ship gate then caught TWO more contention finds, both fixed and
  mutation-checked: package-release.sh ignored --help and ran its real work (now parses args
  first), and the PTY live-filter pilot read rows after a fixed 0.15s gap (now waits on the
  discriminating state). A THIRD gate run caught the smooth-bucket contract test (test_8's
  booked==6: at 40 RPM refill is ~0.67 tokens/s and a lagging write-back crossed a whole token;
  pinned LA_NVIDIA_RPM=1 so a token of refill takes 60s — assertion unchanged).
- 2026-10-25 14:0x — **0.44.1 SHIPPED** (three gate attempts; the second and third surfaced the
  findings above). Tag pushed, release published, installed copy refreshed, brew bumped,
  visibility verified (0.44.1 current, HEAD manifest serves 0.44.1).
- 2026-10-25 14:2x — item 2 (dogfood swap) LIVE ATTEMPT: session on scratch :4149
  (nvidia-glm53-full, multi-request tool task). Mid-flight swap to nvidia-glm53 --force: the
  pre-flight probe returned 200 ON THE SCRATCH BAND (0.43.6's fix verified live, fast, limiter-off),
  the swap executed, but the LIVE probe read 000 curl-failed and the AUTOMATIC REVERT restored
  the previous model (the 0.43.6 rescue path verified live). The session SURVIVED the whole cycle
  and completed its task end-to-end (result JSON at 14:56, 27.5 min, all three tool results).
  Suspect for the live-probe 000: the old proxy ignoring SIGTERM and holding the port while the
  new one tries to bind (same linger my morning orphan showed). NOT yet the clean swap the
  waypoint owes — the kill-then-wait-before-bind fix is the path to it (swap stub suite exists).
- 2026-10-25 15:0x — user mid-turn asks (both handled): (a) gemma _override_ evidence — found
  three-source: transformers 5.14.1 refuses to build Gemma4Processor without
  video_preprocessor_config.json, neither publisher ships it (config.local.sh block +
  local-roster-RESULTS-MANIFEST.md 'BLOCKER found pre-load and fixed' + the symlink-farm artifact
  itself, 412K); the config comment's stale plans pointer fixed in place. (b) the Devstral
  duplicate the user suspected traced to the 2026-10-19 auto-register sweep (not two catalog
  rows): ~/.models/Devstral-Small-2-24B-4bit is byte-identical to devstral-small2-uniform4
  (shasum match, 14G wasted); disk-wide scan of 40 weight folders found NO other true duplicates
  (the gemma _override_ pair is intended symlink sharing). Recommendation recorded on the
  waypoint: delete the unregistered copy — user's confirmation, it is destructive.
- 2026-10-25 15:3x — user mid-turn asks (both handled): merged chore/rtc-hooks to main (0acc85b,
  pushed; 37-check branch test green in its worktree, version surfaces 0.44.1 agree, the new test
  joins the shell-suite gate) — this also UNBLOCKS item D's rtc-ship.sh route. Then item D of the
  rule-memories-into-plugins waypoint: GATED — the predecessor's transcript (41a9f4ff) was
  written 21 min before the check, inside the 30-minute window; the gate says skip while that
  session works. Re-check after 30 min of quiet. The four target skills read; the method is the
  plan's section 5.
- 2026-10-25 1x:xx — item 5 receipt IN FLIGHT: baseline prefill run launched through the shipped
  0.44.1 tool (fixed timeout). mcp=all run + the §5 B3 table + the Task-11 proposal follow.

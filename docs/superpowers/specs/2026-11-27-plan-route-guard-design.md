# Plan routing barrier + three-route offload prose — design

Date: 2026-11-27 · Branch: `feat/plan-route-guard` · Base: `cc64ec0` (0.64.1)

## Intent (the why)

On PAID sessions, plans keep coming out with zero free steps although the offload rule is
present in CLAUDE.md, in the SessionStart nudge and in the offload skill. Measured instance:
2026-11-27, a 9-task check-code plan written all-cloud with the reason "a stateless dispatch
cannot edit code or run tests" — the reason CLAUDE.md lists as INVALID. The user has seen this
recur more often recently. A stronger note would fail the same way; the fix is a barrier at
the one step every plan passes (the write of the plan file), plus correcting the prose that
actively steers tool-using work away from free lanes.

## Root causes (verified 2026-11-27)

1. **The nudge fires at the wrong moment and in the wrong words.** `hooks/offload-nudge.py`
   runs once at SessionStart and frames the rule around "delegated work — before ANY
   Agent/Task/Workflow call". Planning involves no Agent call, so nothing matches when a plan
   is written. `writing-plans` has no routing step; the `offload` skill was not suggested.
2. **The single-source routing block says something false.** `skills/offload/SKILL.md:48-50`
   (emitted verbatim by both hooks) says tool work goes to "a free SESSION in its own window
   via free-session-handover … (no headless form …)". But:
   - `bin/remote-session.sh:1733-1738,2735` forwards every option after the alias (or after
     `--`) to `claude`, so `remote-session.sh <alias> --prompt-file <brief.md> -- -p
     --output-format json` IS a headless session; `bin/fa-prefill-measure.py:221` drives
     exactly that, and its live dogfood verified it end to end (CHANGELOG ~2660).
   - `references/offload-in-depth.md:144-149` and the 0.49.x CHANGELOG describe the correct
     three-route model (headless background session for offloading, window only for handover).
   - The block's wording came from a later fix that removed a NON-existent `-p` flag placed
     BEFORE the alias, and over-corrected to "no headless form".
   - LIVE CHECK (2026-11-27, verified): `remote-session.sh nvidia-mistral-large4 --prompt-file
     <brief> -- -p --output-format json` → EXIT 0 in 91 s, `num_turns: 2`, result
     `HEADLESS-OK cc64ec0` = the real `git rev-parse --short HEAD` (the model ran the command).
     claude warned "no stdin data received in 3s" → the documented form ends with `< /dev/null`.
   - LOCAL lane (verified): `bin/launch-claude-agent.sh:1024` builds the claude call only from
     its own flags (usage `<alias> [effort-override]`), so there is NO headless local session
     today. The `session` route is remote-lane only until that launcher forwards args.
3. **The tag vocabulary hides the dispatch/session choice.** `local:<role>` /
   `remote:<provider>` name WHERE the model runs, never HOW (dispatch vs session), so "needs
   tools" reads as "not local" and drifts to cloud. `local:` is also wrong for remote free APIs.

## Goals

1. **The three free routes, named everywhere the same way:**
   - **dispatch** — one stateless request (`bin/free-agent-tool.py`; text in/out, `files` attached).
   - **session** — a headless free session in the background of THIS window, for multi-step
     tool work while the user stays here (`remote-session.sh <alias> --prompt-file <brief> --
     -p --output-format json < /dev/null` via `run_in_background`; REMOTE lane only today —
     see "Open for the user").
   - **handover** — an interactive successor in its OWN window that the user types into
     (`free-session-handover` skill; unchanged).
2. **Plan tags:** `free:dispatch`, `free:session`, `free:handover`, `cloud:<reason>`. No role
   in the tag: for a single offload the model ladder picks the model. A role suffix
   (`free:session/reasoner`) is allowed but only meaningful for multi-agent coordination where
   roles are assigned on purpose. Legacy `local:<x>` and `remote:<x>` are accepted as aliases
   of `free:` (route unspecified), so existing plans keep passing.
3. **The barrier** — new `hooks/plan-route-guard.py`, PreToolUse on `Write|Edit|MultiEdit`:
   - fires only on a PAID session (`ANTHROPIC_BASE_URL` not localhost — same test as the other
     hooks) and only when `plan_check` is on;
   - a "plan file" = path under `docs/superpowers/plans/` or `~/.claude/plans/` (any depth,
     `.md`) whose RESULTING content (Write: the content; Edit/MultiEdit: the current file with
     the edits applied in memory) has ≥ 2 task headings (`^#{2,4} (Task|Step|Phase) `);
   - every task heading's section must carry a route tag (heading line or the section's first
     non-empty lines); a `cloud:` reason matching the invalid list is refused (`tool use`,
     `tools`, `too hard`, `too complex`, `local is slow`, `slow`, `not delegatable`); a plan
     with ZERO `free:` tasks needs a plan-level `Routing:` line whose reason is not invalid;
   - on failure: `permissionDecision: deny` naming each offending task, the rule, the valid
     tags, and the opt-out (`cloud_session_env.py set plan_check 0`);
   - fail open on any internal error (never blocks on a bug), like the other guards.
4. **csl setting `plan_check`** (user choice: separate explicit setting, more user control):
   `cloud_session_env.py set plan_check {1,0}` → `FA_PLAN_CHECK` in settings.json env;
   `show` prints it; a csl menu row "Plan routing check: ON/OFF" next to the route rows;
   default ON. The nudge and guards section mention it only when ON, plus its opt-out.
5. **Prose rewrite (single sources only):** the routing block (three routes, the real headless
   command, < 1,500 bytes, no model names); the nudge's opening sentence (covers PLANNING, not
   only Agent calls); the offload skill's "Decide at decomposition" section and
   `offload-in-depth.md` (new tags, no role default); `free-session-handover`'s wording where
   it contrasts session vs handover. Existing tests that pin the block against the real parser
   are extended to pin the `-- -p` form.
6. **User files (approved):** `~/.claude/CLAUDE.md` and `~/AGENTS.md` Credit Efficient Mode
   lines (`local:<role>` → the new tags), edited in place after a backup; outside the repo.

## Non-goals

- Changing how dispatch/session/handover actually run (launchers are correct; only prose and
  the guard change). Adding a new headless flag to `remote-session.sh`.
- Blocking via Bash-written plans (`cat > plan.md`): a plan written through Bash bypasses the
  Write/Edit matcher. Out of scope; recorded as a known gap (plans are written with Write).
- The summon-skills follow-up (suggest `offload` / `free-session-handover` more) — waypoint
  `summon-skills-suggest-free`, after this ships.

## Anti-goals

- A barrier that keeps stopping legitimate all-cloud plans: the `Routing:` line and the
  `plan_check 0` opt-out must always be available and named in the deny message.
- Blocking on free sessions (they already run free) or on non-plan markdown.
- A second copy of the routing text anywhere (the 0.54.0 single-source test must stay green).

## Approaches considered

- **Barrier opt-out:** (A) third route path `route[plan]`; (B) separate `plan_check` setting —
  CHOSEN by the user ("more control"); (C) fold into `route[agent]` — rejected (couples two
  unrelated choices).
- **Tag shape:** `free:dispatch|session|handover` — CHOSEN (names HOW, so tool work visibly
  maps to `free:session`); flat `free:<role>` — rejected (keeps the dispatch/session ambiguity).
- **Stronger SessionStart wording only** — rejected: it is still a note at the wrong moment.

## Decided with the user (2026-11-27)

- Local-lane headless session is IN this change: `launch-claude-agent.sh <alias> [effort] --
  <claude args>` forwards everything after `--` to claude (mirroring `remote-session.sh`), so
  `free:session` means the same on both lanes and the routing block names one form.
- Design approved.

## Builder's calls (open to correction)

- Invalid-reason matching is case-insensitive substring on the text after `cloud:`; valid
  examples in the deny message: `cloud:spec`, `cloud:verify`, `cloud:review`,
  `cloud:security`, `cloud:not-worth-it — <why>`.
- The guard reads `plan_check` through `cloud_session_env.py` (same strict settings read as
  `route()`; a corrupt settings.json → fail open + one stderr line).
- Version: next free minor at ship time (tag namespace is shared with in-flight branches —
  fetch + ls-remote before picking).

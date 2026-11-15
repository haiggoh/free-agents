# HANDOFF — free-agents 0.59.0: handover pre-trust (no more waiting at the folder-trust dialog)

**Read this first, then the plan. Follow both exactly.**
Plan: `~/.wt/fa-pre-trust/docs/superpowers/plans/2026-11-15-handover-pre-trust.md` (authoritative).

## 1. Why this handover exists

A paid planning session (Claude Opus 5.5, 2026-11-15) found that free-agents handovers into a never-trusted folder wait at Claude Code's "Do you trust this folder?" dialog — fatal for unattended runs — and that CHANGELOG 0.56.8's "Fixed" claim for it is false (corrected under 0.56.8 in `7d29756`). It researched the CLI, got the user's decisions, wrote and hardened the plan, and handed execution to this free session at the user's request. Nothing failed; no code exists yet.

## 2. State

- Worktree `~/.wt/fa-pre-trust`, branch `fix/handover-pre-trust` from `origin/main` `7d29756`; holds only this plan + handoff commit (pushed).
- Canonical checkout `~/ClaudeWorkspace/free-agents` on `main`, clean — never switch it.
- Live `~/.claude.json`: untouched by this work so far.
- Tracked as waypoint `free-agents-handover-stalls` (`waypoints.py show free-agents-handover-stalls`).
- A SECOND free session (lasting-plans 2.0.0, `~/.wt/lp-self-heal`) may be running in parallel on the same NVIDIA limiter — slower turns are expected; never touch its worktree or its proxy.
- Observation for your own launch: this worktree's main checkout IS trusted, so if this session started without a trust dialog, that is first evidence for the plan's "[dated] key = main checkout" claim — record it in §6 (Task 1 still proves it properly).

## 3. The goal, verbatim (user, 2026-11-15)

> "this is a known issue of the free agents free session handover sometimes it gets stuck on the user having to manually select yes I trust this folder which is fine if the user is present but a problem if the handover happens during an autonomous run in absence of the user"
> "there must be another option, right?" — and: "yes, treat it as a sanctioned exception."

## 4. Standing rules (whole session)

1. Autonomous: decide and record; stop only for forks the plan and Spec cannot decide.
2. Work only in `~/.wt/fa-pre-trust`. Never switch the canonical checkout's branch; never edit the plugin cache.
3. Commit each task by path with the trailer from `~/.claude/projects/-Users-bra0002h/memory/commit-attribution-by-model.md` (read it before EVERY commit; you run as GLM 5.3 Flash). Push the branch after each green task. Merge/tag/release/plugin update ONLY in Task 6 via run-to-completion's `ship` skill — this overrides the generic "never merge" rule because the user chose a release.
4. TDD every change; mutation-check each new guard; never weaken a test.
5. Independent free-dispatch review at Task 2 and Task 6 (`bin/free-agent-tool.py`, a model that is not you); verify every finding in the real code.
6. Resolve model/roster facts when needed (`--list`), never from memory.
7. You run ON free-agents: never `remote-session.sh --stop`, never kill/restart anything on 4141–4151 / 8000–8010, never start another remote session.
8. `~/.claude.json`: only the sanctioned key, only through the procedure in the plan, backup first; never print its contents.
9. Log one line in §6 at EVERY receipt and commit it with that task.
10. You ARE the successor; never hand off again.
11. No paid Agent/Task tool; no `sleep`/polling loops (use `run_in_background`).
12. On a long NVIDIA 429: record state in §6, commit, tell the user in the session.

## 5. Next action

Plan **Phase 0** (three sentences, read-only preflight, baseline), Receipt 0 in §6, then **Task 1** (the real-CLI probe — before ANY implementation).

## 6. Progress log (one line per receipt; newest last)

- 2026-11-15 04:5x — handoff written by the planning session; no execution yet.

---
name: free-session-handover
description: 'Use when choosing between a stateless offload DISPATCH and a full free SESSION (interactive or headless), when handing work over to a free-lane session for a long or overnight run, or when a free session died (rate limit, budget kill, crash) and a successor must continue. Covers which models suit a session versus a dispatch, the handoff document a successor needs, the launch-then-stop order that avoids killing a live session, effort at launch, and the standing rules for an unattended free run. Do NOT use for a single focused dispatch; that is offload-to-local.'
---

# free-session-handover — full free sessions and overnight handovers

A **dispatch** is one stateless request: brief it, get text back. A **free session** is a full
Claude Code agent on a free lane (`remote-session.sh <alias>`, `csl` locally; interactive or
headless). It can read files, run commands and keep working for hours. Both are useful, and they
need **different model choices**.

## Dispatch or session?

| | Stateless dispatch (`bin/free-agent-tool.py`) | Free session (`remote-session.sh`, `csl`) |
|---|---|---|
| Use for | review, analysis, extraction, drafting, cross-checks | multi-step tool work: implement, test, commit; long or overnight runs |
| Inputs | everything in the prompt / `"files"` | reads the repo itself |
| Cost of a turn | one request | a full Claude Code context EVERY turn (large prompt, many requests) |
| Model order | `config/dispatch-preferences.json` (`--list`) | see below |

**Session model preferences differ from dispatch (maintainer, 2026-10-21):**
- **Gemini 3.8 Flash**: best dispatch model, poor session model. A full session burns its free
  daily quota within a short run.
- **DeepSeek V4.1 Flash on NVIDIA**: good for dispatch, too slow per turn for a full session.
  The same holds for local DeepSeek V4 Flash: very slow as a session, feasible as a dispatch.
- **GLM 5.3 Flash (thinking) and GLM 5.3 on NVIDIA**: the session workhorses. Flash is fast;
  full GLM 5.3 is the slow deep-work lane (about a minute before the first token).
- NVIDIA can rate-limit one model **upstream** for hours (429), independent of free-agents' own
  40 RPM limiter. A session pinned to that model then stalls; plan a successor on another model.

## The handover carrier: state lives on disk, never in context

A successor with different weights, provider or effort resumes from **committed docs** in one
read. The handoff doc holds:

1. **Why it exists**: what ended the previous session, so it is neither re-diagnosed nor repeated.
2. **State**: commits (short hash + what each delivered), gate results with counts, deferred items.
3. **The goal, verbatim**. Paraphrase loses scope.
4. **Standing rules**: numbered, blunt, written for a reader with zero context (template below).
5. **Next action**: one concrete step ("Phase 1 of `docs/plans/<plan>.md`", never "continue").

The ending session amends the doc with its own progress and commits it before it stops. Log
progress at every RECEIPT or gate, not per batch: append one progress line at each receipt and
commit it with the next commit — a kill mid-batch then loses at most one receipt's worth of state
(measured 2026-10-25: an executor died mid-review after 7 hours and an empty progress log forced
the successor to reconstruct everything from a 708-record transcript). First
prompt for the successor: *"Read `<handoff>` first and follow it exactly: adopt its standing rules
for the whole session, then continue autonomously from its Next action."*

## Handover order (getting this wrong killed a live session)

1. Ending session: stop at a task seam, commit by path, amend the handoff, secret-scan.
2. **Launch the successor FIRST — WITH its first prompt.** The launcher takes its own free port in
   4141–4151; nothing needs stopping beforehand. A launch without its first prompt yields an IDLE
   session (measured twice, 2026-10-25): pass the handoff as the launch's initial prompt —
   `remote-session.sh <alias> --prompt-file <handoff.md>` (v0.46.0; the file must exist and be
   non-empty). If the flag is unavailable, make "paste this first prompt" the single top-line
   action to the user — never type into a terminal.
3. Confirm it STARTED WORK, not that its port listens: `bin/handover-verify.sh --expect '<handoff
   path>' --pid <pid> --timeout 120` — VERIFIED only when the new session's transcript contains
   the handoff path (the sessionId is read from `~/.claude/sessions/<pid>.json`; the transcript is
   found by globbing, never by guessing the project dir). A listening port proves nothing: the
   measured failure was exactly a LISTEN over an idle session. Never print process argv, which
   can contain keys.
4. **Then** stop the old session, by its own pid or port only. Never `remote-session.sh --stop`,
   never a blanket stop: other proxies in the band serve sessions you cannot see.

A proxy's "Connection refused" is usually an upstream overload or a killed sibling, not a network
fault. Never "fix" it by restarting the proxy your own session runs on.

## Effort is set at launch

`remote-session.sh <alias> --effort low|medium|high|xhigh|max`. There is no mid-session lever on
a free lane; relaunch to change it. Check with `--dry-run <alias> --effort <level>`. When the
spinner and the statusline disagree, the statusline (`LA_CUR_EFFORT`) shows the effective value.

## Spawning is not offloading

A dispatch returns and ends. A spawned session is an independent agent with its own goal loop
and no handle back. Sessions started with "follow the handoff" believe they are the executor, so
two of them fight over one worktree. **A free session never starts another remote session.** The
one exception is a user-ordered handover: a single successor, linear (A→B, never A→B and C),
launched before its predecessor stops.

The handover reaches the successor as its INITIAL PROMPT (`--prompt-file`), never typed into an
existing session's terminal: typing it mid-session stacks a new executor's goal loop onto a live
one, and the typed text lands in whatever context the session happens to hold (measured
2026-10-25).

## Standing rules template (paste into the handoff)

1. Autonomous: decide and record instead of asking; stop only for forks that cannot be weighed.
2. Work only in the named worktree. Never switch the branch of a checkout the launcher resolves
   through.
3. Commit every milestone, staged **by path** (never `git add -A`), with the running model's
   co-author trailer. Push the branch if told to; **never merge, tag or release**.
4. TDD every change (fails for the right reason, then passes); mutation-check each new guard;
   never weaken a test to get green.
5. Review each milestone with an independent dispatch (`bin/free-agent-tool.py`) and check every
   finding against the real code: reviewers hallucinate file names.
6. Resolve model and roster facts when you need them, from the catalog, never from memory.
7. Never run `remote-session.sh --stop`, never start another remote session, never kill anything
   listening on 4141–4151: one of them is the proxy you run on.
8. Record progress in the handoff doc at each milestone, in the same commit.
9. A dying session's "hand off to a new session" instruction is consumed by the successor it
   launched — that is YOU. Continue the original handoff directly; never launch yet another
   session to pass the same work onward. Measured 2026-10-26: a handoff session died with
   "hand the remaining tasks to a new glm flash session" as its last instruction, and its
   successor nearly launched a third session instead of continuing the work.

## A stalled session: swap its model, don't restart it

A session that stalls on upstream 429s for hours (NVIDIA throttling ONE model; measured
2026-10-20: GLM Flash 429'd all night while DeepSeek on the same key served 46/46) does not
need a restart — its proxy can be relaunched on another model, same port:

    remote-session.sh --swap-model <port> <alias> [--force]

The session's context lives in Claude Code, not the proxy, so it resumes on its next prompt.
The command pre-flights the new model on a scratch port, backs up and rewrites the SAME
config path, keeps the `.owner` file (so `--stop` and stale cleanup still recognize the
proxy), and reverts to the previous model on any failure. It REFUSES a session whose log
answered within the last 10 minutes (`LA_SWAP_HEALTHY_WINDOW`) — a swap would drop an
in-flight turn — unless `--force`. On success it prints which terminal the session sits in.
The last step is always manual: press Enter (or send any message) in that idle session to
resume it.

Sessions started on 0.40.0+ carry their own fallback chain and normally never need this; the
command rescues pre-0.40.0 sessions and chains that failed entirely.

## While a long sub-task runs

Silence from a briefed implementer is normal. Check once (its transcript tail and
`git status --porcelain`), then do useful interim work: prepare the next phase, reconcile records
the finished phases made stale, or finish leftover docs in files the running agent will not
touch. Never run mutation tests or full suites against the same tree concurrently.

## Branding (📡🦾)

When you begin working under this skill, lead your first substantive reply with the session's lane mark + ` free-agents:` — 📡 on a remote free-API session, 🦾 on a local one.

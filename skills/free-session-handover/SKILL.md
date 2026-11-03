---
name: free-session-handover
description: 'Use when choosing between a stateless offload DISPATCH and a full free SESSION (interactive or headless), when handing work over to a free-lane session for a long or overnight run, or when a free session died (rate limit, budget kill, crash) and a successor must continue. Covers which models suit a session versus a dispatch, the handoff document a successor needs, the launch-then-stop order that avoids killing a live session, effort at launch, and the standing rules for an unattended free run. Invoking this skill IS the authorization to launch the successor: name concerns (blind-trust, branch pushes) but launch — defer only on the user''s explicit request. A handover is ALWAYS launched interactive in its own Terminal window, never headless from the Bash tool. Do NOT use for a single focused dispatch; that is the offload skill.'
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
- **Mistral Large 4 (Le Chonk) on NVIDIA and GLM 5.3 Flash (thinking)**: the session workhorses.
  Le Chonk is the top pick (user 2026-10-29): 1.05T total / 52B active MoE, 1M context, fast and
  strong on tool use. GLM 5.3 Flash is fast and sharp. GLM 5.3 full is GONE — NVIDIA retired it
  (410 Gone, EOL 2026-10-28T08:00:00Z; live catalog no longer lists z-ai/glm-5.3) and it is off
  every list: pins, dispatch order, fallback chains.
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
2. **Launch the successor FIRST — WITH its first prompt, in its own INTERACTIVE Terminal window.**
   A handover is interactive by definition; a headless Bash-tool launch is for offloading (see the
   `offload` skill), never for a handover. The launcher takes its own free port in 4141–4151;
   nothing needs stopping beforehand. A launch without its first prompt yields an IDLE session
   (measured twice, 2026-10-25): pass the handoff as the initial prompt — in the LINK form,
   `--prompt "Read <handoff.md> first and follow it exactly…"` (0.50.8; the prompt TEXT names
   the path, so the path lands in the successor's transcript — see step 3). Launch mechanics:
   - **prefer `bin/launch-remote-window.sh <alias> --effort <level> --prompt "Read <handoff.md>
     first and follow it exactly…"`** (0.50.8): it hard-requires a TTY and execs `csl remote`, so a
     real window opens when run in Terminal.app and it REFUSES headless from an agent Bash tool;
   - when the agent shell has no TTY, drive a REAL window via
     `~/.claude/scripts/sudo-in-terminal.sh --command "launch-remote-window.sh <alias>
     --prompt 'Read <handoff.md> first…' --effort <level>" --verify '<LISTEN on 4141-4151>'
     --label '<t>'` (verify = port/LISTEN; `pgrep -f` matches the driver's own argv);
   - pass `--effort` explicitly or rely on the stored last-launch effort (0.49.3+ default) — never
     an unset default when the stored one is higher;
   - **invoking this skill IS the launch authorization** — name concerns (blind-trust, branch
     pushes) but launch; defer only on the user's explicit doc-only request;
   - if `--prompt` is unavailable, make "paste this first prompt" the top-line action to the
     user — never type into an existing session's terminal.
3. Confirm it STARTED WORK, not that its port listens: `bin/handover-verify.sh --expect '<a text
   line from the doc, e.g. its title or the handoff PATH>' --pid <pid> --timeout 120` — VERIFIED
   only when the new session's transcript contains that text (sessionId from
   `~/.claude/sessions/<pid>.json`; transcript found by globbing). With the LINK form
   (`--prompt "Read <path> first…"`) the handoff PATH DOES land in the transcript as the prompt
   text, so `--expect '<handoff path>'` works (0.50.8; with the old `--prompt-file` it sent the
   file's CONTENTS and the path never appeared — expecting it reported NOT_VERIFIED on a
   correct handover, 2026-10-31). A LISTEN alone proves nothing (measured: LISTEN over an idle
   session). Never print process argv, which can contain keys.
4. **Then** stop the old session, by its own pid or port only. Never `remote-session.sh --stop`,
   never a blanket stop: other proxies in the band serve sessions you cannot see.

A proxy's "Connection refused" is usually an upstream overload, not a network fault — never
"fix" it by restarting the proxy your own session runs on (details: `references/operational-notes.md`).

## Effort is set at launch

`remote-session.sh <alias> --effort low|medium|high|xhigh|max`. There is no mid-session lever on
a free lane; relaunch to change it. Check with `--dry-run <alias> --effort <level>`. When the
spinner and the statusline disagree, the statusline (`LA_CUR_EFFORT`) shows the effective value.
Omitting `--effort` (0.49.3+) uses the alias's stored last-launch effort if recorded, else the
provider default — never let a handover silently drop to medium when the stored effort is max
(2026-10-31).

## Spawning is not offloading

A dispatch returns and ends; a spawned session is an independent agent with its own goal loop.
**A free session never starts another remote session** — the one exception is a user-ordered
handover: a single successor, linear (A→B), launched before its predecessor stops, reaching the
successor as its INITIAL PROMPT (`--prompt` with the handoff path in the text — the link form,
0.50.8), never typed into an existing session's terminal. Full rationale:
`references/operational-notes.md`.

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

`remote-session.sh --swap-model <port> <alias> [--force]` relaunches a 429-stalled session's proxy
on another model, same port (refuses a healthy session unless `--force`; press Enter in the idle
session afterwards to resume). Full procedure: `references/operational-notes.md`.

## While a long sub-task runs

Check once (transcript tail + `git status --porcelain`), then do interim work on other files —
never mutation tests or full suites against the same tree concurrently. Details:
`references/operational-notes.md`.

## Branding (📡🦾)

When you begin working under this skill, lead your first substantive reply with the session's lane mark + ` free-agents:` — 📡 on a remote free-API session, 🦾 on a local one.

# HANDOFF — free-agents session-model split + two launch/roster regressions (2026-11-19)

You are a fresh free session on `nvidia-mistral-large4` at max effort. Read this file first and
follow it exactly: adopt its standing rules for the whole session, then continue autonomously
from "Next action".

## Why this handoff exists

The previous context was a motion-pipeline session (ship v0.6.0/v0.6.1 work — NOT yours). It
added `config/session-preferences.json` (the full-SESSION model ladder) after a live mistake:
its session launcher put **Gemini 3.8 Flash** on top of a full interactive session and the
quota was already exhausted (user, 2026-11-19: "gemini quota is exhausted you shouldn't have
launched a fresh interactive window with a session … gemini usually shouldn't be used for
full sessions … the model itself isn't the problem, the api provider quota limit is"). The
user then reported two more free-agents regressions and handed THIS work to you via
/free-agents:free-session-handover at max effort. The previous context also freed one stale
proxy for you (see State) so a port exists for your launch.

## State (verified)

- `config/session-preferences.json` NEW (commit 6a7cfd7, amended 0d87cad→current tip): full-
  SESSION order = Mistral Large 4 → GLM 5.3 Flash → Step 5 Preview → Nemotron Ultra.
  `nvidia-deepseek-v4` was added by mistake and REMOVED — NVIDIA retired
  `deepseek-ai/deepseek-v4.1-flash` (410 EOL 2026-11-16, CHANGELOG top of 0.59.8: "a retired
  id must never be in a fallback chain"); the row stays in the roster hidden by the broken
  list. Rule: keep this file in sync with the live `--inventory` roster; retired ids never
  stay in any chain.
- The model-preference SPLIT (maintainer, confirmed by the user): preferences are mostly the
  same for handover and offload; **Gemini is the exception** — fine for stateless dispatches,
  never for full sessions (the quota is the problem, not the model). Provider alternates
  exist for stateless dispatches; for full sessions, per-provider quota feasibility is NOT
  established yet (open question, don't guess).
- Port band 4141–4151 was FULL → every launch failed "no free port in 4141-4151". Evidence-
  based cleanup done 2026-11-19 ~13:20: port 4146 (pid 20316, started Nov 18 11:01) had a
  proxy log stale for ~14.5 h (last write Nov 18 22:44) — an orphaned proxy from a dead
  session — killed BY ITS OWN PID; 4146 is now FREE for your launch. The other ten proxies'
  logs were written within minutes of the check (live sessions — one of them is the
  previous context's own session, still running; NEVER touch it).

## The goal, verbatim (user 2026-11-19)

1. "in the remote session list I'm only seeing 7 models listed for nvidia api, this must be a
   mistake, there must be a lot more, we used to have many more. many I think are correctly
   filtered out as locally runnable, I think many of those weren't correctly filtered in the
   past. but only 7 seems surprising I want to make sure the list is fetched correctly"
2. "when I try to launch any remote session I get this error: … proxy : starting LiteLLM
   (Anthropic /v1/messages → nvidia)… remote-session: no free port in 4141-4151 —
   remote-session: could not start the translating proxy." (0.59.8)
3. Keep `config/session-preferences.json` correct and in sync with the roster (done; verify),
   and the session-vs-dispatch model split documented (Gemini = dispatch only).

## Standing rules (zero-context reader — follow all)

1. Autonomous: decide and record instead of asking; stop only for forks that cannot be weighed.
2. Work ONLY in `~/ClaudeWorkspace/free-agents` on `main`. Never switch the branch of a
   checkout other tooling resolves through; never touch `~/ClaudeWorkspace/motion-pipeline`
   or its worktrees (a parallel session ships motion-pipeline v0.6.1 right now).
3. Commit every milestone, staged **by path** (never `git add -A`), with your running model's
   `Co-Authored-By:` trailer below `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
   Push `main` ONLY after both regressions are verified fixed (bump 0.59.9 + CHANGELOG entry
   first — "a retired id must never be in a fallback chain" is the same discipline). Never
   merge, tag or release anything else; never force-push.
4. TDD every change (watch it fail for the right reason, then pass); mutation-check each new
   guard; never weaken a test to get green. The repo's gates run via its own rtc-ship wiring.
5. Verify every claim against the live machine: `remote-session.sh --inventory` is the roster
   source; `--list` the picker view. Review each milestone with an independent dispatch
   (`bin/free-agent-tool.py`, `avoid_family` your own) and check every finding against the
   real code.
6. **Never kill anything on ports 4141–4151 except a proxy you can prove is orphaned** (log
   mtime stale for hours AND no live session in `~/.claude/sessions/` maps to it), and never
   `remote-session.sh --stop` — one of those proxies is YOURS, and another serves the
   previous context's still-running session. Never start another remote session: you ARE the
   handover. If you need a port and none is free, apply the same evidence test to the stalest
   proxy, document it, and free exactly one.
7. Record progress in THIS handoff doc at each receipt/gate, in the same commit.
8. A dying session's "hand off" instruction is consumed by YOU — never launch yet another
   session to pass the same work onward.

## Next action

1. Reproduce the roster regression: `bin/remote-session.sh --inventory` (roster truth, ~30
   nvidia rows incl. broken-flagged) vs `--list` / the picker view (7 shown). Find the
   filter that over-hides (locally-runnable filter is NEW and intended — but 7 is too few;
   diff the two paths). TDD the fix.
2. Reproduce the port regression: with the band full of LIVE proxies, the launcher must
   either reuse a free port correctly or report WHICH ports are held by WHAT. Likely fix:
   stale-proxy detection (proxy log mtime + session-registry liveness) inside the port
   allocator + a loud line naming the stale pid. TDD with a fake band. Known leak mechanism
   from this repo's own history: `exec` discards an EXIT trap — a launcher that `exec`s the
   child never runs its proxy cleanup, so proxies leak (repo dev notes, 0.4x). Check whether
   remote-session.sh (or a recent refactor of it) `exec`s where it used to run the child.
3. `config/session-preferences.json`: verify the order against `--inventory` (drop anything
   the roster no longer offers), and add a test that fails when a session-order alias is
   retired-but-present in a chain.
4. Bump 0.59.9 + CHANGELOG (the 0.59.8 top entry already records the deepseek retirement —
   follow that format), push `main` after the gate is green.

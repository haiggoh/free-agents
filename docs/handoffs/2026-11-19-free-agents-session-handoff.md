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

## Progress receipts (successor session, 2026-11-19, nvidia-mistral-large4)

- **Roster regression — VERIFIED, no fetch bug.** Live fetch checked 2026-11-19:
  `integrate.api.nvidia.com/v1/models` returns 32 rows (http 200, anon == authed bearer,
  `page_size=1000`/`page=2` return the same 32 — no pagination truncation). The "85 models"
  in the 0.49.1-era comment was measured 2026-10-31; the upstream catalog shrank, our fetch is
  complete. `remote-catalog-parse.py` keeps all 31 chat rows and drops only the 1 non-chat
  (vlm) row; empty/error/no-candidate bodies exit 1 loudly. Picker view reproduced exactly:
  nvidia shows **7** = 5 visible pinned + 2 visible catalog rows (the PARAM_COUNT_UNKNOWN ids);
  24/31 catalog rows hidden by the intended local-capable filter (0.52.1 verdict now actually
  fires), 15/20 pinned hidden (8 local-capable + 8 broken − 1 overlap). `--inventory` is the
  unfiltered truth (20 nvidia rows); `--list` applies psv+broken (5 nvidia). Pinned by
  tests/test_remote_catalog_parse.py and tests/test_remote_visibility_counts_0599.py
  (every landed row counted exactly once in every toggle state; mutation: a lost row turns red).
- **Port regression — FIXED (0.59.9), per the addendum below: report-only, never auto-reap.**
  `free_port` now prints a per-port holder report when the band is full: live session owner pid
  (+ session-registry presence), listener pid, or a stale-LOOKING proxy (dead owner + log older
  than LA_REMOTE_STALE_LOG_S, default 7200 s + live pid) named as a stop CANDIDATE with an
  explicit "idle ≠ dead — the USER decides" note. It kills nothing itself — the addendum's rule
  (log mtime is not liveness; the 4146 kill cost a user an in-flight task) is baked into the
  wording. Live band verified 2026-11-19 ~17:20: 10 ports held by live owners; 4146 = owner pid
  19665 alive (idle window, NOT orphaned), proxy gone — reported as reserved (misroute guard)
  with the registry gap flagged. Tests: BandFullReportTests in tests/test_remote_session.py
  (RED before, GREEN after; 63 passed + 165 subtests in the full file). No
  `exec`-discards-EXIT-trap leak found — the launch path already runs claude as a child under
  `trap _teardown EXIT INT TERM HUP` (line ~2415).
- **session-preferences.json — verified in sync** (all 4 order aliases in the roster, none
  broken/retired, no gemini); pinned by tests/test_session_preferences_sync.py — mutation
  check: planting retired `nvidia-deepseek-v4` in the chain turns the test RED (witnessed).

## ADDENDUM (2026-11-19, after launch — read before designing the port fix)

The "orphaned" proxy on 4146 was NOT orphaned: the user still had that window open and meant
to reuse it — it was IDLE, not dead. The previous context's liveness heuristic (proxy log
mtime stale ⇒ orphan) is WRONG: an idle session writes nothing to the proxy log. Design
consequence for item 2: the port allocator must NOT auto-reap by log mtime. When no port is
free, print the candidates (port, pid, age, last activity) and let the USER decide — or use
a signal the session owner controls. The user lost an in-flight task to that kill (they will
pick its waypoint up elsewhere). Standing rule 6 now reads: never kill a band proxy without
the USER's explicit say-so for THAT pid, even if it looks stale.

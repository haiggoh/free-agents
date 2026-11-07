# Handoff — free-agents 0.55.6: `la-evict --orphans` + `free-agent-tool.py --out`, then ship

**Written:** 2026-11-07 ~20:10 CET by a cloud Opus 5.5 session (ending). **Executor:** a free session
(Mistral Large 4 "Le Chonk", NVIDIA, effort max). **Worktree:** `~/ClaudeWorkspace/.wt/fa-la-evict-orphans`,
branch `feat/la-evict-orphans`, forked from `origin/main` 27861cc (v0.55.5).

## 1. Why this handoff exists

The cloud session was moved to a free lane to save gateway budget (user request). Nothing failed. Two
facts it measured that you must NOT re-diagnose:

- **The full pytest suite WEDGES on this box** (0% CPU, no output; ran 75 min with `pytest -n auto`, then
  killed by pid). Known issue — memory `ship-gate-wedge-and-env-leaking-tests`. Run the full suite
  in the background with a watchdog. If it shows 0% CPU and no new output for more than 5 minutes,
  kill ONLY the pids you started, then run it file by file (`pytest -q tests/<file>` per file). Main gate: NO
  `CLAUDE_CONFIG_DIR` isolation.
- **Version 0.55.6 is contested and the user decided:** a live GLM 5.3 Flash session (pid 25792,
  worktree `~/ClaudeWorkspace/.wt/fa-0556-feedback`, branch `fix/0556-feedback-round`) has a plan that
  ships its csl feedback round as 0.55.6. **User ruling 2026-11-07: THIS branch ships as 0.55.6 now; the
  GLM release becomes 0.55.7.** Do NOT touch the GLM worktree, its branch or its plan file — the user
  coordinates that session. A waypoint records the renumber.

## 2. State (verified by the ending session)

| Commit | What |
|---|---|
| `fbc840d` | `feat(la-evict): --orphans` — the tier-0 sweep outside the ladder; refused with `--tier`/`--all`/`--port` (exit 2); `--help` documents it |

- `bash tests/test_la_evict.sh` → **135 passed, 0 failed** (was 128/7 RED before the code).
- Mutation checks done: dropping the conflict refusal → 2 red (`--tier`, `--all`; `--port` is ALSO caught
  by the pre-existing `--port`+`--tier` guard — defence in depth, not a vacuous test); making `--orphans`
  a bare/ladder run → 2 red (mid-ladder tier 0, byte-identical state).
- Full suite: NOT yet green on this tree (wedged, see §1). Version NOT bumped. No CHANGELOG entry yet.

## 3. The goal (user's words, verbatim)

> "we probably should add an argument to make it easier to ensure only tier 0 is run (outside of the
> tier escalation ladder)" → "do it now"

> (on adding `--out FILE` to free-agent-tool.py that always writes the full dispatch envelope
> atomically, so no caller can save just `content` again) → **"Add it, same release"**

> (versioning) → **"Ship now as 0.55.6"**

## 4. Standing rules (read all; they bind the whole session)

1. Autonomous: decide and record instead of asking; stop only for forks that cannot be weighed.
2. Work ONLY in `~/ClaudeWorkspace/.wt/fa-la-evict-orphans`. Never switch the branch of
   `~/ClaudeWorkspace/free-agents` (the launchers resolve through it — including the one you run on).
   Never touch `~/ClaudeWorkspace/.wt/fa-0556-feedback`.
3. Commit every milestone, staged **by path** (never `git add -A` / `.`), confirm with
   `git diff --cached --stat`, ending with exactly these two trailers:
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
   `Co-Authored-By: Mistral Large 4 "Le Chonk" 1T-A52B <noreply@mistral.ai>`
   (if you are NOT Le Chonk, check your real model identity and use its trailer instead).
4. TDD every change: write the test, watch it fail for the RIGHT reason, then implement. Mutation-check
   each new guard (apply mutation via an anchor-count-checked script, confirm `git diff` is non-empty,
   run, restore with `git checkout -- <file>` from a commit). Never weaken a test to get green.
5. Review each milestone with an independent dispatch (`bin/free-agent-tool.py`, attach the diff as a
   file, `"role": "validator"`; a lane-latency probe on 2026-11-07 found Nemotron 3 Ultra
   1.1 s and Mistral Large 4 1.8 s fast, while Gemini / NVIDIA DeepSeek V4.1 / GLM 5.3 Flash timed out at 150 s).
   Check every finding against the real code — reviewers hallucinate file names.
6. Resolve model/roster facts from the catalog when needed (`bin/free-agent-tool.py --list`), never memory.
7. Never run `remote-session.sh --stop`, never start another remote session, never kill anything
   listening on 4141–4151 or 8000–8010 that you did not start. Never `pkill`/blanket kill.
   `bin/la-evict.sh --tier 0` (or your new `--orphans`) IS allowed any time — it only evicts orphans.
8. Record progress in §6 of THIS file at each milestone, in the same commit.
9. You are the successor. Never launch yet another session to pass this work on.
10. Ship authority (user-granted 2026-11-07, "Ship now as 0.55.6"): through **merge to main, tag
    v0.55.6, GitHub release, plugin refresh, dogfood** — via `rtc-ship.sh` only, never by hand.
    Never force-push, never move a published tag; a broken release is fixed forward as 0.55.7… only
    after telling the user, since 0.55.7 is promised to the GLM session.
11. Never print process argv (`ps aux`, `pgrep -fl`) — keys leak. Never type an expanded home path;
    write `~` / `$HOME`.

## 5. Next action — phases

**Phase 1 — `free-agent-tool.py --out FILE` (TDD).**
Spec (keep it this small):
- `free-agent-tool.py --out PATH` (stdin JSON as today). `--out` may be combined with nothing else
  but itself; `--out` without a value → exit 2 with a usage line on stderr; an unknown flag still → exit 2.
  Parse ALL arguments before reading stdin or dispatching (today `main()` rejects any arg other than
  `--help`/`--list` — extend that parser, don't bolt on a second one).
- On success it writes EXACTLY the JSON object it prints to stdout (the full envelope: `content`,
  `model_used`, `display`, `route`, `family`, `role`, …, `attempts`) to PATH; on "every preference failed"
  it writes the error object (`error`, `attempts`, `paid_fallback_allowed`). stdout behaviour unchanged.
- Atomic: write `PATH.tmp.<pid>` in the same directory, then `os.replace`. A failed write → exit 1 and a
  stderr line naming PATH (never silently succeed). A PATH whose directory does not exist → exit 2
  before any dispatch (explicit overrides fail loudly).
- `--help` documents `--out` and states the file holds the full envelope, not bare content.
- Tests: new file `tests/test_free_agent_tool_out.py`. Drive the REAL CLI as a subprocess with a
  stub provider (see how `tests/test_free_agent_tool_preferences.py` / `_provider.py` fake the
  dispatch — reuse that mechanism, do not hit a network). Assert: envelope written + equals stdout
  JSON; error object written on total failure; missing value → 2; missing dir → 2 and NO dispatch
  attempted; no `.tmp.` file left behind.
- Also: one line in `skills/offload/SKILL.md` (or `skills/compose-the-payload/SKILL.md`, whichever
  describes saving results) — "persist results with `--out FILE` (full envelope); never save
  `content` alone". Keep it to one line.

**Phase 2 — CHANGELOG + version + gates.**
- CHANGELOG `## [0.55.6] — 2026-11-07` with two `### Added` entries: `la-evict.sh --orphans` and
  `free-agent-tool.py --out`. Match the existing entry style (see `[0.55.5]`).
- `rtc-ship.sh --bump-versions . 0.55.6`, then `rtc-ship.sh --versions-only .` must pass.
- Gates: `bash tests/test_la_evict.sh` (expect ≥135/0), `pytest -q tests/test_free_agent_tool_out.py`,
  then the full suite per §1 (watchdog; file-by-file fallback). Run `bash .rtc/pre-flight.sh` with
  `CLAUDE_CONFIG_DIR=$(mktemp -d)`. Record counts in §6.

**Phase 3 — ship.** `rtc-ship.sh` end to end from this worktree (read `rtc-ship.sh --help` first).
If its test phase wedges, kill only its pids and re-run with `RTC_SKIP_TESTS=1` — ONLY with a green
manual run of the SAME tree recorded in §6. After the tag is pushed, confirm the update is visible
(`get-haiggoh plan`, re-check once after ~60 s if negative), refresh, then DOGFOOD through the
INSTALLED copy: `la-evict.sh --orphans --dry-run` prints `evicting (tier 0)` or "nothing"; a real
dispatch with `--out ~/.cache/memcls/dogfood-envelope.json` writes a full envelope.

**Phase 4 — memory-index classification review (read-only; stage, never apply).**
Inputs: `~/.cache/memcls/run4/out/env-{1..6}-1.json` (Nemotron verdicts, all 6 conserved, 77/77) and
`~/.cache/memcls/run4/data/batch-*.txt`; method in `~/.claude/plans/_playbooks/Playbook-classify-memory-index.md`
§3 steps 3–5. Spot-check ≥10 verdicts against the real files in
`~/.claude/projects/-Users-bra0002h/memory/`; re-read every `low` confidence and every `CLAUDE_MD`
verdict yourself. Write the staged table (file → home → owner → proposed index line → your agree/
disagree + reason) to `~/.cache/memcls/run4/review.md`. Do NOT edit, move or delete any memory file,
MEMORY.md or CLAUDE.md — the user approves moves.

**Done when:** v0.55.6 is live and dogfooded, §6 holds the receipts, `review.md` exists, and your final
message to the user lists: release URL, gate counts, dogfood output, and the review's top findings.

## 6. Progress log (append one line per milestone; commit it with that milestone)

- 2026-11-07 20:10 — handoff written; `fbc840d` la-evict `--orphans`, test_la_evict 135/0, mutations 2+2 red.

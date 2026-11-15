# free-agents waypoint cleanup (0.59.3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear the open free-agents waypoints from 2026-11-05..15 — fix the real defects (dispatch crash, two guard false positives, venv-health parity, bash-3.2 compat), do the small hygiene work (lint, smoke worktree-safety, nudge text), close the two already-verified stale items with evidence — and ship the code fixes as ONE release 0.59.3 through rtc-ship.

**Architecture:** One branch `fix/fa-waypoints-cleanup` in worktree `~/.wt/fa-0592` (based on `origin/main` `fc32662` = 0.59.2). Each fix is TDD'd (test red → fix → green → mutation check). One release because every fix is small, independent and shares the same gate; the bash-3.2 item is re-judged after reading it (Task 7 says when it splits into its own release).

**Tech Stack:** bash (incl. macOS `/bin/bash` 3.2), Python 3 (pytest, Pyright advisory), pytest gate `-n 12 -m 'not load_sensitive'`, rtc-ship for merge/tag/release, waypoints.py for the queue.

**Spec:** the waypoint store itself — `waypoints.py show <id>` per item (ids listed per task). Two user playbooks govern quality: `~/.claude/plans/_PLAYBOOKS/Playbook-milestone-qc-offload-test-proof.md` (delivery-shaped assertions, mutation-to-RED, out-of-family review) and `Playbook-split-monolithic-test-suites.md` (only if a suite needs splitting — not expected here).

## Global Constraints

- Version target **0.59.3** (0.59.0–0.59.2 are taken; verify again at Phase 0 and before the bump).
- Four version surfaces must agree: `VERSION`, `.claude-plugin/plugin.json`, `CHANGELOG.md` top entry, `docs/ROADMAP.md` `Current released version` line. Gate: `rtc-ship.sh --versions-only <worktree>`.
- TDD per fix; a test must be watched FAILING before the fix; one temporary mutation per fix must turn it RED, then be restored (`git status` clean afterwards).
- Assertions must be **delivery-shaped** (bytes on disk, argv executed, exit code of the real entry point, matched file list non-empty) — never "the source contains X" (Playbook §Declared ≠ delivered).
- Full gate command (repo's own): `"$HOME/.local/bin/pytest" -q tests -p no:cacheprovider -n 12 -m 'not load_sensitive' --timeout=900 --timeout-method=thread` — redirect to a file, read `EXIT:` and the counts; never judge through a pipe.
- Commit attribution: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` + `Co-Authored-By: GLM 5.3 <noreply@z.ai>` (verified row in `~/.claude/projects/-Users-bra0002h/memory/commit-attribution-by-model.md`).
- Stage by path; never `git add -A`. Never force-push; never move a tag.
- Work only in `~/.wt/fa-0592`; never switch the canonical checkout's branch; never edit `~/.claude/plugins/cache/**`.
- You run ON free-agents: never `remote-session.sh --stop`, never kill/restart anything on ports 4141–4151 / 8000–8010.

## 🔒 Authority matrix

| Operation | Status | Gate |
|---|---|---|
| Read-only inspection, `waypoints.py show/list/search` | allowed now | — |
| Edit + commit on `fix/fa-waypoints-cleanup` in `~/.wt/fa-0592` | allowed now | staged diff + trailer |
| Push the branch | allowed now | targeted tests green |
| Close a waypoint whose evidence is already verified | allowed now | cite the verification command + output |
| Merge to `main`, tag `v0.59.3`, release, `claude plugin update` | allowed after gate | Tasks 1–9 receipts + Task 10 review resolved; **ONLY** via `rtc-ship.sh` |
| Worktree/branch reap after ship | allowed after gate | merge confirmed; gitignored unique files archived first |
| Force-push, moving tags, editing the canonical checkout, killing proxies, editing the plugin cache | **prohibited** | — |

## 🛑 Phase 0 — forced preflight (read-only)

1. Three sentences linking Task 1 to the final outcome: the dispatch crash is fixed so fallback lanes stop dying mid-run; the guards stop blocking read-only work; the release ships as 0.59.3 and the waypoints close with evidence.
2. `git -C ~/.wt/fa-0592 status --porcelain` → empty; `git branch --show-current` → `fix/fa-waypoints-cleanup`.
3. `git fetch -q && git log --oneline HEAD..origin/main` → check for new commits (a concurrent session shipped 0.59.2 mid-run; re-check before the bump).
4. Baseline gate (command in Global Constraints) → record EXIT + counts.
5. `rtc-ship.sh --versions-only ~/.wt/fa-0592` → 4/4 agree.
6. For each waypoint id below, `waypoints.py show <id>`; if any is already fixed or closed, record that and skip its task.

## 🧪 Receipts (every task)

Command run, EXIT read from a file, pass/fail counts, the RED line before the fix, the mutation that went RED, `git log --oneline -1`, one state assertion. "Not tested" is said as such, never as passed.

---

### Task 1: free-agent-tool.py — null `timeout` in a dispatch entry crashes the ladder

**Waypoints:** `free-agent-tool-py-dispatch`, `free-agent-tool-0-56-0` (same root cause, two reports).
**Files:** `bin/free-agent-tool.py` (`_dispatch_entry` timeout resolution, ~line 586/644; `entry.get("timeout", 1800)` returns `None` when the key EXISTS with a null value → `min(None, int)` TypeError); Test: `tests/test_free_agent_tool_timeout.py` (new).

- [ ] Step 1: read the timeout resolution in `dispatch_entry` and the `min(...)` line; confirm the null-key path (grep the fresh file, do not trust this plan's line numbers).
- [ ] Step 2: failing test — construct the real shape (an entry dict with `"timeout": None`) and assert the resolved timeout falls back to the default (1800 or the documented value) instead of raising. Also assert a real value still wins. RED first.
- [ ] Step 3: fix — treat null/absent alike (`entry.get("timeout") or DEFAULT`); keep an explicit `0`/negative guarded the same way the file already validates.
- [ ] Step 4: green + mutation (drop the `or DEFAULT` → test RED) → restore → green.
- [ ] Step 5: commit by path.

### Task 2: two PreToolUse guard false positives

**Waypoints:** `fa-plugin-state-guard-exec-false-positive` (read-only EXECUTION of a plugin-cache binary blocked), `guard-false-positives-free` (compound command with a heredoc write into a git-ignored scratch dir blocked).
**Files:** `hooks/guard-tool-owned-state.py` (+ whichever guard owns the second case — locate by reading `hooks/hooks.json` matchers); Test: extend the guard's existing tests (find them: `tests/test_*guard*`).

- [ ] Step 1: read both guards and their tests; write down for each: the pattern that matched, the command shape that should be allowed, and the write shape that must stay blocked.
- [ ] Step 2: failing tests — (a) executing a plugin-cache binary read-only exits allowed; writing INTO the cache stays blocked; (b) a heredoc whose redirect target is a git-ignored scratch path under the session worktree is allowed while the same command writing into the repo tree stays blocked. RED first.
- [ ] Step 3: implement the narrowest rule that passes (segment-aware: the guard must classify the WRITE TARGET, not the mere presence of a heredoc).
- [ ] Step 4: green + one mutation per guard (widen the block back → RED) → restore → green.
- [ ] Step 5: commit by path.

### Task 3: la-detect-backend.py — venv-health parity with the shell discovery

**Waypoint:** `la-detect-backend-py-discover`.
**Files:** `bin/la-detect-backend.py` (`_discover_rapid_bin`, ~133–160: only `os.access(X_OK)`); parity source `config/config-lib.sh:261` `la_backend_bin_health`, used by `la_discover_rapid_bin` (~304–320); Test: `tests/test_la_detect_backend_health.py` (new).

- [ ] Step 1: read `la_backend_bin_health` (what it runs, its exit contract) and the python discovery order.
- [ ] Step 2: failing test — a fake venv whose bin passes X_OK but FAILS health must be rejected by the python discovery just as by the shell one; a healthy one must be accepted. Build the fixture as a real script pair on disk (delivery-shaped: the chosen path comes back, not "a function was called").
- [ ] Step 3: implement parity (run the same health probe; on bash-3.2/no-health-tool cases fall back exactly as the shell does — read it, do not invent).
- [ ] Step 4: green + mutation (skip the health call → RED) → restore → green.
- [ ] Step 5: commit by path.

### Task 4: lint/type hygiene (advisory findings)

**Waypoints:** `pre-existing-defect-unused` (unused `import os` in `tests/test_theme_files.py:9`), `free-agents-pre-existing-lint` (`bin/la-model-profile.py` ~217/298/319 Pyright `None` vs `List[Dict]`; install/ findings).
- [ ] Step 1: reproduce each finding (run the linter/Pyright on those files); record the exact lines.
- [ ] Step 2: fix them minimally (remove the import; make the `None` cases real defaults or annotate honestly). No behavior change intended — if a fix would change behavior, STOP and record instead.
- [ ] Step 3: re-run the linter on the touched files → clean; run the affected test files → green.
- [ ] Step 4: commit by path.

### Task 5: smoke_qwen38_mtp.sh — worktree-safe

**Waypoint:** `smoke-qwen38-mtp-sh-cannot` (needs the git-ignored `config.local.sh`; alias lookup fails in agent worktrees).
**Files:** the smoke script (locate: `ls bin/ commands/ scripts/ | grep -i smoke_qwen38`) + its test if any.
- [ ] Step 1: run it in this worktree to reproduce the failure; read how it resolves config.
- [ ] Step 2: make it worktree-safe per the waypoint's own suggestion — accept `LA_LIVE_CONFIG_DIR`/a sandbox config fixture, or mark it `load_sensitive` + document why (choose the smaller change; state the choice in the receipt).
- [ ] Step 3: prove it: run the script in the worktree (with the sandbox config) → it reaches alias lookup / prints the intended guard instead of failing on the missing file.
- [ ] Step 4: commit by path.

### Task 6: paid-session offload nudge suggests an unrunnable command

**Waypoint:** `free-agents-paid-session` (`remote-session.sh <alias> --effort <lvl> -p "<brief>"` — the flag parser has only `--prompt`/`--prompt-file`; from the Bash tool there is no TTY).
**Files:** the nudge text (`hooks/offload-nudge.py` — find the literal).
- [ ] Step 1: grep the literal string; read the surrounding instruction.
- [ ] Step 2: failing check — assert (in the nudge's test, or a new one) that the text names a form the parser accepts AND that the command it suggests is runnable from the Bash tool (no TTY-only flag). RED first.
- [ ] Step 3: fix the text to the accepted form (`--prompt`/`--prompt-file`, and note the Bash-tool caveat).
- [ ] Step 4: green → commit by path.

### Task 7: config-lib.sh under /bin/bash 3.2 (assoc arrays)

**Waypoint:** `config-lib-sh-requires-bash-4` (`declare -A` at `config/config-lib.sh:173–184`; `la_load_config` crashes under 3.2 with the catalogue-derive path).
- [ ] Step 1: reproduce under `/bin/bash` (3.2 on macOS): run the smallest loader path that touches `LA_CATALOGUE_*` and capture the crash.
- [ ] Step 2: judge the fix size:
  - contained (a version guard + a portable fallback, or moving the assoc-array block behind a `BASH_VERSINFO>=4` gate with a documented degraded path) → do it in THIS release;
  - structural (assoc arrays genuinely required by many call sites) → STOP, record the finding on the waypoint, and split it into its own release (own branch/worktree), reporting why.
- [ ] Step 3: if fixed here — test that the loader path works under `/bin/bash` AND under a bash-5 (`bash` on PATH) run; mutation check.
- [ ] Step 4: commit by path.

### Task 8: manual full-run flake investigation

**Waypoint:** `fa-manual-full-run-flakes` (la_evict / la_reboot / rapid_backend shell suites flake in MANUAL full runs; deselected from the ship gate since 0.56.13).
- [ ] Step 1: run each named suite alone (they are bash suites under `tests/`) and record pass/fail + timing.
- [ ] Step 2: if a flake reproduces with a concrete cause and the fix is small → fix it TDD-style and note the repro. If it does not reproduce or the fix is structural → do NOT thrash: record the evidence gathered on the waypoint (`waypoints.py edit <id> --point`) and leave it open.
- [ ] Step 3: commit whatever changed; the waypoint edit is a record, not a closure.

### Task 9: close the two verified-stale waypoints (no code)

- [ ] `verify-catalog-overlay`: already verified on disk (user store `~/.claude/free-agents/model-catalog.local.psv` exists mode 600; checkout path is a symlink to it, created 2026-11-15 13:17). Re-run that check, then `waypoints.py done verify-catalog-overlay --evidence "<the commands + output>"`.
- [ ] `free-agents-glm-csl-feedback`: tag `v0.55.7` exists on origin (fa1ba0fd) and 0.59.2 is current — the release it demanded shipped. `waypoints.py done free-agents-glm-csl-feedback --no-evidence "superseded: v0.55.7 shipped 2026-11-07; the requested version split is long past (current 0.59.3)"`.

### Task 10: quality-control milestone (independent review before ship)

Per the QC playbook, runs once, before the bump:

- [ ] Step 1: dispatch an out-of-family reviewer on the branch diff: `git diff origin/main...HEAD > $TMPDIR/diff.patch`, then `bin/free-agent-tool.py` with `role: validator`, `avoid_family: zhipu`, files = the diff + the new/changed test files + this plan; brief = the playbook's dispatch brief verbatim (requirement → PROVEN/WEAK/UNPROVEN/DECLARED-ONLY, smallest mutation that stays green, fixture check, outcome-vs-proxy, declared-vs-delivered). Record `model_used` + `route`.
- [ ] Step 2: verify EVERY finding against the real code before acting (the playbook's 3/3-vs-30/47 warning); apply each proposed mutation in isolation and watch it go RED; fix or rebut with a quoted line.
- [ ] Step 3: `pytest -rs` on the touched files → 0 unexpected skips; record.

### Task 11: version 0.59.3 + CHANGELOG + full gate

- [ ] Bump `VERSION`, `.claude-plugin/plugin.json`, `CHANGELOG.md` (new top entry: what each fix does, the waypoint ids it closes), `docs/ROADMAP.md`; re-check `origin/main` for new commits first; `--versions-only` → 4/4.
- [ ] Full gate → EXIT:0; counts ≥ baseline + new tests; push the branch.

### Task 12: ship

- [ ] `rtc-ship.sh ~/.wt/fa-0592 0.59.3 "<title from the CHANGELOG entry>"` (background; it runs the gate again, merges, tags, releases, refreshes the plugin, and reaps the worktree/branch).
- [ ] Receipt: `git ls-remote --tags origin v0.59.3` SHA; `git ls-remote origin main` = merge; newest plugin-cache dir reads 0.59.3; the ship log says the tag is VISIBLE.
- [ ] Reap leftovers if the ship did not (worktree + branches), archiving any git-ignored unique files first.

### Task 13: close the waypoints with evidence + close-out

- [ ] `waypoints.py done <id> --evidence "<commit/tag/tests>"` for every fixed waypoint (Task 1,2,3,4,5,6, [7]); `waypoints.py prune`.
- [ ] `waypoints.py list --waiting` + `resolve` (release anything whose target landed).
- [ ] Close-out report: which waypoints closed and how, which left open and why, receipts per task, anything noticed but not touched.

## ⚠️ Side-effect classification

| Step | Side effect | Containment |
|---|---|---|
| Gate runs | CPU-heavy pytest ×N | fine; the box is ours; never through a pipe |
| Dispatch (Task 10) | free NVIDIA/Gemini API call | stateless, no repo writes; `--out` file only |
| rtc-ship (Task 12) | merge/tag/release/plugin update | the only route; reaps the worktree |
| waypoint writes | the live ~/.claude/waypoints.json | only via `waypoints.py` |
| guard tests | may exercise PreToolUse hooks | temp dirs/fixtures only; never point a guard test at the live repo outside a temp copy |

## 🧨 Adversarial review

- **Green-but-inert test** → the fix is in a path the test never reaches (e.g. asserting `_discover_rapid_bin` helper instead of the entry point) → every assertion must be delivery-shaped and go through the real entry point; mutation must go RED.
- **Guard "fix" that widens the block instead of narrowing the false positive** → a read-only command still blocked OR a real write now allowed → each guard test pairs an allowed case with a still-blocked case.
- **The null-timeout "fix" that special-cases the fixture** → real entries with `0`/absent timeouts regress → test all three shapes (null, absent, real value).
- **bash-3.2 "fix" that degrades bash 5 silently** → the loader must work under BOTH shells; both are run.
- **Version collision with a concurrent session** (0.59.2 landed mid-run!) → re-check origin/main + tags immediately before the bump and before the ship.
- **Waypoint closed without evidence** → every `done` carries the command + output or a `--no-evidence` reason.
- **Ship leaves the worktree/branch** → Task 12 explicitly reaps and verifies.
- **A finding from the reviewer taken at face value** → every finding verified in code first (playbook).

## 🏁 Final delivery (answer in the closing report)

1. Which waypoints closed (with the evidence), which left open (with why)?
2. Receipts: gate EXIT + counts; the mutation that went RED per fix; the reviewer model/route + verdicts.
3. Did any fix change behavior beyond its stated scope?
4. Was the version bumped in all four surfaces, and did the tag come out visible to installers?
5. Was anything touched outside this worktree (canonical checkout, plugin cache, other sessions)?
6. Leftovers: any branch/worktree/waypoint still hanging?

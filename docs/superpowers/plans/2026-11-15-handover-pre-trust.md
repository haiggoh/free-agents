# Handover Pre-Trust (free-agents 0.59.0) Implementation Plan

**Status:** completed — shipped as v0.59.0 (tag 521f71268226f6acf4f8b7b1f59856c6a75d6a2c, merged to main as e437955c264abd70e6af968c51bc23e88b3b34b3; receipts in the HANDOFF §6)

> **Lasting Plans:** when every task below is done, set the Status line to `completed` (this file lives in the repo, so do it in a docs commit — see Task 6).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (native execution: the executor does every task, then one final whole-branch review). Steps use checkbox (`- [ ]`) syntax for tracking.

## 🚨 System instructions for the executor — read first

1. This plan is self-contained. You have NO chat history; the Spec list below is final and outranks your inferences. Revalidate live state and tool schemas before any mutation; a tool-schema error ⇒ read the schema, never guess-retry.
2. Before any file edit, write exactly three sentences linking Task 1 to the final outcome (0.59.0 released, a handover into a never-trusted repo starts without a dialog), then do Phase 0 (read-only).
3. Work ONLY in `~/.wt/fa-pre-trust` on branch `fix/handover-pre-trust`. Never switch the branch of `~/ClaudeWorkspace/free-agents` (the canonical checkout: launchers, symlinks and `config/config.local.sh` resolve through it and it is LIVE). Never edit `~/.claude/plugins/cache/**`.
4. Native tools for files; the shell for git, tests, running code. Edit anchors from a FRESH Read, whole lines. No whole-file rewrites from partial views; no placeholder code.
5. Shell is **zsh**: quote globs; no `$N` literals or backticks inside double quotes; write home paths as `~/…`/`$HOME/…`. Never judge a test run through a pipe — redirect to a file, then `echo EXIT:$?`.
6. Stage by path only; check `git diff --cached --stat` before each commit.
7. **Commit trailer:** you run on a FREE model. Before EVERY commit read `~/.claude/projects/-Users-bra0002h/memory/commit-attribution-by-model.md` and add your model's row verbatim on the line BELOW `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never invent one.
8. Highest boundary granted: **release 0.59.0 through run-to-completion's `ship` skill (merge, tag, release, plugin update)**, only after Tasks 1–5 receipts and the Task 6 review. Any write to the REAL `~/.claude.json` happens ONLY in Task 7 with its backup receipt.
9. Every task ends with a 🧪 Receipt. Failure ⇒ STOP, diagnose (systematic-debugging), fix, rerun, show both runs. Never weaken a test; if a test contradicts a verified fact, inspect the test.
10. No secrets in receipts (no `ps aux`, env dumps, printed keys). `~/.claude.json` holds OAuth/account data: NEVER print it, `cat` it or dump it; read it only through a Python snippet that prints the specific `projects[...]` keys you need.
11. Never use the paid Agent/Task tool. Reviews go through free-agents' stateless dispatch (`bin/free-agent-tool.py`).
12. **You are running ON free-agents.** Never `remote-session.sh --stop`, never kill or restart anything on ports 4141–4151 or 8000–8010 (one is your own proxy), never start another remote session. Shipping 0.59.0 installs a new cache dir; it does not restart running proxies — do not "apply" it by restarting anything.
13. If a premise in this plan is false (a file, function, line or behaviour differs), STOP that step, record what you found in the handoff §6, and adapt only if the Spec still decides; otherwise ask the user in the session.

## 📌 Context, evidence and unknowns

Labels: **[live]** verified 2026-11-15 · **[dated]** may have changed · **[decision]** user decision · **[unknown]** inspect before relying on it.

- [live] Repo `~/ClaudeWorkspace/free-agents` (GitHub `haiggoh/free-agents`), `main` at `7d29756`, VERSION 0.58.3, latest tag `v0.58.3`. Ships via `rtc-ship.json` (plugin's own `rtc-ship.sh`, plain-repo wiring; `test_workers` 12, `stall_seconds` 1200).
- [live] The stall: an interactive handover (`launch-remote-window.sh <alias> --prompt …` → `csl remote` → `bin/remote-session.sh`) into a folder Claude Code has never trusted sits at the "Do you trust this folder?" dialog until a human answers. Reproduced 2026-11-15 04:18 (lasting-plans worktree `~/.wt/lp-self-heal`).
- [live] `bin/remote-session.sh` ~L2445–2458: `--trust-workspace` is appended only if top-level `claude --help` lists it; on Claude Code 2.1.325 it does NOT (the flag is `claude ultrareview`-only) — so nothing pre-accepts trust. CHANGELOG 0.56.8 "Fixed" claim is corrected under 0.56.8 (`7d29756`).
- [live] Read from the 2.1.325 binary: the trust check is `projects[<key>].hasTrustDialogAccepted === true` in `~/.claude.json` (or `$CLAUDE_CONFIG_DIR/.claude.json` — **[unknown]**, confirm in Task 1), where `<key>` = `gMe()` = `ZR(cwd)` (a cached git-root lookup) else the cwd; an ancestor walk stops at the git repo root; home-folder trust is NEVER persisted (session-only); `claude config` no longer exists, so no CLI command writes trust; the CLI's own error text prescribes setting `projects[<path>].hasTrustDialogAccepted: true`, and its own updater adds only that key (`{...existing ?? defaults, hasTrustDialogAccepted: true}`).
- [dated] Strong evidence that for a linked worktree `<key>` is the MAIN checkout path: accepting the dialog in `~/.wt/lp-self-heal` created `~/.claude/projects/-Users-bra0002h-ClaudeWorkspace-lasting-plans/` and set `projects["…/ClaudeWorkspace/lasting-plans"].hasTrustDialogAccepted = true`, while NO `…/.wt/lp-self-heal` entry exists. **Task 1 must prove this empirically** before the implementation relies on it.
- [live] `CLAUDE_CODE_SANDBOXED` also bypasses a permission gate — REJECTED as a trust switch. `CLAUDE_BG_WORKSPACE_TRUSTED` is internal — REJECTED.
- [live] `bin/fa_notify.py` is the ONE notification helper (`notify()`); `FA_NOTIFY_SINK=<file>` = test fence (append JSON, send nothing); `tests/test_no_real_notifications_0541.py` forbids other notification routes.
- [live] `bin/handover-verify.sh` proves a launch by finding `--expect` text in the new session's transcript; tests `tests/test_handover_verify.py`.
- [live] Other claude launch sites: `bin/launch-local-auto-mode.sh:288`. **[unknown]** whether `csl local` / `launch-claude-agent.sh` supports a first prompt (a handover form) — Phase 0 enumerates.
- [decision] See Spec.

## Spec (user decisions, 2026-11-15; final)

1. Sanctioned exception: Lasting Plans/free-agents may write EXACTLY ONE key in `~/.claude.json`: `projects[<path>].hasTrustDialogAccepted = true` (recorded in memory `plugin-registry-clobber-incident`). Nothing else in that file is ever written.
2. Trust rule: **"Any repo a handover targets"** — a handover launch (an interactive launch that carries a first prompt) counts as the user's explicit act of trust in its target folder; the launcher pre-accepts trust for it.
3. Fallback when pre-trust is impossible or did not hold (target is the home folder — its trust is never persisted; write failed; the key was lost to a concurrent writer): **"Notify and wait"** — launch as today, and handover-verify detects the waiting dialog and sends ONE macOS notification (via `fa_notify`) naming the folder. Nothing is trusted silently beyond rule 2.
4. Never re-introduce `--trust-workspace` as an unconditional flag (the 0.56.8 crash). The existing `--help`-guarded form stays as is.
5. Record correction already done (CHANGELOG under 0.56.8). The 0.59.0 entry says the stall is fixed and HOW, and what was tested against the real CLI.

## Global Constraints

- ONE helper owns every write: `bin/fa_pre_trust.py`. Interface (Task 2): `pre_trust(target_dir: str, *, config_path: str | None = None, dry_run: bool = False) -> PreTrustResult` and CLI `bin/fa_pre_trust.py <dir> [--config PATH] [--dry-run] [--json]`; exits 0 = trusted (already or now), 3 = not possible (home folder / not a directory), 4 = write failed or did not hold; never any other side effect.
- Write procedure (exact): resolve the key(s) Task 1 proved; if every key is already `true` ⇒ no write. Else: copy the config to `~/.claude.json.bak.pre-trust-<UTC-stamp>` (mode 600), load JSON, set `projects[k] = {**projects.get(k, {}), "hasTrustDialogAccepted": True}` for each key, write to a temp file in the same directory with the original mode, `os.replace`; then re-read after 1 s and confirm every key is `true` (else exit 4). Keep at most the 5 newest `pre-trust` backups (delete older ones of THAT prefix only).
- Config path resolution: `--config` > `$CLAUDE_CONFIG_DIR/.claude.json` when `CLAUDE_CONFIG_DIR` is set (per Task 1's finding) > `~/.claude.json`. A set-but-missing `--config` ⇒ exit 2 with a message (explicit overrides fail loudly).
- The helper NEVER prints config contents; `--json` prints only `{target, keys, already, wrote, verified, backup, reason}`.
- Tests NEVER touch the real `~/.claude.json`: every test passes `--config` into a temp dir or sets `CLAUDE_CONFIG_DIR` + `HOME` to temp dirs. A guard test asserts the real file's mtime is unchanged across the test module.
- Every script/subcommand supports `--help` and runs nothing with it; unknown flag ⇒ exit 2 + usage on stderr.

## 🔒 Authority matrix

| Operation | Status | Gate / evidence |
|---|---|---|
| Read-only inspection | allowed now | rule 10 for `~/.claude.json` |
| Edit + commit on `fix/handover-pre-trust` in `~/.wt/fa-pre-trust` | allowed now | staged diff + trailer |
| Task 1 probe: launch real `claude` interactively in scratch repos under `$TMPDIR` (PTY), observe the dialog, kill it | allowed after gate | backup of `~/.claude.json` taken first (Task 1 Step 1); kill only the PIDs you started |
| Push the branch | allowed after gate | full suite green (Task 5 receipt) |
| Merge to `main`, tag `v0.59.0`, release, `claude plugin update free-agents` | allowed after gate | Tasks 1–5 receipts + Task 6 review resolved; ONLY via run-to-completion `ship` |
| Write the real `~/.claude.json` via the INSTALLED helper | allowed after gate | Task 7 backup receipt; only the sanctioned key |
| Any other key in `~/.claude.json`; editing it by hand / with `sed`/`jq`; restarting proxies; `--stop`; another remote session; paid subagents; force-push; moving tags | prohibited | — |

## 🛑 Phase 0 — forced preflight (read-only)

1. Three strategy sentences.
2. `cd ~/.wt/fa-pre-trust && git status --porcelain && git branch --show-current && git log --oneline -2` → clean, `fix/handover-pre-trust`.
3. `git fetch -q && git log --oneline HEAD..origin/main` → empty (else rebase and redo step 5). `git ls-remote --tags origin v0.59.0` → empty.
4. Enumerate EVERY place a `claude` session is started in `bin/` (`grep -n "claude_cmd=\|^[[:space:]]*claude --\|exec claude" bin/*.sh`), and for each: does it accept a first prompt (handover form)? Record the table in Receipt 0. Expected: `remote-session.sh` (yes: `--prompt`/`--prompt-file`), `launch-local-auto-mode.sh` (inspect).
5. Baseline: the gate command from `rtc-ship.json` (`test_cmd`) if present, else `python3 -m pytest -n 12 -m 'not load_sensitive' tests > $TMPDIR/fa-baseline.txt 2>&1; echo EXIT:$?` → record EXIT and counts (`-rs` for skips).
6. `claude --version` → record (2.1.325 expected; if different, re-check `--help` for a top-level `--trust-workspace` — if it now exists, STOP and tell the user: the simplest fix may have landed upstream).
**🧪 Receipt 0:** the outputs, the launch-site table, baseline EXIT + counts.

## 🧪 Receipts (every task)

Each task ends with the exact test command, its EXIT read from a file, pass/fail/skip counts, `git log --oneline -1`, and one state assertion. New tests must be shown FAILING before implementation (TDD red is part of the receipt). For Tasks 2 and 3 also show one temporary mutation turning a key test red (e.g. drop the `os.replace` re-read check; skip the helper call in the launcher), after committing the green state, then `git restore <file>` and confirm a clean status. Never `git stash` (shared stack).

---

### Task 1: Characterise the real CLI (empirical, before any code)

**Files:** Create `docs/notes/2026-11-15-trust-key-probe.md` (findings + exact commands); Create `tests/manual/probe_trust_dialog.py` (the probe, `--help`, not collected by pytest: name it so `tests/conftest.py`'s collection skips it, or put it under `scripts/`).

- [ ] Step 1 — backup receipt: `cp -p ~/.claude.json "$HOME/.claude.json.bak.pre-trust-probe-$(date -u +%Y%m%dT%H%M%SZ)"`; confirm with `ls -l` (size only).
- [ ] Step 2 — probe script: spawns `claude` in a PTY (`pty.fork`) in a given cwd with `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` and NO prompt, reads output for ≤ 20 s, reports `DIALOG` if the text contains `trust` and `folder` (case-insensitive), else `NO_DIALOG`, then kills ONLY its own child (SIGTERM, then SIGKILL after 3 s). Never answers the dialog. `--help` runs nothing.
- [ ] Step 3 — scenarios, each in a fresh scratch dir under `$TMPDIR/fa-trust-probe-<stamp>/` (never under a trusted folder): (a) plain git repo `r1`; (b) linked worktree `r1-wt` of `r1` (`git worktree add`); (c) a non-repo folder `plain`; then (d) set `projects[<r1 path>].hasTrustDialogAccepted=true` with the Task 2 write procedure done by a throwaway inline Python (same exact rules) and re-probe `r1-wt`; (e) set only `projects[<r1-wt path>]` (reset (d) first by restoring the key to its previous value, or use a second repo `r2`/`r2-wt`) and re-probe the worktree; (f) `CLAUDE_CONFIG_DIR` — record from `claude --help`/docs or the binary where the global config lives when it is set (do not launch with a fresh config dir: it shows onboarding, not the trust dialog).
- [ ] Step 4 — record the truth table in the notes file: for a worktree, which key(s) suppress the dialog (main checkout path, worktree path, or both); for a plain repo, the repo root; for a non-repo folder, the folder itself; realpath vs symlinked `$TMPDIR` (`/var/folders` vs `/private/var/folders`) — which spelling the CLI uses as the key.
- [ ] Step 5 — clean up: remove the scratch DIRS only. Do NOT remove their `projects` entries from `~/.claude.json`: the sanctioned exception covers setting one key, nothing else (Claude Code itself creates such entries for every folder it opens, so leftover temp-path entries are harmless). Print the count of scratch-path entries present, never their content.
**🧪 Receipt 1:** the truth table, the probe outputs per scenario, `claude --version`, and the scratch-entry count. **If the evidence contradicts "[dated] the key is the MAIN checkout path", the implementation in Task 2 follows the measured table, not this plan's expectation.**

### Task 2: `bin/fa_pre_trust.py` — the one writer

**Files:** Create `bin/fa_pre_trust.py`; Test: `tests/test_fa_pre_trust.py`.

**Interfaces — Produces:** `resolve_keys(target_dir) -> list[str]` (exactly the keys Task 1 proved; for a linked worktree derive the main checkout from `git rev-parse --path-format=absolute --git-common-dir` → its parent; realpath per Task 1); `pre_trust(...) -> PreTrustResult` (dataclass: `target, keys, already, wrote, verified, backup, reason, exit_code`); CLI as in Global Constraints.

- [ ] Step 1 — failing tests (all with a temp config + temp HOME): `test_untrusted_plain_repo_gets_key`; `test_worktree_writes_the_proven_keys` (main checkout and/or worktree per Task 1); `test_already_trusted_is_a_noop` (file bytes + mtime unchanged, no backup); `test_other_keys_untouched` (a planted config with oauth-like fields, other projects, unknown top-level keys and unicode paths: after the write, `json.load` equal except the one key; key order and other projects preserved); `test_existing_project_entry_keeps_its_fields` (`allowedTools` etc. survive); `test_home_folder_is_exit_3_and_no_write`; `test_not_a_directory_exit_3`; `test_missing_explicit_config_exit_2`; `test_unparseable_config_exit_4_untouched` (garbage file ⇒ no write, file bytes unchanged); `test_lost_write_detected_exit_4` (monkeypatch the re-read to see the key missing); `test_backup_created_mode_600_and_rotated_to_5`; `test_mode_preserved`; `test_dry_run_writes_nothing`; `test_json_output_never_contains_config_values` (planted secret-like value absent from stdout/stderr); `test_help_runs_nothing`; `test_unknown_flag_exit_2`; `test_real_claude_json_untouched` (module-level: mtime of the real file before == after, or the file absent).
- [ ] Step 2: run → FAIL. Step 3: implement. Step 4: `python3 -m pytest tests/test_fa_pre_trust.py -v > $TMPDIR/t2.txt 2>&1; echo EXIT:$?` → PASS. Mutation per Receipts. Step 5: commit by path: `feat(pre-trust): one helper that pre-accepts folder trust (sanctioned key only)`.

### Task 3: Wire into the handover launch (the choke point)

**Files:** Modify `bin/remote-session.sh` (just before `claude_cmd` is executed, AFTER argument parsing); every other launch site from Phase 0's table that accepts a first prompt; Test: `tests/test_remote_session.py` (extend), `tests/test_pre_trust_contract.py` (create).

- Behaviour: when the launch is INTERACTIVE and carries a first prompt (`--prompt` or `--prompt-file`), run `"$REPO_ROOT/bin/fa_pre_trust.py" "$PWD" --json`; exit 0 ⇒ continue; exit 3/4 ⇒ continue the launch unchanged AND export `FA_PRE_TRUST_RESULT=<3|4>` + `FA_PRE_TRUST_TARGET="$PWD"` for the fallback (Task 4) and print one line on stderr: `free-agents: folder trust could not be pre-accepted for <dir> (<reason>) — the trust dialog will appear; you will be notified`. Launch without a first prompt (user present) ⇒ no pre-trust (unchanged behaviour). `--dry-run` ⇒ print what it would do (`fa_pre_trust.py --dry-run`), write nothing. Opt-out: `FA_PRE_TRUST=0` ⇒ skip (documented in `--help` env list).
- The `--trust-workspace` `--help` guard stays exactly as is (Spec 4).
- Contract test `test_pre_trust_contract.py`: for EVERY launch site listed in Phase 0 that accepts a first prompt, the helper call appears before the `claude` exec on the same code path (static check over the script text + one dynamic run per site with a fake `claude` that records argv AND the config state at exec time). Planted mutation: remove the call from one site ⇒ red. Empty extraction (no launch sites found) ⇒ FAIL, never pass.
- [ ] Step 1 — failing tests: `test_handover_launch_pre_trusts_before_claude_starts` (fake `claude` asserts the temp config has the key at exec time); `test_launch_without_prompt_does_not_pre_trust`; `test_pre_trust_opt_out`; `test_pre_trust_failure_still_launches_and_flags_fallback`; `test_dry_run_reports_and_writes_nothing`; `test_trust_workspace_guard_unchanged` (fake `claude --help` without the flag ⇒ not passed; with it ⇒ passed); plus the contract test.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS (targeted). Mutation per Receipts. Step 5: commit by path.

### Task 4: Fallback — notify once when the dialog is waiting

**Files:** Modify `bin/handover-verify.sh` (or the Python it calls); `bin/remote-session.sh` only if needed to pass the target; Test: `tests/test_handover_verify.py` (extend).

- Behaviour: when handover-verify times out with "no session registered" (the dialog-or-crash case) AND the target folder is known (`FA_PRE_TRUST_TARGET` or the launch cwd it already resolves), send ONE notification via `fa_notify.notify()`: title `free-agents handover waiting`, text `Claude Code is asking whether you trust <dir> — answer it in the new window to start the handover.` Detection window default 60 s (`--timeout` unchanged; the notify fires at the first timeout, not on every poll). If the session registers later, nothing more happens. Never a second notification for the same launch.
- [ ] Step 1 — failing tests (with `FA_NOTIFY_SINK`): `test_waiting_dialog_notifies_once`; `test_registered_session_never_notifies`; `test_notify_text_names_the_folder`; and the existing `tests/test_no_real_notifications_0541.py` still green.
- [ ] Step 2: FAIL. Step 3: implement. Step 4: PASS. Step 5: commit by path.

### Task 5: Docs, version 0.59.0, full gate

**Files:** `CHANGELOG.md` (new `## [0.59.0]` entry: "Fixed — handover into a never-trusted folder no longer waits at the trust dialog" — what the helper writes (the one sanctioned key), the user's trust rule, the fallback, the Task 1 truth table in two lines, and "supersedes the 0.56.8 attempt"), `VERSION`, `.claude-plugin/plugin.json`, any other version surface `rtc-ship.sh --versions-only` reports, `README.md`/the free-session-handover skill text where it describes the trust dialog (`skills/free-session-handover/SKILL.md` step 2 and `references/operational-notes.md` if they mention the dialog), `bin/remote-session.sh --help` env list (`FA_PRE_TRUST`).
- [ ] Bump all surfaces; `rtc-ship.sh --versions-only` (resolve the plugin's script path: `ls ~/.claude/plugins/cache/haiggoh/run-to-completion/ | sort -V | tail -1`) → all agree.
- [ ] Full gate: the `rtc-ship.json` `test_cmd` → `EXIT:0`, counts ≥ baseline + new tests. Push the branch.
**🧪 Receipt 5:** versions-only output, gate EXIT + counts, `git ls-remote origin fix/handover-pre-trust`.

### Task 6: Independent review, then ship

- [ ] Review by a DIFFERENT model via stateless dispatch: `bin/free-agent-tool.py --list` → pick the strongest that is not you; send the diff (`git diff origin/main... > $TMPDIR/fa-diff.patch`) + this plan + Spec, asking for correctness bugs, any path that writes more than the sanctioned key, any test that cannot fail. Repeat `model_used`/`route` in your reply. Verify every finding against the real code before acting (reviewers hallucinate names). No free route ⇒ STOP and ask the user.
- [ ] Ship via run-to-completion `ship` (worktree `~/.wt/fa-pre-trust`, never the canonical checkout); let rtc-ship's watcher handle a slow gate. **🧪 Receipt:** `git ls-remote --tags origin v0.59.0` SHA; `git ls-remote origin main` = merge commit; newest `~/.claude/plugins/cache/haiggoh/free-agents/` dir reads `0.59.0`.
- [ ] On a branch `docs/plan-0.59.0-complete` from the new `origin/main`, set this plan's Status to `completed`, commit, push the branch — do NOT merge it.

### Task 7: Dogfood against the real CLI (live — backup first)

- [ ] Step 1 — backup receipt: `cp -p ~/.claude.json "$HOME/.claude.json.bak.pre-trust-dogfood-<UTC-stamp>"` (size via `ls -l`).
- [ ] Step 2: create a fresh scratch repo NEVER opened in Claude Code: `$TMPDIR/fa-dogfood-<stamp>/repo` + a linked worktree `…/repo-wt`. Run the INSTALLED 0.59.0 helper in dry-run: `~/.claude/plugins/cache/haiggoh/free-agents/0.59.0/bin/fa_pre_trust.py <repo-wt> --dry-run --json` → shows the keys it would write.
- [ ] Step 3: run the Task 1 probe against `repo-wt` → `DIALOG` (known-bad baseline). Then run the installed helper for real → exit 0, `verified: true`. Probe again → `NO_DIALOG`.
- [ ] Step 4: home-folder fallback: `fa_pre_trust.py ~ --json` → exit 3, no write (file mtime unchanged).
- [ ] Step 5: remove the scratch DIRS; leave their `projects` entries (same reason as Task 1 Step 5); report the count.
**🧪 Receipt 7:** all outputs; the backup and the live file compare equal after dropping every `projects` key under the scratch roots AND ignoring top-level fields Claude Code updates on its own (`numStartups`, timestamps/caches — list which ones were ignored): print only `EQUAL`/`DIFFERENT` plus the ignored field NAMES, never values.

## ⚠️ Side-effect classification

| Step | Side effect | Containment |
|---|---|---|
| Task 1 probe, Task 7 dogfood | real `claude` processes; entries in the real `~/.claude.json` | backup first; scratch dirs under `$TMPDIR`; kill only own PIDs; remove only scratch entries afterwards |
| `fa_pre_trust.py` | writes `~/.claude.json` | one key only; backup + atomic replace + verify; tests on temp configs only; real-file-mtime guard test |
| notification | macOS banner | `FA_NOTIFY_SINK` in every test; the 0541 stray-notification test |
| ship | merge/tag/release/plugin update | rtc-ship only |
| running free sessions (including YOUR OWN) | could be killed by a careless stop | prohibited in the matrix (rule 12) |

## 🧨 Adversarial review

- Shortcut: stub `claude` that accepts anything → green suite, real launches break → exactly the 0.56.8 failure → rule: Task 1 and Task 7 run the REAL CLI; the fake in Task 3 records config state at exec time → detect: Receipt 1 + Receipt 7 probe outputs.
- Shortcut: `json.dump` the whole config with sorted keys/new indentation → "valid JSON" → harm: rewrites the user's file layout, may drop unknown fields or reorder → rule: load + set one key + dump preserving order (`json.dump(obj, f, indent=2, ensure_ascii=False)` matching the original's indent style — detect the original indent) → detect: `test_other_keys_untouched`.
- Shortcut: write the worktree path only (as originally assumed) → no effect, dialog still appears → rule: Task 1's truth table decides → detect: Task 7 Step 3 `NO_DIALOG`.
- Shortcut: run tests with the real HOME → harm: the user's real trust records get rewritten by tests → rule: temp config everywhere → detect: `test_real_claude_json_untouched`.
- Shortcut: print `~/.claude.json` while debugging → harm: OAuth/account data in the transcript → rule 10.
- Shortcut: "apply" the release by restarting proxies → harm: kills your own session and others → rule 12.
- Shortcut: pipe the gate into `tail` → false green → rule 5.
- Lower permission read as higher: pushing the branch is not merging; merging is not writing the real config; the real config is written only in Task 7 by the installed helper.
- Not safely testable: a concurrent live session overwriting the key right after we write it (covered by the verify + fallback notification, not by a deterministic test).

## 🏁 Final review and delivery (answer in the final reply)

1. Implemented and shipped (tag, release URL) vs only planned?
2. Which receipts ran; which tests did not run and why?
3. Task 1 truth table: which key(s) the CLI reads for a worktree — and does the shipped helper write exactly those?
4. Is the real `~/.claude.json` changed ONLY by scratch-path entries (Receipt 7 `EQUAL`)? Backups present?
5. Did anything restart or stop a proxy/session? (Expected: no.)
6. Did the run stop at its boundary (docs branch pushed, not merged)?
7. Was this plan re-read after its last edit?

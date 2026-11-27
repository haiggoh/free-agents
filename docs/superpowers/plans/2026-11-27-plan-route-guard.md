# Plan routing barrier + three-route offload prose — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (native execution by the coordinating session, which DELEGATES each `free:` task as tagged and verifies it; one fresh whole-branch review at the end). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A paid session cannot write a plan file whose tasks lack a free/cloud route tag (opt-out `plan_check 0`), the routing prose names the three real free routes (dispatch / headless session / handover), and both launchers support a headless session.

**Architecture:** New PreToolUse guard `hooks/plan-route-guard.py` (pure parser + decision, fail-open) switched by a new `plan_check` setting in `bin/cloud_session_env.py` with a csl menu row. The single-source routing block in `skills/offload/SKILL.md` is rewritten; every other prose copy follows it. `bin/launch-claude-agent.sh` gains `-- <claude args>` passthrough like `bin/remote-session.sh`.

**Tech Stack:** Python 3 stdlib (hooks, settings), bash (launcher, macOS bash 3.2-safe where the file already is), unittest/pytest (+ xdist).

**Spec:** `docs/superpowers/specs/2026-11-27-plan-route-guard-design.md` (read first).

**Routing (this plan follows its own rule):** tags are on each task heading. Free sessions run HEADLESS in the background (`remote-session.sh <alias> --prompt-file <brief.md> -- -p --output-format json < /dev/null`, `run_in_background`), each in its OWN worktree branched from `feat/plan-route-guard` (`free-agents:isolate-parallel-work`), briefed with `free-agents:brief-the-delegate`; the model comes from the session ladder at launch (`remote-session.sh --list` / `free-session-handover`), never from this plan. The coordinator verifies every free result against the task's named tests (`free-agents:verify-delegated-work`) before merging it. Tasks 1, 2 and 3 touch disjoint files and run in PARALLEL.

## Global Constraints

- Tags (exact): `free:dispatch`, `free:session`, `free:handover`, `cloud:<reason>`; optional role suffix `/<role>` (only meaningful for multi-agent coordination); legacy `local:<x>` / `remote:<x>` accepted as `free:`.
- Invalid `cloud:` reasons (case-insensitive substring): `tool use`, `tool-use`, `tools`, `too hard`, `too complex`, `local is slow`, `slow`, `not delegatable`.
- Plan file = `.md` under any `docs/superpowers/plans/` or under `~/.claude/plans/` (any depth) with ≥ 2 headings matching `^#{2,4} (Task|Step|Phase)\b`.
- A plan with zero `free:` tasks needs a line `^Routing: ` (anywhere) whose text holds no invalid reason.
- Guard fires only when `ANTHROPIC_BASE_URL` is not localhost (reuse the existing `is_free_session` test) AND `plan_check` is on. Fail OPEN on any internal error.
- Setting: env key `FA_PLAN_CHECK` in settings.json env, values `"1"`/`"0"`, default ON; CLI `cloud_session_env.py set plan_check {0,1}`; `show` prints `plan_check = ON|OFF`.
- Routing block ≤ 1,500 bytes, no model names, single source (test `test_routing_single_source_0540.py` stays green).
- Headless command form (exact): `remote-session.sh <alias> --prompt-file <brief.md> -- -p --output-format json < /dev/null`; local: `launch-claude-agent.sh <alias> [effort] -- -p --output-format json < /dev/null` (prompt via `--prompt-file` once Task 3 adds it there too, see Task 3).
- Never edit the plugin cache; never touch the canonical checkout's branch; stage by path.

## Review Focus

1. **A plan whose tags sit in a list item under the heading, not on the heading line** → accepted (section's first non-empty lines) — test in Task 2.
2. **An Edit that only fixes ONE task's tag in an otherwise valid plan** → judged on the RESULTING file, not the fragment — test in Task 2.
3. **A non-plan markdown file under `docs/superpowers/plans/` with one heading** (an index/README) → not a plan, allowed — test in Task 2.
4. **`launch-claude-agent.sh <alias> high --`** with nothing after `--` → behaves exactly like today (no stray empty arg) — test in Task 3.
5. **settings.json corrupt while `plan_check` is read** → guard fails open with one stderr line, never blocks — test in Task 2.

---

## File Structure

- Create `hooks/plan-route-guard.py` — `parse_plan(text) -> PlanReport`, `decide(path, text, env) -> Optional[str]` (deny reason or None), `main()` hook I/O. Has `--help`.
- Modify `hooks/hooks.json` — register the guard on `Write|Edit|MultiEdit` (PreToolUse).
- Modify `bin/cloud_session_env.py` — `plan_check()`, `save_plan_check()`, CLI `set plan_check`, `show` line.
- Modify `bin/session_picker_model.py` — one toggle row next to the route rows.
- Modify `bin/launch-claude-agent.sh` — `--` passthrough (+ `--prompt-file`/`--prompt` parity).
- Modify `skills/offload/SKILL.md` (routing block + "Decide at decomposition"), `skills/offload/references/offload-in-depth.md`, `skills/free-session-handover/SKILL.md`, `hooks/offload-nudge.py` (opening sentence + plan_check line), `README.md`.
- Create `tests/test_plan_route_guard.py`, `tests/test_plan_check_setting.py`, `tests/test_local_launcher_passthrough.py`; extend `tests/test_routing_single_source_0540.py`.
- Outside the repo: `~/.claude/CLAUDE.md`, `~/AGENTS.md` (Task 5).

---

### Task 1: `plan_check` setting + csl row — free:session

**Files:** Modify `bin/cloud_session_env.py`, `bin/session_picker_model.py`; Test `tests/test_plan_check_setting.py`.

**Interfaces:**
- Produces: `PLAN_CHECK_ENV_KEY = "FA_PLAN_CHECK"`; `plan_check(env: dict | None = None) -> bool` (settings.json env merged over `env`, same merge as `timeout_tier`; anything but `"0"` = ON); `save_plan_check(on: bool) -> Path` (via `_write_env((PLAN_CHECK_ENV_KEY,), {...: "1"|"0"})`); `PLAN_CHECK_LABELS = {True: "ON — plans need free:/cloud: route tags", False: "OFF — plan routing not checked"}`.
- CLI: `set` key choices gain `plan_check` (values `0`/`1`); `show` prints `plan_check = ON|OFF`; the `set` help string names it.
- Picker: a two-state toggle row "Plan routing check" directly after the `route[agent]` row, built like the `cloud_bypass_permissions` toggle (`(False, True)` values, getter `cse.plan_check()`, setter `cse.save_plan_check`).

- [ ] **Step 1: Failing tests:** `test_default_is_on` (no key → `True`); `test_zero_is_off`; `test_save_roundtrip_writes_only_its_key` (other env keys untouched); `test_cli_set_and_show` (`main(["set","plan_check","0"])` → stdout has `plan_check = OFF`); `test_cli_rejects_bad_value` (`set plan_check 2` → `SystemExit` non-zero); `test_picker_row_present_and_toggles` (find the row by label in the model's settings rows; toggling calls `save_plan_check`). Use `CLAUDE_CONFIG_DIR` → `tmp_path`.
- [ ] **Step 2:** `pytest tests/test_plan_check_setting.py -v` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `pytest tests/test_plan_check_setting.py tests/test_cloud_session_env*.py -v` → PASS; `python3 bin/cloud_session_env.py --help` lists `plan_check`.
- [ ] **Step 5:** Commit `feat: plan_check setting (FA_PLAN_CHECK) + csl row`.

### Task 2: The guard — free:session, then cloud:review — guard logic is the deliverable's core and blocks writes; I read every line before merge

**Files:** Create `hooks/plan-route-guard.py`, `tests/test_plan_route_guard.py`; Modify `hooks/hooks.json`.

**Interfaces:**
- Consumes: `cloud_session_env.plan_check` (Task 1; import via `sys.path` like `offload-nudge.py` does; module missing → treat as ON). The free-session test: `_is_free(base_url) -> bool` defined in the guard with the SAME rule as `hooks/cloud-agent-intercept.py:is_free_session` (hook files have dashes, so they are not importable by name); `test_free_rule_parity_with_intercept` loads the intercept via `importlib` and asserts both agree on localhost, 127.0.0.1, a gateway URL, empty, and None.
- Produces: `TAG_RE`, `INVALID_CLOUD = (...)` (Global Constraints list), `PlanReport(tasks: list[(heading, tag|None)], free_count: int, routing_line: str|None)`, `parse_plan(text) -> PlanReport`, `is_plan_path(path, home) -> bool`, `resulting_text(tool_name, tool_input) -> Optional[str]` (Write → content; Edit/MultiEdit → current file with edits applied, `replace_all` honoured; unreadable → None = allow), `decide(path, text, env) -> Optional[str]`, `main()`.
- Deny reason (exact shape): first line `🧭 free-agents plan_check: this plan has tasks without a route tag` (or `…uses an invalid cloud reason` / `…has no free: task and no valid Routing: line`), then one line per offending task `  - <heading>`, then `Tag each task: free:dispatch | free:session | free:handover | cloud:<checkable reason>`, then `Opt out: python3 <plugin>/bin/cloud_session_env.py set plan_check 0`.
- hooks.json: a NEW PreToolUse entry `{"matcher": "Write|Edit|MultiEdit", "hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/plan-route-guard.py\"", "timeout": 5}]}`.

- [ ] **Step 1: Failing tests** (pure functions + `main` via stdin JSON): `test_all_tagged_plan_allowed`; `test_untagged_task_denied_and_named`; `test_tag_in_first_list_item_counts` (Review Focus 1); `test_invalid_cloud_reason_denied` (each INVALID_CLOUD entry, parametrized); `test_valid_cloud_reasons_allowed` (`cloud:spec`, `cloud:verify`, `cloud:review`, `cloud:security`); `test_zero_free_needs_routing_line` + `test_zero_free_with_valid_routing_line_allowed` + `test_routing_line_with_invalid_reason_denied`; `test_legacy_local_and_remote_tags_accepted`; `test_role_suffix_accepted`; `test_edit_judged_on_resulting_file` (Review Focus 2: file on disk untagged in Task 2, Edit adds the tag → allowed; Edit that leaves Task 2 untagged → denied); `test_single_heading_md_is_not_a_plan` (Review Focus 3); `test_non_plan_path_ignored`; `test_free_session_never_blocks` (`ANTHROPIC_BASE_URL=http://localhost:4141`); `test_plan_check_off_never_blocks`; `test_corrupt_settings_fails_open` (Review Focus 5); `test_garbage_stdin_fails_open`; `test_free_rule_parity_with_intercept`; `test_hooks_json_registers_guard`; `test_help_runs` (subprocess `--help` exit 0).
- [ ] **Step 2:** `pytest tests/test_plan_route_guard.py -v` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** `pytest tests/test_plan_route_guard.py -v` → PASS. **Mutation check:** delete one INVALID_CLOUD entry → its parametrized case RED; make `decide` ignore `plan_check` → `test_plan_check_off_never_blocks` RED. Restore.
- [ ] **Step 5:** Commit `feat: plan-route-guard — a paid session cannot write an untagged plan`.

### Task 3: Local launcher passthrough — free:session, live check cloud:verify — the live check judges a free result on the real local stack

**Files:** Modify `bin/launch-claude-agent.sh`; Create `tests/test_local_launcher_passthrough.py`.

**Interfaces:**
- Produces: everything after a literal `--` is appended to the claude argv AFTER `"${CLAUDE_EXTRA_ARGS[@]}"` on the `la_memguard_claude … --` line (`:1024`); `--prompt-file PATH` / `--prompt TEXT` accepted before the alias with `remote-session.sh` semantics (content becomes claude's first positional, BEFORE the passthrough; missing file → exit 2 before any backend work). `--dry-run` prints the passthrough args. Usage/`--help` text documents both.
- [ ] **Step 1: Failing tests** (drive `--dry-run-skip-preflight` with a config fixture, like the existing launcher tests): `test_passthrough_args_reach_claude_argv`; `test_double_dash_alone_adds_nothing` (Review Focus 4); `test_prompt_file_content_is_first_positional`; `test_missing_prompt_file_exits_2_before_backend`; `test_help_documents_passthrough`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement (bash 3.2-safe: guard empty-array expansion with `${arr[@]+"${arr[@]}"}`). **Step 4:** run → PASS; `bash -n bin/launch-claude-agent.sh`.
- [ ] **Step 5:** Commit `feat: launch-claude-agent.sh -- passthrough + --prompt-file (headless local sessions)`.
- [ ] **Step 6 (coordinator, cloud:verify):** RAM check first (`la-evict.sh --tier 0` is pre-approved); one live headless local session with the same `HEADLESS-OK <sha>` brief used in the design's live check → EXIT 0, `num_turns ≥ 2`, sha matches. Record output in the commit message of Task 4's commit.

### Task 4: Routing prose — cloud:spec for the routing block + nudge sentence (the one text every paid session receives; ≤ 1,500 bytes), then free:session to propagate it

**Files:** Modify `skills/offload/SKILL.md`, `hooks/offload-nudge.py`, `skills/offload/references/offload-in-depth.md`, `skills/free-session-handover/SKILL.md`, `README.md`; extend `tests/test_routing_single_source_0540.py`.

- [ ] **Step 1 (cloud:spec):** rewrite the `fa:routing` block: (1) shape test → three routes with the exact commands (dispatch; headless session both lanes, `run_in_background`, `< /dev/null`; handover = own window, `free-session-handover`); (2) plans: tag every task `free:dispatch|session|handover` or `cloud:<checkable reason>`, tool use is never a cloud reason, the barrier + `plan_check` opt-out; (3)–(4) keep announce + paid-only-with-reason. Rewrite the nudge's first sentence to cover PLANNING ("before you write a plan or make ANY Agent/Task/Workflow call …"), and add one line naming `plan_check` state + opt-out when ON.
- [ ] **Step 2 (cloud:spec): failing tests** in `test_routing_single_source_0540.py`: `test_block_names_three_routes` (contains `free:dispatch`, `free:session`, `free:handover`); `test_block_headless_form_parses` (the block's `remote-session.sh … -- -p` line, split with `shlex`, is accepted by the real parser via `remote-session.sh --dry-run`); `test_block_has_no_no_headless_claim` (no `no headless`); `test_nudge_mentions_plans`. Existing ≤ 1,500-byte test stays.
- [ ] **Step 3 (free:session):** propagate to the "Decide at decomposition" section, `offload-in-depth.md` (§ routes + tag list; drop role defaults for single offloads; roles only for multi-agent coordination), `free-session-handover` (session ≠ handover wording), README; brief = this task + the new block verbatim + the spec's Goals 1–2.
- [ ] **Step 4:** `pytest tests/test_routing_single_source_0540.py tests/test_offload_nudge_single_source_0540.py tests/test_cloud_agent_intercept.py -v` → PASS; `grep -rn "no headless" skills hooks README.md` → no hits.
- [ ] **Step 5:** Commit `docs: three free routes (dispatch / headless session / handover) + free: tags`.

### Task 5: User rule files — cloud:not-worth-offloading — four lines in two private files outside the repo, edited in place after a backup

- [ ] **Step 1:** back up `~/.claude/CLAUDE.md` and `~/AGENTS.md` to `~/.claude/backups/claude-md-free-tags-2026-11-27/` and `cmp` the copies.
- [ ] **Step 2:** fresh Read of the Credit Efficient Mode block in each; Edit in place: `local:<role>` → `free:dispatch|session|handover`, `local:` slices → `free:` slices; nothing else changes. Show the user the diff in the reply.

### Task 6: CHANGELOG + version + portable — free:dispatch for the CHANGELOG/README draft, cloud:verify for the gate

- [ ] **Step 1 (free:dispatch):** dispatch the branch diff (`git diff origin/main...HEAD -- . ':!portable' ':!dist'`) + the spec; ask for a CHANGELOG entry in the repo's existing style. Coordinator checks every claim against the diff.
- [ ] **Step 2:** version = next free minor: `git fetch --tags` + `git ls-remote --tags origin` first (shared namespace); bump every version surface the repo's `rtc-ship.json` names; regenerate `portable/`/`dist/` with the repo's own build step.
- [ ] **Step 3 (cloud:verify):** full gate `pytest -n auto -rs` → all PASS; list every SKIP with its reason.
- [ ] **Step 4:** Commit `chore(<version>): CHANGELOG, version, portable`; push.

### Task 7: Dogfood the barrier on a real plan — cloud:verify — judging the guard's verdicts is the coordinator's job

- [ ] **Step 1:** With the branch's hook loaded via `claude --plugin-dir <worktree>` in a NEW paid session (user-visible window via `launch-claude-session`, never headless for a check the user should see): ask it to write the check-code plan WITHOUT tags → denied, message names the tasks; with tags → allowed; `set plan_check 0` → allowed untagged; set back to 1.
- [ ] **Step 2:** Add route tags to `check-code`'s `docs/superpowers/plans/2026-11-27-typescript-native-resolver.md` per the routing table agreed on 2026-11-27, committed on `feat/ts7-native-resolver`.

### Task 8: Whole-branch review — free:dispatch (cross-family second opinion), then cloud:review (final)

- [ ] **Step 1:** dispatch `git diff origin/main...HEAD` + spec to a free reviewer with `"avoid_family"` set to the family that wrote most of the code (from the free sessions' `model_used`).
- [ ] **Step 2:** one fresh final review on the most capable model; fix findings with red-green; re-run the gate.

### Ship — cloud:release — GATED on the user's explicit go and depth

Not part of executing this plan. On go: `rtc-ship.sh <worktree> <version> "<title>"` from the WORKTREE, verify with `git ls-remote` + `get-haiggoh plan`, refresh the installed plugin, dogfood Task 7-Step 1 through the INSTALLED copy. Then waypoint `summon-skills-suggest-free` becomes actionable.

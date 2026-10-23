# Cloud Screen Completion and Token-Profile Submenu — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every row of csl's Cloud Session Configuration screen act as labelled (per-path subagent routing with honest paid fallback, a bypass default, an evidence-gated cost box), move registry auto-sync to the Local screen, and add a measured token-savings submenu per lane.

**Architecture:** Cloud settings stay in Claude Code's own `~/.claude/settings.json` (`bin/cloud_session_env.py` owns every key it writes). Routing is enforced by the existing PreToolUse hook (`hooks/cloud-agent-intercept.py`) plus a session-scoped "free exhausted" mark written by `bin/free-agent-tool.py`. The token submenu is a new picker screen backed by csl's store for the free lanes and by ONE registered `--settings` profile for the cloud lane; its numbers come from a new measurement tool that runs headless free-lane sessions.

**Tech Stack:** Python 3 (stdlib), Textual (picker venv `~/.local/share/free-agents/picker-venv`), bash 3.2-compatible shell, pytest + pytest-xdist, the repo's `~/.claude/scripts/fa-ship.sh` gates.

**Spec:** `docs/superpowers/specs/2026-10-23-cloud-screen-and-token-profiles-design.md` (approved 2026-10-23, including every `[my call]`). Read it before starting; this plan argues from it.

## 🚨 System instructions for the AI developer — read first

- This plan is self-contained; do not depend on a previous chat. Revalidate live source and tool schemas before mutation.
- Output exactly three strategy sentences before file edits; then perform the read-only preflight (Task 0). Preserve dirty work and isolate changes in a worktree.
- Prefer native Read/Glob/Grep/Edit; use the shell for git, tests and execution. Do not rewrite a file from fragments; no placeholder implementations.
- Respect the authority matrix. Push permission does not authorize merge, tag or release.
- Every task ends with a receipt (command, expected output, exit status). A failure means STOP, diagnose, fix, rerun, and show both runs.
- Never touch the user's real `~/.claude` from a test. Every test that starts the picker or a cloud_session_env writer runs under a sandboxed `CLAUDE_CONFIG_DIR`; the suite tripwire in `tests/conftest.py` must stay green. Never prove a guard by damaging real state; use a fake `HOME`.

## Global Constraints

- Every row on the Cloud screen changes CLOUD sessions only; no row may claim an effect it does not have on the lane it names.
- Routing modes, exact values: `free-first` (DEFAULT) · `free-only` · `paid`. Row labels: **"Free first, paid as last resort"**, **"Free only"**, **"Paid (free-agents stays out)"**.
- Honest exhaustion (tunable in `config/dispatch-preferences.json`, key `paid_fallback`): `min_real_attempts: 3`, `min_providers: 2`, `retry_after_max_s: 30`, `wall_clock_cap_s: 600`. Only sent-and-failed requests count; skipped entries (no key, no weights) do not.
- The fallback mark is session-scoped, single-use and expires (15 minutes [plan default]).
- `permissions.defaultMode` written only to `~/.claude/settings.json`, values `auto` / `bypassPermissions`; never override a managed `disableBypassPermissionsMode: "disable"`.
- Cloud-lane token levers are written ONLY into the registered cloud profile; never into `~/.claude/settings.json`.
- Files csl does not own: owned keys edited in place, everything else byte-preserved, file mode kept, symlinks followed, timestamped backup before the first write.
- Internal compatibility names stay: `memguard`, `LA_MEMGUARD_*`, `LA_REGISTRY_AUTO_SYNC`, `FA_REPLACE_AGENTS` (legacy alias: `1` → `free-first`, `0` → `paid`).
- Every new script supports `--help` and exits non-zero with a usage line on an unknown flag.
- Shell must run under macOS `/bin/bash` 3.2 (no `declare -A`, no `${x,,}`).
- Version surfaces bump together: `.claude-plugin/plugin.json`, `VERSION`, `docs/ROADMAP.md`, `CHANGELOG.md` (`tests/test_version_consistency.sh`).

## 🔒 Authority matrix

| Operation | Status | Gate |
|---|---|---|
| Read-only source inspection | allowed now | — |
| Create a worktree + feature branch, edit files there | allowed now | Task 0 receipt |
| Commit on the feature branch (staged BY PATH) | allowed after the task's tests pass | test receipt |
| Push the feature branch | allowed after full gates | `fa-ship.sh --test-only` green |
| Merge to main / tag / release / brew bump | **requires separate user permission** (ask at the boundary) | full gates + GLM-branch check |
| Free-lane headless runs (measurement, spikes) | allowed (cost $0) | never on the endpoint serving the current session |
| Paid/cloud requests (eligibility probe runs, cloud spot-checks) | **requires user permission per run** | state the expected cost first |
| Edit the user's real `~/.claude/settings.json` or launch profiles | **prohibited** outside the released feature used by the user | sandboxed tests only |
| Kill/restart processes, delete files outside the worktree | prohibited | — |

## Review Focus

1. **A fallback mark from one session unlocking another** (two cloud sessions open at once) → the mark is keyed by session id and consumed once; a second session's `Agent` call stays redirected. Test in Task 4.
2. **A corrupt or non-object `settings.json` / profile** → every writer refuses, writes nothing, and csl shows the warning instead of crashing. Tests in Tasks 3, 7, 11.
3. **The registered profile edited by hand while csl is open** → writes re-read the file right before writing (no stale overwrite of the user's edit). Test in Task 11.
4. **An upgrading user with `FA_REPLACE_AGENTS=0`** → reads as `paid`: no surprise interception and no offload nudge for that path. Test in Task 3.
5. **The probe started from a free-lane shell** (`ANTHROPIC_BASE_URL` is localhost) → refuses with a reason, never sends a cloud request. Test in Task 8.

---

## Release 1 — Cloud screen completion (+ auto-sync move)

### Task 0: Preflight (no mutation before its receipt)

**Files:** none modified.

- [ ] **Step 1:** Write the three strategy sentences (how Task 1's change connects to the integrated outcome).
- [ ] **Step 2:** In the canonical checkout `~/ClaudeWorkspace/free-agents` (never switch its branch): `git fetch && git status --porcelain && git log --oneline -3 origin/main`. Expected: clean, on `main`.
- [ ] **Step 3:** Check the concurrent GLM 5.3 Flash branch: `git branch -r | grep rapid-auto-mode-tests` and `git -C ../free-agents-fix-autotests log --oneline -1`. Record whether it is pushed or merged (it ships as 0.42.1; this release takes the next number at release time).
- [ ] **Step 4:** `git worktree add -b feat/cloud-screen-completion ../free-agents-wt-cloud origin/main`; `cp -p config/config.local.sh ../free-agents-wt-cloud/config/` (gitignored; the drift test needs it).
- [ ] **Step 5:** Baseline gates in the worktree: `~/.claude/scripts/fa-ship.sh --test-only <worktree>`. Expected: three lanes green. If the limiter test `test_librarian_retries_after_a_429` fails only under `-n`, record it as the known order-dependent flake (not caused by this work).

**Receipt 0:** the outputs of Steps 2–5.

### Task 1: Registry auto-sync moves to the Local screen

**Files:**
- Modify: `bin/session_picker_model.py` (OOM screen `actions()`; `_tool_actions`/Local screen actions; `Settings` dataclass)
- Modify: `bin/session_picker.py` (`Picker.__init__` Settings construction: wire the new callback to `self._save_mg`)
- Modify: `bin/session_picker.py` docstring of `auto_sync_registry` ("Memory guard screen's" → "Local screen's")
- Test: `tests/test_session_picker_model.py`, `tests/test_new_features.py` (`MemoryGuardSettings` uses `a` on the OOM screen today), `tests/test_session_picker_pty.py` (~375)

**Interfaces:**
- Produces: `Settings.on_memguard_saved: Callable[[dict], object] | None = None`; Local screen action key `"y"`, label `f"Auto-sync local registry with the disk (no prompts): {onoff(...)}"`, toggling `settings.memguard["registry_auto_sync"]` and calling `on_memguard_saved({"registry_auto_sync": v})`.

- [ ] **Step 1: Failing tests**

```python
def test_auto_sync_is_a_local_screen_setting_not_an_oom_one(self):
    s = m.Settings()
    oom = m.MemGuardScreen(s)            # use the real class name found in preflight
    self.assertFalse(any("Auto-sync" in a.label for a in oom.actions()))
    saved = []
    s.on_memguard_saved = saved.append
    local = {a.key: a for a in m.LocalScreen(s, []).actions()}
    self.assertIn("Auto-sync local registry with the disk", local["y"].label)
    local["y"].run()
    self.assertEqual(saved[-1], {"registry_auto_sync": True})
```

Update the pilot test in `test_new_features.py` to press `l` then `y` on the Local screen, assert `saved_state()["memguard"] == {"registry_auto_sync": True}` and that a fresh picker shows it ON.

- [ ] **Step 2:** Run `python3 -m pytest -q tests/test_session_picker_model.py -k auto_sync` → FAIL (row still on the OOM screen).
- [ ] **Step 3:** Move the row; storage key and `LA_REGISTRY_AUTO_SYNC` export unchanged.
- [ ] **Step 4:** Run the model test, then the pilot: `~/.local/share/free-agents/picker-venv/bin/python -m pytest -q tests/test_new_features.py tests/test_session_picker_pty.py -k "auto_sync or AutoSync or MemoryGuard"` → PASS.
- [ ] **Step 5:** Commit (by path).

### Task 2: Spikes — settle the three `[verify]` facts the rest depends on

**Files:** append results to the spec's §8 (Open branches) as dated facts. No product code.

- [ ] **V1 Workflow interception.** In a FREE remote session (cost $0), run a tiny ultracode/Workflow with one agent while a temporary PreToolUse hook (in a sandboxed `--settings` file, not the user's settings) logs every `tool_name` it sees. Receipt: whether workflow agents arrive as `Agent` PreToolUse events, another tool name, or not at all. Decision rule: if no interception point exists, Task 6's Workflow row is NOT built.
- [ ] **V2 Cloud-lane levers.** On a free lane, measure prefill (`usage` from `claude -p "ok" --output-format json`) for: baseline; `permissions.deny: ["WebFetch"]` in a `--settings` file; an MCP server disabled via a settings key (find the documented key in code.claude.com/docs/en/settings-reference and mcp docs); `enabledPlugins` with one plugin off. Receipt: which keys REMOVE definitions from the prefill vs only block calls. This decides which cloud-lane categories are working rows in Task 11.
- [ ] **V3 Positive eligibility signal.** Ask the user for permission (one small paid request). Then one plain `claude -p --permission-mode auto --output-format stream-json` run with the env from `joyia agent claude --setup-only --json` (token only in the child env, never printed) with a `fa-eligibility-probe-<random>` command; capture the stream, the `system` messages, the init message and the debug log; check the gateway ledger (cost-tracker) for a classifier-model request. Receipt: whether ANY field states the server-review state. Decision rule: none → Task 8's verdicts are only `not-eligible` / `unknown`.

### Task 3: Routing modes in the settings layer

**Files:**
- Modify: `bin/cloud_session_env.py`
- Test: `tests/test_cloud_session_env.py`

**Interfaces:**
- Produces: `ROUTE_PATHS = ("agent", "workflow")`; `ROUTE_ENV = {"agent": "FA_ROUTE_AGENT", "workflow": "FA_ROUTE_WORKFLOW"}`; `ROUTE_MODES = ("free-first", "free-only", "paid")`; `route(path: str, env: dict | None = None) -> str` (reads settings.json env merged over `env`; legacy `FA_REPLACE_AGENTS`: `"1"` → `free-first`, `"0"` → `paid`, applies to `agent` only; default `free-first`); `save_route(path: str, mode: str) -> Path` (raises `ValueError` and writes nothing on an unknown path/mode; removes `FA_REPLACE_AGENTS` when writing the agent path); CLI `cloud_session_env.py set route <path> <mode>` and `show` prints each path's mode.

- [ ] **Step 1: Failing tests** — `test_route_defaults_to_free_first`, `test_legacy_replace_agents_maps_1_free_first_0_paid` (Review Focus 4), `test_save_route_round_trips_and_drops_the_legacy_key`, `test_invalid_route_writes_nothing`, `test_corrupt_settings_refuses_to_write` (Review Focus 2: non-object JSON → `ValueError`, file bytes unchanged).
- [ ] **Step 2:** Run → FAIL (`route` undefined).
- [ ] **Step 3:** Implement on top of the existing `_write_env`.
- [ ] **Step 4:** Run → PASS. `python3 bin/cloud_session_env.py --help` prints the new subcommand.
- [ ] **Step 5:** Commit.

### Task 4: Honest exhaustion + the session-scoped fallback mark

**Files:**
- Modify: `bin/free-agent-tool.py`, `config/dispatch-preferences.json` (add `paid_fallback`)
- Create: `bin/fa_fallback_mark.py` (small module shared by the tool and the hook)
- Test: `tests/test_free_agent_tool_preferences.py`, create `tests/test_fallback_mark.py`

**Interfaces:**
- Produces in `bin/fa_fallback_mark.py`: `MARK_DIR` (env `FA_FALLBACK_MARK_DIR`, default `~/.claude/free-agents/paid-fallback`); `write_mark(session_id: str, attempts: list, ttl_s: int = 900) -> Path` (mode 600); `consume_mark(session_id: str, now: float | None = None) -> dict | None` (returns the mark and deletes it if unexpired; expired or missing → `None`).
- Produces in `free-agent-tool.py`: `exhausted(attempts: list, available: list, policy: dict) -> bool` — True when real failed attempts ≥ `min_real_attempts` across ≥ `min_providers` distinct providers, OR every available entry failed. Each attempt record gains `"provider"`. One retry for a 429 whose Retry-After ≤ `retry_after_max_s` [verify in preflight whether `remote-agent-dispatch.py` surfaces Retry-After; if not, a 429 is a real failure with no retry]. No new attempt starts after `wall_clock_cap_s`. On total failure: result JSON (stderr error object) gains `"paid_fallback_allowed": bool`; when True and `CLAUDE_CODE_SESSION_ID` is set, `write_mark(...)` is called.

- [ ] **Step 1: Failing tests** — `test_exhausted_needs_three_real_attempts_on_two_providers` (3 NVIDIA failures → False; 2 NVIDIA + 1 Gemini → True); `test_skipped_entries_do_not_count`; `test_every_available_failed_counts_when_fewer_exist` (one key, one failure → True); `test_wall_clock_cap_stops_new_attempts` (fake clock); `test_mark_is_single_use_and_session_scoped` (session A's mark is not consumed by B — Review Focus 1); `test_expired_mark_is_ignored`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS; the original sparse-setup tests still pass.
- [ ] **Step 5:** Commit.

### Task 5: The intercept hook follows the modes

**Files:**
- Modify: `hooks/cloud-agent-intercept.py` (`decide()`; remove the ask-once state file path and `build_ask_reason`)
- Test: the hook's built-in self-test cases and `tests/` file that covers it (find with `Grep cloud-agent-intercept tests/`)

**Interfaces:**
- Consumes: `cloud_session_env.route("agent", env)`, `fa_fallback_mark.consume_mark(session_id)` (session id from the hook payload's `session_id`).
- Behaviour: free session → pass through (unchanged); `paid` → pass through; `free-only` → deny with the redirect, text says it never falls back; `free-first` → deny with the redirect, UNLESS `consume_mark(session_id)` returns a mark → pass through (the one native paid call), and the decision reason names the free attempts that failed.

- [ ] **Step 1: Failing tests** — one per mode; `test_free_first_with_a_valid_mark_lets_exactly_one_call_through` (second call denied); `test_no_ask_once_prompt_remains` (undecided user → deny-redirect, never `ask`).
- [ ] **Step 2–4:** red → implement → green.
- [ ] **Step 5:** Commit.

### Task 6: Workflow path (only if V1 found an interception point)

**Files:** `hooks/hooks.json` (new matcher), `hooks/cloud-agent-intercept.py` (same `decide` with path `workflow`), tests as in Task 5.

- [ ] If V1 found none: skip the task, keep `ROUTE_PATHS` but hide the Workflow row (Task 7), and record the gap on waypoint `csl-cloud-session`. Otherwise red → green per mode, then commit.

### Task 7: Startup nudge and Cloud screen rows

**Files:**
- Modify: `hooks/offload-nudge.py` (`paid_nudge(root, modes: dict)`), `bin/session_picker_model.py` (`CloudConfigScreen`; `Settings.routes: dict[str, str]` replacing `replace_agents`), `bin/session_picker.py` (load `routes` via `cse.route()` per path in `Picker.__init__`; `_save_cloud` handles `{"route": (path, mode)}` via `cse.save_route`, same warning-on-failure pattern as today)
- Test: `tests/test_offload_nudge.py`, `tests/test_session_picker_model.py`, `tests/test_cloud_session_env.py`

**Interfaces:**
- `paid_nudge(root: str, modes: dict[str, str]) -> str`: for a `paid` path the text must NOT call offloading the default for it and says free agents are used only when the user asks; a `free-only` path says it never falls back; all-`free-first` keeps today's wording plus the fallback sentence.
- `CloudConfigScreen`: replace row `x` with one row per offered path (keys `x` = Agent, `w` = Workflow), each cycling `ROUTE_MODES` in order and saving via `Settings.on_cloud_saved({"route": (path, mode)})`; `info_text(item_id)` returns the mode's info-box text from the spec table (exact gist sentences). [verify in preflight what `item_id` a plain Action row passes to `info_text` — the API Keys screen is the reference implementation.]

- [ ] **Step 1: Failing tests** — `test_paid_path_is_never_called_offload_by_default`; `test_free_only_path_says_no_fallback`; `test_cloud_rows_cycle_the_three_modes_and_persist`; `test_info_box_explains_each_mode` (assert each gist sentence).
- [ ] **Step 2–4:** red → implement → green, then the picker pilot lane.
- [ ] **Step 5:** Commit.

### Task 8: Bypass default row (A2) + eligibility probe (A3)

**Files:**
- Modify: `bin/cloud_session_env.py` — `default_mode() -> str | None`, `save_default_mode(mode: str) -> Path` (only `auto` / `bypassPermissions`; writes `permissions.defaultMode`, everything else preserved; generalise `_write_env` into a `_write_doc(mutate)` helper both use), `bypass_blocked_by_policy() -> str | None` (reads macOS managed settings at `/Library/Application Support/ClaudeCode/managed-settings.json` and `managed-settings.d/*.json`; path overridable by `FA_MANAGED_SETTINGS_DIR` for tests)
- Create: `bin/fa-eligibility-probe.py` (`--help`, `--credential-command CMD` [default `joyia agent claude --setup-only --json` when `joyia` is on PATH; skipped when `ANTHROPIC_BASE_URL` + a token or a native login are already present], `--json`; runs plain `claude`; the token travels only in the child env and never in argv, logs, cache or output; verdict cache at `~/.local/state/free-agents/eligibility.json` with date and evidence; refuses when the resolved base URL is localhost — Review Focus 5; reports `unknown` without running when any settings layer allows `Bash` or `Bash(*)`)
- Modify: `CloudConfigScreen` — row `b` "Default permission mode for sessions you start yourself: Auto / Bypass permissions" (disabled with the policy reason when blocked; shows "overruled by <profile>" when the registered cloud profile sets `defaultMode` — registration arrives in Release 2 (Task 11), so in Release 1 this branch is built and tested against a fixture store value only); row `e` "Check whether auto mode's safety checks are free here" (asks for confirmation, states it sends one small request, runs the probe); `info_text` for `b` shows the gateway cost box ONLY when the cached verdict is `not-eligible`
- Test: `tests/test_cloud_session_env.py`, create `tests/test_eligibility_probe.py` (stub launcher executable printing canned stream-json; never a real request)

- [ ] **Step 1: Failing tests** — `test_default_mode_round_trip_preserves_every_other_key`; `test_managed_policy_disables_the_row`; `test_probe_refuses_on_a_free_lane_shell`; `test_credential_command_env_reaches_claude_but_never_argv_or_output` (stub credential command prints a fake token; assert the stub `claude` saw it in env and that it appears in no argv, stdout, stderr or cache file); `test_probe_reports_unknown_when_bash_is_blanket_allowed`; `test_probe_reads_the_notice_as_not_eligible` (stub emits the system warning text); `test_absent_notice_alone_is_never_eligible` (stub emits nothing → `unknown`, unless V3 found a positive signal); `test_cost_box_only_for_not_eligible`.
- [ ] **Step 2–4:** red → implement → green. Run `python3 bin/fa-eligibility-probe.py --help` and `--bogus` (expect exit 2 + usage).
- [ ] **Step 5:** Commit.

### Task 9: Release 1 — docs, version, gates, ship boundary

- [ ] README: Cloud Session Configuration section (three modes, bypass default, probe), Local screen auto-sync.
- [ ] CHANGELOG entry; bump all four version surfaces; `bash tests/test_version_consistency.sh`.
- [ ] `fa-ship.sh --test-only <worktree>` → three lanes green; confirm the real `~/.claude/settings.json` mtime is unchanged (conftest tripwire green).
- [ ] Push the feature branch. **STOP: ask the user before merge/tag/release.** On approval, re-check the GLM branch, then `fa-ship.sh <worktree> <version> "<summary>"`, visibility check, dogfood on the installed copy (routing modes against a stub dispatch, rows and info boxes via the installed picker model, cleanup and probe against sandboxed copies of real state).

---

## Release 2 — Token-savings submenu (after Release 1)

### Task 10: Step 0 — the measurement tool

**Files:**
- Create: `bin/fa-prefill-measure.py` (`--help`; `--lane local|remote|cloud`; `--lever <name>=<level>` repeatable; `--json`), cache `~/.local/state/free-agents/prefill-measurements.json` keyed by lane + inputs hash (installed plugins list, MCP config files, profile file hash, CLAUDE.md hashes)
- Test: create `tests/test_prefill_measure.py` (stub `claude` printing canned `--output-format json` usage)

**Interfaces:**
- Produces: `measure(lane: str, levers: dict[str, str]) -> dict` → `{"input_tokens": int, "cache_creation_input_tokens": int, "cache_read_input_tokens": int, "total_prefill": int, "inputs_hash": str, "at": str}`; `cached(lane, levers) -> dict | None` (stale when the inputs hash changes).
- Runs a FREE lane only (cloud lane = a free-lane run with the cloud profile's settings and flags applied). [verify in preflight how to start a headless free-lane `claude -p` through `remote-session.sh` / `csl`; never against the endpoint serving the current session.]

- [ ] **Steps:** red tests (`test_total_prefill_is_the_sum_of_usage_fields`, `test_cache_goes_stale_when_inputs_change`, `test_never_runs_against_a_cloud_base_url`) → implement → green → commit.
- [ ] **Measurement receipt:** per MCP server (397 tools on the maintainer's machine; magnific 222 first), CLAUDE.md + memory, SessionStart hook output, plugin skills, tools levels — a table in the spec's §5 B3 with real numbers. Present it to the user before Task 11 proposes level contents.

### Task 11: The submenu, free lanes and the cloud profile

**Files:**
- Create: `bin/token_profiles.py` (levels per category and lane; mapping to launcher flags and profile keys; the cloud-profile writer)
- Modify: `bin/session_picker_model.py` (`TokenSavingsScreen`, Home key `t`; remove the `m` MCP rows from Local/Remote, now the MCP category), `bin/session_menu_state.py` (store: `token_profiles` block + `cloud_profile_path`; schema bump with migration of `enable_mcp`), `bin/launch-claude-agent.sh`, `bin/remote-session.sh` (accept the chosen `--tools` list and MCP level; `LA_ENABLE_MCP` still honoured)
- Test: `tests/test_token_profiles.py`, picker model + pilot tests, launcher dry-run tests

**Interfaces:**
- `CATEGORIES = ("tools", "mcp", "plugins")` (+ others only where V2 / step 0 show a lever); levels per category, e.g. `tools: ("all", "restricted", "minimal")`, `mcp: ("all", "essential", "none")`; level contents proposed from the lean profiles and step-0 numbers and approved by the user.
- `write_cloud_profile(path: Path, levels: dict) -> Path`: re-reads the file immediately before writing (Review Focus 3), edits only owned keys, backup `<path>.bak-<UTC timestamp>` before the first write, refuses non-object JSON (Review Focus 2); `create_cloud_profile() -> Path` creates `~/.claude/launch-profiles/csl-cloud.json` (mode 600) and returns the `--settings` line to show.
- Tally: shows the current total and, per row, `saves X%` relative to "everything on"; "not measured" when no measurement exists, with an explicit, confirmed paid-measurement offer only when no free lane is available.

- [ ] **Steps:** red tests (`test_mcp_toggle_migrates_into_the_mcp_category`, `test_cloud_lane_never_writes_settings_json`, `test_cloud_profile_write_keeps_hand_edits`, `test_unregistered_cloud_lane_offers_to_create_a_profile`, `test_category_without_a_settings_lever_is_read_only_on_cloud`, `test_tally_saves_percent_against_everything_on`) → implement → green → launcher dry-runs show the flags for each level → commit.

### Task 12: Release 2 — docs, version, gates, ship boundary

As Task 9, plus the README section for the submenu and the measured table.

---

## 🧨 Adversarial review

| Tempting shortcut | False success | Harm | Rule | Detecting test/receipt |
|---|---|---|---|---|
| Test the picker without sandboxing `CLAUDE_CONFIG_DIR` | Suite green | Real settings edited (happened in 0.42.0) | Sandbox every picker/writer test | conftest tripwire; real file mtime in the Task 9 receipt |
| Treat "no notice" as eligible | Probe says free | Cost box hidden for a billed user, or a false "free" claim | Positive signal or `unknown` | `test_absent_notice_alone_is_never_eligible` |
| Count skipped entries as attempts | Fallback triggers fast | Paid spend after zero real free attempts | Only sent-and-failed requests count | `test_skipped_entries_do_not_count` |
| Leave the nudge unchanged | Rows switch | `paid` paths still offloaded by the agent | Nudge reads the modes | `test_paid_path_is_never_called_offload_by_default` |
| Build the Workflow row without V1 | Row cycles and saves | A row that acts on nothing | V1 decides; else hide | V1 receipt |
| Write cloud levers to settings.json "for convenience" | Cloud saves appear | Leaks into free sessions; deny rules cannot be undone | Registered profile only | `test_cloud_lane_never_writes_settings_json` |
| Estimate the tally | Numbers appear | Users trust invented savings | Measured or "not measured" | Task 10 receipt |
| Run the probe or a cloud measurement automatically | Feature "just works" | Unasked spend | Explicit confirmation per run | Task 8 tests; authority matrix |

Cannot be tested safely without the user: the real eligibility verdict on JoyIA (V3, paid), and the cloud spot-check that definitions do not depend on the serving model.

## 🏁 Final review and delivery (answer before each release boundary)

What is implemented versus planned? Which receipts ran? Which tests did not run? Is user work preserved (GLM branch untouched, canonical checkout on main)? Was the real `~/.claude` untouched by tests? Do direct CLI (`cloud_session_env.py`, the probe, the measurement tool) and the picker agree? Did the work stop at the merge boundary until the user approved?
